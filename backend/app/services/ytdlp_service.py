import logging
import subprocess
from pathlib import Path

import yt_dlp

from app.config import settings

logger = logging.getLogger(__name__)


class YtdlpService:
    """Wrapper for all yt-dlp interactions."""

    def get_channel_info(self, url: str) -> dict | None:
        """Fetch channel metadata without downloading."""
        opts = self._base_opts()
        opts.update({
            "extract_flat": True,
            "playlist_items": "0",
        })

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
        except Exception as e:
            logger.error("Failed to get channel info for %s: %s", url, e)
            return None

    def get_channel_video_list(self, channel_url: str) -> list[dict]:
        """Get flat list of all videos in a channel."""
        opts = self._base_opts()
        opts.update({
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
        })

        # Ensure we're hitting the /videos page
        if "/videos" not in channel_url:
            channel_url = channel_url.rstrip("/") + "/videos"

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(channel_url, download=False)
                if not info:
                    return []
                entries = info.get("entries", [])
                return [e for e in entries if e is not None]
        except Exception as e:
            logger.error("Failed to list videos for %s: %s", channel_url, e)
            return []

    def get_video_info(self, video_id: str) -> dict | None:
        """Get full metadata for a single video (non-flat extraction)."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        opts = self._base_opts()
        opts.update({
            "skip_download": True,
            "ignoreerrors": True,
        })
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
        except Exception as e:
            logger.error("Failed to get video info for %s: %s", video_id, e)
            return None

    def download_video(
        self,
        video_url: str,
        output_path: str,
        quality: str = "best",
        progress_hook=None,
    ) -> dict:
        """Download a single video. Returns info dict on success, raises on failure."""
        opts = self._base_opts()
        opts.update({
            "format": self._quality_to_format(quality),
            "merge_output_format": "mp4",
            "outtmpl": output_path + ".%(ext)s",
            "writethumbnail": True,
            "writeinfojson": True,
            "postprocessors": [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
                {"key": "EmbedThumbnail", "already_have_thumbnail": True},
            ],
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 5,
            "ignoreerrors": False,
        })

        if progress_hook:
            opts["progress_hooks"] = [progress_hook]

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            return info or {}

    def get_version(self) -> str:
        """Get current yt-dlp version."""
        try:
            return yt_dlp.version.__version__
        except Exception:
            return "unknown"

    def update(self) -> tuple[bool, str]:
        """Update yt-dlp to latest version. Returns (success, message)."""
        try:
            result = subprocess.run(
                ["pip", "install", "--upgrade", "yt-dlp"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                return True, result.stdout
            return False, result.stderr
        except Exception as e:
            return False, str(e)

    def test_download_capability(self) -> tuple[bool, str]:
        """Test if yt-dlp can successfully extract info (tests auth/PO tokens)."""
        test_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo" - first YouTube video
        opts = self._base_opts()
        opts["extract_flat"] = True

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(test_url, download=False)
                if info and info.get("title"):
                    return True, "OK"
                return False, "Could not extract video info"
        except Exception as e:
            return False, str(e)

    def _base_opts(self) -> dict:
        """Build base yt-dlp options with anti-detection settings."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "extractor_args": {"youtube": {"player_client": ["web"]}},
        }

        # Add cookies if available
        if settings.has_cookies:
            opts["cookiefile"] = str(settings.cookies_path)

        # PO token provider
        if settings.POT_SERVER_ENABLED:
            opts.setdefault("extractor_args", {}).setdefault("youtube", [])
            # The PO token plugin is configured via yt-dlp plugin system
            # bgutil-ytdlp-pot-provider auto-registers when installed

        # User-agent rotation
        if settings.USER_AGENT_ROTATION:
            from app.utils.user_agents import get_random_user_agent
            opts["http_headers"] = {"User-Agent": get_random_user_agent()}

        return opts

    @staticmethod
    def _quality_to_format(quality: str) -> str:
        """Convert quality setting to yt-dlp format string."""
        formats = {
            "best": "bestvideo+bestaudio/best",
            "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
            "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
        }
        return formats.get(quality, formats["best"])
