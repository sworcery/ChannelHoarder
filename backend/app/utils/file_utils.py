import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse


def validate_url_scheme(url: str) -> None:
    """Reject URLs with unsafe schemes (file://, ftp://, data:, etc.)."""
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme.lower() not in ("http", "https", ""):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}://")


def validate_download_path(path: str, allowed_roots: list[str] | None = None) -> Path:
    """Validate that a download directory path is safe.

    Rejects paths containing '..' traversal. If allowed_roots is provided,
    ensures the resolved path is under one of them.
    """
    resolved = Path(path).resolve()
    # Reject explicit traversal
    if ".." in Path(path).parts:
        raise ValueError(f"Path traversal not allowed: {path}")
    # If allowed roots specified, check containment
    if allowed_roots:
        if not any(resolved.is_relative_to(Path(root).resolve()) for root in allowed_roots):
            raise ValueError(f"Path not under allowed directory: {path}")
    return resolved


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
