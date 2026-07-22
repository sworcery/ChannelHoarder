import atexit
import logging
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import yt_dlp

from app.config import settings

logger = logging.getLogger(__name__)

# SponsorBlock segment categories to mark/remove. The default set covers the
# least-controversial promotional segments; intro/outro/filler are left out so
# wanted content isn't cut.
_SPONSORBLOCK_CATEGORIES = ["sponsor", "selfpromo", "interaction"]

_impersonate_target = None
_impersonate_checked = False
_impersonate_lock = threading.Lock()


def _get_impersonate_target():
    """Return an ImpersonateTarget for Firefox if curl_cffi is available and compatible, else None.

    Firefox (not Chrome) to match the cookie exporter, which only reads Firefox: a
    Cloudflare cf_clearance token is bound to the browser that solved the challenge,
    so the impersonated TLS fingerprint and the exported cookies must be the same
    browser family for authenticated (e.g. Rumble) requests to validate.
    """
    global _impersonate_target, _impersonate_checked
    if _impersonate_checked:
        return _impersonate_target
    with _impersonate_lock:
        if _impersonate_checked:
            return _impersonate_target
        _impersonate_checked = True
        try:
            from yt_dlp.networking._curlcffi import CurlCFFIRH  # noqa: F401
            from yt_dlp.networking.impersonate import ImpersonateTarget
            _impersonate_target = ImpersonateTarget.from_str("firefox")
            logger.info("Browser impersonation available (curl_cffi)")
        except (ImportError, ValueError, TypeError) as e:
            logger.warning("Browser impersonation unavailable: %s", e)
            _impersonate_target = None
    return _impersonate_target


# A curl_cffi request that fails to reach the host (DNS/connection error) can leave
# libcurl's heap state corrupted; the NEXT curl_cffi use in the process then aborts
# with "double free or corruption". Both our Rumble page scrape and yt-dlp's
# impersonated (non-YouTube) extractor use curl_cffi, so after a curl_cffi network
# failure we briefly stop re-entering curl_cffi and let the scan retry next tick.
_curlcffi_cooldown_until = 0.0
_CURLCFFI_COOLDOWN_SECONDS = 120


def _curlcffi_cooling_down() -> bool:
    return time.monotonic() < _curlcffi_cooldown_until


def _trip_curlcffi_cooldown(reason: str) -> None:
    global _curlcffi_cooldown_until
    _curlcffi_cooldown_until = time.monotonic() + _CURLCFFI_COOLDOWN_SECONDS
    logger.warning(
        "curl_cffi network failure (%s); pausing curl_cffi use for %ds to avoid a "
        "native heap-corruption crash", reason, _CURLCFFI_COOLDOWN_SECONDS,
    )


def _is_curlcffi_connection_error(e: Exception) -> bool:
    """True when a request never reached the host (DNS/connect/timeout/SSL-connect).
    curl_cffi errors carry the libcurl error code; also match on the message."""
    code = getattr(e, "code", None)
    try:
        if code is not None and int(code) in (5, 6, 7, 28, 35):
            return True
    except (TypeError, ValueError):
        pass
    msg = str(e).lower()
    return any(s in msg for s in (
        "could not resolve host", "couldn't resolve", "failed to connect",
        "could not connect", "connection refused", "connection reset",
    ))


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

    def get_channel_info(self, url: str, platform: str = "youtube") -> dict | None:
        """Fetch channel metadata without downloading."""
        from app.utils.platform_utils import is_playlist_url

        opts = self._base_opts(platform=platform)

        if is_playlist_url(url):
            # Playlists fail with playlist_items: "0" (triggers tab extraction/404)
            # Use extract_flat: "in_playlist" and grab metadata from results
            opts.update({
                "extract_flat": "in_playlist",
                "playlistend": 1,
            })
        elif platform == "youtube":
            opts.update({
                "extract_flat": True,
                "playlist_items": "0",
            })
        else:
            # Non-YouTube extractors often don't return channel metadata with
            # playlist_items: "0". Fetch one entry so yt-dlp can derive it.
            opts.update({
                "extract_flat": "in_playlist",
                "playlistend": 1,
            })

        # Non-YouTube extraction uses curl_cffi (impersonate). Skip it while cooling
        # down from a network failure so we don't re-enter curl_cffi on a corrupted heap.
        if platform != "youtube" and _curlcffi_cooling_down():
            logger.info("Skipping channel-info extraction for %s (curl_cffi network cooldown)", url)
            return None

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if platform == "rumble":
                    info = self._augment_rumble_channel_info(info, url)
                return info
        except Exception as e:
            logger.error("Failed to get channel info for %s: %s", url, e)
            # A network failure here can corrupt curl_cffi's heap; trip the cooldown so
            # the scrape fallback below (also curl_cffi) is skipped rather than crashing.
            if _is_curlcffi_connection_error(e):
                _trip_curlcffi_cooldown(str(e))
            # Rumble's extractor often fails or returns nothing usable; fall back
            # to scraping the channel page for name/art/description.
            if platform == "rumble":
                scraped = self._scrape_rumble_channel_info(url)
                if scraped.get("title"):
                    logger.info("Using scraped Rumble channel info for %s", url)
                    scraped.setdefault("channel", scraped["title"])
                    return scraped
            raise ValueError(f"yt-dlp could not extract channel info: {e}") from e
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

        # Rumble's yt-dlp channel extractor is unreliable - depending on the
        # channel's page layout it returns 0 videos, or a partial list with no IDs
        # (which the scan then drops). Scrape the channel page directly first; it
        # reliably returns the full list. Fall back to yt-dlp only if it fails.
        if platform == "rumble":
            scraped = self._scrape_rumble_channel(channel_url, tab)
            if scraped:
                return scraped

        # Non-YouTube extraction uses curl_cffi (impersonate). Skip it while cooling
        # down from a network failure so we don't re-enter curl_cffi on a corrupted heap.
        if platform != "youtube" and _curlcffi_cooling_down():
            logger.info("Skipping yt-dlp extraction for %s (curl_cffi network cooldown)", target_url)
            return []

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
                if not info:
                    logger.warning("yt-dlp returned None for %s", target_url)
                    return []
                entries = list(info.get("entries", []))
                # Filter out None entries (failed extractions) and tag each with source tab
                raw_count = len(entries)
                entries = [e for e in entries if e is not None]
                if raw_count > 0 and not entries:
                    logger.warning("All %d entries were None (extraction failures) for %s", raw_count, target_url)
                for entry in entries:
                    entry["_source_tab"] = tab
                logger.info("Found %d entries in %s tab for %s", len(entries), tab, target_url)

                # Non-YouTube: retry without extract_flat if flat extraction returned nothing
                if not entries and platform != "youtube":
                    logger.info("Flat extraction returned 0 entries for %s, retrying with full extraction", target_url)
                    return self._get_channel_video_list_full(target_url, platform, tab)

                return entries
        except Exception as e:
            # /shorts and /streams tabs can 404 on channels that don't have them
            logger.info("Failed to fetch %s tab (may not exist): %s", tab, e)
            return []
        finally:
            self._cleanup_cookie_tmp(opts)

    def _get_channel_video_list_full(self, target_url: str, platform: str, tab: str) -> list[dict]:
        """Fallback: fetch video list without extract_flat for platforms where flat extraction fails."""
        opts = self._base_opts(platform=platform)
        opts.update({
            "ignoreerrors": True,
            "skip_download": True,
            "quiet": False,
            "playlistend": 200,
        })
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
                if not info:
                    return []
                entries = list(info.get("entries", []))
                entries = [e for e in entries if e is not None]
                for entry in entries:
                    entry["_source_tab"] = tab
                    if "url" not in entry and "webpage_url" in entry:
                        entry["url"] = entry["webpage_url"]
                    if not entry.get("id") and not entry.get("video_id") and entry.get("webpage_url"):
                        from urllib.parse import urlparse
                        path = urlparse(entry["webpage_url"]).path.strip("/")
                        if path:
                            entry["id"] = path.split(".")[0] if "." in path else path
                logger.info("Full extraction found %d entries for %s", len(entries), target_url)
                return entries
        except Exception as e:
            logger.warning("Full extraction also failed for %s: %s", target_url, e)
            return []
        finally:
            self._cleanup_cookie_tmp(opts)

    @staticmethod
    def _load_cookies_for_domain(domain_substr: str) -> dict:
        """Load cookies for a domain from the app's cookies.txt (Netscape format)
        as a name->value dict for curl_cffi, so the Rumble scrape uses the same
        authentication as downloads (e.g. for premium content). Empty if none."""
        if not settings.has_cookies:
            return {}
        import http.cookiejar
        try:
            jar = http.cookiejar.MozillaCookieJar(str(settings.cookies_path))
            jar.load(ignore_discard=True, ignore_expires=True)
            return {c.name: c.value for c in jar if domain_substr in (c.domain or "")}
        except Exception as e:
            logger.warning("Could not load %s cookies for scrape: %s", domain_substr, e)
            return {}

    @staticmethod
    def _get_cookie_user_agent() -> str | None:
        """Return the browser User-Agent captured at cookie-export time, embedded as a
        '# User-Agent:' comment near the top of cookies.txt. A Cloudflare cf_clearance
        token is bound to the exact UA (browser and version) that solved the challenge,
        so requests replaying those cookies must send this UA verbatim. None if absent.
        """
        if not settings.has_cookies:
            return None
        try:
            with open(settings.cookies_path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("# User-Agent:"):
                        return line.split(":", 1)[1].strip() or None
                    # The comment sits above the cookie rows; stop at the first real one.
                    if line.strip() and not line.startswith("#"):
                        break
        except OSError:
            return None
        return None

    @staticmethod
    def _parse_rumble_video_hrefs(html: str) -> list[str]:
        """Extract channel video hrefs (e.g. /v3pyn3g-title.html) from a Rumble
        channel page. Uses the embedded JSON 'relative_url' field, which is scoped
        to the channel's own videos (sidebar/recommended use different markup), and
        preserves order while de-duplicating.

        The slug is matched as "anything up to .html" rather than a restricted
        character class: Rumble derives slugs from titles, so any title containing
        a period (an ellipsis, an abbreviation, a decimal) yields a dotted slug
        like /v7d2q7k-i-was-not-prepared-for-this...html. A [a-z0-9-]+ slug pattern
        silently skipped those, dropping the majority of a channel's videos.
        The /v<id>- prefix still excludes non-video links such as /c/<channel>.
        """
        import re
        ordered = {}
        for href in re.findall(r'"relative_url":"(/v[0-9a-z]+-[^"]*?\.html)"', html):
            ordered.setdefault(href, None)
        return list(ordered)

    def _scrape_rumble_channel(self, channel_url: str, tab: str = "videos") -> list[dict]:
        """Fallback for Rumble channels whose page layout yt-dlp's RumbleChannel
        extractor returns 0 videos for. Pages through the channel and emits entries
        in the same shape yt-dlp's flat extraction would (id derived from the URL
        slug, ie_key 'Rumble'), so the rest of the scan pipeline is unchanged."""
        if _curlcffi_cooling_down():
            logger.info("Skipping Rumble scrape for %s (curl_cffi network cooldown)", channel_url)
            return []
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            logger.warning("curl_cffi unavailable; cannot scrape Rumble channel %s", channel_url)
            return []

        base = channel_url.split("?")[0].rstrip("/")
        cookies = self._load_cookies_for_domain("rumble")
        # Impersonate Firefox and replay the exact UA captured at cookie-export time,
        # so a Firefox-issued cf_clearance token (bound to browser + version) validates.
        ua = self._get_cookie_user_agent()
        headers = {"User-Agent": ua} if ua else None
        entries: list[dict] = []
        seen: set[str] = set()
        # Pagination normally ends naturally (404, or a page with no new hrefs).
        # This cap is only a runaway backstop: at ~25 videos/page it allows ~5000
        # videos, since large channels legitimately run past 100 pages.
        for page in range(1, 201):
            try:
                resp = cffi_requests.get(f"{base}?page={page}", impersonate="firefox",
                                         timeout=30, cookies=cookies, headers=headers)
            except Exception as e:
                if _is_curlcffi_connection_error(e):
                    _trip_curlcffi_cooldown(str(e))
                else:
                    logger.warning("Rumble scrape error on %s page %d: %s", base, page, e)
                break
            if resp.status_code == 404:
                break
            fresh = [h for h in self._parse_rumble_video_hrefs(resp.text) if h not in seen]
            if not fresh:
                break
            for href in fresh:
                seen.add(href)
                full_url = "https://rumble.com" + href
                slug = href.lstrip("/")
                vid_id = slug.split(".")[0] if "." in slug else slug
                entries.append({
                    "id": vid_id,
                    "url": full_url,
                    "webpage_url": full_url,
                    "ie_key": "Rumble",
                    "_type": "url",
                    "_source_tab": tab,
                })
        logger.info("Rumble channel scrape found %d videos for %s", len(entries), base)
        return entries

    def _scrape_rumble_channel_info(self, channel_url: str) -> dict:
        """Scrape channel name, avatar/art, and description from a Rumble channel
        page, for channels where yt-dlp returns no usable channel metadata."""
        import re
        if _curlcffi_cooling_down():
            logger.info("Skipping Rumble channel-info scrape for %s (curl_cffi network cooldown)", channel_url)
            return {}
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            return {}
        ua = self._get_cookie_user_agent()
        try:
            html = cffi_requests.get(
                channel_url.split("?")[0], impersonate="firefox", timeout=30,
                cookies=self._load_cookies_for_domain("rumble"),
                headers={"User-Agent": ua} if ua else None,
            ).text
        except Exception as e:
            if _is_curlcffi_connection_error(e):
                _trip_curlcffi_cooldown(str(e))
            else:
                logger.warning("Rumble channel-info scrape failed for %s: %s", channel_url, e)
            return {}
        return self._parse_rumble_channel_info(html)

    @staticmethod
    def _parse_rumble_channel_info(html: str) -> dict:
        """Extract channel name, avatar/art, and description from Rumble channel
        page HTML."""
        import re
        info: dict = {}
        name = (re.search(r'class="channel-header--title[^"]*"[^>]*>([^<]+)<', html)
                or re.search(r'<h1[^>]*>\s*([^<]{2,80})\s*</h1>', html))
        if name:
            info["title"] = name.group(1).strip()
        img_tag = re.search(r'<img[^>]*channel-header--img[^>]*>', html)
        if img_tag:
            src = re.search(r'src="([^"]+)"', img_tag.group(0))
            if src:
                info["thumbnail"] = src.group(1)
        banner = re.search(r'channel-header--backsplash[^>]*>\s*<img[^>]+src="([^"]+)"', html)
        if banner:
            info["banner_url"] = banner.group(1)
        title_tag = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
        if title_tag:
            desc = re.sub(r'\s*-\s*Rumble\s*$', '', title_tag.group(1).strip())
            # Skip when the page title is just the channel name (no real tagline).
            if desc and desc != info.get("title"):
                info["description"] = desc
        return info

    def _augment_rumble_channel_info(self, info: dict | None, channel_url: str) -> dict:
        """Fill in missing Rumble channel name/art/description by scraping the page."""
        info = info or {}
        if (info.get("channel") or info.get("uploader") or info.get("title")) and info.get("thumbnail"):
            return info
        scraped = self._scrape_rumble_channel_info(channel_url)
        if not info.get("title") and scraped.get("title"):
            info["title"] = scraped["title"]
        if not info.get("channel") and scraped.get("title"):
            info["channel"] = scraped["title"]
        if not info.get("thumbnail") and scraped.get("thumbnail"):
            info["thumbnail"] = scraped["thumbnail"]
        if not info.get("banner_url") and scraped.get("banner_url"):
            info["banner_url"] = scraped["banner_url"]
        if not info.get("description") and scraped.get("description"):
            info["description"] = scraped["description"]
        return info

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
        # Non-YouTube extraction uses curl_cffi (impersonate). This runs per-video in
        # the scan loop, so skip it while cooling down to avoid re-entering curl_cffi
        # on a corrupted heap (the double-free crash).
        if platform != "youtube" and _curlcffi_cooling_down():
            logger.info("Skipping video-info extraction for %s (curl_cffi network cooldown)", url)
            return None
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
        except Exception as e:
            logger.error("Failed to get video info for %s: %s", video_id, e)
            if platform != "youtube" and _is_curlcffi_connection_error(e):
                _trip_curlcffi_cooldown(str(e))
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

    @staticmethod
    def _build_postprocessors(chapters_enabled: bool, sponsorblock_mode: str, platform: str) -> list[dict]:
        """Build the yt-dlp postprocessor chain for a download. SponsorBlock only
        has data for YouTube, so it is skipped on other platforms."""
        sponsorblock = sponsorblock_mode in ("mark", "remove") and platform == "youtube"
        pps: list[dict] = []
        if sponsorblock:
            pps.append({
                "key": "SponsorBlock",
                "categories": _SPONSORBLOCK_CATEGORIES,
                "when": "after_filter",
            })
        pps += [
            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
            {"key": "EmbedThumbnail", "already_have_thumbnail": True},
        ]
        if sponsorblock and sponsorblock_mode == "remove":
            pps.append({"key": "ModifyChapters", "remove_sponsor_segments": _SPONSORBLOCK_CATEGORIES})
        # Embed chapters when the user enabled them, or when SponsorBlock is marking
        # segments so the marked segments are written into the file as chapters.
        if chapters_enabled or (sponsorblock and sponsorblock_mode == "mark"):
            pps.append({"key": "FFmpegMetadata", "add_chapters": True})
        return pps

    def download_video(
        self,
        video_url: str,
        output_path: str,
        quality: str = "best",
        progress_hook=None,
        pp_hook=None,
        platform: str = "youtube",
        subtitles_enabled: bool = False,
        chapters_enabled: bool = False,
        sponsorblock_mode: str = "off",
        temp_dir: str | None = None,
    ) -> dict:
        """Download a single video. Returns info dict on success, raises on failure.

        When temp_dir is given, ALL produced files (video, .part/fragment
        intermediates, sidecars) are written inside it under output_path's basename;
        the caller moves them to the final location afterwards. This isolates each
        attempt so an orphaned stalled attempt can never write over a retry's files.
        """
        opts = self._base_opts(platform=platform)
        postprocessors = self._build_postprocessors(chapters_enabled, sponsorblock_mode, platform)
        if temp_dir:
            outtmpl = str(Path(temp_dir) / Path(output_path).name) + ".%(ext)s"
            opts["paths"] = {"home": temp_dir, "temp": temp_dir}
        else:
            outtmpl = output_path + ".%(ext)s"
        opts.update({
            "format": self._quality_to_format(quality),
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "writethumbnail": True,
            "writeinfojson": True,
            "writesubtitles": subtitles_enabled,
            "writeautomaticsub": subtitles_enabled,
            "subtitleslangs": ["en"] if subtitles_enabled else [],
            "postprocessors": postprocessors,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 5,
            "ignoreerrors": False,
            "verbose": logger.isEnabledFor(logging.DEBUG),
        })

        if progress_hook:
            opts["progress_hooks"] = [progress_hook]

        if pp_hook:
            opts["postprocessor_hooks"] = [pp_hook]

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
        """Get current yt-dlp version (the one loaded in this process)."""
        try:
            return yt_dlp.version.__version__
        except Exception:
            return "unknown"

    @staticmethod
    def get_js_runtime_status() -> str | None:
        """Return a description of an available supported JS runtime (Deno or Bun),
        or None if none is found.

        yt-dlp's challenge solver needs one of these to solve YouTube's
        n-signature challenge; Node is no longer an accepted runtime. A missing
        runtime is the difference between working downloads and "No video formats
        found" for logged-in (cookie) sessions.
        """
        for name in ("deno", "bun"):
            path = shutil.which(name)
            if not path:
                continue
            try:
                out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
                text = (out.stdout or out.stderr).strip()
                return text.splitlines()[0] if text else name
            except Exception:
                return name  # present but version probe failed
        return None

    def _format_health_failure(self, msg: str) -> str:
        """Annotate a health-check failure with JS-runtime context so the most
        common cause (no supported runtime) is immediately obvious in logs,
        notifications, and the dashboard."""
        runtime = self.get_js_runtime_status()
        if runtime is None:
            return (f"{msg}. No supported JS runtime (Deno/Bun) found, so yt-dlp "
                    f"cannot solve YouTube's signature challenge - verify Deno is "
                    f"installed in the image.")
        return f"{msg} [JS runtime: {runtime}]"

    def test_download_capability(self) -> tuple[bool, str]:
        """Test whether yt-dlp can resolve real, downloadable video formats using
        the production config (cookies, PO tokens, JS runtime).

        Unlike a flat metadata extract, this exercises format resolution and
        YouTube's signature challenge, so it catches extraction-stack breakage
        (a missing/unsupported JS runtime, or SABR-only formats with no URLs)
        that would otherwise stay invisible until real downloads start failing.
        """
        test_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo" - first YouTube video
        opts = self._base_opts()
        opts.update({"skip_download": True, "quiet": True, "no_warnings": True})

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(test_url, download=False)
                if not info:
                    return False, self._format_health_failure("Could not extract video info")
                formats = info.get("formats") or ([info] if info.get("url") else [])
                has_video = any(f.get("vcodec") not in (None, "none") for f in formats)
                if not has_video:
                    return False, self._format_health_failure("No downloadable video formats resolved")
                runtime = self.get_js_runtime_status()
                return True, f"OK [JS runtime: {runtime or 'none'}]"
        except Exception as e:
            return False, self._format_health_failure(str(e))
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
                # Don't force a single client. YouTube runs per-client experiments
                # (DRM on tv, SABR on web/mweb) that break any one hardcoded client,
                # so let yt-dlp use its own regularly-updated client set, which tries
                # several and skips formats that aren't downloadable.
                yt_args = {}
            else:
                yt_args = {"player_client": player_client.split(",")}

            if settings.POT_SERVER_ENABLED:
                if settings.has_cookies:
                    logger.info("Using cookies + PO tokens for authentication")
                else:
                    logger.info("No cookies - using PO token server: %s", settings.POT_SERVER_URL)
                yt_args["fetch_pot"] = ["always"]
                use_pot = True
            elif settings.has_cookies:
                logger.info("Cookies available, PO token server disabled")
                yt_args["fetch_pot"] = ["never"]
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

        # Non-YouTube: impersonate a browser to bypass anti-bot protections (Cloudflare, etc.)
        if platform != "youtube":
            target = _get_impersonate_target()
            if target is not None:
                opts["impersonate"] = target

        if settings.has_cookies:
            cookie_size = settings.cookies_path.stat().st_size if settings.cookies_path.exists() else 0
            logger.info("Using cookies file: %s (%d bytes)", settings.cookies_path, cookie_size)
            opts["cookiefile"] = self._get_cached_cookie_copy()
        else:
            logger.info("No cookies file found at %s", settings.cookies_path)

        # User-Agent. For impersonated (non-YouTube) sites, replay the exact UA captured
        # at cookie-export time so a Cloudflare cf_clearance token validates - this must
        # win over UA rotation. Elsewhere, keep the optional rotation behaviour.
        cookie_ua = self._get_cookie_user_agent() if platform != "youtube" else None
        if cookie_ua:
            opts["http_headers"] = {"User-Agent": cookie_ua}
        elif settings.USER_AGENT_ROTATION:
            from app.utils.user_agents import get_random_user_agent
            opts["http_headers"] = {"User-Agent": get_random_user_agent()}

        return opts

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
