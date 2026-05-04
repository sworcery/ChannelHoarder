"""Quality tier comparison for upgrade detection."""

QUALITY_ORDER = {"480p": 1, "720p": 2, "1080p": 3, "2160p": 4, "best": 5}

QUALITY_HEIGHT = {"480p": 480, "720p": 720, "1080p": 1080, "2160p": 2160}


def quality_rank(q: str | None) -> int:
    """Get numeric rank for a quality string. Higher = better."""
    if not q:
        return 0
    return QUALITY_ORDER.get(q, 0)


def quality_met(downloaded: str | None, cutoff: str | None) -> bool:
    """Check if downloaded quality meets or exceeds the cutoff.

    Returns True if no cutoff is set, or if downloaded quality >= cutoff.
    """
    if not cutoff:
        return True  # No cutoff means quality is always met
    if not downloaded:
        return False
    return quality_rank(downloaded) >= quality_rank(cutoff)


def height_to_quality(height: int) -> str:
    """Convert a pixel height to the nearest quality tier label."""
    if height >= 2160:
        return "2160p"
    elif height >= 1080:
        return "1080p"
    elif height >= 720:
        return "720p"
    return "480p"


def best_available_quality(formats: list[dict]) -> str | None:
    """Determine the best available quality from a list of yt-dlp format dicts."""
    max_height = 0
    for fmt in formats:
        h = fmt.get("height") or 0
        if h > max_height:
            max_height = h
    if max_height == 0:
        return None
    return height_to_quality(max_height)
