"""Firefox cookie exporter for ChannelHoarder.

Reads cookies directly from Firefox's SQLite database (no encryption,
no file-locking issues). Optionally opens Firefox to refresh cookies
before exporting.

Requirements:
  - Windows 10/11
  - Python 3.10+
  - Firefox installed with a profile logged into YouTube

Usage:
  python cookie_exporter.py                     # uses cookie_exporter.ini
  python cookie_exporter.py --config my.ini     # custom config
  python cookie_exporter.py --dry-run           # preview without pushing
  python cookie_exporter.py --no-refresh        # skip opening Firefox first

Designed to be run via Windows Task Scheduler every 30-60 minutes.
"""

import argparse
import configparser
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("cookie_exporter")

# Default config values
DEFAULT_DOMAINS = ".youtube.com,.google.com"


def _find_firefox_profile(profile_name: str = "") -> str:
    """Find the Firefox profile directory.

    If profile_name is given, look for that specific profile folder.
    Otherwise, find the default profile from profiles.ini.
    """
    appdata = os.environ.get("APPDATA", "")
    ff_dir = os.path.join(appdata, "Mozilla", "Firefox")
    profiles_ini = os.path.join(ff_dir, "profiles.ini")

    if not os.path.exists(profiles_ini):
        logger.error("Firefox profiles.ini not found at: %s", profiles_ini)
        logger.error("Is Firefox installed and has been run at least once?")
        sys.exit(1)

    config = configparser.ConfigParser()
    config.read(profiles_ini)

    profiles_dir = os.path.join(ff_dir, "Profiles")

    # If a specific profile name was given, look for it
    if profile_name:
        # Check if it's a full path
        if os.path.isdir(profile_name):
            return profile_name

        # Look for it in the Profiles directory
        for entry in os.listdir(profiles_dir):
            full = os.path.join(profiles_dir, entry)
            if os.path.isdir(full) and (entry == profile_name or entry.endswith(f".{profile_name}")):
                return full

        # Search profiles.ini sections
        for section in config.sections():
            if not section.startswith("Profile"):
                continue
            name = config.get(section, "Name", fallback="")
            if name.lower() == profile_name.lower():
                path = config.get(section, "Path", fallback="")
                is_relative = config.getboolean(section, "IsRelative", fallback=True)
                if is_relative:
                    return os.path.join(ff_dir, path)
                return path

        logger.error("Firefox profile '%s' not found", profile_name)
        sys.exit(1)

    # Find the default profile
    for section in config.sections():
        if not section.startswith("Install"):
            continue
        default_path = config.get(section, "Default", fallback="")
        if default_path:
            is_relative = True  # Install sections use relative paths
            profile_dir = os.path.join(ff_dir, default_path)
            if os.path.isdir(profile_dir):
                return profile_dir

    # Fallback: look for profile with Default=1
    for section in config.sections():
        if not section.startswith("Profile"):
            continue
        if config.getboolean(section, "Default", fallback=False):
            path = config.get(section, "Path", fallback="")
            is_relative = config.getboolean(section, "IsRelative", fallback=True)
            if is_relative:
                return os.path.join(ff_dir, path)
            return path

    # Last resort: first profile we find
    for section in config.sections():
        if not section.startswith("Profile"):
            continue
        path = config.get(section, "Path", fallback="")
        if path:
            is_relative = config.getboolean(section, "IsRelative", fallback=True)
            if is_relative:
                return os.path.join(ff_dir, path)
            return path

    logger.error("No Firefox profile found in profiles.ini")
    sys.exit(1)


def _find_firefox_exe() -> str:
    """Find Firefox executable path."""
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"),
                     "Mozilla Firefox", "firefox.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
                     "Mozilla Firefox", "firefox.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    logger.error("Firefox not found. Checked: %s", ", ".join(candidates))
    sys.exit(1)


def _kill_firefox():
    """Kill all Firefox processes."""
    result = subprocess.run(
        ["taskkill", "/f", "/im", "firefox.exe"],
        capture_output=True,
    )
    if result.returncode == 0:
        logger.info("Closed Firefox")
    else:
        logger.debug("Firefox was not running")


def _refresh_cookies(firefox_exe: str, url: str, wait_seconds: int):
    """Open Firefox to a URL to refresh cookies, then close it."""
    logger.info("Opening %s in Firefox (waiting %ds for page load)...", url, wait_seconds)
    proc = subprocess.Popen(
        [firefox_exe, url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info("Launched Firefox (PID %d) → %s", proc.pid, url)

    time.sleep(wait_seconds)

    logger.info("Closing Firefox to release cookie DB...")
    _kill_firefox()
    time.sleep(2)


def extract_cookies(
    profile_dir: str,
    domains: list[str],
) -> tuple[str, int]:
    """Extract cookies from Firefox's cookies.sqlite.

    Firefox stores cookies unencrypted in SQLite. The database can usually
    be read while Firefox is closed. If Firefox is open, we copy the DB first.

    Returns (cookies_txt_content, cookie_count).
    """
    cookies_db = os.path.join(profile_dir, "cookies.sqlite")

    if not os.path.exists(cookies_db):
        logger.error("Cookies database not found: %s", cookies_db)
        sys.exit(1)

    logger.info("Reading cookies from: %s", cookies_db)

    # Try to open the DB directly first, fall back to copying if locked
    tmp_path = None
    try:
        conn = sqlite3.connect(f"file:{cookies_db}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM moz_cookies LIMIT 1")
        logger.info("Opened cookies DB directly (read-only)")
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        logger.info("DB locked or busy (%s), copying to temp file...", e)
        conn = None

        # Copy the DB and WAL files to a temp directory
        tmp_dir = tempfile.mkdtemp(prefix="ch_cookies_")
        tmp_path = os.path.join(tmp_dir, "cookies.sqlite")

        for suffix in ("", "-wal", "-shm"):
            src = cookies_db + suffix
            dst = tmp_path + suffix
            if os.path.exists(src):
                try:
                    shutil.copy2(src, dst)
                except PermissionError:
                    if suffix == "":
                        logger.error(
                            "Cannot copy Firefox cookies DB. "
                            "Close Firefox and retry, or run with --no-refresh."
                        )
                        sys.exit(1)
                    logger.debug("Could not copy %s (skipped)", os.path.basename(src))

        conn = sqlite3.connect(tmp_path)
        logger.info("Opened copied cookies DB")

    try:
        cursor = conn.cursor()

        # Build domain filter
        domain_conditions = []
        params = []
        for d in domains:
            domain_conditions.append("host LIKE ?")
            params.append(f"%{d}")

        where = " OR ".join(domain_conditions)
        query = f"""
            SELECT host, name, value, path, expiry, isSecure, isHttpOnly
            FROM moz_cookies
            WHERE ({where})
        """

        cursor.execute(query, params)
        rows = cursor.fetchall()

        logger.info("Found %d cookies matching domains: %s", len(rows), domains)

        now = int(datetime.now(timezone.utc).timestamp())
        lines = [
            "# Netscape HTTP Cookie File",
            "# Exported by ChannelHoarder cookie_exporter (Firefox)",
            "",
        ]
        count = 0

        for host, name, value, path, expiry, is_secure, is_http_only in rows:
            if not value:
                continue

            # Skip expired cookies (expiry=0 means session cookie — include those)
            if expiry > 0 and expiry < now:
                continue

            subdomain = "TRUE" if host.startswith(".") else "FALSE"
            secure_flag = "TRUE" if is_secure else "FALSE"

            lines.append(
                f"{host}\t{subdomain}\t{path}\t{secure_flag}\t{expiry}\t{name}\t{value}"
            )
            count += 1

        return "\n".join(lines) + "\n", count

    finally:
        conn.close()
        # Clean up temp files
        if tmp_path:
            tmp_dir = os.path.dirname(tmp_path)
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass


def push_to_api(server_url: str, cookies_txt: str) -> bool:
    """Push cookies to ChannelHoarder API. Returns True on success."""
    url = server_url.rstrip("/") + "/api/v1/auth/cookies/push"
    payload = json.dumps({"cookies_txt": cookies_txt}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
            logger.info("API push success: %s", body.get("message", "OK"))
            return True
    except urllib.error.HTTPError as e:
        logger.error("API push failed (HTTP %d): %s", e.code, e.read().decode(errors="replace"))
        return False
    except urllib.error.URLError as e:
        logger.error("API push failed (connection error): %s", e.reason)
        return False


def write_to_file(output_path: str, cookies_txt: str) -> bool:
    """Write cookies to a file. Returns True on success."""
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", newline="\n", encoding="utf-8") as f:
            f.write(cookies_txt)
        logger.info("Wrote cookies to %s", output_path)
        return True
    except OSError as e:
        logger.error("Failed to write cookies file: %s", e)
        return False


def load_config(config_path: str) -> dict:
    """Load settings from INI config file."""
    config = configparser.ConfigParser()
    config.read(config_path)

    section = (
        config["cookie_exporter"]
        if "cookie_exporter" in config
        else config[config.default_section]
    )

    return {
        "profile": section.get("profile", ""),
        "domains": [d.strip() for d in section.get("domains", DEFAULT_DOMAINS).split(",")],
        "server_url": section.get("server_url", ""),
        "output_path": section.get("output_path", ""),
        "refresh_url": section.get("refresh_url", "https://www.youtube.com"),
        "refresh_wait": int(section.get("refresh_wait", "20")),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Export Firefox cookies for ChannelHoarder",
    )
    parser.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(__file__), "cookie_exporter.ini"),
        help="Path to config INI file (default: cookie_exporter.ini next to this script)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without pushing")
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Skip opening Firefox to refresh cookies (read existing DB only)",
    )
    parser.add_argument(
        "--refresh-wait",
        type=int,
        help="Seconds to wait for page to load (default: from config or 20)",
    )
    parser.add_argument("--output", help="Override output file path")
    parser.add_argument("--server", help="Override ChannelHoarder server URL")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        logger.error("Config file not found: %s", args.config)
        logger.info("Create a cookie_exporter.ini file — see cookie_exporter.ini for reference")
        sys.exit(1)

    cfg = load_config(args.config)
    server_url = args.server or cfg["server_url"]
    output_path = args.output or cfg["output_path"]
    refresh_url = cfg["refresh_url"]
    refresh_wait = args.refresh_wait or cfg["refresh_wait"]

    if not server_url and not output_path:
        logger.error(
            "No delivery method configured. Set server_url and/or output_path in cookie_exporter.ini"
        )
        sys.exit(1)

    # Find Firefox profile
    profile_dir = _find_firefox_profile(cfg["profile"])
    logger.info("Firefox profile: %s", profile_dir)
    logger.info("Domains: %s", cfg["domains"])
    if server_url:
        logger.info("API push: %s", server_url)
    if output_path:
        logger.info("File output: %s", output_path)

    # Step 1: Optionally refresh cookies by opening Firefox
    if not args.no_refresh:
        firefox_exe = _find_firefox_exe()
        _kill_firefox()
        time.sleep(2)
        _refresh_cookies(firefox_exe, refresh_url, refresh_wait)

    # Step 2: Extract cookies from Firefox DB
    try:
        cookies_txt, count = extract_cookies(profile_dir, cfg["domains"])
    except Exception as e:
        logger.error("Failed to extract cookies: %s", e)
        sys.exit(1)

    if count == 0:
        logger.warning("No cookies exported — is Firefox logged into YouTube?")
        sys.exit(1)

    logger.info("Extracted %d cookies", count)

    if args.dry_run:
        logger.info("[DRY RUN] Would deliver %d cookies", count)
        for line in cookies_txt.split("\n")[:10]:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                logger.info("  %s: %s = %s...", parts[0], parts[5], parts[6][:20])
        return

    # Step 3: Deliver cookies
    success = False

    if server_url:
        if push_to_api(server_url, cookies_txt):
            success = True

    if output_path:
        if write_to_file(output_path, cookies_txt):
            success = True

    if not success:
        logger.error("All delivery methods failed")
        sys.exit(1)

    logger.info("Done — cookies delivered successfully")


if __name__ == "__main__":
    main()
