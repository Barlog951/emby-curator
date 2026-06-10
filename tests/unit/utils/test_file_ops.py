"""
Tests for file operations utility functions
"""
import json
import os

from emby_dedupe.utils.file_ops import dump_object_to_file


class TestFileOps:
    """Tests for the file operations utility functions."""

    def test_dump_dict_to_json_file(self, tmpdir):
        """Test dumping a dictionary to a JSON file."""
        test_dict = {"key1": "value1", "key2": ["item1", "item2"]}
        file_path = os.path.join(tmpdir, "test_file")

        dump_object_to_file(test_dict, file_path)

        # Verify the file was created with correct extension
        assert os.path.exists(f"{file_path}.json")

        # Read and verify the content
        with open(f"{file_path}.json", "r") as f:
            content = json.load(f)
            assert content == test_dict

    def test_dump_string_to_text_file(self, tmpdir):
        """Test dumping a string to a text file."""
        test_string = "This is a test string"
        file_path = os.path.join(tmpdir, "test_file")

        dump_object_to_file(test_string, file_path)

        # Verify the file was created with correct extension
        assert os.path.exists(f"{file_path}.txt")

        # Read and verify the content
        with open(f"{file_path}.txt", "r") as f:
            content = f.read()
            assert content == test_string

    def test_dump_bytes_to_binary_file(self, tmpdir):
        """Test dumping bytes to a binary file."""
        test_bytes = b"This is a test bytes object"
        file_path = os.path.join(tmpdir, "test_file")

        dump_object_to_file(test_bytes, file_path)

        # Verify the file was created with correct extension
        assert os.path.exists(f"{file_path}.bin")

        # Read and verify the content
        with open(f"{file_path}.bin", "rb") as f:
            content = f.read()
            assert content == test_bytes

    def test_directory_creation(self, tmpdir):
        """Test that directories are created if they don't exist."""
        test_dict = {"key": "value"}
        dir_path = os.path.join(tmpdir, "nested", "dir")
        file_path = os.path.join(dir_path, "test_file")

        dump_object_to_file(test_dict, file_path)

        # Verify the directory and file were created
        assert os.path.exists(dir_path)
        assert os.path.exists(f"{file_path}.json")
