"""Exercise folder journals with real TIFF writes and a stubbed ImageJ runtime."""

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
import tifffile
from channel_run_log import ChannelRunLog

mod = sys.modules["select_channels"]


@pytest.fixture
def runtime(monkeypatch):
    image = MagicMock()
    image.getDimensions.return_value = (17, 10, 3, 4, 1)
    cal = image.getCalibration.return_value
    cal.copy.return_value = cal
    cal.pixelWidth = cal.pixelHeight = 0.2
    cal.pixelDepth = 0.5
    cal.getZUnit.return_value = "um"
    ij = MagicMock()
    ij.openImage.return_value = image
    ij.saveAs.side_effect = lambda imp, fmt, path: tifffile.imwrite(
        path, np.zeros((10, 17), dtype=np.uint8), imagej=True)
    splitter = MagicMock()
    splitter.split.return_value = [MagicMock(), MagicMock(), MagicMock()]
    projector = MagicMock()
    imports = {"ij.IJ": ij, "ij.plugin.ChannelSplitter": splitter,
               "ij.plugin.ZProjector": projector}
    monkeypatch.setattr(mod, "jimport", lambda name: imports.get(name, MagicMock()))
    initialized = MagicMock()
    initialized.getVersion.return_value = "test ImageJ"
    monkeypatch.setattr(mod, "initialize_imagej", lambda: initialized)
    monkeypatch.setattr(mod.spatial, "read_source", lambda path: (
        mod.spatial.normalize(0.2, 0.3, "um", "test metadata"), (10, 17)))
    return ij


def source_folder(parent, name="images", filenames=("good.tif",)):
    folder = parent / name
    folder.mkdir()
    for filename in filenames:
        (folder / filename).touch()
    return folder


def run(monkeypatch, folders, choices=("3", "1", "1", "2"), input_json=None):
    answers = iter(choices)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    return mod.process_image([str(folder) for folder in folders], input_json)


def journal(folder):
    return (folder / "foci_assay" / "1_log.log").read_text()


@pytest.mark.parametrize("file_type,filename,processing", [
    ("1", "good.nd2", "nuclei_projection=MAX | foci_projection=SD"),
    ("2", "good.tiff", "nuclei_projection=MAX | foci_projection=SD"),
    ("3", "good.tif", "projection=none (2D channel extraction)"),
])
def test_success_logs_settings_outputs_and_preserves_calibration(
        tmp_path, monkeypatch, runtime, file_type, filename, processing):
    folder = source_folder(tmp_path, filenames=(filename, "._good.tif", ".hidden.nd2", "notes.txt"))
    (folder / "directory.tif").mkdir()
    root_handlers = list(logging.getLogger().handlers)
    manifest = tmp_path / "input.json"
    assert run(monkeypatch, [folder], (file_type, "1", "1", "2"), manifest) == ["SUCCESS"]
    log = journal(folder)
    assert "FINISHED | status=SUCCESS | input_images=1 | attempted=1 | completed=1 | failed=0" in log
    assert "output_files=2" in log
    assert "ignored_hidden_files=2 | ignored_unsupported_files=1" in log
    assert "nuclei_channel=1 | foci_channels=[2]" in log
    assert "pixel_X_um=0.2 | pixel_Y_um=0.3" in log
    assert str(manifest) in log
    assert "W=17 | H=10 | C=3 | Z=4 | T=1" in log
    assert processing in log
    assert "native_XY_preserved=True" in log
    assert "ImageJ=test ImageJ" in log
    assert logging.getLogger().handlers == root_handlers
    assert runtime.openImage.call_count == 1
    nuclei = folder / "foci_assay" / "Nuclei"
    snapshot = json.loads((nuclei / "spatial_calibration.json").read_text())
    record = next(iter(snapshot.values()))
    assert record["Pixel_size_X_um"] == 0.2
    assert record["Shape_YX"] == [10, 17]
    assert mod.spatial.snapshot_calibration(next(nuclei.glob("*.tif")), (10, 17))


def test_partial_and_failed_folders_have_independent_logs(tmp_path, monkeypatch, runtime):
    first = source_folder(tmp_path, "first", ("good.tif", "bad.tif"))
    second = source_folder(tmp_path, "second", ("another_bad.tif",))
    image = runtime.openImage.return_value
    runtime.openImage.side_effect = lambda path: image if Path(path).name == "good.tif" else None
    assert run(monkeypatch, [first, second]) == ["PARTIAL", "FAILED"]
    assert "completed=1 | failed=1" in journal(first)
    assert "another_bad.tif" not in journal(first)
    assert "file=good.tif" not in journal(second)
    assert "completed=0 | failed=1" in journal(second)


def test_previous_log_is_archived_on_rerun(tmp_path, monkeypatch, runtime):
    folder = source_folder(tmp_path)
    run(monkeypatch, [folder])
    original = journal(folder)
    run(monkeypatch, [folder], ("yes", "3", "1", "1", "2"))
    archives = list((folder / "foci_assay" / "logs").glob("1_log_*.log"))
    assert len(archives) == 1
    assert archives[0].read_text() == original
    assert journal(folder) != original
    assert journal(folder).count("STARTED | run_id=") == 1


def test_save_exception_records_traceback_and_counts(tmp_path, monkeypatch, runtime):
    folder = source_folder(tmp_path)
    runtime.saveAs.side_effect = OSError("Disk is full")
    with pytest.raises(OSError, match="Disk is full"):
        run(monkeypatch, [folder])
    log = journal(folder)
    assert "Traceback (most recent call last)" in log
    assert "Disk is full" in log
    assert "status=FAILED" in log
    assert "completed=0 | failed=1" in log
    assert "output_files=0" in log
    assert "IMAGE_COMPLETED" not in log


def test_initialization_failure_is_logged(tmp_path, monkeypatch, runtime):
    folder = source_folder(tmp_path)
    def fail():
        raise mod.ImageJInitializationError("JVM failed")
    monkeypatch.setattr(mod, "initialize_imagej", fail)
    with pytest.raises(mod.ImageJInitializationError):
        run(monkeypatch, [folder])
    log = journal(folder)
    assert "JVM failed" in log
    assert "status=FAILED" in log
    assert "attempted=0 | completed=0 | failed=0 | not_attempted=1" in log


@pytest.mark.parametrize("file_type", ["2", "3"])
def test_missing_channel_is_not_reported_as_success(tmp_path, monkeypatch, runtime, file_type):
    folder = source_folder(tmp_path)
    assert run(monkeypatch, [folder], (file_type, "1", "1", "4")) == ["FAILED"]
    assert "completed=0 | failed=1" in journal(folder)
    assert "output_files=0" in journal(folder)


def test_uncalibrated_success_records_reason(tmp_path, monkeypatch, runtime):
    folder = source_folder(tmp_path)
    monkeypatch.setattr(mod.spatial, "read_source", lambda path: (
        mod.spatial.uncalibrated("No physical pixel size in metadata."), (10, 17)))
    assert run(monkeypatch, [folder]) == ["SUCCESS"]
    log = journal(folder)
    assert "status=uncalibrated | units=pixel | reason=No physical pixel size in metadata." in log
    assert "FINISHED | status=SUCCESS" in log


def test_incomplete_run_does_not_print_success(tmp_path, monkeypatch, runtime, capsys):
    folder = source_folder(tmp_path)
    runtime.openImage.return_value = None
    monkeypatch.setattr(mod, "validate_folders", lambda path: [str(folder)])
    answers = iter(("yes", "3", "1", "1", "2"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    mod.select_channel_name("input.json")
    output = capsys.readouterr().out
    assert "successfully completed" not in output
    assert "incomplete results" in output


def test_no_input_is_not_success(tmp_path, monkeypatch, runtime):
    with pytest.raises(ValueError, match="No supported input images"):
        run(monkeypatch, [])


@pytest.mark.parametrize("exception,status", [(OSError("write error"), "FAILED"), (KeyboardInterrupt(), "CANCELLED")])
def test_handlers_close_after_abort(tmp_path, exception, status):
    folder = source_folder(tmp_path)
    (folder / "foci_assay").mkdir()
    audit = ChannelRunLog(folder, None, "test", "digest")
    with pytest.raises(type(exception)):
        with audit:
            audit.inventory()
            handlers = audit.logger.handlers[:]
            raise exception
    assert audit.status == status
    assert audit.logger.handlers == []
    assert handlers[0].stream is None


def test_bad_saved_dimensions_abort_success(tmp_path, monkeypatch, runtime):
    folder = source_folder(tmp_path)
    runtime.saveAs.side_effect = lambda imp, fmt, path: tifffile.imwrite(
        path, np.zeros((5, 5), dtype=np.uint8))
    with pytest.raises(ValueError, match="Saved image dimensions"):
        run(monkeypatch, [folder])
    assert "status=FAILED" in journal(folder)
    assert "output_files=0" in journal(folder)
