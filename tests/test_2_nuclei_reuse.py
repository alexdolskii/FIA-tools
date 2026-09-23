"""Regression tests for StarDist reuse; inference and ImageJ are mocked."""

import contextlib
import io
import json
import logging
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from skimage.io import imread, imsave


mod = sys.modules["nuclei_mask_generation"]


class TestStarDistReuse(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        handlers = set(logging.getLogger().handlers)

        def close_run_logs():
            for handler in list(logging.getLogger().handlers):
                if handler not in handlers:
                    logging.getLogger().removeHandler(handler)
                    handler.close()

        self.addCleanup(close_run_logs)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)
        self.source = self.make_source("first")

    def make_source(self, name):
        folder = self.root / name / "foci_assay" / "Nuclei"
        folder.mkdir(parents=True)
        image = np.zeros((16, 16), dtype=np.uint8)
        image[4:12, 4:12] = 200
        imsave(str(folder / "cell.v2.tif"), image, check_contrast=False)
        return folder

    def make_run(self, source=None, timestamp="20260923_120000", legacy=False):
        source = source or self.source
        folder = source.parent / f"Nuclei_StarDist_mask_processed_{timestamp}"
        folder.mkdir()
        mask = np.zeros((16, 16), dtype=np.uint16)
        mask[4:12, 4:12] = 1
        filename = "cell.v2_StarDist_processed.tif"
        imsave(str(folder / filename), mask, check_contrast=False)
        if not legacy:
            metadata = {
                "schema_version": 1,
                "status": "complete",
                "settings": dict(mod.STARDIST_SETTINGS),
                "sources": mod.source_fingerprints(source),
                "masks": {filename: mod.file_digest(folder / filename)},
            }
            mod.write_run_metadata(folder, mod.STARDIST_METADATA, metadata)
        return folder

    def inspect(self, folder):
        return mod.inspect_stardist_folder(
            folder, self.source, mod.source_fingerprints(self.source))

    def change_metadata(self, folder, **changes):
        path = folder / mod.STARDIST_METADATA
        metadata = json.loads(path.read_text())
        metadata.update(changes)
        mod.write_run_metadata(folder, mod.STARDIST_METADATA, metadata)

    def test_visible_files_and_masks_ignore_hidden_entries(self):
        run = self.make_run()
        for folder in (self.source, run):
            (folder / "._cell.v2.tif").write_bytes(bytes.fromhex("00051607"))
            (folder / ".hidden.tif").write_bytes(b"not a TIFF")
            (folder / ".DS_Store").write_bytes(b"metadata")
        (self.source / "directory.tif").mkdir()
        (self.source.parent / "._Nuclei_StarDist_mask_processed_20260924_120000").mkdir()
        (self.source.parent / "Nuclei_StarDist_mask_processed_invalid").mkdir()
        candidates = mod.discover_stardist_folders(str(self.source))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["count"], 1)
        self.assertTrue(candidates[0]["usable"])
        self.assertEqual(mod.nuclei_source_files(self.source), ["cell.v2.tif"])

    def test_missing_and_unexpected_masks_are_not_reused(self):
        run = self.make_run(legacy=True)
        (run / "cell.v2_StarDist_processed.tif").rename(run / "wrong.tif")
        result = self.inspect(run)
        self.assertFalse(result["usable"])
        self.assertIn("1 missing, 1 unexpected", result["reason"])

    def test_empty_source_and_output_are_not_reused(self):
        run = self.make_run(legacy=True)
        (run / "cell.v2_StarDist_processed.tif").unlink()
        (self.source / "cell.v2.tif").unlink()
        self.assertFalse(self.inspect(run)["usable"])

    def test_corrupt_legacy_tiff_is_not_reused(self):
        run = self.make_run(legacy=True)
        (run / "cell.v2_StarDist_processed.tif").write_bytes(b"broken TIFF")
        self.assertFalse(self.inspect(run)["usable"])

    def test_legacy_shape_and_dtype_must_match(self):
        run = self.make_run(legacy=True)
        for image in (np.zeros((8, 8), dtype=np.uint16),
                      np.zeros((16, 16), dtype=np.uint8)):
            with self.subTest(shape=image.shape, dtype=image.dtype):
                imsave(str(run / "cell.v2_StarDist_processed.tif"), image,
                       check_contrast=False)
                self.assertFalse(self.inspect(run)["usable"])

    def test_changed_source_with_same_name_is_not_reused(self):
        run = self.make_run()
        image = imread(str(self.source / "cell.v2.tif"))
        image[0, 0] = 1
        imsave(str(self.source / "cell.v2.tif"), image, check_contrast=False)
        self.assertIn("Source images have changed", self.inspect(run)["reason"])

    def test_changed_saved_mask_is_not_reused(self):
        run = self.make_run()
        image = imread(str(run / "cell.v2_StarDist_processed.tif"))
        image[0, 0] = 2
        imsave(str(run / "cell.v2_StarDist_processed.tif"), image,
               check_contrast=False)
        self.assertIn("Saved mask has changed", self.inspect(run)["reason"])

    def test_incomplete_status_and_changed_settings_are_not_reused(self):
        run = self.make_run()
        for changes in ({"status": "running"}, {"status": "incomplete"},
                        {"status": "complete", "settings": {}}):
            with self.subTest(changes=changes):
                self.change_metadata(run, **changes)
                self.assertFalse(self.inspect(run)["usable"])

    def test_malformed_metadata_cannot_fall_back_to_legacy_reuse(self):
        run = self.make_run()
        for text in ("{broken", "[]", "null"):
            with self.subTest(text=text):
                (run / mod.STARDIST_METADATA).write_text(text)
                self.assertFalse(self.inspect(run)["usable"])

    def test_number_selects_older_run(self):
        older = self.make_run(timestamp="20260922_120000")
        self.make_run()
        with patch("builtins.input", return_value="2"):
            selected = mod.select_stardist_sources([str(self.source)])
        self.assertEqual(selected[str(self.source)], str(older))

    def test_invalid_answer_reprompts_and_enter_chooses_newest_valid(self):
        valid = self.make_run(timestamp="20260922_120000")
        invalid = self.make_run()
        self.change_metadata(invalid, status="running")
        with patch("builtins.input", side_effect=["99", "bad", ""]):
            selected = mod.select_stardist_sources([str(self.source)])
        self.assertEqual(selected[str(self.source)], str(valid))

    def test_legacy_reuse_requires_explicit_confirmation(self):
        run = self.make_run(legacy=True)
        with patch("builtins.input", side_effect=["1", "y"]) as prompt:
            selected = mod.select_stardist_sources([str(self.source)])
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(selected[str(self.source)], str(run))

    def test_declined_legacy_confirmation_allows_fresh_run(self):
        self.make_run(legacy=True)
        with patch("builtins.input", side_effect=["1", "", "n"]):
            selected = mod.select_stardist_sources([str(self.source)])
        self.assertIsNone(selected[str(self.source)])

    def test_reuse_all_confirms_legacy_once_and_recomputes_missing_inputs(self):
        first_run = self.make_run(legacy=True)
        second = self.make_source("second")
        second_run = self.make_run(second, legacy=True)
        third = self.make_source("third")
        with patch("builtins.input", side_effect=["a", "y"]) as prompt:
            selected = mod.select_stardist_sources(
                list(map(str, (self.source, second, third))))
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(selected[str(self.source)], str(first_run))
        self.assertEqual(selected[str(second)], str(second_run))
        self.assertIsNone(selected[str(third)])

    def test_cancel_occurs_before_new_stardist_or_imagej_runs(self):
        self.make_run()
        with patch.object(mod, "validate_folders", return_value=[str(self.source)]), \
             patch("builtins.input", return_value="q"), \
             patch.object(mod, "find_nuclei") as find, \
             patch.object(mod, "process_nuclei") as process:
            with self.assertRaisesRegex(ValueError, "canceled"):
                mod.main("unused.json", 2000)
        find.assert_not_called()
        process.assert_not_called()

    def test_reuse_only_never_loads_stardist_model(self):
        run = self.make_run()
        with patch.object(mod, "validate_folders", return_value=[str(self.source)]), \
             patch("builtins.input", return_value=""), \
             patch.object(mod, "StarDist2D") as stardist, \
             patch.object(mod, "process_nuclei") as process:
            mod.main("unused.json", 2000)
        stardist.from_pretrained.assert_not_called()
        process.assert_called_once_with([str(run)], 2000)

    def test_mixed_inputs_only_segment_fresh_folder_and_preserve_order(self):
        run = self.make_run()
        second = self.make_source("second")
        model = MagicMock()
        model.predict_instances.return_value = (
            np.ones((16, 16), dtype=np.uint16), {})
        with patch.object(mod, "validate_folders",
                          return_value=[str(self.source), str(second)]), \
             patch("builtins.input", return_value=""), \
             patch.object(mod, "StarDist2D") as stardist, \
             patch.object(mod, "normalize", side_effect=lambda image: image), \
             patch.object(mod, "process_nuclei") as process:
            stardist.from_pretrained.return_value = model
            mod.main("unused.json", 1500)
        stardist.from_pretrained.assert_called_once_with("2D_versatile_fluo")
        self.assertEqual(model.predict_instances.call_count, 1)
        folders, area = process.call_args.args
        self.assertEqual(folders[0], str(run))
        self.assertEqual(Path(folders[1]).parent, second.parent)
        self.assertEqual(area, 1500)
        self.assertTrue(mod.discover_stardist_folders(str(second))[0]["usable"])

    def test_forced_rerun_preserves_old_masks_and_prediction_parameters(self):
        run = self.make_run()
        original = (run / "cell.v2_StarDist_processed.tif").read_bytes()
        model = MagicMock()
        labels = np.ones((16, 16), dtype=np.uint16)
        model.predict_instances.return_value = (labels, {})
        with patch.object(mod, "validate_folders", return_value=[str(self.source)]), \
             patch("builtins.input", return_value="n"), \
             patch.object(mod, "StarDist2D") as stardist, \
             patch.object(mod, "normalize", side_effect=lambda image: image), \
             patch.object(mod, "process_nuclei") as process:
            stardist.from_pretrained.return_value = model
            mod.main("unused.json", 2000)
        new_folder = Path(process.call_args.args[0][0])
        self.assertNotEqual(new_folder, run)
        self.assertEqual((run / "cell.v2_StarDist_processed.tif").read_bytes(), original)
        self.assertEqual(model.predict_instances.call_args.kwargs,
                         {"nms_thresh": 0.9, "prob_thresh": 0.7})
        np.testing.assert_array_equal(
            imread(str(new_folder / "cell.v2_StarDist_processed.tif")), labels)

    def test_interrupted_prediction_leaves_non_reusable_run(self):
        with patch.object(mod, "StarDist2D") as stardist, \
             patch.object(mod, "normalize", side_effect=lambda image: image):
            stardist.from_pretrained.return_value.predict_instances.side_effect = RuntimeError("stop")
            with self.assertRaisesRegex(RuntimeError, "stop"):
                mod.find_nuclei([str(self.source)])
        run = next(self.source.parent.glob("Nuclei_StarDist_mask_processed_*"))
        metadata = json.loads((run / mod.STARDIST_METADATA).read_text())
        self.assertEqual(metadata["status"], "running")
        self.assertFalse(self.inspect(run)["usable"])

    def test_non_uint8_source_cannot_reach_imagej_as_complete_run(self):
        imsave(str(self.source / "cell.v2.tif"),
               np.ones((16, 16), dtype=np.uint16), check_contrast=False)
        with patch.object(mod, "validate_folders", return_value=[str(self.source)]), \
             patch.object(mod, "StarDist2D"), \
             patch.object(mod, "process_nuclei") as process:
            with self.assertRaisesRegex(ValueError, "Incomplete StarDist"):
                mod.main("unused.json", 2000)
        process.assert_not_called()

    def test_same_second_runs_never_overwrite(self):
        with patch.object(mod, "datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 23, 12, 0, 0)
            first = Path(mod.create_output_folder(self.root, "Final_Nuclei_Mask_"))
            (first / "keep.txt").write_text("previous result")
            second = Path(mod.create_output_folder(self.root, "Final_Nuclei_Mask_"))
        self.assertNotEqual(first, second)
        self.assertEqual((first / "keep.txt").read_text(), "previous result")
        self.assertEqual(second.name, "Final_Nuclei_Mask_20260923_120001")

    def test_imagej_reruns_record_area_and_preserve_inputs_and_earlier_outputs(self):
        run = self.make_run()
        (run / "._cell.v2_StarDist_processed.tif").write_bytes(b"AppleDouble")
        before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
        imagej = MagicMock()
        window = MagicMock()
        imagej.saveAs.side_effect = lambda image, fmt, path: Path(path).write_bytes(b"mask")
        with patch.object(mod, "initialize_imagej"), \
             patch.object(mod, "jimport", side_effect=lambda name: imagej if name == "ij.IJ" else window), \
             patch.object(mod, "NucleiMorphologyExport") as export:
            export.return_value.save.return_value = "complete"
            mod.process_nuclei([str(run)], 2000)
            mod.process_nuclei([str(run)], 1000)
        outputs = sorted(run.parent.glob("Final_Nuclei_Mask_*"))
        self.assertEqual(len(outputs), 2)
        self.assertEqual(imagej.openImage.call_count, 2)
        records = [json.loads((p / "nuclei_run.json").read_text()) for p in outputs]
        self.assertEqual([r["particle_size_pixels_squared"] for r in records], [2000, 1000])
        self.assertTrue(all(r["status"] == "complete" for r in records))
        self.assertTrue(all(r["morphology_status"] == "complete" for r in records))
        self.assertEqual(export.return_value.add_image.call_count, 2)
        self.assertTrue(all(r["stardist_folder"] == str(run.resolve()) for r in records))
        particle_calls = [call for call in imagej.run.call_args_list
                          if len(call.args) > 1 and call.args[1] == "Analyze Particles..."]
        self.assertEqual([call.args[2] for call in particle_calls],
                         ["size=2000-Infinity pixel show=Masks",
                          "size=1000-Infinity pixel show=Masks"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()})

    def test_failed_imagej_open_records_incomplete_output(self):
        run = self.make_run()
        imagej = MagicMock()
        imagej.openImage.return_value = None
        with patch.object(mod, "initialize_imagej"), \
             patch.object(mod, "jimport", return_value=imagej):
            mod.process_nuclei([str(run)], 2000)
        output = next(run.parent.glob("Final_Nuclei_Mask_*"))
        record = json.loads((output / "nuclei_run.json").read_text())
        self.assertEqual(record["status"], "incomplete")
        self.assertEqual(record["skipped_files"], ["cell.v2_StarDist_processed.tif"])

    def test_zero_area_keeps_existing_filter_semantics(self):
        run = self.make_run()
        with patch.object(mod, "validate_folders", return_value=[str(self.source)]), \
             patch("builtins.input", return_value=""), \
             patch.object(mod, "process_nuclei") as process:
            mod.main("unused.json", 0)
        process.assert_called_once_with([str(run)], 0)
