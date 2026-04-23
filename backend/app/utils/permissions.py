"""File permission management for downloaded videos."""

import grp
import logging
import os

from app.utils.file_utils import ASSOCIATED_EXTENSIONS

logger = logging.getLogger(__name__)


def apply_permissions(file_path: str, settings_dict: dict) -> None:
    """Apply chmod and chown settings to a downloaded file and its parent directory.

    settings_dict should contain:
        set_permissions: bool
        chmod_folder: str (octal, e.g. "755")
        chmod_file: str (octal, e.g. "644")
        chown_group: str (group name or GID)
    """
    if not settings_dict.get("set_permissions"):
        return

    try:
        parent_dir = os.path.dirname(file_path)

        # chmod folder
        chmod_folder = settings_dict.get("chmod_folder")
        if chmod_folder and parent_dir:
            try:
                os.chmod(parent_dir, int(chmod_folder, 8))
            except (ValueError, OSError) as e:
                logger.warning("chmod folder failed for %s: %s", parent_dir, e)

        # chmod file
        chmod_file = settings_dict.get("chmod_file")
        if chmod_file and os.path.exists(file_path):
            try:
                mode = int(chmod_file, 8)
                os.chmod(file_path, mode)
                base = os.path.splitext(file_path)[0]
                for ext in ASSOCIATED_EXTENSIONS:
                    extra = base + ext
                    if os.path.exists(extra):
                        os.chmod(extra, mode)
            except (ValueError, OSError) as e:
                logger.warning("chmod file failed for %s: %s", file_path, e)

        # chown group
        chown_group = settings_dict.get("chown_group")
        if chown_group:
            try:
                # Try as group name first, then as GID
                try:
                    gid = grp.getgrnam(chown_group).gr_gid
                except KeyError:
                    gid = int(chown_group)

                os.chown(file_path, -1, gid)
                if parent_dir:
                    os.chown(parent_dir, -1, gid)
                base = os.path.splitext(file_path)[0]
                for ext in ASSOCIATED_EXTENSIONS:
                    extra = base + ext
                    if os.path.exists(extra):
                        os.chown(extra, -1, gid)
            except (ValueError, OSError) as e:
                logger.warning("chown group failed for %s: %s", file_path, e)

    except Exception as e:
        logger.error("Permission application failed for %s: %s", file_path, e)
