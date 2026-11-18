"""Every "Learn more" anchor in the frontend must land on a real README heading."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
FRONTEND_SRC = ROOT / "frontend" / "src"

# anchor="..." props, and literal links to the repo README such as .../ChannelHoarder#some-heading
ANCHOR_PROP = re.compile(r"""\banchor=["']([^"']+)["']""")
README_LINK = re.compile(r"""github\.com/sworcery/ChannelHoarder/?#([A-Za-z0-9_-]+)""")


def github_slug(heading: str) -> str:
    """GitHub's heading id: inline markup removed, lowercase, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # links: keep the text
    text = re.sub(r"[`*_~]", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def readme_slugs() -> set[str]:
    slugs: set[str] = set()
    seen: dict[str, int] = {}
    in_fence = False
    for line in README.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not m:
            continue
        slug = github_slug(m.group(1))
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        slugs.add(slug if n == 0 else f"{slug}-{n}")
    return slugs


def frontend_anchors() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in FRONTEND_SRC.rglob("*.ts*"):
        text = path.read_text(encoding="utf-8")
        for pattern in (ANCHOR_PROP, README_LINK):
            for anchor in pattern.findall(text):
                found.setdefault(anchor, set()).add(str(path.relative_to(ROOT)))
    return found


def test_slug_rules():
    assert github_slug("YouTube Data API key (optional)") == "youtube-data-api-key-optional"
    assert github_slug("Adding `channels` & scanning") == "adding-channels--scanning"


def test_every_learn_more_anchor_is_a_readme_heading():
    anchors = frontend_anchors()
    assert anchors, "no anchors found; the scan pattern is out of date"
    slugs = readme_slugs()
    missing = {a: sorted(files) for a, files in anchors.items() if a not in slugs}
    assert not missing, f"anchors with no README heading: {missing}"
