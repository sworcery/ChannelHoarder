import atexit
import logging
import shutil
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import yt_dlp

from app.config import settings

logger = logging.getLogger(__name__)


_cookie_cache_path: str | None = None
_cookie_cache_mtime: float = 0.0
_cookie_cache_lock = threading.Lock()


def _cleanup_cookie_cache():
    global _cookie_cache_path
    if _cookie_cache_path:
        try:
            Path(_cookie_cache_path).unlink(missing_ok=True)
        except Exception:
            pass


atexit.register(_cleanup_cookie_cache)


class YtdlpService:
    """Wrapper for all yt-dlp interactions."""

    def get_channel_info(self, url: str) -> dict | None:
        """Fetch channel metadata without downloading."""
        from app.utils.platform_utils import is_playlist_url

        opts = self._base_opts()

        if is_playlist_url(url):
            # Playlists fail with playlist_items: "0" (triggers tab extraction/404)
            # Use extract_flat: "in_playlist" and grab metadata from results
            opts.update({
                "extract_flat": "in_playlist",
                "playlistend": 1,
            })
        else:
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

    def get_channel_video_list(self, channel_url: str, platform: str = "youtube", tab: str = "videos") -> list[dict]:
        """Get flat list of videos from a specific channel tab.

        tab: "videos" (default), "shorts", or "streams". Non-YouTube platforms
        only support "videos".
        """
        from app.utils.platform_utils import get_channel_tab_url, get_channel_videos_url

        opts = self._base_opts(platform=platform)
        opts.update({
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
            "quiet": False,
        })

        # Resolve URL for the requested tab
        if platform == "youtube" and tab != "videos":
            target_url = get_channel_tab_url(platform, channel_url, tab)
            if not target_url:
                return []
        else:
            target_url = get_channel_videos_url(platform, channel_url)

        logger.info("Fetching %s list from: %s", tab, target_url)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
                if not info:
                    logger.warning("yt-dlp returned None for %s", target_url)
                    return []
                entries = list(info.get("entries", []))
                # Filter out None entries (failed extractions) and tag each with source tab
                entries = [e for e in entries if e is not None]
                for entry in entries:
                    entry["_source_tab"] = tab
                logger.info("Found %d entries in %s tab for %s", len(entries), tab, target_url)
                return entries
        except Exception as e:
            # /shorts and /streams tabs can 404 on channels that don't have them
            logger.info("Failed to fetch %s tab (may not exist): %s", tab, e)
            return []
        finally:
            self._cleanup_cookie_tmp(opts)

    def get_channel_video_list_all_tabs(self, channel_url: str, platform: str = "youtube") -> list[dict]:
        """Fetch videos from /videos, /shorts, and /streams tabs (YouTube) and merge.

        Deduplicates by video ID, preferring the more specific tab when a video
        appears in multiple (shorts/streams > videos).
        """
        from app.utils.platform_utils import is_playlist_url

        # Playlists have no tab structure
        if is_playlist_url(channel_url):
            return self.get_channel_video_list(channel_url, platform, tab="videos")

        if platform != "youtube":
            return self.get_channel_video_list(channel_url, platform, tab="videos")

        videos = self.get_channel_video_list(channel_url, platform, tab="videos")
        shorts = self.get_channel_video_list(channel_url, platform, tab="shorts")
        streams = self.get_channel_video_list(channel_url, platform, tab="streams")

        # Deduplicate: shorts/streams override videos for the same video_id
        merged: dict[str, dict] = {}
        for entry in videos:
            vid_id = entry.get("id") or entry.get("video_id", "")
            if vid_id:
                merged[vid_id] = entry
        for entry in shorts:
            vid_id = entry.get("id") or entry.get("video_id", "")
            if vid_id:
                merged[vid_id] = entry  # shorts tab wins
        for entry in streams:
            vid_id = entry.get("id") or entry.get("video_id", "")
            if vid_id:
                merged[vid_id] = entry  # streams tab wins

        return list(merged.values())

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
        subtitles_enabled: bool = False,
    ) -> dict:
        """Download a single video. Returns info dict on success, raises on failure."""
        opts = self._base_opts(platform=platform)
        opts.update({
            "format": self._quality_to_format(quality),
            "merge_output_format": "mp4",
            "outtmpl": output_path + ".%(ext)s",
            "writethumbnail": True,
            "writeinfojson": True,
            "writesubtitles": subtitles_enabled,
            "writeautomaticsub": subtitles_enabled,
            "subtitleslangs": ["en"] if subtitles_enabled else [],
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

        logger.info("yt-dlp download starting: %s -> %s", video_url, output_path)
        logger.info("yt-dlp extracting info and acquiring PO token (this may take a moment)...")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                logger.info("yt-dlp download completed: %s", video_url)
                return info or {}
        except Exception as e:
            logger.error("yt-dlp download failed: %s - %s", video_url, e)
            raise
        finally:
            self._cleanup_cookie_tmp(opts)

    def download_subtitles_only(
        self,
        video_url: str,
        output_path: str,
        platform: str = "youtube",
    ) -> bool:
        """Download only subtitles for a video without re-downloading the video itself.

        Args:
            video_url: The video URL to fetch subtitles for
            output_path: Base output path (without extension) -- subtitles land as .en.vtt next to it
            platform: Platform identifier

        Returns:
            True if subtitles were downloaded, False otherwise
        """
        opts = self._base_opts(platform=platform)
        opts.update({
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "outtmpl": output_path + ".%(ext)s",
            "quiet": True,
            "no_warnings": True,
        })

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(video_url, download=True)
                return True
        except Exception as e:
            logger.warning("Subtitle download failed for %s: %s", video_url, e)
            return False
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

        Auth strategy:
        - If valid cookies exist, use cookies as primary auth (skip PO tokens)
        - If no cookies, fall back to PO token server for authentication
        This avoids hammering the PO token server when cookies are available.

        YouTube-specific extractor args are only injected when platform == "youtube".
        """
        extractor_args = {}
        use_pot = False

        # YouTube-specific anti-detection and PO token config
        if platform == "youtube":
            player_client = settings.YTDLP_PLAYER_CLIENT
            if player_client == "default":
                yt_args = {"player_client": ["mweb"]}
            else:
                yt_args = {"player_client": player_client.split(",")}

            # Only use PO tokens if cookies are not available
            if settings.has_cookies:
                logger.info("Cookies available - using cookies as primary auth (PO tokens skipped)")
                yt_args["fetch_pot"] = ["never"]
            elif settings.POT_SERVER_ENABLED:
                logger.info("No cookies - falling back to PO token server: %s", settings.POT_SERVER_URL)
                yt_args["fetch_pot"] = ["always"]
                use_pot = True
            else:
                logger.warning("No cookies and PO token server disabled - downloads may fail")
                yt_args["fetch_pot"] = ["never"]

            extractor_args["youtube"] = yt_args

            if use_pot:
                extractor_args["youtubepot-bgutilhttp"] = {
                    "base_url": [settings.POT_SERVER_URL],
                }

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
            opts["cookiefile"] = self._get_cached_cookie_copy()
        else:
            logger.info("No cookies file found at %s", settings.cookies_path)

        # User-agent rotation
        if settings.USER_AGENT_ROTATION:
            from app.utils.user_agents import get_random_user_agent
            opts["http_headers"] = {"User-Agent": get_random_user_agent()}

        return opts

    @staticmethod
    def _subtitles_enabled() -> bool:
        """Check if subtitle downloading is enabled in settings."""
        try:
            import json
            from pathlib import Path
            db_path = Path(settings.CONFIG_DIR) / "archiver.db"
            if not db_path.exists():
                return False
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("SELECT value FROM app_settings WHERE key = 'subtitles_enabled'")
            row = cursor.fetchone()
            conn.close()
            if row:
                return json.loads(row[0]) is True
        except Exception:
            pass
        return False

    @staticmethod
    def _get_cached_cookie_copy() -> str:
        """Return path to a cached temp copy of the cookies file.

        Re-copies only when the source file's mtime changes, avoiding
        repeated disk I/O during batch operations like channel scans.
        """
        global _cookie_cache_path, _cookie_cache_mtime
        with _cookie_cache_lock:
            src = settings.cookies_path
            try:
                current_mtime = src.stat().st_mtime
            except FileNotFoundError:
                if _cookie_cache_path and Path(_cookie_cache_path).exists():
                    return _cookie_cache_path
                raise

            if _cookie_cache_path and _cookie_cache_mtime == current_mtime and Path(_cookie_cache_path).exists():
                return _cookie_cache_path

            if _cookie_cache_path:
                try:
                    Path(_cookie_cache_path).unlink(missing_ok=True)
                except Exception:
                    pass

            tmp = tempfile.NamedTemporaryFile(prefix="ch_cookies_", suffix=".txt", delete=False)
            shutil.copy2(str(src), tmp.name)
            tmp.close()
            _cookie_cache_path = tmp.name
            _cookie_cache_mtime = current_mtime
            return _cookie_cache_path

    @staticmethod
    def _cleanup_cookie_tmp(opts: dict) -> None:
        """No-op: cookie temp files are now managed by the module-level cache."""
        pass

    @staticmethod
    def _quality_to_format(quality: str) -> str:
        """Convert quality setting to yt-dlp format string.

        Uses multiple fallbacks to handle player clients (e.g. mweb) that may
        only provide muxed streams instead of separate video+audio tracks.
        """
        formats = {
            "best": "bestvideo*+bestaudio/bestvideo+bestaudio/best",
            "2160p": "bestvideo*[height<=2160]+bestaudio/bestvideo[height<=2160]+bestaudio/best[height<=2160]/best",
            "1080p": "bestvideo*[height<=1080]+bestaudio/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "720p": "bestvideo*[height<=720]+bestaudio/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "480p": "bestvideo*[height<=480]+bestaudio/bestvideo[height<=480]+bestaudio/best[height<=480]/best",
        }
        return formats.get(quality, formats["best"])
