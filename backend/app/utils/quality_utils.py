"""Quality tier comparison for upgrade detection."""

QUALITY_ORDER = {"480p": 1, "720p": 2, "1080p": 3, "2160p": 4, "best": 5}


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
