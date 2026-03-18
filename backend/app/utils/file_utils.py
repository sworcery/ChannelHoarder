import re
import unicodedata


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """Make a string safe for use as a filename."""
    # Normalize unicode
    name = unicodedata.normalize("NFKD", name)

    # Replace unsafe characters
    unsafe = r'[<>:"/\\|?*\x00-\x1f]'
    name = re.sub(unsafe, "_", name)

    # Collapse multiple underscores/spaces
    name = re.sub(r"[_\s]+", " ", name).strip()

    # Remove leading/trailing dots and spaces
    name = name.strip(". ")

    # Truncate
    if len(name) > max_length:
        name = name[:max_length].rstrip(". ")

    return name or "Untitled"
