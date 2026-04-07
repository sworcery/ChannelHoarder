import logging
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
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
        finally:
            self._cleanup_cookie_tmp(opts)

    def get_channel_video_list(self, channel_url: str, platform: str = "youtube") -> list[dict]:
        """Get flat list of all videos in a channel."""
        from app.utils.platform_utils import get_channel_videos_url
        opts = self._base_opts(platform=platform)
        opts.update({
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
            "quiet": False,
        })

        # Append platform-appropriate suffix (e.g. /videos for YouTube)
        channel_url = get_channel_videos_url(platform, channel_url)

        logger.info("Fetching video list from: %s", channel_url)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(channel_url, download=False)
                if not info:
                    logger.warning("yt-dlp returned None for %s", channel_url)
                    return []
                entries = list(info.get("entries", []))
                # Filter out None entries (failed extractions)
                entries = [e for e in entries if e is not None]
                logger.info("Found %d entries for %s", len(entries), channel_url)
                if entries:
                    sample = entries[0]
                    logger.info(
                        "Sample entry fields  - upload_date=%s, timestamp=%s, keys=%s",
                        sample.get("upload_date"),
                        sample.get("timestamp"),
                        [k for k in sample.keys() if "date" in k.lower() or "time" in k.lower() or "publish" in k.lower()],
                    )
                return entries
        except Exception as e:
            logger.error("Failed to list videos for %s: %s", channel_url, e)
            return []
        finally:
            self._cleanup_cookie_tmp(opts)

    @staticmethod
    def get_rss_upload_dates(channel_id: str, platform: str = "youtube") -> dict[str, str]:
        """Fetch upload dates from YouTube's public RSS feed (no auth needed).

        Returns a dict mapping video_id -> upload_date (YYYYMMDD format).
        The RSS feed covers the ~15 most recent videos.
        Only works for YouTube  - returns empty dict for other platforms.
        """
        if platform != "youtube":
            return {}
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        dates: dict[str, str] = {}
        try:
            resp = httpx.get(url, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
            for entry in root.findall("atom:entry", ns):
                vid_el = entry.find("yt:videoId", ns)
                pub_el = entry.find("atom:published", ns)
                if vid_el is not None and pub_el is not None and vid_el.text and pub_el.text:
                    # published is ISO format like "2024-01-15T12:00:00+00:00"
                    dates[vid_el.text] = pub_el.text[:10].replace("-", "")
            logger.info("RSS feed returned dates for %d videos from channel %s", len(dates), channel_id)
        except Exception as e:
            logger.warning("Failed to fetch RSS feed for channel %s: %s", channel_id, e)
        return dates

    def get_video_info(self, video_id: str, platform: str = "youtube") -> dict | None:
        """Get full metadata for a single video (non-flat extraction)."""
        from app.utils.platform_utils import build_video_url
        url = build_video_url(platform, video_id)
        opts = self._base_opts(platform=platform)
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
        finally:
            self._cleanup_cookie_tmp(opts)

    def get_video_info_by_url(self, url: str) -> dict | None:
        """Get full metadata for a video by its URL (any platform)."""
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
            logger.error("Failed to get video info for URL %s: %s", url, e)
            return None
        finally:
            self._cleanup_cookie_tmp(opts)

    def download_video(
        self,
        video_url: str,
        output_path: str,
        quality: str = "best",
        progress_hook=None,
        platform: str = "youtube",
    ) -> dict:
        """Download a single video. Returns info dict on success, raises on failure."""
        opts = self._base_opts(platform=platform)
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
            "verbose": logger.isEnabledFor(logging.DEBUG),
        })

        if progress_hook:
            opts["progress_hooks"] = [progress_hook]

        logger.info("yt-dlp download starting: %s → %s", video_url, output_path)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                logger.info("yt-dlp download completed: %s", video_url)
                return info or {}
        except Exception as e:
            logger.error("yt-dlp download failed: %s  - %s", video_url, e)
            raise
        finally:
            self._cleanup_cookie_tmp(opts)

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
        finally:
            self._cleanup_cookie_tmp(opts)

    def _base_opts(self, platform: str = "youtube") -> dict:
        """Build base yt-dlp options with anti-detection settings.

        PO token generation is handled entirely by the bgutil-ytdlp-pot-provider
        plugin, which generates per-video tokens via the HTTP provider (bgutil:http).
        We do NOT manually inject PO tokens  - YouTube now binds GVS tokens to
        specific video IDs, so pre-generated generic tokens are rejected.

        YouTube-specific extractor args are only injected when platform == "youtube".
        """
        extractor_args = {}

        # YouTube-specific anti-detection and PO token config
        if platform == "youtube":
            player_client = settings.YTDLP_PLAYER_CLIENT
            if player_client == "default":
                yt_args = {"player_client": ["mweb"]}
            else:
                yt_args = {"player_client": player_client.split(",")}
            yt_args["fetch_pot"] = ["always"]
            extractor_args["youtube"] = yt_args

            if settings.POT_SERVER_ENABLED:
                extractor_args["youtubepot-bgutilhttp"] = {
                    "base_url": [settings.POT_SERVER_URL],
                }
                logger.info("PO token plugin configured with server: %s", settings.POT_SERVER_URL)

        opts = {
            "quiet": True,
            "no_warnings": False,
            "extract_flat": False,
            "extractor_args": extractor_args,
            # Persistent cache dir for yt-dlp
            "cachedir": str(settings.ytdlp_cache_dir),
        }

        # YouTube-specific JS runtime and remote components
        if platform == "youtube":
            opts["js_runtimes"] = {"node": {}, "deno": {}}
            opts["remote_components"] = {"ejs:github"}

        if settings.has_cookies:
            cookie_size = settings.cookies_path.stat().st_size if settings.cookies_path.exists() else 0
            logger.info("Using cookies file: %s (%d bytes)", settings.cookies_path, cookie_size)
            tmp = tempfile.NamedTemporaryFile(
                prefix="ch_cookies_", suffix=".txt", delete=False,
            )
            shutil.copy2(str(settings.cookies_path), tmp.name)
            tmp.close()
            opts["cookiefile"] = tmp.name
            opts["_cookie_tmp"] = tmp.name  # marker for cleanup
        else:
            logger.info("No cookies file found at %s", settings.cookies_path)

        # User-agent rotation
        if settings.USER_AGENT_ROTATION:
            from app.utils.user_agents import get_random_user_agent
            opts["http_headers"] = {"User-Agent": get_random_user_agent()}

        return opts

    @staticmethod
    def _cleanup_cookie_tmp(opts: dict) -> None:
        """Remove the temporary cookie file created by _base_opts."""
        tmp_path = opts.get("_cookie_tmp")
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _quality_to_format(quality: str) -> str:
        """Convert quality setting to yt-dlp format string.

        Uses multiple fallbacks to handle player clients (e.g. mweb) that may
        only provide muxed streams instead of separate video+audio tracks.
        """
        formats = {
            "best": "bestvideo*+bestaudio/bestvideo+bestaudio/best",
            "1080p": "bestvideo*[height<=1080]+bestaudio/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "720p": "bestvideo*[height<=720]+bestaudio/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "480p": "bestvideo*[height<=480]+bestaudio/bestvideo[height<=480]+bestaudio/best[height<=480]/best",
        }
        return formats.get(quality, formats["best"])
