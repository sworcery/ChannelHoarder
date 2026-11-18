from enum import Enum
from dataclasses import dataclass


class ErrorCode(str, Enum):
    AUTH_EXPIRED = "AUTH_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    GEO_BLOCKED = "GEO_BLOCKED"
    VIDEO_UNAVAILABLE = "VIDEO_UNAVAILABLE"
    VIDEO_PRIVATE = "VIDEO_PRIVATE"
    VIDEO_REMOVED = "VIDEO_REMOVED"
    NETWORK_ERROR = "NETWORK_ERROR"
    YTDLP_OUTDATED = "YTDLP_OUTDATED"
    FFMPEG_ERROR = "FFMPEG_ERROR"
    DISK_FULL = "DISK_FULL"
    PO_TOKEN_FAILURE = "PO_TOKEN_FAILURE"
    FORMAT_UNAVAILABLE = "FORMAT_UNAVAILABLE"
    AGE_RESTRICTED = "AGE_RESTRICTED"
    DOWNLOAD_STALLED = "DOWNLOAD_STALLED"
    QUALITY_TOO_LOW = "QUALITY_TOO_LOW"
    SCAN_FAILED = "SCAN_FAILED"
    METADATA_DEGRADED = "METADATA_DEGRADED"
    LIVESTREAM_SCHEDULED = "LIVESTREAM_SCHEDULED"
    UNKNOWN = "UNKNOWN"


# The Settings tabs that exist, in the words the page shows. Catalogue text may name only these.
SETTINGS_TABS = (
    "General", "Scanning", "Media Management", "Authentication",
    "Anti-Detection", "Notifications", "Diagnostics",
)


@dataclass
class ErrorInfo:
    code: ErrorCode
    summary: str
    explanation: str
    suggested_fix: str
    retry_strategy: str  # "none", "linear", "exponential_backoff", "retry", "scheduled"
    severity: str  # "info", "warning", "error", "critical"
    # Short label for lists, and a one-line tip for a group of failures. "{site}" in the
    # tip is the platform's name (the page fills it in), so the tip is right on every platform.
    label: str = ""
    tip: str = ""
    # True when the text may name YouTube because the cause only exists there.
    youtube_only: bool = False

    @property
    def retryable(self) -> bool:
        """A plain retry can fix it (as opposed to a cause that needs a change first)."""
        return self.retry_strategy != "none"


ERROR_CATALOG: dict[ErrorCode, ErrorInfo] = {
    ErrorCode.AUTH_EXPIRED: ErrorInfo(
        code=ErrorCode.AUTH_EXPIRED,
        summary="Authentication required",
        explanation="The site is requiring sign-in to access this content. The PO tokens may not be sufficient, or cookies have expired.",
        suggested_fix="Try uploading fresh cookies.txt in Settings > Authentication. If using PO tokens only, check the PO token server in Settings > Diagnostics.",
        retry_strategy="none",
        severity="error",
        label="Authentication required",
        tip="{site} is requiring sign-in. Upload fresh cookies in Settings > Authentication, then retry.",
    ),
    ErrorCode.RATE_LIMITED: ErrorInfo(
        code=ErrorCode.RATE_LIMITED,
        summary="The site is rate limiting downloads",
        explanation="Too many download requests in a short period. The site has temporarily blocked this IP address.",
        suggested_fix="The app will automatically increase delay between downloads. No action needed. If persistent, try increasing the download delay in Settings > Anti-Detection.",
        retry_strategy="exponential_backoff",
        severity="warning",
        label="Rate limited",
        tip="{site} is limiting requests. These retry on their own with a longer delay; if it keeps happening, raise the download delay in Settings > Anti-Detection.",
    ),
    ErrorCode.GEO_BLOCKED: ErrorInfo(
        code=ErrorCode.GEO_BLOCKED,
        summary="Video is geo-blocked",
        explanation="This video is not available in your country/region.",
        suggested_fix="This video cannot be downloaded from your current location. A VPN may help, but the app does not manage VPN connections.",
        retry_strategy="none",
        severity="info",
        label="Geo-blocked",
        tip="These videos are not available in your region. A retry will not change that.",
    ),
    ErrorCode.VIDEO_UNAVAILABLE: ErrorInfo(
        code=ErrorCode.VIDEO_UNAVAILABLE,
        summary="Video is unavailable",
        explanation="The video has been made unavailable by the platform or the creator.",
        suggested_fix="No action needed. The video may have been deleted or made private by the uploader.",
        retry_strategy="none",
        severity="info",
        label="Unavailable",
        tip="{site} no longer serves these videos. A retry will not change that.",
    ),
    ErrorCode.VIDEO_PRIVATE: ErrorInfo(
        code=ErrorCode.VIDEO_PRIVATE,
        summary="Video is private",
        explanation="This video has been set to private by the uploader.",
        suggested_fix="No action needed. Only the uploader can make this video public again.",
        retry_strategy="none",
        severity="info",
        label="Private",
        tip="The uploader made these private (or members-only). Only they can reopen them.",
    ),
    ErrorCode.VIDEO_REMOVED: ErrorInfo(
        code=ErrorCode.VIDEO_REMOVED,
        summary="Video has been removed",
        explanation="This video has been removed from the platform, possibly due to a copyright claim or terms violation.",
        suggested_fix="No action needed. This video is permanently unavailable.",
        retry_strategy="none",
        severity="info",
        label="Removed",
        tip="{site} removed these videos for good. A retry will not bring them back.",
    ),
    ErrorCode.NETWORK_ERROR: ErrorInfo(
        code=ErrorCode.NETWORK_ERROR,
        summary="Network connection error",
        explanation="Could not connect to the site. This may be a temporary network issue.",
        suggested_fix="Check your internet connection. The download will be retried automatically.",
        retry_strategy="linear",
        severity="warning",
        label="Network error",
        tip="{site} could not be reached. These retry on their own; check the server's internet connection if it keeps happening.",
    ),
    ErrorCode.YTDLP_OUTDATED: ErrorInfo(
        code=ErrorCode.YTDLP_OUTDATED,
        summary="yt-dlp needs updating",
        explanation="The current version of yt-dlp may be incompatible with the site's latest changes.",
        suggested_fix="Update the container image; each build ships a current yt-dlp. Sites change often, so updates matter.",
        retry_strategy="none",
        severity="error",
        label="yt-dlp out of date",
        tip="{site} needs a newer yt-dlp than the one bundled. Update the container image, then retry.",
    ),
    ErrorCode.FFMPEG_ERROR: ErrorInfo(
        code=ErrorCode.FFMPEG_ERROR,
        summary="Video processing error",
        explanation="ffmpeg failed to merge or convert the video/audio streams.",
        suggested_fix="This is usually a temporary issue. The download will be retried. If persistent, check disk space and try a different quality setting.",
        retry_strategy="linear",
        severity="warning",
        label="Processing error",
        tip="Merging the video and audio failed. These retry on their own; if it keeps happening, check free disk space.",
    ),
    ErrorCode.DISK_FULL: ErrorInfo(
        code=ErrorCode.DISK_FULL,
        summary="Disk is full",
        explanation="There is not enough disk space to save the downloaded video.",
        suggested_fix="Free up disk space on the downloads volume and retry. All downloads have been paused.",
        retry_strategy="none",
        severity="critical",
        label="Disk full",
        tip="There was no space left to save these. Free space on the downloads volume, resume the queue, then retry.",
    ),
    ErrorCode.PO_TOKEN_FAILURE: ErrorInfo(
        code=ErrorCode.PO_TOKEN_FAILURE,
        summary="PO token generation failed",
        explanation="The Proof of Origin token server could not generate a valid token.",
        suggested_fix="The PO token server will be restarted automatically. If this persists, check Settings > Diagnostics for PO token server status, or try uploading cookies as a fallback.",
        retry_strategy="linear",
        severity="error",
        label="PO token failed",
        tip="The PO token server could not make a token for YouTube. These retry on their own; if it keeps happening, check Settings > Diagnostics.",
        youtube_only=True,
    ),
    ErrorCode.FORMAT_UNAVAILABLE: ErrorInfo(
        code=ErrorCode.FORMAT_UNAVAILABLE,
        summary="Requested quality not available",
        explanation="The requested video quality/format is not available for this video. This usually means the selected player client returned only DRM-protected or URL-less (SABR) formats.",
        suggested_fix="Make sure the player client in Settings > Authentication is set to Default (automatic), which lets yt-dlp pick a working client. If a specific video still fails, try 'ios' or 'web_safari'.",
        retry_strategy="retry",
        severity="warning",
        label="Format unavailable",
        tip="{site} offered no usable format. On YouTube, set the player client to Default in Settings > Authentication, then retry.",
    ),
    ErrorCode.AGE_RESTRICTED: ErrorInfo(
        code=ErrorCode.AGE_RESTRICTED,
        summary="Age-restricted video",
        explanation="This video is age-restricted and requires authentication to download.",
        suggested_fix="Upload cookies.txt from a logged-in account in Settings > Authentication to download age-restricted content.",
        retry_strategy="none",
        severity="warning",
        label="Age restricted",
        tip="{site} wants a logged-in account for these. Upload cookies in Settings > Authentication, then retry.",
    ),
    ErrorCode.DOWNLOAD_STALLED: ErrorInfo(
        code=ErrorCode.DOWNLOAD_STALLED,
        summary="Download stalled",
        explanation="The download stopped making progress. This can happen with very large files on slow connections, or if the source server stopped responding.",
        suggested_fix="The download will be retried automatically. If this keeps happening, check your network connection and available disk space.",
        retry_strategy="linear",
        severity="warning",
        label="Download stalled",
        tip="These stopped making progress. They retry on their own; if it keeps happening, check the network and free disk space.",
    ),
    ErrorCode.QUALITY_TOO_LOW: ErrorInfo(
        code=ErrorCode.QUALITY_TOO_LOW,
        summary="Video quality below minimum",
        explanation="The best available quality for this video is below the channel's minimum quality threshold. New uploads often show only low formats for the first hours, so the download is retried after 1 hour, 6 hours and 24 hours.",
        suggested_fix="If it still fails after the 24 hour retry, use \"Download anyway\" to get the best available quality, or lower the minimum quality setting on the channel.",
        retry_strategy="scheduled",
        severity="info",
        label="Quality below minimum",
        tip="Only low formats were available. They retry after 1, 6 and 24 hours; use Download anyway to take the best quality now, or lower the channel's minimum quality.",
    ),
    ErrorCode.SCAN_FAILED: ErrorInfo(
        code=ErrorCode.SCAN_FAILED,
        summary="Channel scan failed",
        explanation="The last scan of this channel could not read its video list, so new videos are not being found.",
        suggested_fix="Check the channel's error on its page and Settings > Diagnostics. The scan tries again on its next run.",
        retry_strategy="linear",
        severity="warning",
        label="Scan failed",
        tip="The channel's video list could not be read. The scan tries again on its next run; see Settings > Diagnostics if it keeps failing.",
    ),
    ErrorCode.LIVESTREAM_SCHEDULED: ErrorInfo(
        code=ErrorCode.LIVESTREAM_SCHEDULED,
        summary="Scheduled livestream or premiere",
        explanation="This video is a scheduled livestream or premiere that hasn't started yet.",
        suggested_fix="The video has been marked as a livestream and unmonitored. Wait until the stream ends, then manually unmark it or enable livestream downloading in channel settings.",
        retry_strategy="none",
        severity="info",
        label="Scheduled livestream",
        tip="These are livestreams or premieres that have not started. Wait until they end, then unmark them or turn on livestream downloads for the channel.",
    ),
    ErrorCode.METADATA_DEGRADED: ErrorInfo(
        code=ErrorCode.METADATA_DEGRADED,
        summary="Video metadata could not be fetched",
        explanation="During the last scan, upload dates could not be retrieved for some videos - usually expired cookies, bot detection, or a network problem. Those videos were left out rather than saved with a wrong date, which would have scrambled their season and episode numbers.",
        suggested_fix="Upload fresh cookies.txt in Settings > Authentication, then scan again. The skipped videos are picked up automatically on the next successful scan; nothing is lost. For videos already saved with a wrong date, open the channel and use Repair Dates.",
        retry_strategy="linear",
        severity="warning",
        label="Dates could not be read",
        tip="Upload dates could not be read, so those videos were left out of the scan. Upload fresh cookies in Settings > Authentication and scan again; nothing is lost.",
    ),
    ErrorCode.UNKNOWN: ErrorInfo(
        code=ErrorCode.UNKNOWN,
        summary="Unknown error",
        explanation="An unexpected error occurred during download.",
        suggested_fix="Check the diagnostic report in Settings > Diagnostics. Try retrying the download, or update the container image if the issue persists.",
        retry_strategy="linear",
        severity="error",
        label="Unknown error",
        tip="Open Settings > Diagnostics for the report. Retrying may help; if it keeps failing, update the container image.",
    ),
}


def classify_error(error_str: str) -> ErrorCode:
    """Classify an error string into an ErrorCode."""
    error_lower = error_str.lower()

    if "live event" in error_lower or "premieres in" in error_lower or "will begin in" in error_lower:
        return ErrorCode.LIVESTREAM_SCHEDULED

    if "members-only" in error_lower or "join this channel" in error_lower:
        return ErrorCode.VIDEO_PRIVATE

    if "private video" in error_lower:
        return ErrorCode.VIDEO_PRIVATE

    if "sign in" in error_lower or "confirm you're not a bot" in error_lower or "login required" in error_lower:
        return ErrorCode.AUTH_EXPIRED

    if "429" in error_lower or "too many requests" in error_lower or "rate limit" in error_lower:
        return ErrorCode.RATE_LIMITED

    if "not available in your country" in error_lower or "available in your country" in error_lower or "geo restriction" in error_lower or "geo-blocked" in error_lower or "geoblocked" in error_lower:
        return ErrorCode.GEO_BLOCKED

    if "video has been removed" in error_lower or "removed by the uploader" in error_lower:
        return ErrorCode.VIDEO_REMOVED

    if "video unavailable" in error_lower or "is unavailable" in error_lower:
        return ErrorCode.VIDEO_UNAVAILABLE

    if "age" in error_lower and ("restricted" in error_lower or "gate" in error_lower):
        return ErrorCode.AGE_RESTRICTED

    if "download stalled" in error_lower:
        return ErrorCode.DOWNLOAD_STALLED

    if "timed out" in error_lower and "po token" in error_lower:
        return ErrorCode.PO_TOKEN_FAILURE

    if "download timed out" in error_lower:
        return ErrorCode.PO_TOKEN_FAILURE

    if any(x in error_lower for x in ["connection", "timeout", "dns", "socket", "network", "ssl"]):
        return ErrorCode.NETWORK_ERROR

    if "no space left" in error_lower or "disk full" in error_lower or "enospc" in error_lower:
        return ErrorCode.DISK_FULL

    if "ffmpeg" in error_lower or "postprocess" in error_lower or "muxing" in error_lower:
        return ErrorCode.FFMPEG_ERROR

    if "po token" in error_lower or "po_token" in error_lower or "pot provider" in error_lower or "pot server" in error_lower:
        return ErrorCode.PO_TOKEN_FAILURE

    if "below minimum" in error_lower and "quality" in error_lower:
        return ErrorCode.QUALITY_TOO_LOW

    if "requested format" in error_lower or "format not available" in error_lower:
        return ErrorCode.FORMAT_UNAVAILABLE

    if "outdated" in error_lower or "yt-dlp update" in error_lower or "please update" in error_lower or "incompatible" in error_lower:
        return ErrorCode.YTDLP_OUTDATED

    return ErrorCode.UNKNOWN
