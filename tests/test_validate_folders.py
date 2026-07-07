"""
Unit tests for validate_folders.py — validate_input_file().

Each test is structured in three sections:
  PRECONDITION – state of the system before the call.
  STEP         – the action taken (function call).
  RESULT       – assertions on the outcome.
"""

import json
import os

import pytest

from validate_folders import validate_input_file


class TestValidateInputFilePositive:
    """Happy-path tests: valid JSON files with real or plausible paths."""

    def test_returns_list_with_single_path(self, tmp_path, data_dir):
        # PRECONDITION: well-formed JSON file, paths_to_files contains one
        # entry that is the absolute path to the workspace data/ folder.
        json_file = tmp_path / "input.json"
        json_file.write_text(json.dumps({"paths_to_files": [data_dir]}))

        # STEP: parse the JSON file.
        result = validate_input_file(str(json_file))

        # RESULT: a non-empty list is returned, and it contains the given path.
        assert isinstance(result, list)
        assert len(result) == 1
        assert data_dir in result

    def test_returns_all_paths_for_multiple_entries(self, tmp_path, data_dir):
        # PRECONDITION: JSON file contains two (identical) paths.
        json_file = tmp_path / "multi.json"
        json_file.write_text(json.dumps({"paths_to_files": [data_dir, data_dir]}))

        # STEP: parse the JSON file.
        result = validate_input_file(str(json_file))

        # RESULT: all two paths are present in the returned list.
        assert len(result) == 2
        assert all(p == data_dir for p in result)

    def test_converts_windows_paths_to_wsl_format(self, tmp_path):
        # PRECONDITION: JSON file contains a Windows-style path starting
        # with "C:\".
        json_file = tmp_path / "win.json"
        json_file.write_text(
            json.dumps({"paths_to_files": ["C:\\Users\\data\\project\\"]})
        )

        # STEP: parse the JSON file.
        result = validate_input_file(str(json_file))

        # RESULT: the returned path uses the WSL-style "/mnt/c/" prefix and
        # forward slashes instead of backslashes.
        assert len(result) == 1
        assert result[0].startswith("/mnt/c/")
        assert "\\" not in result[0]


class TestValidateInputFileNegative:
    """Error-path tests: missing file, empty list, missing key."""

    def test_raises_value_error_when_json_file_not_found(self, tmp_path):
        # PRECONDITION: the JSON file does not exist on disk.
        missing = str(tmp_path / "nonexistent.json")

        # STEP: attempt to parse a non-existent file.
        # RESULT: ValueError is raised with a message about the missing file.
        with pytest.raises(ValueError, match="does not exist"):
            validate_input_file(missing)

    def test_raises_value_error_for_empty_paths_list(self, tmp_path):
        # PRECONDITION: JSON file exists, but paths_to_files is an empty list.
        json_file = tmp_path / "empty.json"
        json_file.write_text(json.dumps({"paths_to_files": []}))

        # STEP: parse the JSON file with an empty list.
        # RESULT: ValueError is raised mentioning no folder paths.
        with pytest.raises(ValueError, match="does not contain any folder paths"):
            validate_input_file(str(json_file))

    def test_raises_value_error_when_paths_to_files_key_is_absent(self, tmp_path):
        # PRECONDITION: JSON file uses an unrecognised top-level key.
        json_file = tmp_path / "bad_key.json"
        json_file.write_text(json.dumps({"other_key": ["/some/path"]}))

        # STEP: parse the JSON file with the wrong key.
        # RESULT: ValueError is raised because paths_to_files resolves to [].
        with pytest.raises(ValueError, match="does not contain any folder paths"):
            validate_input_file(str(json_file))
