"""Exercise folder journals with real TIFF writes and a stubbed ImageJ runtime."""

import io
import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
import tifffile
from channel_run_log import ChannelRunLog
from terminal_progress import CompactProgress

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


def test_channel_journal_omits_block_percentages_but_keeps_phases_and_diagnostics(tmp_path):
    folder = source_folder(tmp_path, filenames=("image.nd2",))
    (folder / "foci_assay").mkdir()
    stream = io.StringIO()
    with CompactProgress(1, stream) as progress:
        with ChannelRunLog(folder, None, "test", "digest", progress) as audit:
            audit.inventory()
            progress.logger = audit.logger
            audit.start_image("image.nd2")
            progress.phase("Opening source")
            progress.bioformats_event("ND2Reader initializing image.nd2")
            for percent in range(100):
                progress.bioformats_event(f"Parsing block 'ImageDataSeq' {percent}%")
            progress.bioformats_event("Parsing block 'ND2 FILEMAP ' 99%")
            assert (progress.stage, progress.percent) == ("ND2 structure", 99)
            progress.bioformats_event("Parsing block 'ImageDataSeq' 42%: warning", logging.WARNING)
            progress.bioformats_event("Parsing block 'ImageDataSeq' 42%: error", logging.ERROR)
            progress.phase("Creating nuclei MAX projection")
            audit.finish_image()

    log = journal(folder)
    assert "INFO - Bio-Formats: Parsing block" not in log
    assert log.count("PHASE | file=image.nd2 | ND2 structure") == 1
    assert "PHASE | file=image.nd2 | Opening source" in log
    assert "PHASE | file=image.nd2 | Creating nuclei MAX projection" in log
    assert "ND2Reader initializing image.nd2" in log
    for level, message in (("WARNING", "warning"), ("ERROR", "error")):
        assert f"{level} - Bio-Formats: Parsing block 'ImageDataSeq' 42%: {message}" in log
        assert stream.getvalue().count(f"42%: {message}") == 1
    assert "IMAGE_COMPLETED | file=image.nd2 | elapsed_s=" in log
    assert "STARTED | run_id=test" in log and "FINISHED | status=SUCCESS" in log


@pytest.mark.parametrize("file_type,filename,processing", [
    ("1", "good.nd2", "nuclei_projection=MAX | marker_projection=SD"),
    ("2", "good.tiff", "nuclei_projection=MAX | marker_projection=SD"),
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
    assert "nuclei_channel=1 | marker_channels=[2]" in log
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


def existing_results(folder):
    output = folder / "foci_assay"
    output.mkdir()
    (output / "1_log.log").write_text("previous log")
    (output / "image_metadata.txt").write_text("previous metadata")
    return {path: path.read_bytes() for path in output.iterdir()}


def test_all_overwrite_confirmations_precede_initialization_and_writes(tmp_path, monkeypatch, runtime):
    first = source_folder(tmp_path, "first")
    second = source_folder(tmp_path, "second")
    previous = {**existing_results(first), **existing_results(second)}
    events = []
    initialize = mod.initialize_imagej

    def start_imagej():
        assert events == [str(first / "foci_assay"), str(second / "foci_assay")]
        events.append("ImageJ")
        return initialize()

    choices = iter(("3", "1", "1", "2"))

    def answer(prompt):
        if "overwrite" in prompt:
            assert "ImageJ" not in events
            assert all(path.read_bytes() == content for path, content in previous.items())
            assert all(not list(folder.glob("foci_assay/logs/*")) for folder in (first, second))
            target = first if str(first) in prompt else second
            events.append(str(target / "foci_assay"))
            return "yes"
        assert events[-1] == "ImageJ"
        return next(choices)

    # Both confirmations must occur before the first ImageJ call or log rotation.
    monkeypatch.setattr(mod, "initialize_imagej", start_imagej)
    monkeypatch.setattr("builtins.input", answer)
    assert mod.process_image([str(first), str(second)]) == ["SUCCESS", "SUCCESS"]
    assert events == [str(first / "foci_assay"), str(second / "foci_assay"), "ImageJ"]


@pytest.mark.parametrize("first_exists", [False, True])
def test_canceling_later_folder_preserves_the_whole_batch(tmp_path, monkeypatch, runtime, first_exists):
    first = source_folder(tmp_path, "first")
    second = source_folder(tmp_path, "second")
    previous = existing_results(second)
    if first_exists:
        previous.update(existing_results(first))
    initializer = MagicMock()
    monkeypatch.setattr(mod, "initialize_imagej", initializer)
    choices = ("yes", "q") if first_exists else ("q",)
    assert run(monkeypatch, [first, second], choices) == []
    initializer.assert_not_called()
    runtime.openImage.assert_not_called()
    assert all(path.read_bytes() == content for path, content in previous.items())
    for folder in (first, second):
        assert not (folder / "foci_assay" / "logs").exists()
    if not first_exists:
        assert not (first / "foci_assay").exists()


def test_invalid_confirmation_does_not_allow_overwrite(tmp_path, monkeypatch, runtime, capsys):
    folder = source_folder(tmp_path)
    previous = existing_results(folder)
    assert run(monkeypatch, [folder], ("maybe", "", "no")) == []
    assert capsys.readouterr().out.count("Please enter yes, no, or q.") == 2
    runtime.openImage.assert_not_called()
    assert all(path.read_bytes() == content for path, content in previous.items())


@pytest.mark.parametrize("choices,selected_index", [(("no", "yes"), 1), (("yes", "n"), 0)])
def test_independent_folder_choices_preserve_skips_and_count_selected_images(
        tmp_path, monkeypatch, runtime, capsys, choices, selected_index):
    first = source_folder(tmp_path, "first", ("first.tif", "extra.tif", "._first.tif"))
    second = source_folder(tmp_path, "second", ("second.tif", ".hidden.tif"))
    fresh = source_folder(tmp_path, "fresh", ("new.tif",))
    previous = {**existing_results(first), **existing_results(second)}
    folders = [first, second, fresh]
    skipped = folders[1 - selected_index]
    skipped_paths = set(skipped.rglob('*'))
    selected = [folders[selected_index], fresh]
    expected_sources = {p for folder in selected for p in folder.glob('*.tif') if not p.name.startswith('.')}
    initialize = mod.initialize_imagej
    prompts = []
    answers = iter((*choices, "3", "1", "1", "2"))

    def answer(prompt):
        prompts.append(prompt)
        if 'overwrite' in prompt:
            assert all(path.read_bytes() == content for path, content in previous.items())
        return next(answers)

    def start_imagej():
        assert len(prompts) == 2
        assert str(first) in prompts[0] and str(second) in prompts[1]
        assert all(path.read_bytes() == content for path, content in previous.items() if skipped in path.parents)
        return initialize()

    monkeypatch.setattr('builtins.input', answer)
    monkeypatch.setattr(mod, 'initialize_imagej', start_imagej)
    assert mod.process_image([str(folder) for folder in folders]) == ['SUCCESS', 'SUCCESS']
    assert {Path(call.args[0]) for call in runtime.openImage.call_args_list} == expected_sources
    assert set(skipped.rglob('*')) == skipped_paths
    assert all(path.read_bytes() == content for path, content in previous.items() if skipped in path.parents)
    output = capsys.readouterr().out
    count = len(expected_sources)
    assert f'Ready: 2 folder(s), {count} images. Skipped: 1 folder(s).' in output
    assert f'Total {count}/{count} (100.0%)' in output
    assert 'Folder 2/2:' in output and 'Folder 3/' not in output


def test_skipping_all_folders_exits_without_false_success_or_writes(tmp_path, monkeypatch, runtime, capsys):
    folders = [source_folder(tmp_path, name) for name in ('first', 'second')]
    previous = {path: content for folder in folders for path, content in existing_results(folder).items()}
    paths_before = set(tmp_path.rglob('*'))
    initializer = MagicMock()
    monkeypatch.setattr(mod, 'initialize_imagej', initializer)
    monkeypatch.setattr(mod, 'validate_folders', lambda path: [str(folder) for folder in folders])
    answers = iter(['yes', 'no', 'no'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    assert mod.select_channel_name('input.json') is None
    initializer.assert_not_called()
    runtime.openImage.assert_not_called()
    assert set(tmp_path.rglob('*')) == paths_before
    assert all(path.read_bytes() == content for path, content in previous.items())
    output = capsys.readouterr()
    assert 'No folders selected for processing.' in output.out
    assert 'Nothing to do.' in output.out
    assert 'successfully completed' not in output.out and 'incomplete results' not in output.out
    assert 'Traceback' not in output.out + output.err


@pytest.mark.parametrize('response', ['no', 'N', 'q'])
def test_initial_decline_is_normal_exit_and_invalid_input_reprompts(monkeypatch, capsys, response):
    monkeypatch.setattr(mod, 'validate_folders', lambda path: ['images'])
    process = MagicMock()
    monkeypatch.setattr(mod, 'process_image', process)
    answers = iter(['maybe', '', response])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    assert mod.select_channel_name('input.json') is None
    process.assert_not_called()
    output = capsys.readouterr()
    assert output.out.count('Please enter yes, no, or q.') == 2
    assert 'Analysis canceled.' in output.out
    assert 'Traceback' not in output.out + output.err


def test_output_path_error_is_still_an_error_before_any_writes(tmp_path, monkeypatch, runtime):
    first = source_folder(tmp_path, 'first')
    second = source_folder(tmp_path, 'second')
    bad_output = second / 'foci_assay'
    bad_output.write_text('not a directory')
    with pytest.raises(NotADirectoryError, match='Output path is not a directory'):
        run(monkeypatch, [first, second], ())
    runtime.openImage.assert_not_called()
    assert not (first / 'foci_assay').exists()
    assert bad_output.read_text() == 'not a directory'
