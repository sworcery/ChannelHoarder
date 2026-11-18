"""Find channels that share one folder and move one of them into its own."""
import asyncio
import logging
import os
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Channel, Video
from app.services import channel_activity
from app.services.channel_service import other_recorded
from app.utils.file_utils import (
    ASSOCIATED_EXTENSIONS, channel_folder_name, file_keys, move_video_files, stem_key,
)

logger = logging.getLogger(__name__)


async def find_shared_groups(db: AsyncSession) -> list[dict]:
    """Groups of channels whose folder resolves to the same path. DB only."""
    from app.services.channel_service import _folder_key

    channels = (await db.execute(
        select(Channel).where(Channel.channel_id != "__standalone__")
    )).scalars().all()
    counts = dict((await db.execute(
        select(Video.channel_id, func.count(Video.id))
        .where(Video.file_path.isnot(None), Video.file_path != "")
        .group_by(Video.channel_id)
    )).all())

    infos = [(c, c.download_dir, channel_folder_name(c)) for c in channels]
    keys = await asyncio.to_thread(lambda: [_folder_key(d, f) for _, d, f in infos])

    by_key: dict[str, list[Channel]] = {}
    for (c, _, _), key in zip(infos, keys):
        by_key.setdefault(key, []).append(c)

    groups = []
    for members in by_key.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda c: (c.created_at, c.id))
        first = members[0]
        groups.append({
            "folder": channel_folder_name(first),
            "path": os.path.join(first.download_dir or settings.DOWNLOAD_DIR, channel_folder_name(first)),
            "channels": [{
                "id": c.id,
                "channel_name": c.channel_name,
                "platform": c.platform,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "file_count": counts.get(c.id, 0),
            } for c in members],
            "default_channel_id": members[-1].id,
        })
    return groups


async def group_of(db: AsyncSession, channel_id: int) -> dict | None:
    for g in await find_shared_groups(db):
        if any(c["id"] == channel_id for c in g["channels"]):
            return g
    return None


async def busy_reason(
    db: AsyncSession, group: dict, channel_id: int, claimed: bool = False,
) -> str | None:
    """Why this channel can't be moved right now, or None.

    claimed: the caller has already put this channel in _moving_channels itself
    (after checking it wasn't there), so its own entry doesn't count as busy.
    """
    ca = channel_activity
    if (channel_id in ca.moving_channels and not claimed) or ca.is_move_all_running():
        return "A move of this channel's files is already running"
    if any(c["id"] in ca.scanning_channels or c["id"] in ca.exclusive_scans
           for c in group["channels"]):
        return "A scan of one of these channels is running; try again when it finishes"
    # Another channel in the folder being renamed, imported, cleaned or moved could
    # place files the move would misread as untracked.
    if any(c["id"] in ca.working_channels
           or (c["id"] != channel_id and c["id"] in ca.moving_channels)
           for c in group["channels"]):
        return "Another operation on one of these channels is running; try again when it finishes"
    rows = (await db.execute(
        select(Video.video_id, Video.status).where(Video.channel_id == channel_id)
    )).all()
    if any(status == "downloading" or ca.is_running(vid) for vid, status in rows):
        return "This channel has a download running; try again when it finishes"
    return None


async def _new_folder(db: AsyncSession, channel: Channel) -> str:
    from app.services.channel_service import pick_free_folder, taken_folder_keys

    taken = await taken_folder_keys(db, exclude_id=channel.id)
    return await asyncio.to_thread(
        pick_free_folder, taken, channel.channel_name, channel.platform, channel.download_dir
    )


def _analyze(own_paths: list[str], own_ids: set[str], others_keys: set[str], other_stems: set[str],
             old_dir: str, new_dir: str) -> dict:
    """Disk facts for a move, using the same path logic the move uses. Runs in a thread."""
    from app.routers.channels import _remap_path, _same_location
    from app.services.import_service import VIDEO_EXTENSIONS, _exact_id_match, _is_partial_download

    out = {"video_files": 0, "sidecar_files": 0, "missing_files": 0, "staying_files": [],
           "shared_sidecar_files": [], "skipped_files": [], "untracked_move": [],
           "untracked_stay": []}
    own_keys: set[str] = set()
    for p in own_paths:
        keys = file_keys(p)
        own_keys |= keys
        if keys & others_keys:
            out["staying_files"].append(p)
            continue
        new_path = _remap_path(p, old_dir, new_dir)
        if new_path is None or _same_location(new_path, p):
            out["skipped_files"].append(p)
            continue
        if not os.path.exists(p):
            out["missing_files"] += 1
            continue
        out["video_files"] += 1
        if stem_key(p) in other_stems:
            out["shared_sidecar_files"].append(p)
        else:
            base = os.path.splitext(p)[0]
            out["sidecar_files"] += sum(1 for e in ASSOCIATED_EXTENSIONS if os.path.exists(base + e))

    # Video files in the folder that no channel records. Those tagged with one of this
    # channel's ids move with it so its auto-import can attach them; the rest stay.
    by_id = {i: i for i in own_ids}
    recorded = own_keys | others_keys
    for dp, _dirs, fns in os.walk(old_dir):
        for fn in sorted(fns):
            full = os.path.join(dp, fn)
            if os.path.splitext(fn)[1].lower() not in VIDEO_EXTENSIONS:
                continue
            if _is_partial_download(Path(full)) or file_keys(full) & recorded:
                continue
            (out["untracked_move"] if _exact_id_match(Path(full), by_id) else out["untracked_stay"]).append(full)
    return out


async def _gather(db: AsyncSession, channel: Channel) -> dict:
    channel_pk = channel.id
    others_keys, other_stems = await other_recorded(db, channel_pk)
    new_folder = await _new_folder(db, channel)
    rows = (await db.execute(
        select(Video.video_id, Video.file_path).where(Video.channel_id == channel_pk)
    )).all()
    own_ids = {v for v, _ in rows}
    # An id another channel in this folder also has (e.g. a channel and a playlist on
    # the same platform) is ambiguous: its untracked file stays for that one too.
    group = await group_of(db, channel_pk)
    other_ids_in_group = [c["id"] for c in (group or {}).get("channels", []) if c["id"] != channel_pk]
    if other_ids_in_group:
        shared_ids = set((await db.execute(
            select(Video.video_id).where(Video.channel_id.in_(other_ids_in_group))
        )).scalars().all())
        own_ids -= shared_ids
    own_paths = [p for _, p in rows if p]
    root = channel.download_dir or settings.DOWNLOAD_DIR
    old_folder = channel_folder_name(channel)
    old_dir = os.path.join(root, old_folder)
    new_dir = os.path.join(root, new_folder)
    facts = await asyncio.to_thread(
        _analyze, own_paths, own_ids, others_keys, other_stems, old_dir, new_dir
    )
    return {
        "channel_id": channel_pk, "channel_name": channel.channel_name,
        "old_folder": old_folder, "new_folder": new_folder, "new_path": new_dir,
        "_old_dir": old_dir, "_others_keys": others_keys, "_other_stems": other_stems,
        **facts,
    }


async def preview_move(db: AsyncSession, channel_id: int) -> dict | None:
    """What moving this channel out of its shared folder would do. Changes nothing."""
    channel = await db.get(Channel, channel_id)
    if not channel or not await group_of(db, channel_id):
        return None
    full = await _gather(db, channel)
    return {k: v for k, v in full.items() if not k.startswith("_")}


def _move_untracked(files: list[str], old_dir: str, new_dir: str, other_stems: set[str]) -> tuple[int, list[str]]:
    """Move this channel's tagged, unrecorded video files into its new folder. Never overwrites."""
    from app.routers.channels import _remap_path

    moved, notes = 0, []
    for f in files:
        dest = _remap_path(f, old_dir, new_dir)
        if dest is None or not os.path.exists(f):
            continue
        try:
            move_video_files(f, dest, False, stem_key(f) not in other_stems)
            moved += 1
        except FileExistsError:
            notes.append(f"{f}: a file is already at the target; left in place")
        except Exception as e:
            raise RuntimeError(f"{f}: {e}") from e
    return moved, notes


async def move_task(channel_id: int) -> None:
    """Move one channel's own files into its own folder, then announce the result."""
    from app.database import async_session
    from app.routers import channels as ch
    from app.services.metadata_service import write_tvshow_nfo
    from app.services.notification_service import NotificationService

    try:
        async with async_session() as db:
            channel = await db.get(Channel, channel_id)
            if not channel:
                return
            name = channel.channel_name
            root = channel.download_dir or settings.DOWNLOAD_DIR
            moved, errors, stayed = 0, [], 0
            try:
                facts = await _gather(db, channel)
                if facts["untracked_move"]:
                    n, notes = await asyncio.to_thread(
                        _move_untracked, facts["untracked_move"], facts["_old_dir"],
                        facts["new_path"], facts["_other_stems"],
                    )
                    moved += n
                    stayed += len(notes)
                stayed += len(facts["untracked_stay"])
                m, errors, _left = await ch._move_channel_contents(
                    db, channel, root, root, facts["_others_keys"], facts["_other_stems"],
                    overwrite=False, strict_sidecars=True,
                )
                moved += m
            except Exception as e:
                logger.error("Shared-folder move of '%s' crashed: %s", name, e, exc_info=True)
                errors = [str(e)]
                try:
                    await db.rollback()
                except Exception:
                    pass
            if errors:
                hint = ""
                fresh = await db.get(Channel, channel_id)
                if fresh is not None and fresh.folder_name:
                    hint = (f" The folder '{fresh.folder_name}' was already saved and the channel may have "
                            "left the list; move any files still in the old folder by hand.")
                else:
                    hint = " Run it again to finish."
                await NotificationService.broadcast("move_complete", {
                    "channel_name": name,
                    "failed": True,
                    "message": (f"Separating '{name}' FAILED: {moved} files moved, "
                                + ch._failure_message(errors) + "." + hint),
                })
                return
            channel = await db.get(Channel, channel_id)
            folder = channel_folder_name(channel)
            nfo_note = ""
            try:
                written = await asyncio.to_thread(
                    write_tvshow_nfo,
                    channel_name=channel.channel_name,
                    channel_id=channel.channel_id,
                    channel_url=channel.channel_url,
                    description=channel.description,
                    thumbnail_url=channel.thumbnail_url,
                    base_dir=channel.download_dir,
                    platform=channel.platform,
                    folder_name=folder,
                )
            except Exception as e:
                logger.warning("tvshow.nfo not written for '%s': %s", name, e)
                written = None
            if not written:
                nfo_note = "; tvshow.nfo could not be written and will be rewritten on the next scan"
            stay_note = f"; {stayed} untracked file(s) stayed in the old folder" if stayed else ""
            await NotificationService.broadcast("move_complete", {
                "channel_name": name,
                "message": f"'{name}' now has its own folder '{folder}' ({moved} files moved{stay_note}{nfo_note})",
            })
    finally:
        channel_activity.moving_channels.discard(channel_id)
