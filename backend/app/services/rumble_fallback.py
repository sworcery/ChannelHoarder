"""Work around Rumble's embedJS endpoint answering 403 to yt-dlp.

Upstream yt-dlp issue 17496 (still open): the RumbleEmbed extractor requests
https://rumble.com/embedJS/u3/?request=video&ver=2&v=<id>, which Rumble now
refuses with HTTP 403, so every Rumble download fails with "Unable to download
JSON metadata: HTTP Error 403".

The embed page https://rumble.com/embed/<id>/ still loads and carries the same
player data inline as JavaScript (`<minified>.f["<id>"]={...,loaded:v()}`).
When embedJS answers 403, we read the data from that page instead.

This module can be deleted, along with its install() call, once yt-dlp fixes
the extractor upstream.
"""

import logging
import re

logger = logging.getLogger(__name__)

_installed = False


def _is_embedjs_403(exc: Exception, url_or_request) -> bool:
    cause = getattr(exc, "cause", None)
    if getattr(cause, "status", None) != 403:
        return False
    url = getattr(url_or_request, "url", url_or_request)
    return "embedJS" in str(url)


def _strip_loaded(source: str) -> str:
    # The player data ends with `loaded:v()`, a call that is not valid JSON.
    return re.sub(r",\s*loaded\s*:\s*[\w$.]+\(\)", "", source)


def install() -> None:
    """Patch RumbleEmbedIE._download_json once. Safe to call repeatedly."""
    global _installed
    if _installed:
        return
    try:
        from yt_dlp.extractor.rumble import RumbleEmbedIE

        original = RumbleEmbedIE._download_json
        if not callable(getattr(RumbleEmbedIE, "_search_json", None)):
            raise AttributeError("_search_json")
        if not callable(getattr(RumbleEmbedIE, "_download_webpage", None)):
            raise AttributeError("_download_webpage")
    except Exception as e:
        logger.warning("Rumble 403 fallback not installed (yt-dlp internals changed: %s)", e)
        _installed = True
        return

    def patched(self, url_or_request, video_id, *args, **kwargs):
        try:
            return original(self, url_or_request, video_id, *args, **kwargs)
        except Exception as err:
            if not _is_embedjs_403(err, url_or_request):
                raise
            try:
                page = self._download_webpage(
                    f"https://rumble.com/embed/{video_id}/", video_id,
                    note="Downloading embed page")
                data = self._search_json(
                    r"[\w$]+\.f\[\"%s\"\]\s*=" % re.escape(video_id), page,
                    "player data", video_id, transform_source=_strip_loaded)
            except Exception as fallback_err:
                logger.debug("Rumble embed page fallback failed for %s: %s", video_id, fallback_err)
                raise err
            # yt-dlp builds the formats from "ua"; without it the real 403 is the better error.
            if not isinstance(data, dict) or not data.get("ua"):
                raise err
            logger.info("Rumble embedJS returned 403 for %s; used the embed page instead", video_id)
            return data

    RumbleEmbedIE._download_json = patched
    _installed = True
