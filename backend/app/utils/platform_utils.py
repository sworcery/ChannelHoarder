"""Platform detection and URL helpers for multi-site support.

yt-dlp handles the actual extraction for 1000+ sites. This module
provides URL parsing, video URL construction, and platform-specific
feature flags so the rest of the codebase can avoid hardcoding YouTube.
"""

from urllib.parse import urlparse

PLATFORMS: dict[str, dict] = {
    "youtube": {
        "label": "YouTube",
        "domains": ["youtube.com", "youtu.be", "m.youtube.com"],
        "video_url_template": "https://www.youtube.com/watch?v={video_id}",
        "channel_video_suffix": "/videos",
        "cookie_domains": [".youtube.com", ".google.com"],
        "supports_api": True,
        "supports_rss": True,
        "tab_suffixes": ["/featured", "/about", "/community", "/playlists", "/shorts"],
    },
    "rumble": {
        "label": "Rumble",
        "domains": ["rumble.com"],
        "video_url_template": "https://rumble.com/{video_id}",
        "channel_video_suffix": "",
        "cookie_domains": [".rumble.com"],
        "supports_api": False,
        "supports_rss": False,
        "tab_suffixes": [],
    },
    "twitch": {
        "label": "Twitch",
        "domains": ["twitch.tv", "www.twitch.tv"],
        "video_url_template": "https://www.twitch.tv/videos/{video_id}",
        "channel_video_suffix": "/videos",
        "cookie_domains": [".twitch.tv"],
        "supports_api": False,
        "supports_rss": False,
        "tab_suffixes": [],
    },
    "dailymotion": {
        "label": "Dailymotion",
        "domains": ["dailymotion.com", "www.dailymotion.com"],
        "video_url_template": "https://www.dailymotion.com/video/{video_id}",
        "channel_video_suffix": "",
        "cookie_domains": [".dailymotion.com"],
        "supports_api": False,
        "supports_rss": False,
        "tab_suffixes": [],
    },
    "vimeo": {
        "label": "Vimeo",
        "domains": ["vimeo.com", "www.vimeo.com"],
        "video_url_template": "https://vimeo.com/{video_id}",
        "channel_video_suffix": "/videos",
        "cookie_domains": [".vimeo.com"],
        "supports_api": False,
        "supports_rss": False,
        "tab_suffixes": [],
    },
    "odysee": {
        "label": "Odysee",
        "domains": ["odysee.com"],
        "video_url_template": "https://odysee.com/{video_id}",
        "channel_video_suffix": "",
        "cookie_domains": [".odysee.com"],
        "supports_api": False,
        "supports_rss": False,
        "tab_suffixes": [],
    },
}

# Fallback for unknown platforms  - yt-dlp will still try to handle them
_GENERIC_PLATFORM = {
    "label": "Other",
    "domains": [],
    "video_url_template": "{video_id}",
    "channel_video_suffix": "",
    "cookie_domains": [],
    "supports_api": False,
    "supports_rss": False,
    "tab_suffixes": [],
}


def is_playlist_url(url: str) -> bool:
    """Check if a URL is a YouTube playlist (not a channel)."""
    lower = url.lower()
    return "playlist?list=" in lower or "/playlist/" in lower


def detect_platform(url: str) -> str:
    """Detect the platform from a URL. Returns platform key or 'other'."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        hostname = (parsed.hostname or "").lower().lstrip("www.")
    except Exception:
        return "other"

    for platform_key, config in PLATFORMS.items():
        for domain in config["domains"]:
            clean_domain = domain.lower().lstrip("www.")
            if hostname == clean_domain or hostname.endswith(f".{clean_domain}"):
                return platform_key

    return "other"


def get_platform_config(platform: str) -> dict:
    """Get the config dict for a platform, falling back to generic."""
    return PLATFORMS.get(platform, _GENERIC_PLATFORM)


def build_video_url(platform: str, video_id: str) -> str:
    """Construct a full video URL from platform + video_id."""
    config = get_platform_config(platform)
    return config["video_url_template"].format(video_id=video_id)


def get_channel_videos_url(platform: str, channel_url: str) -> str:
    """Append the platform-appropriate suffix for listing videos.
    Playlists are returned as-is (no suffix needed)."""
    if is_playlist_url(channel_url):
        return channel_url
    config = get_platform_config(platform)
    suffix = config["channel_video_suffix"]
    if suffix and not channel_url.rstrip("/").endswith(suffix):
        return channel_url.rstrip("/") + suffix
    return channel_url


def get_cookie_domains(platform: str) -> list[str]:
    """Return the cookie domains relevant to a platform."""
    config = get_platform_config(platform)
    return config["cookie_domains"]


def supports_rss(platform: str) -> bool:
    """Whether this platform supports RSS feeds for video discovery."""
    return get_platform_config(platform).get("supports_rss", False)


def supports_api(platform: str) -> bool:
    """Whether this platform has a dedicated API for video discovery."""
    return get_platform_config(platform).get("supports_api", False)


def get_tab_suffixes(platform: str) -> list[str]:
    """Get URL tab suffixes to strip for a platform (e.g. /featured)."""
    return get_platform_config(platform).get("tab_suffixes", [])


def get_platform_label(platform: str) -> str:
    """Get the human-readable label for a platform."""
    return get_platform_config(platform).get("label", platform.capitalize())
