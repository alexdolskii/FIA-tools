"""
Unit tests for 3_foci_mask_generation.py.

Covers:
  - validate_folders()         – checks Foci/ and Nuclei_StarDist/ subfolders
  - parse_metadata_file()      – reads image_metadata.txt
  - find_metadata_for_file()   – filename-to-calibration lookup
  - filter_foci()              – ImageJ-based mask creation (ImageJ mocked)
  - main_filter_foci()         – end-to-end with mocked user input

Each test is structured in three sections:
  PRECONDITION – state / setup before the call.
  STEP         – the function call being exercised.
  RESULT       – assertions on the outcome.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# The module was loaded and registered by conftest.py.
mod = sys.modules["foci_mask_generation"]


# ─────────────────────────────────────────────────────────────────────────────
# validate_folders
# ─────────────────────────────────────────────────────────────────────────────
class TestValidateFolders:
    """Tests for validate_folders() in 3_foci_mask_generation.py."""

    def test_returns_foci_and_nuclei_paths_for_valid_data_dir(
        self, test_input_json, data_dir
    ):
        # PRECONDITION: data/ contains foci_assay/Foci/ and
        # foci_assay/Nuclei_StarDist_mask_processed_<timestamp>/.
        expected_foci = os.path.join(data_dir, "foci_assay", "Foci")

        # STEP: validate the directory described by the JSON.
        result = mod.validate_folders(test_input_json)

        # RESULT: the dict contains an entry for data_dir with both
        # 'foci_folder' and 'nuclei_folder' keys populated.
        assert data_dir in result
        folder_info = result[data_dir]
        assert "foci_folder" in folder_info
        assert os.path.normpath(folder_info["foci_folder"]) == \
               os.path.normpath(expected_foci)
        assert "nuclei_folder" in folder_info

    def test_selects_latest_nuclei_stardist_folder(
        self, tmp_path
    ):
        # PRECONDITION: foci_assay contains two Nuclei_StarDist_mask_processed
        # folders with different timestamps; foci_assay/Foci/ also exists.
        base = tmp_path / "project"
        foci_assay = base / "foci_assay"
        (foci_assay / "Foci").mkdir(parents=True)
        (foci_assay / "Nuclei_StarDist_mask_processed_20240101_120000").mkdir()
        (foci_assay / "Nuclei_StarDist_mask_processed_20251231_235959").mkdir()
        json_file = tmp_path / "input.json"
        json_file.write_text(json.dumps({"paths_to_files": [str(base)]}))

        # STEP: validate folders.
        result = mod.validate_folders(str(json_file))

        # RESULT: the later timestamp folder is chosen as nuclei_folder.
        assert "20251231_235959" in result[str(base)]["nuclei_folder"]

    def test_missing_foci_subfolder_not_in_result_dict(self, tmp_path):
        # PRECONDITION: foci_assay exists but Foci/ subfolder is absent.
        base = tmp_path / "project"
        foci_assay = base / "foci_assay"
        foci_assay.mkdir(parents=True)
        (foci_assay / "Nuclei_StarDist_mask_processed_20240101_120000").mkdir()
        json_file = tmp_path / "input.json"
        json_file.write_text(json.dumps({"paths_to_files": [str(base)]}))

        # STEP: validate folders.
        result = mod.validate_folders(str(json_file))

        # RESULT: 'foci_folder' key is absent because Foci/ was not found.
        assert "foci_folder" not in result.get(str(base), {})


# ─────────────────────────────────────────────────────────────────────────────
# parse_metadata_file
# ─────────────────────────────────────────────────────────────────────────────
class TestParseMetadataFile:
    """Tests for parse_metadata_file() in 3_foci_mask_generation.py."""

    def test_parses_real_metadata_file(self, data_dir):
        # PRECONDITION: data/foci_assay/image_metadata.txt exists and contains
        # at least one image entry with calibration data.
        metadata_path = os.path.join(data_dir, "foci_assay", "image_metadata.txt")

        # STEP: parse the metadata file.
        result = mod.parse_metadata_file(metadata_path)

        # RESULT: returns a non-empty dict; each value has pixel_width,
        # pixel_height, pixel_depth, and unit keys.
        assert isinstance(result, dict)
        assert len(result) > 0
        for key, cal in result.items():
            assert "pixel_width" in cal
            assert "pixel_height" in cal
            assert "pixel_depth" in cal
            assert "unit" in cal

    def test_returns_correct_calibration_values(self, data_dir):
        # PRECONDITION: image_metadata.txt contains entries for images '1' and
        # '2' with pixel_width = 0.2071... as written by the pipeline.
        metadata_path = os.path.join(data_dir, "foci_assay", "image_metadata.txt")

        # STEP: parse the file.
        result = mod.parse_metadata_file(metadata_path)

        # RESULT: at least one key has pixel_width close to 0.2071.
        widths = [v["pixel_width"] for v in result.values()]
        assert any(abs(w - 0.2071601898007465) < 1e-9 for w in widths)

    def test_returns_empty_dict_when_file_not_found(self, tmp_path):
        # PRECONDITION: the metadata file does not exist.
        missing = str(tmp_path / "no_metadata.txt")

        # STEP: attempt to parse the missing file.
        result = mod.parse_metadata_file(missing)

        # RESULT: an empty dict is returned (no exception raised).
        assert result == {}

    def test_parses_custom_metadata_written_inline(self, tmp_path):
        # PRECONDITION: a minimal metadata file is written with two images.
        meta_path = tmp_path / "image_metadata.txt"
        meta_path.write_text(
            "Image Metadata:\n"
            "================\n"
            "Image Name: img_A.tif\n"
            "  Pixel Width: 0.1\n"
            "  Pixel Height: 0.2\n"
            "  Pixel Depth: 0.3\n"
            "  Unit: micron\n\n"
            "Image Name: img_B.tif\n"
            "  Pixel Width: 0.5\n"
            "  Pixel Height: 0.5\n"
            "  Pixel Depth: 1.0\n"
            "  Unit: um\n"
        )

        # STEP: parse the inline metadata file.
        result = mod.parse_metadata_file(str(meta_path))

        # RESULT: both image entries are present with correct calibration.
        assert "img_A" in result
        assert result["img_A"]["pixel_width"] == pytest.approx(0.1)
        assert result["img_A"]["unit"] == "micron"
        assert "img_B" in result
        assert result["img_B"]["pixel_depth"] == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────────────
# find_metadata_for_file
# ─────────────────────────────────────────────────────────────────────────────
class TestFindMetadataForFile:
    """Tests for find_metadata_for_file() in 3_foci_mask_generation.py."""

    _METADATA = {
        "image_1": {"pixel_width": 0.207, "pixel_height": 0.207,
                    "pixel_depth": 0.5, "unit": "micron"},
        "image_2": {"pixel_width": 0.100, "pixel_height": 0.100,
                    "pixel_depth": 0.5, "unit": "micron"},
    }

    def test_returns_calibration_when_base_key_in_filename(self):
        # PRECONDITION: metadata_dict contains key "image_1";
        # the filename contains "image_1".
        filename = "image_1_foci_projection.tif"

        # STEP: look up calibration data for the filename.
        result = mod.find_metadata_for_file(filename, self._METADATA)

        # RESULT: the dict for "image_1" is returned.
        assert result == self._METADATA["image_1"]

    def test_returns_none_when_no_key_matches(self):
        # PRECONDITION: the filename does not contain any key from the dict.
        filename = "image_99_foci_projection.tif"

        # STEP: attempt the lookup.
        result = mod.find_metadata_for_file(filename, self._METADATA)

        # RESULT: None is returned because no key matches.
        assert result is None

    def test_returns_none_for_empty_metadata_dict(self):
        # PRECONDITION: the metadata dictionary is empty.
        # STEP: look up calibration in an empty dict.
        result = mod.find_metadata_for_file("image_1.tif", {})

        # RESULT: None is returned.
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# filter_foci
# ─────────────────────────────────────────────────────────────────────────────
class TestFilterFoci:
    """Tests for filter_foci() in 3_foci_mask_generation.py."""

    def _make_foci_folder_dict(self, tmp_path: Path, subfolder: str) -> dict:
        """Build a minimal folder dict with one .tif file in Foci/<subfolder>/."""
        foci_assay = tmp_path / "foci_assay"
        foci_dir = foci_assay / "Foci" / subfolder
        foci_dir.mkdir(parents=True)
        (foci_dir / "sample_foci.tif").touch()
        return {
            "foci_assay_folder": str(foci_assay),
            "foci_folder": str(foci_assay / "Foci"),
        }

    def test_processes_tif_files_and_calls_imagej_save(self, tmp_path):
        # PRECONDITION: folder dict with one .tif in Foci/Foci_1_Channel_1/.
        # All ImageJ operations are mocked.
        subfolder = "Foci_1_Channel_1"
        folder_dict = self._make_foci_folder_dict(tmp_path, subfolder)

        fake_imp = MagicMock()
        fake_mask = MagicMock()
        mock_IJ = MagicMock()
        mock_IJ.openImage.return_value = fake_imp
        mock_win = MagicMock()
        mock_win.getImage.return_value = fake_mask

        with patch.object(mod, "imagej") as mock_imagej, \
             patch.object(mod, "jimport") as mock_jimport:
            mock_imagej.init.return_value = MagicMock()
            mock_jimport.side_effect = lambda cls: {
                "ij.IJ": mock_IJ,
                "ij.WindowManager": mock_win,
            }.get(cls, MagicMock())

            # STEP: call filter_foci for the subfolder.
            mod.filter_foci(folder_dict, subfolder, foci_threshold=150)

        # RESULT: IJ.openImage and IJ.saveAs were each called once.
        assert mock_IJ.openImage.call_count == 1
        assert mock_IJ.saveAs.call_count == 1

    def test_skips_when_chosen_subfolder_not_present(self, tmp_path, capsys):
        # PRECONDITION: folder dict whose Foci/ has no subfolder matching
        # the requested name.
        foci_assay = tmp_path / "foci_assay"
        (foci_assay / "Foci").mkdir(parents=True)
        folder_dict = {
            "foci_assay_folder": str(foci_assay),
            "foci_folder": str(foci_assay / "Foci"),
        }

        # STEP: call filter_foci with a subfolder that doesn't exist.
        mod.filter_foci(folder_dict, "Foci_99_Channel_99", foci_threshold=150)
        captured = capsys.readouterr()

        # RESULT: a "not found" message is printed; no exception is raised.
        assert "not found" in captured.out.lower() or "skipping" in captured.out.lower()

    def test_creates_foci_masks_output_directory(self, tmp_path):
        # PRECONDITION: folder dict with one .tif; ImageJ is mocked.
        subfolder = "Foci_1_Channel_1"
        folder_dict = self._make_foci_folder_dict(tmp_path, subfolder)

        with patch.object(mod, "imagej") as mock_imagej, \
             patch.object(mod, "jimport") as mock_jimport:
            mock_imagej.init.return_value = MagicMock()
            mock_IJ = MagicMock()
            mock_IJ.openImage.return_value = MagicMock()
            mock_jimport.side_effect = lambda cls: {
                "ij.IJ": mock_IJ,
                "ij.WindowManager": MagicMock(),
            }.get(cls, MagicMock())

            # STEP: call filter_foci.
            mod.filter_foci(folder_dict, subfolder, foci_threshold=100)

        # RESULT: Foci_Masks/ directory was created inside foci_assay/.
        foci_masks_base = os.path.join(folder_dict["foci_assay_folder"],
                                       "Foci_Masks")
        assert os.path.isdir(foci_masks_base)


# ─────────────────────────────────────────────────────────────────────────────
# main_filter_foci
# ─────────────────────────────────────────────────────────────────────────────
class TestMainFilterFoci:
    """Tests for main_filter_foci() in 3_foci_mask_generation.py."""

    def test_raises_when_foci_threshold_is_not_int(self, test_input_json):
        # PRECONDITION: threshold is passed as a float instead of int.
        # STEP / RESULT: ValueError about integer threshold.
        with pytest.raises(ValueError, match="integer"):
            mod.main_filter_foci(test_input_json, foci_threshold=150.5)

    def test_cancels_when_user_confirms_no(self, test_input_json):
        # PRECONDITION: valid JSON; user chooses "1" for subfolder then "no".
        with patch("builtins.input", side_effect=["1", "no"]):
            # STEP / RESULT: ValueError because the user cancelled.
            with pytest.raises(ValueError, match="canceled"):
                mod.main_filter_foci(test_input_json, foci_threshold=150)

    def test_raises_for_invalid_subfolder_choice(self, test_input_json):
        # PRECONDITION: valid JSON; user enters "999" which is out of range.
        with patch("builtins.input", return_value="999"):
            # STEP / RESULT: ValueError about invalid choice.
            with pytest.raises(ValueError, match="[Ii]nvalid"):
                mod.main_filter_foci(test_input_json, foci_threshold=150)
