"""Library check: finds and repairs files damaged by the old title-matching scan bug.

Until 1.9.50 a scan could move an already-downloaded file onto a new video's path and
rename it to that video's name, so a row may point at a file holding another video, and
the video that really owns the file may have lost it. The check only reports; nothing
changes until fixes are applied.

Evidence, strongest first:
- certain: the file's embedded source URL (a `purl` tag, or a `comment` that is exactly one
  URL) or its sibling .info.json names a different video than the row pointing at it.
  The `[id]` in the file name is used only when neither exists.
- likely: the file's length fits a video of the same channel whose file is gone, and either
  the file's length does not fit its own row, or the lost video's own sidecars were left
  behind at its recorded path while the file has no matching .info.json beside it.
- missing: completed videos whose file is gone.
- manual: wrong length, no single match.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.models import Channel, DownloadQueue, Video
from app.services.import_service import VIDEO_EXTENSIONS
from app.services.metadata_service import write_episode_nfo
from app.services.naming_service import build_output_path, resolve_naming_template
from app.services.notification_service import NotificationService
from app.utils.file_utils import ASSOCIATED_EXTENSIONS, channel_folder_name, move_video_files
from app.utils.platform_utils import detect_platform

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 20  # seconds per file
DURATION_ABS_TOLERANCE = 3.0
DURATION_REL_TOLERANCE = 0.02

KIND_WRONG_FILE = "wrong_file"  # certain
KIND_LIKELY = "likely"
KIND_LOST = "lost"
KIND_MANUAL = "manual"

_SINGLE_URL_RE = re.compile(r"https?://\S+")

_state: dict[str, Any] = {
    "status": "idle",  # idle | running | done | error
    "processed": 0,
    "total": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "notes": [],
    "items": [],
}
_task: "asyncio.Task | None" = None


def get_state() -> dict[str, Any]:
    return dict(_state)


def is_running() -> bool:
    return _state["status"] == "running"


def _probe(path: str) -> dict[str, Any]:
    """ffprobe one video file: {"duration": float|None, "tags": {lowercase key: value}}."""
    out: dict[str, Any] = {"duration": None, "tags": {}}
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_entries", "format=duration:format_tags", path],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT, stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            return out
        fmt = json.loads(proc.stdout or "{}").get("format", {})
        try:
            out["duration"] = float(fmt.get("duration"))
        except (TypeError, ValueError):
            pass
        out["tags"] = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return out


def _duration_fits(file_duration: float, video_duration: int | None) -> bool:
    if not video_duration:
        return True  # nothing to compare against
    tol = max(DURATION_ABS_TOLERANCE, DURATION_REL_TOLERANCE * video_duration)
    return abs(file_duration - video_duration) <= tol


def _fingerprint(path: str) -> list[int]:
    # Size and mtime only: inode numbers on Unraid's /mnt/user (FUSE) can change
    # without the file changing, which would make every fix refuse.
    st = os.stat(path)
    return [st.st_size, st.st_mtime_ns]


def _is_video_file(path: str | None) -> bool:
    return bool(path) and os.path.isfile(path) and os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def _single_url(value: str) -> str | None:
    """The value if it is exactly one http(s) URL and nothing else, else None."""
    value = (value or "").strip()
    return value if _SINGLE_URL_RE.fullmatch(value) else None


def _video_from_url(url: str, by_vid: dict[str, Video], platform_of: dict[int, str]) -> Video | None:
    """The known video a source URL points at, using per-platform URL shapes.

    Only a video whose channel is on the URL's own platform counts, so a loose
    guess (e.g. a Vimeo number) can't match another platform's id.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    platform = detect_platform(url)
    parts = [p for p in parsed.path.split("/") if p]
    cands: list[str] = []
    if platform == "youtube":
        cands += parse_qs(parsed.query).get("v", [])
        if (parsed.hostname or "").lower().endswith("youtu.be") and parts:
            cands.append(parts[0])
        elif len(parts) > 1 and parts[0] in ("shorts", "live", "embed", "v"):
            cands.append(parts[1])
    elif parts:
        last = re.sub(r"\.html?$", "", parts[-1])
        cands += [last, last.split("-")[0], "v" + last, last.lstrip("v")]
    for c in cands:
        hit = by_vid.get(c)
        if hit is not None and platform_of.get(hit.channel_id) == platform:
            return hit
    return None


def _read_info(path: str) -> tuple[bool, str | None, bool]:
    """(exists, id, shows SponsorBlock cuts) of an .info.json."""
    if not os.path.isfile(path):
        return False, None, False
    try:
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return True, None, False
    if not isinstance(info, dict):
        return True, None, False
    vid = info.get("id")
    return True, vid if isinstance(vid, str) else None, bool(info.get("sponsorblock_chapters"))


def _stat_row(path: str | None) -> dict[str, Any]:
    """Worker-thread disk look at one completed row's recorded path."""
    exists = bool(path) and os.path.exists(path)
    leftover, cuts = False, False
    if path and not exists:
        base = os.path.splitext(path)[0]
        leftover = any(os.path.exists(base + ext) for ext in ASSOCIATED_EXTENSIONS)
        cuts = _read_info(base + ".info.json")[2]
    return {"exists": exists, "leftover": leftover, "cuts": cuts}


def _inspect_file(path: str) -> dict[str, Any] | None:
    """Worker-thread look at a row's file: fingerprint, ffprobe data and sibling info.json."""
    if not _is_video_file(path):
        return None
    base = os.path.splitext(path)[0]
    info_exists, info_id, cuts = _read_info(base + ".info.json")
    return {
        "fingerprint": _fingerprint(path),
        "probe": _probe(path),
        "info_exists": info_exists,
        "info_id": info_id,
        "cuts": cuts,
    }


def _item(kind: str, row: Video, channel_name: str, **extra) -> dict[str, Any]:
    base = {
        # The run's start time keeps a stale browser tab from applying an old item.
        "id": f"{kind}:{row.id}:{_state.get('started_at') or ''}",
        "kind": kind,
        "certain": kind == KIND_WRONG_FILE,
        "video_id": row.id,
        "title": row.title,
        "channel_name": channel_name,
        "file_path": row.file_path,
        "fingerprint": None,
        "real_video_id": None,
        "real_title": None,
        "detail": "",
        "state": "open",
        "result": None,
    }
    base.update(extra)
    return base


async def _broadcast(event: str, payload: dict[str, Any]) -> None:
    try:
        await NotificationService.broadcast(event, payload)
    except Exception:
        logger.debug("library check broadcast failed", exc_info=True)


async def _run_check(maker) -> None:
    _state.update(status="running", processed=0, total=0, error=None, items=[], notes=[],
                  started_at=datetime.now(timezone.utc).isoformat(), finished_at=None)
    try:
        async with maker() as db:
            channels = {c.id: c for c in (await db.execute(select(Channel))).scalars().all()}
            videos = (await db.execute(select(Video))).scalars().all()
            try:
                from app.services.settings_service import get_setting
                sb_mode = await get_setting(db, "sponsorblock_mode", "off")
            except Exception:
                sb_mode = "off"

        by_vid = {v.video_id: v for v in videos}
        platform_of = {cid: c.platform for cid, c in channels.items()}
        completed = [v for v in videos if v.status == "completed"]
        _state["total"] = len(completed)

        def cname(v: Video) -> str:
            ch = channels.get(v.channel_id)
            return ch.channel_name if ch else ""

        def length_unreliable(v: Video) -> bool:
            # SponsorBlock removal cuts YouTube files shorter than their stored duration.
            ch = channels.get(v.channel_id)
            return sb_mode == "remove" and ch is not None and ch.platform == "youtube"

        stats = await asyncio.to_thread(lambda: [_stat_row(v.file_path) for v in completed])
        lost = [v for v, s in zip(completed, stats) if not s["exists"]]
        lost_stat = {v.id: s for v, s in zip(completed, stats) if not s["exists"]}

        items: list[dict[str, Any]] = []
        certain_ids: set[int] = set()
        inspected: dict[int, dict[str, Any]] = {}
        cuts_seen = False

        for n, (row, st) in enumerate(zip(completed, stats), 1):
            _state["processed"] = n
            if n % 25 == 0:
                await _broadcast("library_check_progress", {"processed": n, "total": len(completed)})
                await asyncio.sleep(0)
            if not st["exists"]:
                continue
            data = await asyncio.to_thread(_inspect_file, row.file_path)
            if data is None:
                continue
            inspected[row.id] = data
            tags = data["probe"]["tags"]

            real: Video | None = None
            how = ""
            comment = tags.get("comment", "")
            description = (tags.get("description") or tags.get("synopsis") or "").strip()
            if description and comment.strip() == description:
                comment = ""  # youtube-dl wrote the description here, not the source URL
            urls = [u for u in (_single_url(tags.get("purl", "")), _single_url(comment)) if u]
            url_resolved = False
            if urls:
                for u in urls:
                    hit = _video_from_url(u, by_vid, platform_of)
                    if hit is not None:
                        url_resolved = True
                        if hit.id != row.id:
                            real, how = hit, "its embedded source URL"
                        break
            elif data["info_exists"]:
                if data["info_id"] in by_vid and by_vid[data["info_id"]].id != row.id:
                    real, how = by_vid[data["info_id"]], "the .info.json beside it"
            else:
                stem = os.path.splitext(os.path.basename(row.file_path))[0]
                for tag in reversed(re.findall(r"\[([^\[\]]+)\]", stem)):
                    if tag in by_vid:
                        if by_vid[tag].id != row.id:
                            real, how = by_vid[tag], "the id in its file name"
                        break
            if real is None and urls and not url_resolved and data["info_exists"]:
                # An unrecognised URL tag does not rule out an .info.json that does name a
                # video; a URL that resolved to this row itself does.
                hit_id = data["info_id"]
                if hit_id in by_vid and by_vid[hit_id].id != row.id:
                    real, how = by_vid[hit_id], "the .info.json beside it"

            if real is not None and real.id not in lost_stat:
                # The real video still has its own file (or isn't downloaded), so moving
                # this one there would be refused; don't count it as fixable.
                certain_ids.add(row.id)
                items.append(_item(
                    KIND_MANUAL, row, cname(row), fingerprint=data["fingerprint"],
                    detail=f"This file looks like '{real.title}' (from {how}), but that video "
                           f"isn't missing its file. Check by hand.",
                ))
            elif real is not None:
                certain_ids.add(row.id)
                items.append(_item(
                    KIND_WRONG_FILE, row, cname(row), real_video_id=real.id, real_title=real.title,
                    fingerprint=data["fingerprint"],
                    detail=f"This file is really '{real.title}' (from {how}).",
                ))

        # Length-based evidence. Lost videos a certain item already restores are off limits.
        claimed = {it["real_video_id"] for it in items if it["kind"] == KIND_WRONG_FILE}
        pairs: set[tuple[int, int]] = set()  # (row id holding the file, lost video id)
        pair_why: dict[tuple[int, int], str] = {}
        mismatched: dict[int, float] = {}
        for row in completed:
            data = inspected.get(row.id)
            if data is None or row.id in certain_ids:
                continue
            fd = data["probe"]["duration"]
            if fd is None:
                continue
            if length_unreliable(row) or data["cuts"]:
                cuts_seen = True
                continue
            lost_here = [c for c in lost if c.channel_id == row.channel_id and c.id != row.id and c.duration
                         and c.id not in claimed
                         and not length_unreliable(c) and not lost_stat[c.id]["cuts"]]
            if not _duration_fits(fd, row.duration):
                mismatched[row.id] = fd
                for c in lost_here:
                    if _duration_fits(fd, c.duration):
                        pairs.add((row.id, c.id))
                        pair_why[(row.id, c.id)] = (
                            f"File is {int(fd)}s long, this video is {row.duration}s; "
                            f"it fits '{c.title}' ({c.duration}s), whose file is gone."
                        )
            for c in lost_here:
                # The old bug moved only the video file, so a moved file has no .info.json
                # beside it. (A mismatched one proves nothing: Rumble's holds an embed id.)
                if lost_stat[c.id]["leftover"] and _duration_fits(fd, c.duration) and (
                    not data["info_exists"]
                ) and (row.id, c.id) not in pairs:
                    pairs.add((row.id, c.id))
                    pair_why[(row.id, c.id)] = (
                        f"'{c.title}' lost its file but its own sidecar files are still at its old path, "
                        f"and this file ({int(fd)}s) fits it with no matching .info.json beside it."
                    )

        by_id = {v.id: v for v in videos}
        for row_id, fd in mismatched.items():
            row = by_id[row_id]
            if not any(p[0] == row_id for p in pairs):
                items.append(_item(
                    KIND_MANUAL, row, cname(row), fingerprint=inspected[row_id]["fingerprint"],
                    detail=f"File is {int(fd)}s long, this video is {row.duration}s, and no single "
                           f"missing video fits. Check by hand.",
                ))
        for row_id in sorted({p[0] for p in pairs}):
            row = by_id[row_id]
            mine = [p for p in pairs if p[0] == row_id]
            ok = len(mine) == 1 and sum(1 for p in pairs if p[1] == mine[0][1]) == 1
            if ok:
                c = by_id[mine[0][1]]
                items.append(_item(
                    KIND_LIKELY, row, cname(row), real_video_id=c.id, real_title=c.title,
                    fingerprint=inspected[row_id]["fingerprint"], detail=pair_why[mine[0]],
                ))
            else:
                items.append(_item(
                    KIND_MANUAL, row, cname(row), fingerprint=inspected[row_id]["fingerprint"],
                    detail="More than one video could own this file, or more than one file fits the "
                           "same missing video. Check by hand.",
                ))

        for v in lost:
            items.append(_item(KIND_LOST, v, cname(v), detail="The file is gone from disk."))

        notes = []
        if cuts_seen or sb_mode == "remove":
            notes.append("Length tests were skipped for YouTube files, because SponsorBlock removal "
                         "makes files shorter than their stored length.")
        _state.update(items=items, notes=notes, status="done",
                      finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as e:
        logger.exception("Library check failed")
        _state.update(status="error", error=str(e), finished_at=datetime.now(timezone.utc).isoformat())
    await _broadcast("library_check_progress", {
        "processed": _state["processed"], "total": _state["total"], "status": _state["status"],
    })


def start_check(maker=None) -> bool:
    """Start the check in the background. Returns False if one is already running."""
    global _task
    if is_running():
        return False
    if maker is None:
        import app.database as database
        maker = database.async_session
    _state.update(status="running", processed=0, total=0, items=[], notes=[], error=None)
    _task = asyncio.create_task(_run_check(maker))
    return True


async def apply_fixes(
    item_ids: list[str] | None,
    all_certain: bool = False,
    actions: dict[str, str] | None = None,
    maker=None,
) -> list[dict[str, Any]]:
    """Apply fixes for items of the last finished check. Returns one result per item."""
    if is_running():
        raise RuntimeError("A library check is running")
    if maker is None:
        import app.database as database
        maker = database.async_session
    actions = actions or {}
    by_id = {i["id"]: i for i in _state["items"]}
    if all_certain:
        wanted = [i for i in _state["items"] if i["kind"] == KIND_WRONG_FILE and i["state"] == "open"]
    else:
        wanted = [by_id[i] for i in (item_ids or []) if i in by_id]
    # Moves first, so a lost-file item whose file just got restored is recognised as such.
    order = {KIND_WRONG_FILE: 0, KIND_LIKELY: 0, KIND_LOST: 1}
    wanted = [i for i in wanted if i["kind"] in order and i["state"] == "open"]
    wanted.sort(key=lambda i: order[i["kind"]])

    results: list[dict[str, Any]] = []
    async with maker() as db:
        for item in wanted:
            try:
                if item["kind"] == KIND_LOST:
                    status, msg = await _fix_lost(db, item, actions.get(item["id"], "redownload"))
                else:
                    status, msg = await _fix_misplaced(db, item)
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.exception("Library repair failed for %s", item["id"])
                status, msg = "error", str(e)
            item["state"], item["result"] = status, msg
            results.append({"id": item["id"], "state": status, "message": msg})
    return results


def _queue(db, video: Video) -> None:
    video.status = "queued"
    video.file_path = None
    video.file_size = None
    video.error_code = None
    video.error_message = None
    video.error_details = None
    db.add(DownloadQueue(video_id=video.id))


async def _has_queue_entry(db, video_id: int) -> bool:
    r = await db.execute(select(DownloadQueue.id).where(DownloadQueue.video_id == video_id))
    return r.first() is not None


async def _fix_lost(db, item, action: str) -> tuple[str, str]:
    video = await db.get(Video, item["video_id"])
    if video is None or video.status != "completed":
        return "skipped", "Video changed since the check."
    if video.file_path and await asyncio.to_thread(os.path.exists, video.file_path):
        return "skipped", "The file is back; nothing to do."
    if action == "skip":
        video.status = "skipped"
        video.file_path = None
        video.file_size = None
        return "fixed", "Marked as skipped."
    if await _has_queue_entry(db, video.id):
        video.status = "queued"
        video.file_path = None
    else:
        _queue(db, video)
    return "fixed", "Queued for re-download."


def _precheck_move(src: str, fingerprint, real_path: str | None, target: str) -> str | None:
    """Worker-thread safety checks before a move. Returns a skip reason, or None."""
    if not _is_video_file(src):
        return "The file is no longer there."
    if fingerprint is not None and _fingerprint(src) != list(fingerprint):
        return "The file changed since the check."
    if real_path and os.path.exists(real_path) and not os.path.samefile(real_path, src):
        return "The real video already has a file."
    if os.path.exists(target) and not os.path.samefile(src, target):
        return f"Target path is taken: {target}"
    return None


async def _fix_misplaced(db, item) -> tuple[str, str]:
    row = await db.get(Video, item["video_id"])
    real = await db.get(Video, item["real_video_id"])
    if row is None or real is None or row.status != "completed" or real.status != "completed":
        return "skipped", "Video changed since the check."
    src = row.file_path
    if not src or src != item["file_path"]:
        return "skipped", "Video changed since the check."
    channel = await db.get(Channel, real.channel_id)
    template = await resolve_naming_template(db, channel.naming_template)
    target = build_output_path(
        channel_name=channel.channel_name, video_title=real.title, video_id=real.video_id,
        upload_date=real.upload_date, season=real.season, episode=real.episode,
        naming_template=template, base_dir=channel.download_dir,
        folder_name=channel_folder_name(channel),
    ) + os.path.splitext(src)[1]

    reason = await asyncio.to_thread(_precheck_move, src, item.get("fingerprint"), real.file_path, target)
    if reason:
        return "skipped", reason
    try:
        # Only the video file moves: sidecars beside it belong to the row it was renamed for.
        await asyncio.to_thread(move_video_files, src, target, False, False)
    except (FileExistsError, OSError) as e:
        return "skipped", f"Could not move the file: {e}"

    real.status = "completed"
    real.file_path = target
    real.file_size = await asyncio.to_thread(os.path.getsize, target)
    if real.downloaded_at is None:
        real.downloaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
    real.error_code = real.error_message = real.error_details = None
    try:
        await asyncio.to_thread(
            write_episode_nfo,
            channel_name=channel.channel_name, video_title=real.title, video_id=real.video_id,
            description=real.description, upload_date=real.upload_date, season=real.season,
            episode=real.episode, duration=real.duration, thumbnail_url=real.thumbnail_url,
            video_file_path=target, platform=channel.platform,
        )
    except Exception:
        logger.warning("Could not write NFO for %s", target, exc_info=True)
    if row.id != real.id:
        if await _has_queue_entry(db, row.id):
            row.status = "queued"
            row.file_path = None
            row.file_size = None
        else:
            _queue(db, row)
    return "fixed", f"Moved to {target}; '{row.title}' queued for re-download."
