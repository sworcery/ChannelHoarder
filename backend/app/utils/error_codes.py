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
    SCAN_FAILED = "SCAN_FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass
class ErrorInfo:
    code: ErrorCode
    summary: str
    explanation: str
    suggested_fix: str
    retry_strategy: str  # "none", "linear", "exponential_backoff"
    severity: str  # "info", "warning", "error", "critical"


ERROR_CATALOG: dict[ErrorCode, ErrorInfo] = {
    ErrorCode.AUTH_EXPIRED: ErrorInfo(
        code=ErrorCode.AUTH_EXPIRED,
        summary="Authentication required",
        explanation="YouTube is requiring sign-in to access this content. The PO tokens may not be sufficient, or cookies have expired.",
        suggested_fix="Try uploading fresh cookies.txt in Settings > Authentication. If using PO tokens only, check that the PO token server is running in Settings > yt-dlp.",
        retry_strategy="none",
        severity="error",
    ),
    ErrorCode.RATE_LIMITED: ErrorInfo(
        code=ErrorCode.RATE_LIMITED,
        summary="YouTube is rate limiting downloads",
        explanation="Too many download requests in a short period. YouTube has temporarily blocked this IP address.",
        suggested_fix="The app will automatically increase delay between downloads. No action needed. If persistent, try increasing the download delay in Settings > Anti-Detection.",
        retry_strategy="exponential_backoff",
        severity="warning",
    ),
    ErrorCode.GEO_BLOCKED: ErrorInfo(
        code=ErrorCode.GEO_BLOCKED,
        summary="Video is geo-blocked",
        explanation="This video is not available in your country/region.",
        suggested_fix="This video cannot be downloaded from your current location. A VPN may help, but the app does not manage VPN connections.",
        retry_strategy="none",
        severity="info",
    ),
    ErrorCode.VIDEO_UNAVAILABLE: ErrorInfo(
        code=ErrorCode.VIDEO_UNAVAILABLE,
        summary="Video is unavailable",
        explanation="The video has been made unavailable by YouTube or the creator.",
        suggested_fix="No action needed. The video may have been deleted or made private by the uploader.",
        retry_strategy="none",
        severity="info",
    ),
    ErrorCode.VIDEO_PRIVATE: ErrorInfo(
        code=ErrorCode.VIDEO_PRIVATE,
        summary="Video is private",
        explanation="This video has been set to private by the uploader.",
        suggested_fix="No action needed. Only the uploader can make this video public again.",
        retry_strategy="none",
        severity="info",
    ),
    ErrorCode.VIDEO_REMOVED: ErrorInfo(
        code=ErrorCode.VIDEO_REMOVED,
        summary="Video has been removed",
        explanation="This video has been removed from YouTube, possibly due to a copyright claim or terms violation.",
        suggested_fix="No action needed. This video is permanently unavailable.",
        retry_strategy="none",
        severity="info",
    ),
    ErrorCode.NETWORK_ERROR: ErrorInfo(
        code=ErrorCode.NETWORK_ERROR,
        summary="Network connection error",
        explanation="Could not connect to YouTube. This may be a temporary network issue.",
        suggested_fix="Check your internet connection. The download will be retried automatically.",
        retry_strategy="linear",
        severity="warning",
    ),
    ErrorCode.YTDLP_OUTDATED: ErrorInfo(
        code=ErrorCode.YTDLP_OUTDATED,
        summary="yt-dlp needs updating",
        explanation="The current version of yt-dlp may be incompatible with YouTube's latest changes.",
        suggested_fix="Update yt-dlp in Settings > yt-dlp > Update. YouTube frequently changes their systems, requiring yt-dlp updates.",
        retry_strategy="none",
        severity="error",
    ),
    ErrorCode.FFMPEG_ERROR: ErrorInfo(
        code=ErrorCode.FFMPEG_ERROR,
        summary="Video processing error",
        explanation="ffmpeg failed to merge or convert the video/audio streams.",
        suggested_fix="This is usually a temporary issue. The download will be retried. If persistent, check disk space and try a different quality setting.",
        retry_strategy="linear",
        severity="warning",
    ),
    ErrorCode.DISK_FULL: ErrorInfo(
        code=ErrorCode.DISK_FULL,
        summary="Disk is full",
        explanation="There is not enough disk space to save the downloaded video.",
        suggested_fix="Free up disk space on the downloads volume and retry. All downloads have been paused.",
        retry_strategy="none",
        severity="critical",
    ),
    ErrorCode.PO_TOKEN_FAILURE: ErrorInfo(
        code=ErrorCode.PO_TOKEN_FAILURE,
        summary="PO token generation failed",
        explanation="The Proof of Origin token server could not generate a valid token.",
        suggested_fix="The PO token server will be restarted automatically. If this persists, check Settings > yt-dlp for PO token server status, or try uploading cookies as a fallback.",
        retry_strategy="linear",
        severity="error",
    ),
    ErrorCode.FORMAT_UNAVAILABLE: ErrorInfo(
        code=ErrorCode.FORMAT_UNAVAILABLE,
        summary="Requested quality not available",
        explanation="The requested video quality/format is not available for this video. This can happen when the player client (e.g. mweb) returns limited format options.",
        suggested_fix="Retry the download — format selection has been improved. If it persists, try changing the player client in Settings > Authentication (e.g. 'web' or 'android').",
        retry_strategy="retry",
        severity="warning",
    ),
    ErrorCode.AGE_RESTRICTED: ErrorInfo(
        code=ErrorCode.AGE_RESTRICTED,
        summary="Age-restricted video",
        explanation="This video is age-restricted and requires authentication to download.",
        suggested_fix="Upload cookies.txt from a logged-in YouTube account in Settings > Authentication to download age-restricted content.",
        retry_strategy="none",
        severity="warning",
    ),
    ErrorCode.UNKNOWN: ErrorInfo(
        code=ErrorCode.UNKNOWN,
        summary="Unknown error",
        explanation="An unexpected error occurred during download.",
        suggested_fix="Check the diagnostic report for details. Try retrying the download, or update yt-dlp if the issue persists.",
        retry_strategy="linear",
        severity="error",
    ),
}


def classify_error(error_str: str) -> ErrorCode:
    """Classify an error string into an ErrorCode."""
    error_lower = error_str.lower()

    if "sign in" in error_lower or "confirm you're not a bot" in error_lower or "login required" in error_lower:
        return ErrorCode.AUTH_EXPIRED

    if "429" in error_lower or "too many requests" in error_lower or "rate limit" in error_lower:
        return ErrorCode.RATE_LIMITED

    if "not available in your country" in error_lower or "geo restriction" in error_lower or "geo-blocked" in error_lower or "geoblocked" in error_lower:
        return ErrorCode.GEO_BLOCKED

    if "private video" in error_lower:
        return ErrorCode.VIDEO_PRIVATE

    if "video has been removed" in error_lower or "removed by the uploader" in error_lower:
        return ErrorCode.VIDEO_REMOVED

    if "video unavailable" in error_lower or "is unavailable" in error_lower:
        return ErrorCode.VIDEO_UNAVAILABLE

    if "age" in error_lower and ("restricted" in error_lower or "gate" in error_lower):
        return ErrorCode.AGE_RESTRICTED

    if any(x in error_lower for x in ["connection", "timeout", "dns", "socket", "network", "ssl"]):
        return ErrorCode.NETWORK_ERROR

    if "no space left" in error_lower or "disk full" in error_lower or "enospc" in error_lower:
        return ErrorCode.DISK_FULL

    if "ffmpeg" in error_lower or "postprocess" in error_lower or "muxing" in error_lower:
        return ErrorCode.FFMPEG_ERROR

    if "po token" in error_lower or "po_token" in error_lower or "pot provider" in error_lower or "pot server" in error_lower:
        return ErrorCode.PO_TOKEN_FAILURE

    if "requested format" in error_lower or "format not available" in error_lower:
        return ErrorCode.FORMAT_UNAVAILABLE

    if "outdated" in error_lower or "yt-dlp update" in error_lower or "please update" in error_lower or "incompatible" in error_lower:
        return ErrorCode.YTDLP_OUTDATED

    return ErrorCode.UNKNOWN
