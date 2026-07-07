"""
Unit tests for 4_foci_quantification.py.

Covers pure helper functions, folder-discovery utilities, and the core
measurement / colocalization logic.  The integration test
(test_process_nuclei_image_with_real_data) uses the actual .tif files that
ship in data/.

Each test is structured in three sections:
  PRECONDITION – state / setup before the call.
  STEP         – the function call being exercised.
  RESULT       – assertions on the outcome.
"""

import os
import sys

import numpy as np
import pytest

# The module was loaded and registered by conftest.py.
mod = sys.modules["foci_quantification"]


# ─────────────────────────────────────────────────────────────────────────────
# extract_image_key
# ─────────────────────────────────────────────────────────────────────────────
class TestExtractImageKey:
    """Tests for the pure function extract_image_key()."""

    @pytest.mark.parametrize("filename,expected", [
        (
            "1_nuclei_projection_StarDist_processed_processed.tif",
            "1",
        ),
        (
            "processed_1_foci_projection.tif",
            "1",
        ),
        (
            "2_nuclei_projection_StarDist_processed_processed.tif",
            "2",
        ),
        (
            "processed_2_foci_projection.tif",
            "2",
        ),
        (
            "sample.tif",
            "sample",
        ),
    ])
    def test_key_extraction(self, filename, expected):
        # PRECONDITION: a filename with known pipeline suffixes / prefixes.
        # STEP: extract the image key.
        result = mod.extract_image_key(filename)
        # RESULT: known suffixes and .tif extension are stripped.
        assert result == expected


# ─────────────────────────────────────────────────────────────────────────────
# extract_metadata
# ─────────────────────────────────────────────────────────────────────────────
class TestExtractMetadata:
    """Tests for extract_metadata()."""

    def test_parses_real_metadata_file(self, data_dir):
        # PRECONDITION: data/foci_assay/image_metadata.txt exists with
        # two image entries (1.nd2 and 2.nd2).
        meta_path = os.path.join(data_dir, "foci_assay", "image_metadata.txt")

        # STEP: extract metadata.
        result = mod.extract_metadata(meta_path)

        # RESULT: dict has keys "1" and "2" with full calibration info.
        assert "1" in result and "2" in result
        for key in ("Pixel Width", "Pixel Height", "Pixel Depth", "Unit"):
            assert key in result["1"]
            assert key in result["2"]

    def test_pixel_width_matches_expected_value(self, data_dir):
        # PRECONDITION: same real metadata file with known pixel width.
        meta_path = os.path.join(data_dir, "foci_assay", "image_metadata.txt")

        # STEP: extract metadata.
        result = mod.extract_metadata(meta_path)

        # RESULT: pixel width is the value written by the pipeline.
        assert result["1"]["Pixel Width"] == pytest.approx(0.2071601898007465)

    def test_returns_empty_dict_when_file_absent(self, tmp_path):
        # PRECONDITION: the metadata file does not exist.
        missing = str(tmp_path / "no_meta.txt")

        # STEP: attempt to extract metadata.
        result = mod.extract_metadata(missing)

        # RESULT: empty dict, no exception.
        assert result == {}

    def test_parses_inline_metadata(self, tmp_path):
        # PRECONDITION: a minimal metadata file with one image entry.
        meta_path = tmp_path / "meta.txt"
        meta_path.write_text(
            "Image Name: test_img.nd2\n"
            "Pixel Width: 0.300\n"
            "Pixel Height: 0.300\n"
            "Pixel Depth: 1.000\n"
            "Unit: micron\n"
        )

        # STEP: extract metadata.
        result = mod.extract_metadata(str(meta_path))

        # RESULT: key is the base name without extension; values match.
        assert "test_img" in result
        assert result["test_img"]["Pixel Width"] == pytest.approx(0.3)
        assert result["test_img"]["Unit"] == "micron"


# ─────────────────────────────────────────────────────────────────────────────
# get_nuclei_mask_folder
# ─────────────────────────────────────────────────────────────────────────────
class TestGetNucleiMaskFolder:
    """Tests for get_nuclei_mask_folder()."""

    def test_returns_final_nuclei_mask_folder_for_data_dir(self, data_dir):
        # PRECONDITION: data/foci_assay/Final_Nuclei_Mask_20260705_190551/
        # exists.
        foci_assay = os.path.join(data_dir, "foci_assay")

        # STEP: find the nuclei mask folder.
        result = mod.get_nuclei_mask_folder(foci_assay)

        # RESULT: the returned path ends with the expected folder name.
        assert os.path.isdir(result)
        assert "Final_Nuclei_Mask_" in os.path.basename(result)

    def test_returns_latest_when_multiple_folders_exist(self, tmp_path):
        # PRECONDITION: two Final_Nuclei_Mask_ folders with different timestamps.
        foci_assay = tmp_path / "foci_assay"
        foci_assay.mkdir()
        (foci_assay / "Final_Nuclei_Mask_20230101_100000").mkdir()
        (foci_assay / "Final_Nuclei_Mask_20251231_235959").mkdir()

        # STEP: find the latest nuclei mask folder.
        result = mod.get_nuclei_mask_folder(str(foci_assay))

        # RESULT: the folder with the later timestamp is returned.
        assert "20251231_235959" in result

    def test_raises_when_no_matching_folder_exists(self, tmp_path):
        # PRECONDITION: foci_assay/ exists but has no Final_Nuclei_Mask_ dir.
        foci_assay = tmp_path / "foci_assay"
        foci_assay.mkdir()

        # STEP: attempt to find the folder.
        # RESULT: FileNotFoundError is raised.
        with pytest.raises(FileNotFoundError, match="Final_Nuclei_Mask_"):
            mod.get_nuclei_mask_folder(str(foci_assay))


# ─────────────────────────────────────────────────────────────────────────────
# get_latest_foci_folders
# ─────────────────────────────────────────────────────────────────────────────
class TestGetLatestFociFolders:
    """Tests for get_latest_foci_folders()."""

    def test_returns_channel_dict_for_data_dir(self, data_dir):
        # PRECONDITION: data/foci_assay/Foci_Masks/Foci_1_Channel_1_*/
        # folder exists.
        foci_assay = os.path.join(data_dir, "foci_assay")

        # STEP: get the latest foci folders by channel.
        result = mod.get_latest_foci_folders(foci_assay)

        # RESULT: the dict maps channel name to an existing directory path.
        assert isinstance(result, dict)
        assert len(result) >= 1
        for channel, path in result.items():
            assert "Foci_" in channel
            assert os.path.isdir(path)

    def test_returns_only_latest_when_multiple_timestamps_for_same_channel(
        self, tmp_path
    ):
        # PRECONDITION: Foci_Masks/ has two Foci_1_Channel_1_ entries with
        # different timestamps.
        foci_masks = tmp_path / "foci_assay" / "Foci_Masks"
        foci_masks.mkdir(parents=True)
        (foci_masks / "Foci_1_Channel_1_20230101_100000").mkdir()
        (foci_masks / "Foci_1_Channel_1_20251231_235959").mkdir()

        # STEP: get the latest foci folders.
        result = mod.get_latest_foci_folders(str(tmp_path / "foci_assay"))

        # RESULT: only the later timestamp is in the returned path.
        assert "20251231_235959" in result["Foci_1_Channel_1"]

    def test_raises_when_foci_masks_folder_absent(self, tmp_path):
        # PRECONDITION: foci_assay/ has no Foci_Masks/ subdirectory.
        foci_assay = tmp_path / "foci_assay"
        foci_assay.mkdir()

        # STEP: attempt to find foci folders.
        # RESULT: FileNotFoundError is raised.
        with pytest.raises(FileNotFoundError, match="Foci_Masks"):
            mod.get_latest_foci_folders(str(foci_assay))


# ─────────────────────────────────────────────────────────────────────────────
# count_foci_in_nuclei
# ─────────────────────────────────────────────────────────────────────────────
class TestCountFociInNuclei:
    """Tests for count_foci_in_nuclei()."""

    def _make_masks(self):
        """
        Return a pair (nuclei_mask, foci_mask) of 8×8 arrays.

        nuclei_mask  – label 1 at [1:4, 1:4], label 2 at [4:7, 4:7].
        foci_mask    – one foci pixel inside nucleus 1 (at [2,2])
                       and one inside nucleus 2 (at [5,5]).
        """
        nuclei = np.zeros((8, 8), dtype=np.uint16)
        nuclei[1:4, 1:4] = 1
        nuclei[4:7, 4:7] = 2

        foci = np.zeros((8, 8), dtype=np.uint8)
        foci[2, 2] = 255   # inside nucleus 1
        foci[5, 5] = 255   # inside nucleus 2
        return nuclei, foci

    def test_counts_one_foci_per_nucleus(self):
        # PRECONDITION: two non-overlapping nuclei; each contains one foci
        # pixel as a single connected component.
        nuclei, foci = self._make_masks()

        # STEP: count foci in each nucleus.
        results = mod.count_foci_in_nuclei(nuclei, foci, pixel_area=1.0,
                                           image_key="test")

        # RESULT: two entries returned, each with Foci Count = 1.
        assert len(results) == 2
        counts = {r["Nucleus"]: r["Foci Count"] for r in results}
        assert counts[1] == 1
        assert counts[2] == 1

    def test_returns_zero_foci_count_when_nucleus_is_empty(self):
        # PRECONDITION: one nucleus with no foci.
        nuclei = np.zeros((8, 8), dtype=np.uint16)
        nuclei[1:5, 1:5] = 1
        foci = np.zeros((8, 8), dtype=np.uint8)

        # STEP: count foci.
        results = mod.count_foci_in_nuclei(nuclei, foci, pixel_area=1.0,
                                           image_key="test")

        # RESULT: one entry with Foci Count = 0.
        assert len(results) == 1
        assert results[0]["Foci Count"] == 0

    def test_nucleus_area_is_calculated_correctly(self):
        # PRECONDITION: one 3×3 nucleus (9 pixels), pixel_area = 2.0 µm².
        nuclei = np.zeros((8, 8), dtype=np.uint16)
        nuclei[1:4, 1:4] = 1  # 9 pixels
        foci = np.zeros((8, 8), dtype=np.uint8)

        # STEP: count foci with known pixel_area.
        results = mod.count_foci_in_nuclei(nuclei, foci, pixel_area=2.0,
                                           image_key="test")

        # RESULT: Nucleus Area (micron²) = 9 * 2.0 = 18.
        assert results[0]["Nucleus Area (pixels)"] == 9
        assert results[0]["Nucleus Area (micron²)"] == pytest.approx(18.0)

    def test_returns_default_entry_when_nuclei_mask_is_empty(self):
        # PRECONDITION: nuclei mask is all zeros (no nuclei detected).
        nuclei = np.zeros((8, 8), dtype=np.uint16)
        foci = np.zeros((8, 8), dtype=np.uint8)

        # STEP: count foci.
        results = mod.count_foci_in_nuclei(nuclei, foci, pixel_area=1.0,
                                           image_key="test")

        # RESULT: a default entry with Nucleus=0, Foci Count=0 is returned.
        assert len(results) == 1
        assert results[0]["Nucleus"] == 0
        assert results[0]["Foci Count"] == 0

    def test_raises_when_mask_shapes_differ(self):
        # PRECONDITION: nuclei and foci masks have different shapes.
        nuclei = np.zeros((8, 8), dtype=np.uint16)
        foci = np.zeros((6, 6), dtype=np.uint8)

        # STEP / RESULT: ValueError about shape mismatch.
        with pytest.raises(ValueError, match="same shape"):
            mod.count_foci_in_nuclei(nuclei, foci, pixel_area=1.0,
                                     image_key="test")


# ─────────────────────────────────────────────────────────────────────────────
# build_intersection_mask
# ─────────────────────────────────────────────────────────────────────────────
class TestBuildIntersectionMask:
    """Tests for build_intersection_mask()."""

    def test_intersection_of_two_partially_overlapping_masks(self):
        # PRECONDITION: mask1 covers [1:5, 1:5] (label 1);
        # mask2 covers [3:7, 3:7] (label 1); overlap is [3:5, 3:5].
        mask1 = np.zeros((8, 8), dtype=np.uint16)
        mask1[1:5, 1:5] = 1
        mask2 = np.zeros((8, 8), dtype=np.uint16)
        mask2[3:7, 3:7] = 1

        # STEP: build the intersection mask.
        result = mod.build_intersection_mask(mask1, mask2)

        # RESULT: pixels in the overlapping region are non-zero;
        # pixels outside are zero.
        assert np.any(result[3:5, 3:5] > 0), "Overlap region should be labeled"
        assert not np.any(result[1:3, 1:3] > 0), \
            "Non-overlapping part of mask1 should be zero"
        assert not np.any(result[5:7, 5:7] > 0), \
            "Non-overlapping part of mask2 should be zero"

    def test_non_overlapping_masks_produce_all_zero_result(self):
        # PRECONDITION: mask1 and mask2 have no overlapping non-zero pixels.
        mask1 = np.zeros((8, 8), dtype=np.uint16)
        mask1[0:3, 0:3] = 1
        mask2 = np.zeros((8, 8), dtype=np.uint16)
        mask2[5:8, 5:8] = 1

        # STEP: build intersection.
        result = mod.build_intersection_mask(mask1, mask2)

        # RESULT: all pixels are zero because there is no overlap.
        assert not np.any(result > 0)

    def test_raises_for_single_mask_argument(self):
        # PRECONDITION: only one mask is passed (function requires ≥ 2).
        mask = np.zeros((8, 8), dtype=np.uint16)
        mask[1:4, 1:4] = 1

        # STEP / RESULT: ValueError because at least 2 masks are required.
        with pytest.raises(ValueError, match="at least 2"):
            mod.build_intersection_mask(mask)

    def test_raises_for_different_shape_masks(self):
        # PRECONDITION: two masks with incompatible shapes.
        mask1 = np.zeros((8, 8), dtype=np.uint16)
        mask2 = np.zeros((6, 6), dtype=np.uint16)

        # STEP / RESULT: ValueError about shape mismatch.
        with pytest.raises(ValueError, match="same shape"):
            mod.build_intersection_mask(mask1, mask2)


# ─────────────────────────────────────────────────────────────────────────────
# validate_folders
# ─────────────────────────────────────────────────────────────────────────────
class TestValidateFolders:
    """Tests for validate_folders() in 4_foci_quantification.py."""

    def test_returns_dict_with_foci_assay_path_for_data_dir(
        self, test_input_json, data_dir
    ):
        # PRECONDITION: data/ contains foci_assay/; test_input_json points there.
        # STEP: validate the folder from the JSON.
        result = mod.validate_folders(test_input_json)

        # RESULT: data_dir is a key; value has 'foci_assay_folder'.
        assert data_dir in result
        assert "foci_assay_folder" in result[data_dir]
        expected = os.path.join(data_dir, "foci_assay")
        assert os.path.normpath(result[data_dir]["foci_assay_folder"]) == \
               os.path.normpath(expected)

    def test_excludes_folder_without_foci_assay_subfolder(self, tmp_path):
        # PRECONDITION: base folder exists but has no foci_assay/ inside it.
        base = tmp_path / "empty_project"
        base.mkdir()
        json_file = tmp_path / "input.json"
        json_file.write_text(
            __import__("json").dumps({"paths_to_files": [str(base)]})
        )

        # STEP: validate.
        result = mod.validate_folders(str(json_file))

        # RESULT: the folder is excluded from the result dict.
        assert str(base) not in result


# ─────────────────────────────────────────────────────────────────────────────
# process_nuclei_image  (integration test with real data)
# ─────────────────────────────────────────────────────────────────────────────
class TestProcessNucleiImage:
    """Integration tests for process_nuclei_image() using real data files."""

    def test_returns_results_list_for_valid_nuclei_and_foci(
        self, data_dir, tmp_path
    ):
        # PRECONDITION: real nuclei mask file and foci mask file exist in data/;
        # metadata contains calibration for key "1".
        foci_assay = os.path.join(data_dir, "foci_assay")
        nuclei_file = os.path.join(
            foci_assay,
            "Final_Nuclei_Mask_20260705_190551",
            "1_nuclei_projection_StarDist_processed_processed.tif",
        )
        foci_file = os.path.join(
            foci_assay,
            "Foci_Masks",
            "Foci_1_Channel_1_20260705_190921",
            "processed_1_foci_projection.tif",
        )
        metadata = mod.extract_metadata(
            os.path.join(foci_assay, "image_metadata.txt")
        )
        results_folder = str(tmp_path / "results")
        os.makedirs(results_folder)

        # STEP: process one nucleus image with one foci channel.
        results = mod.process_nuclei_image(
            nuc_file_path=nuclei_file,
            foci_channels_info={"Foci_1_Channel_1": foci_file},
            metadata=metadata,
            results_folder=results_folder,
            perform_colocalization=False,
        )

        # RESULT: a non-empty list of dicts; each entry has the mandatory
        # columns produced by count_foci_in_nuclei.
        assert isinstance(results, list)
        assert len(results) > 0
        required_cols = {
            "Image Key",
            "Nucleus",
            "Foci Count (Foci_1_Channel_1)",
            "Nucleus Area (pixels)",
            "Nucleus Area (micron²)",
        }
        actual_cols = set(results[0].keys())
        assert required_cols.issubset(actual_cols), \
            f"Missing columns: {required_cols - actual_cols}"

    def test_image_key_matches_filename_without_suffixes(
        self, data_dir, tmp_path
    ):
        # PRECONDITION: nuclei file base name strips to "1" via extract_image_key.
        foci_assay = os.path.join(data_dir, "foci_assay")
        nuclei_file = os.path.join(
            foci_assay,
            "Final_Nuclei_Mask_20260705_190551",
            "1_nuclei_projection_StarDist_processed_processed.tif",
        )
        foci_file = os.path.join(
            foci_assay,
            "Foci_Masks",
            "Foci_1_Channel_1_20260705_190921",
            "processed_1_foci_projection.tif",
        )
        metadata = mod.extract_metadata(
            os.path.join(foci_assay, "image_metadata.txt")
        )
        results_folder = str(tmp_path / "results_key")
        os.makedirs(results_folder)

        # STEP: process the image.
        results = mod.process_nuclei_image(
            nuclei_file,
            {"Foci_1_Channel_1": foci_file},
            metadata,
            results_folder,
            perform_colocalization=False,
        )

        # RESULT: all Image Key values are "1".
        image_keys = {r["Image Key"] for r in results}
        assert image_keys == {"1"}

    def test_returns_empty_list_when_no_metadata_for_image(
        self, data_dir, tmp_path
    ):
        # PRECONDITION: nuclei file exists, but metadata dict is empty,
        # so no calibration data can be looked up.
        foci_assay = os.path.join(data_dir, "foci_assay")
        nuclei_file = os.path.join(
            foci_assay,
            "Final_Nuclei_Mask_20260705_190551",
            "1_nuclei_projection_StarDist_processed_processed.tif",
        )
        foci_file = os.path.join(
            foci_assay,
            "Foci_Masks",
            "Foci_1_Channel_1_20260705_190921",
            "processed_1_foci_projection.tif",
        )
        results_folder = str(tmp_path / "results_no_meta")
        os.makedirs(results_folder)

        # STEP: process with an empty metadata dict.
        results = mod.process_nuclei_image(
            nuclei_file,
            {"Foci_1_Channel_1": foci_file},
            metadata={},          # no calibration data
            results_folder=results_folder,
            perform_colocalization=False,
        )

        # RESULT: empty list because the image key "1" is absent from metadata.
        assert results == []

    def test_colocalization_adds_intersection_columns(
        self, data_dir, tmp_path
    ):
        # PRECONDITION: both a nuclei file and two foci-channel files exist
        # (here we use the same foci file for both channels to keep the test
        # self-contained); colocalization is requested.
        foci_assay = os.path.join(data_dir, "foci_assay")
        nuclei_file = os.path.join(
            foci_assay,
            "Final_Nuclei_Mask_20260705_190551",
            "1_nuclei_projection_StarDist_processed_processed.tif",
        )
        foci_file = os.path.join(
            foci_assay,
            "Foci_Masks",
            "Foci_1_Channel_1_20260705_190921",
            "processed_1_foci_projection.tif",
        )
        metadata = mod.extract_metadata(
            os.path.join(foci_assay, "image_metadata.txt")
        )
        results_folder = str(tmp_path / "results_coloc")
        os.makedirs(results_folder)

        # STEP: process with perform_colocalization=True and two channels.
        results = mod.process_nuclei_image(
            nuclei_file,
            {
                "Foci_1_Channel_1": foci_file,
                "Foci_1_Channel_2": foci_file,   # reuse same file
            },
            metadata,
            results_folder,
            perform_colocalization=True,
        )

        # RESULT: the results contain columns for the intersection subset
        # "Foci_1_Channel_1+Foci_1_Channel_2".
        assert len(results) > 0
        intersection_key = "Foci Count (Foci_1_Channel_1+Foci_1_Channel_2)"
        assert intersection_key in results[0], \
            f"Expected colocalization column '{intersection_key}' in result"
