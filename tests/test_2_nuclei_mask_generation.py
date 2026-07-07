"""
Unit tests for 2_nuclei_mask_generation.py.

Covers:
  - validate_folders()   – checks for foci_assay/Nuclei/ subdirectory
  - find_nuclei()        – StarDist2D inference (StarDist is mocked)
  - process_nuclei()     – ImageJ-based processing (ImageJ is mocked)
  - initialize_imagej()  – success and failure paths

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

import numpy as np
import pytest
from skimage.io import imsave as sk_imsave

# The module was loaded and registered by conftest.py.
mod = sys.modules["nuclei_mask_generation"]


# ─────────────────────────────────────────────────────────────────────────────
# validate_folders
# ─────────────────────────────────────────────────────────────────────────────
class TestValidateFolders:
    """Tests for validate_folders() in 2_nuclei_mask_generation.py."""

    def test_returns_nuclei_folder_for_valid_data_directory(
        self, test_input_json, data_dir
    ):
        # PRECONDITION: data/ folder has foci_assay/Nuclei/ with .tif images;
        # test_input_json points to data/.
        expected_nuclei = os.path.join(data_dir, "foci_assay", "Nuclei")

        # STEP: validate the folder described by the JSON file.
        result = mod.validate_folders(test_input_json)

        # RESULT: the list contains the path to foci_assay/Nuclei/.
        assert any(os.path.normpath(p) == os.path.normpath(expected_nuclei)
                   for p in result)

    def test_excludes_folder_without_nuclei_subfolder(self, tmp_path):
        # PRECONDITION: a folder has foci_assay/ but NOT foci_assay/Nuclei/.
        base = tmp_path / "project"
        (base / "foci_assay").mkdir(parents=True)
        json_file = tmp_path / "input.json"
        json_file.write_text(json.dumps({"paths_to_files": [str(base)]}))

        # STEP: validate the folder.
        result = mod.validate_folders(str(json_file))

        # RESULT: no nuclei folder is returned because Nuclei/ is absent.
        assert result == []

    def test_reports_file_types_found_in_nuclei_folder(
        self, test_input_json, data_dir, capsys
    ):
        # PRECONDITION: data/foci_assay/Nuclei/ contains .tif files.
        # STEP: validate folders and capture stdout.
        mod.validate_folders(test_input_json)
        captured = capsys.readouterr()

        # RESULT: the printed message mentions .tif and the Nuclei path.
        assert ".tif" in captured.out
        assert "Nuclei" in captured.out


# ─────────────────────────────────────────────────────────────────────────────
# find_nuclei
# ─────────────────────────────────────────────────────────────────────────────
class TestFindNuclei:
    """Tests for find_nuclei() in 2_nuclei_mask_generation.py."""

    def _make_nuclei_folder(self, tmp_path: Path, dtype=np.uint8) -> Path:
        """Create a temporary Nuclei folder with one synthetic .tif image."""
        folder = tmp_path / "Nuclei"
        folder.mkdir()
        img = np.zeros((32, 32), dtype=dtype)
        img[8:16, 8:16] = 200 if dtype == np.uint8 else 1
        sk_imsave(str(folder / "cell_nuclei_projection.tif"), img)
        return folder

    def test_creates_output_folder_with_processed_tif(self, tmp_path):
        # PRECONDITION: a Nuclei folder contains one uint8 .tif image.
        # StarDist2D is mocked to return a labeled mask.
        nuclei_folder = self._make_nuclei_folder(tmp_path)
        labels = np.zeros((32, 32), dtype=np.uint16)
        labels[8:16, 8:16] = 1

        mock_model = MagicMock()
        mock_model.predict_instances.return_value = (labels, {})

        with patch.object(mod, "StarDist2D") as mock_sd, \
             patch.object(mod, "normalize", side_effect=lambda x: x):
            mock_sd.from_pretrained.return_value = mock_model

            # STEP: run find_nuclei on the synthetic folder.
            result = mod.find_nuclei([str(nuclei_folder)])

        # RESULT: one output folder is returned and it contains a .tif file.
        assert len(result) == 1
        output_tifs = list(Path(result[0]).glob("*.tif"))
        assert len(output_tifs) == 1, "Expected exactly one processed .tif"

    def test_output_file_is_saved_as_uint16(self, tmp_path):
        # PRECONDITION: same setup as above.
        nuclei_folder = self._make_nuclei_folder(tmp_path)
        labels = np.zeros((32, 32), dtype=np.uint16)
        labels[8:16, 8:16] = 1

        mock_model = MagicMock()
        mock_model.predict_instances.return_value = (labels, {})

        with patch.object(mod, "StarDist2D") as mock_sd, \
             patch.object(mod, "normalize", side_effect=lambda x: x):
            mock_sd.from_pretrained.return_value = mock_model
            result = mod.find_nuclei([str(nuclei_folder)])

        # STEP: read the saved file.
        from skimage.io import imread as sk_imread
        saved = sk_imread(str(list(Path(result[0]).glob("*.tif"))[0]))

        # RESULT: the saved mask is uint16.
        assert saved.dtype == np.uint16

    def test_skips_non_uint8_images_and_logs_error(self, tmp_path, caplog):
        # PRECONDITION: the Nuclei folder contains a uint16 image (not uint8).
        nuclei_folder = self._make_nuclei_folder(tmp_path, dtype=np.uint16)

        mock_model = MagicMock()

        import logging
        with patch.object(mod, "StarDist2D") as mock_sd, \
             patch.object(mod, "normalize", side_effect=lambda x: x), \
             caplog.at_level(logging.ERROR):
            mock_sd.from_pretrained.return_value = mock_model

            # STEP: run find_nuclei.
            result = mod.find_nuclei([str(nuclei_folder)])

        # RESULT: output folder exists but is empty (file was skipped).
        assert len(result) == 1
        output_tifs = list(Path(result[0]).glob("*.tif"))
        assert output_tifs == []
        assert mock_model.predict_instances.call_count == 0

    def test_skips_folder_with_no_tif_files(self, tmp_path, caplog):
        # PRECONDITION: the Nuclei folder is empty (no .tif files).
        nuclei_folder = tmp_path / "Nuclei"
        nuclei_folder.mkdir()

        mock_model = MagicMock()
        import logging
        with patch.object(mod, "StarDist2D") as mock_sd, \
             caplog.at_level(logging.ERROR):
            mock_sd.from_pretrained.return_value = mock_model

            # STEP: run find_nuclei.
            result = mod.find_nuclei([str(nuclei_folder)])

        # RESULT: output folder created but no images processed.
        assert len(result) == 1
        assert mock_model.predict_instances.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# process_nuclei
# ─────────────────────────────────────────────────────────────────────────────
class TestProcessNuclei:
    """Tests for process_nuclei() in 2_nuclei_mask_generation.py."""

    def test_processes_tif_files_and_calls_imagej_pipeline(self, tmp_path):
        # PRECONDITION: an input folder with one .tif stub; IJ is fully mocked.
        input_dir = tmp_path / "StarDist_output"
        input_dir.mkdir()
        (input_dir / "cell_StarDist_processed.tif").touch()

        # Mock the ImageJ objects that will be returned by jimport().
        mock_IJ = MagicMock()
        mock_win = MagicMock()
        fake_imp = MagicMock()
        fake_mask = MagicMock()

        mock_IJ.openImage.return_value = fake_imp
        mock_win.getImage.return_value = fake_mask
        mock_win.getCurrentImage.return_value = fake_mask

        with patch.object(mod, "imagej") as mock_imagej, \
             patch.object(mod, "jimport") as mock_jimport:
            mock_imagej.init.return_value = MagicMock()
            mock_jimport.side_effect = lambda cls: {
                "ij.IJ": mock_IJ,
                "ij.WindowManager": mock_win,
            }.get(cls, MagicMock())

            # STEP: run process_nuclei with a particle_size of 100.
            mod.process_nuclei([str(input_dir)], particle_size=100)

        # RESULT: IJ.openImage was called once for the .tif file;
        # IJ.saveAs was called to persist the mask.
        assert mock_IJ.openImage.call_count == 1
        assert mock_IJ.saveAs.call_count == 1

    def test_skips_non_tif_files_silently(self, tmp_path):
        # PRECONDITION: input folder contains only a .png file (unsupported).
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "image.png").touch()

        mock_IJ = MagicMock()

        with patch.object(mod, "imagej") as mock_imagej, \
             patch.object(mod, "jimport") as mock_jimport:
            mock_imagej.init.return_value = MagicMock()
            mock_jimport.return_value = mock_IJ

            # STEP: run process_nuclei.
            mod.process_nuclei([str(input_dir)], particle_size=100)

        # RESULT: openImage is never called because .png is skipped.
        assert mock_IJ.openImage.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# initialize_imagej
# ─────────────────────────────────────────────────────────────────────────────
class TestInitializeImageJ:
    """Tests for initialize_imagej() in 2_nuclei_mask_generation.py."""

    def test_returns_ij_on_success(self):
        # PRECONDITION: imagej.init is mocked to return a fake IJ object.
        fake_ij = MagicMock()
        fake_ij.getVersion.return_value = "2.14.0"
        with patch.object(mod, "imagej") as mock_imagej:
            mock_imagej.init.return_value = fake_ij

            # STEP: call initialize_imagej().
            result = mod.initialize_imagej()

        # RESULT: the returned value is the fake IJ instance.
        assert result is fake_ij

    def test_raises_custom_error_on_failure(self):
        # PRECONDITION: imagej.init raises an exception.
        with patch.object(mod, "imagej") as mock_imagej:
            mock_imagej.init.side_effect = OSError("No JVM")

            # STEP: call initialize_imagej().
            # RESULT: ImageJInitializationError wraps the original error.
            with pytest.raises(mod.ImageJInitializationError,
                               match="Failed to initialize ImageJ"):
                mod.initialize_imagej()
