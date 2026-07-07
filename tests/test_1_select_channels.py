"""
Unit tests for 1_select_channels.py.

Covers:
  - validate_folders()         – folder/file-extension logic
  - initialize_imagej()        – success and failure paths
  - process_image()            – happy path and channel-validation guard
    (ImageJ calls are fully mocked via the stubs set up in conftest.py)

Each test is structured in three sections:
  PRECONDITION – state / setup before the call.
  STEP         – the function call being exercised.
  RESULT       – assertions on the outcome.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# The module was loaded and registered by conftest.py.
mod = sys.modules["select_channels"]


# ─────────────────────────────────────────────────────────────────────────────
# validate_folders
# ─────────────────────────────────────────────────────────────────────────────
class TestValidateFolders:
    """Tests for the validate_folders() function in 1_select_channels.py."""

    def test_returns_folder_containing_tif_files(self, tmp_path):
        # PRECONDITION: a folder exists and contains exactly one .tif file.
        img_dir = tmp_path / "images"
        img_dir.mkdir()
        (img_dir / "sample.tif").touch()
        json_file = tmp_path / "input.json"
        json_file.write_text(json.dumps({"paths_to_files": [str(img_dir)]}))

        # STEP: call validate_folders with the JSON path.
        result = mod.validate_folders(str(json_file))

        # RESULT: the folder appears in the returned list.
        assert str(img_dir) in result

    def test_returns_folder_containing_nd2_files(self, tmp_path):
        # PRECONDITION: a folder exists and contains one .nd2 file.
        img_dir = tmp_path / "nd2_images"
        img_dir.mkdir()
        (img_dir / "image.nd2").touch()
        json_file = tmp_path / "input.json"
        json_file.write_text(json.dumps({"paths_to_files": [str(img_dir)]}))

        # STEP: call validate_folders.
        result = mod.validate_folders(str(json_file))

        # RESULT: folder is recognised because it contains a .nd2 file.
        assert str(img_dir) in result

    def test_excludes_folder_with_no_recognised_files(self, tmp_path):
        # PRECONDITION: a folder exists but contains only an unsupported .csv.
        img_dir = tmp_path / "misc"
        img_dir.mkdir()
        (img_dir / "data.csv").touch()
        json_file = tmp_path / "input.json"
        json_file.write_text(json.dumps({"paths_to_files": [str(img_dir)]}))

        # STEP: call validate_folders.
        result = mod.validate_folders(str(json_file))

        # RESULT: no folder is returned because no recognised image files exist.
        assert result == []

    def test_skips_hidden_and_dot_underscore_files(self, tmp_path):
        # PRECONDITION: folder contains only hidden / macOS temp files.
        img_dir = tmp_path / "hidden"
        img_dir.mkdir()
        (img_dir / ".hidden.tif").touch()
        (img_dir / "._mac_temp.tif").touch()
        json_file = tmp_path / "input.json"
        json_file.write_text(json.dumps({"paths_to_files": [str(img_dir)]}))

        # STEP: call validate_folders.
        result = mod.validate_folders(str(json_file))

        # RESULT: hidden / temp files are not counted; folder is excluded.
        assert result == []

    def test_raises_for_non_existent_folder(self, tmp_path):
        # PRECONDITION: JSON references a folder path that does not exist.
        missing = str(tmp_path / "does_not_exist")
        json_file = tmp_path / "input.json"
        json_file.write_text(json.dumps({"paths_to_files": [missing]}))

        # STEP: call validate_folders.
        # RESULT: ValueError is raised because the folder is absent.
        with pytest.raises(ValueError, match="does not exist"):
            mod.validate_folders(str(json_file))


# ─────────────────────────────────────────────────────────────────────────────
# initialize_imagej
# ─────────────────────────────────────────────────────────────────────────────
class TestInitializeImageJ:
    """Tests for the initialize_imagej() function."""

    def test_returns_ij_instance_on_success(self):
        # PRECONDITION: imagej.init is mocked and returns a fake IJ object
        # with a getVersion() method.
        fake_ij = MagicMock()
        fake_ij.getVersion.return_value = "2.14.0/1.54f"
        with patch.object(mod, "imagej") as mock_imagej:
            mock_imagej.init.return_value = fake_ij

            # STEP: call initialize_imagej().
            result = mod.initialize_imagej()

        # RESULT: the returned object is the fake IJ instance.
        assert result is fake_ij

    def test_raises_custom_error_when_imagej_init_fails(self):
        # PRECONDITION: imagej.init raises a RuntimeError (e.g. JVM failure).
        with patch.object(mod, "imagej") as mock_imagej:
            mock_imagej.init.side_effect = RuntimeError("JVM crashed")

            # STEP: call initialize_imagej().
            # RESULT: ImageJInitializationError is raised with context.
            with pytest.raises(mod.ImageJInitializationError,
                               match="Failed to initialize ImageJ"):
                mod.initialize_imagej()


# ─────────────────────────────────────────────────────────────────────────────
# process_image
# ─────────────────────────────────────────────────────────────────────────────
class TestProcessImage:
    """Tests for the process_image() function."""

    def _make_folder_with_tif(self, tmp_path: Path) -> Path:
        """Helper: create a temp folder structure with one stub .tif file."""
        img_dir = tmp_path / "raw"
        img_dir.mkdir()
        (img_dir / "image1.tif").touch()
        return img_dir

    def test_creates_output_folders_and_metadata_file(self, tmp_path):
        # PRECONDITION: one input folder with a .tif file; all ImageJ/Java
        # classes are mocked so no JVM is needed.
        img_dir = self._make_folder_with_tif(tmp_path)

        fake_imp = MagicMock()
        fake_imp.getDimensions.return_value = (1024, 1024, 3, 1, 1)
        fake_cal = MagicMock()
        fake_cal.pixelWidth = 0.207
        fake_cal.pixelHeight = 0.207
        fake_cal.pixelDepth = 0.5
        fake_cal.getUnit.return_value = "micron"
        fake_imp.getCalibration.return_value = fake_cal

        fake_ij_instance = MagicMock()
        fake_ij_instance.openImage.return_value = fake_imp

        # Patch module-level imagej so initialize_imagej() succeeds silently.
        with patch.object(mod, "imagej") as mock_imagej, \
             patch.object(mod, "jimport") as mock_jimport, \
             patch("builtins.input", return_value="yes"):

            mock_imagej.init.return_value = MagicMock()
            # jimport returns a mock IJ class whose openImage returns fake_imp
            mock_IJ = MagicMock()
            mock_IJ.openImage.return_value = fake_imp
            mock_jimport.side_effect = lambda cls: {
                "ij.IJ": mock_IJ,
                "ij.plugin.ZProjector": MagicMock(),
                "ij.plugin.ChannelSplitter": MagicMock(),
                "ij.WindowManager": MagicMock(),
            }.get(cls, MagicMock())

            # STEP: call process_image with the single folder and user choices
            # that select file_type=3, nuclei_channel=1, 1 foci channel=2.
            with patch("builtins.input",
                       side_effect=["3", "1", "1", "2"]):
                mod.process_image([str(img_dir)])

        # RESULT: foci_assay/Nuclei and foci_assay/Foci/... directories were
        # created, and image_metadata.txt was written.
        foci_assay = img_dir / "foci_assay"
        assert (foci_assay / "Nuclei").exists()
        assert (foci_assay / "image_metadata.txt").exists()

    def test_raises_for_invalid_file_type_selection(self, tmp_path):
        # PRECONDITION: one input folder, but the user enters 0 (invalid).
        img_dir = self._make_folder_with_tif(tmp_path)

        with patch.object(mod, "imagej"), \
             patch.object(mod, "jimport"), \
             patch("builtins.input", side_effect=["0"]):

            # STEP / RESULT: ValueError because file type 0 is not in [1, 2, 3].
            with pytest.raises(ValueError, match="Invalid file type"):
                mod.process_image([str(img_dir)])
