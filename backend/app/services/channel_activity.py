"""What each channel is doing right now: one home for every in-memory busy flag.

Everything is process-local (the app runs one worker). Readers ask here instead of
keeping their own copy of a set:

- moving: the channel's files are being moved
- scanning: a plain scan (scheduled, RSS, Scan Now) is reading the listing; downloads may run beside it
- exclusive: Force Rescan / Repair Dates holds the channel (claim kind "scan")
- working: a file-touching operation (renumber, rename, import, delete...)
- draining: a whole-channel action was refused only for a running download, so new
  downloads for the channel are held until it can claim
- downloading: a download of the channel is running
"""
import asyncio
import time
from contextlib import asynccontextmanager

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Video

moving_channels: set[int] = set()
scanning_channels: set[int] = set()
exclusive_scans: set[int] = set()
working_channels: set[int] = set()
draining_channels: dict[int, float] = {}
DRAINING_MAX_SECONDS = 30 * 60
DRAINING_MESSAGE = (
    "Finishing this channel's current download first; new downloads for it are held. "
    "Try again in a few minutes."
)

# Source video ids whose yt-dlp download runs right now. A set (not a bool) so that with
# max_concurrent_downloads > 1 the first download to finish doesn't make
# is_download_active() report False while others are still running.
active_downloads: set[str] = set()
# {video row id: channel id} for the whole download task (rate-limit wait, quality
# lookup, yt-dlp, moving the files), not just the yt-dlp window. activity() and the
# claim check both read it. Set and cleared by process_queue._run_download.
download_channels: dict[int, int] = {}
# Background download tasks (named "download-<source video id>").
active_tasks: set[asyncio.Task] = set()

# True while a Move All runs. Two moves of the same files at once could race between one
# task's finished copy and the other's equal-size replace, so they're refused.
_move_all_running = False


def set_move_all_running(value: bool) -> None:
    global _move_all_running
    _move_all_running = value


def is_move_all_running() -> bool:
    return _move_all_running


def running_ids() -> set[str]:
    """Source video ids whose download is running right now."""
    ids = set(active_downloads)
    ids |= {t.get_name().removeprefix("download-") for t in active_tasks if not t.done()}
    return ids


def is_running(video_id: str) -> bool:
    return video_id in running_ids()


def draining_channel_ids() -> set[int]:
    """Channels held back from starting new downloads; entries older than 30 minutes expire."""
    now = time.monotonic()
    for cid, since in list(draining_channels.items()):
        if now - since > DRAINING_MAX_SECONDS:
            del draining_channels[cid]
    return set(draining_channels)


def activity(channel_id: int) -> dict:
    """What the channel is doing now (all flags are in-memory, so this is instant)."""
    return {
        "moving": channel_id in moving_channels,
        "scanning": channel_id in scanning_channels,
        "exclusive": channel_id in exclusive_scans,
        "working": channel_id in working_channels,
        "draining": channel_id in draining_channel_ids(),
        "downloading": channel_id in set(download_channels.values()),
    }


def blocked_channel_ids() -> set[int]:
    """Channels the dispatcher starts nothing for: moving, working, exclusive scan, draining.
    A plain scan does not block downloads."""
    return moving_channels | working_channels | exclusive_scans | draining_channel_ids()


def flag_reason(channel_id: int, from_move_all: bool = False) -> str | None:
    """Busy reason from the in-memory flags alone (no await, so it can be claimed atomically)."""
    if _move_all_running and not from_move_all:
        return "Move All is running; try again when it finishes"
    if channel_id in moving_channels:
        return "A move of this channel's files is running; try again when it finishes"
    if channel_id in scanning_channels or channel_id in exclusive_scans:
        return "A scan of this channel is running; try again when it finishes"
    if channel_id in working_channels:
        return "Another operation on this channel is running; try again when it finishes"
    return None


def start_block_reason(channel_id: int) -> str | None:
    """Why Download Now must not start a download in this channel (a move, file work or
    an exclusive scan holds it), or None. A plain scan and draining do not block it."""
    if (channel_id in moving_channels or channel_id in working_channels
            or channel_id in exclusive_scans):
        return flag_reason(channel_id, from_move_all=True)
    return None


def is_scan_blocked(channel_id: int) -> bool:
    """A scan, RSS check or file operation already holds the channel (a scan reads the
    listing and touches files, so it also waits for moves and file work)."""
    return (channel_id in scanning_channels or channel_id in exclusive_scans
            or channel_id in moving_channels or channel_id in working_channels)


async def _download_reason(
    db: AsyncSession, channel_id: int, video_ids: list[int] | None = None,
) -> str | None:
    """A running download in the channel, or only among ``video_ids`` (row ids) when given:
    an operation on a few videos shouldn't wait for an unrelated download to finish."""
    query = select(Video.video_id, Video.status).where(
        Video.channel_id == channel_id,
        or_(Video.status == "downloading", Video.video_id.in_(list(running_ids()) or [""]),
            Video.id.in_(list(download_channels) or [0])),
    )
    if video_ids is not None:
        query = query.where(Video.id.in_(video_ids))
    rows = (await db.execute(query.limit(1))).all()
    if rows:
        if video_ids is not None:
            return "That video is downloading; try again when it finishes"
        return "This channel has a download running; try again when it finishes"
    return None


def _claim_set(kind: str) -> set[int]:
    return {"move": moving_channels, "scan": exclusive_scans, "work": working_channels}[kind]


async def try_claim(
    db: AsyncSession, channel_id: int, kind: str, from_move_all: bool = False,
    video_ids: list[int] | None = None, drain: bool = False,
) -> str | None:
    """Claim a channel for an operation: None on success, else the busy reason.

    drain: only for a user-initiated whole-channel action that will be retried. A
    refusal for a running download then holds the channel's new downloads (see
    draining_channels). Background callers (retention, Move All, library fixes) leave
    it off, since they don't come back soon.

    The flags are checked and the claim added with no await in between, so two
    concurrent requests can't both pass. The download check runs after the claim
    (ignoring it); on busy the claim is released. kind: "move", "scan" (exclusive:
    Force Rescan / Repair Dates) or "work". video_ids: the operation touches only
    these videos (row ids), so only their downloads count. The caller must
    release_claim() when the operation ends.
    """
    reason = flag_reason(channel_id, from_move_all)
    if reason:
        return reason
    claim = _claim_set(kind)
    claim.add(channel_id)
    try:
        reason = await _download_reason(db, channel_id, video_ids)
    except BaseException:
        claim.discard(channel_id)
        raise
    if reason:
        claim.discard(channel_id)
        if drain and video_ids is None:
            # Refused only because a download is running: hold new downloads for this
            # channel so the running one is the last, and a retry can then claim it.
            draining_channels[channel_id] = time.monotonic()
            return DRAINING_MESSAGE
        return reason
    draining_channels.pop(channel_id, None)
    return reason


def release_claim(channel_id: int, kind: str) -> None:
    _claim_set(kind).discard(channel_id)


@asynccontextmanager
async def claim_channel(
    db: AsyncSession, channel_id: int, kind: str = "work", video_ids: list[int] | None = None,
):
    """Hold a channel's claim for the whole operation; 409 when it is busy."""
    from fastapi import HTTPException

    reason = await try_claim(db, channel_id, kind, video_ids=video_ids, drain=True)
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    try:
        yield
    finally:
        release_claim(channel_id, kind)
