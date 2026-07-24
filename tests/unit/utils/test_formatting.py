"""Tests for the shared file-size formatter (utils.formatting)."""

from emby_dedupe.utils.formatting import format_file_size


class TestFormatFileSize:
    def test_bytes(self):
        assert format_file_size(512) == "512.00 B"

    def test_kilobytes(self):
        assert format_file_size(2048) == "2.00 KB"

    def test_megabytes(self):
        assert format_file_size(5 * 1024 * 1024) == "5.00 MB"

    def test_gigabytes(self):
        assert format_file_size(5 * 1024**3) == "5.00 GB"

    def test_terabytes_and_petabytes(self):
        assert format_file_size(3 * 1024**4) == "3.00 TB"
        assert format_file_size(2 * 1024**5) == "2.00 PB"

    def test_zero_uses_zero_label(self):
        assert format_file_size(0) == "0 B"  # default zero_label
        assert format_file_size(0, zero_label="unknown") == "unknown"
        assert format_file_size(0, zero_label="Unknown") == "Unknown"

    def test_none_is_zero_label_not_crash(self):
        # Emby returns null Sizes; the previous reports.common version crashed here.
        assert format_file_size(None) == "0 B"
        assert format_file_size(None, zero_label="unknown") == "unknown"
