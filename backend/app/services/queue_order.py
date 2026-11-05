"""Download queue ordering shared by the dispatcher and the queue list."""

from app.models import Channel, DownloadQueue, Video

QUEUE_ORDER_MODES = ("queued", "newest", "oldest", "shortest", "longest")


def queue_order_by(mode: str | None) -> list:
    """order_by clauses for the queue. The query must join Video and Channel.

    Precedence: manual priority, channel "download first", chosen mode, queued_at, id.
    """
    clauses = [DownloadQueue.priority.desc(), Channel.prioritize_downloads.desc()]
    if mode == "newest":
        clauses.append(Video.upload_date.desc().nulls_last())
    elif mode == "oldest":
        clauses.append(Video.upload_date.asc().nulls_last())
    elif mode == "shortest":
        clauses.append(Video.duration.asc().nulls_last())
    elif mode == "longest":
        clauses.append(Video.duration.desc().nulls_last())
    clauses.extend([DownloadQueue.queued_at.asc(), DownloadQueue.id.asc()])
    return clauses
