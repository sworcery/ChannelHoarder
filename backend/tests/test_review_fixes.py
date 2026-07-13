import os

import pytest

from app.utils.quality_utils import quality_met
from app.utils.file_utils import move_video_files


class TestQualityMetUnknown:
    """Unknown downloaded quality (None) must count as met so imported files /
    pre-column records are not mass re-downloaded by the quality-upgrade job."""

    def test_none_downloaded_is_met_when_cutoff_set(self):
        assert quality_met(None, "1080p") is True

    def test_no_cutoff_always_met(self):
        assert quality_met(None, None) is True
        assert quality_met("480p", None) is True

    def test_real_comparison_still_enforced(self):
        assert quality_met("720p", "1080p") is False
        assert quality_met("1080p", "1080p") is True
        assert quality_met("2160p", "1080p") is True


class TestMoveVideoFilesOverwriteGuard:
    def test_refuses_to_overwrite_existing_without_flag(self, tmp_path):
        src = tmp_path / "a.mp4"
        dst = tmp_path / "b.mp4"
        src.write_bytes(b"source")
        dst.write_bytes(b"IMPORTANT EXISTING")
        with pytest.raises(FileExistsError):
            move_video_files(str(src), str(dst))
        assert dst.read_bytes() == b"IMPORTANT EXISTING"  # not clobbered
        assert src.exists()  # source untouched

    def test_overwrite_flag_replaces(self, tmp_path):
        src = tmp_path / "a.mp4"
        dst = tmp_path / "b.mp4"
        src.write_bytes(b"NEW")
        dst.write_bytes(b"OLD")
        move_video_files(str(src), str(dst), overwrite=True)
        assert dst.read_bytes() == b"NEW"

    def test_moves_when_destination_absent(self, tmp_path):
        src = tmp_path / "a.mp4"
        dst = tmp_path / "sub" / "b.mp4"
        src.write_bytes(b"x")
        moved = move_video_files(str(src), str(dst))
        assert moved == 1
        assert dst.exists() and not src.exists()

    def test_same_path_is_noop_move(self, tmp_path):
        p = tmp_path / "a.mp4"
        p.write_bytes(b"x")
        # old == new must not raise (abspath equality short-circuits the guard)
        move_video_files(str(p), str(p))
        assert p.exists()
