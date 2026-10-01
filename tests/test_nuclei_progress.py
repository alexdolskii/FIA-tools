"""Compact nuclei output, truthful outcomes, and journal isolation/restoration."""

import io
import json
import logging
import os
import sys
import time
import warnings
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from skimage.io import imread, imsave

from nuclei_run_log import NucleiLogSession, NucleiProgress, NucleiRunLog

mod = sys.modules['nuclei_mask_generation']


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_live_line_counts_attempts_without_filename_or_fake_inference_percentage(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm')
    monkeypatch.setenv('COLUMNS', '160')
    stream = Terminal()
    with NucleiProgress(stream) as progress:
        progress.group('Folder 1/2 | StarDist', 3)
        progress.begin_image('successful.tif')
        progress.phase('Predicting nuclei')
        progress._render(force=True)
        assert '0/3 (0.0%) | Predicting nuclei' in stream.getvalue()
        assert progress.group_completed == 0
        progress.finish_image()
        progress.begin_image('failed.tif')
        progress.finish_image(False)
        progress.begin_image('skipped.tif')
        progress.finish_image(skipped=True)
        assert '3/3 (100.0%) | failed 1 | skipped 1' in stream.getvalue()
        assert stream.getvalue().count('\n') == 1
        assert '.tif' not in stream.getvalue()
    assert not progress._thread.is_alive()


def test_narrow_terminal_keeps_elapsed_time(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm')
    monkeypatch.setenv('COLUMNS', '42')
    stream = Terminal()
    with NucleiProgress(stream) as progress:
        progress.group('Folder 1/2 | ImageJ', 144)
        progress.phase('Measuring morphology and saving QC')
        progress._render(force=True)
        last = stream.getvalue().rsplit('\033[2K', 1)[1]
        assert len(last) <= 41
        assert last.startswith('0/144 | Measuring')
        assert last.endswith('00:00')


@pytest.mark.parametrize('exception', [None, KeyboardInterrupt, OSError])
def test_scoped_journal_restores_warnings_and_closes_on_every_exit(tmp_path, monkeypatch, exception):
    monkeypatch.setenv('TERM', 'xterm')
    stream = Terminal()
    root_handlers = list(logging.getLogger().handlers)
    original_showwarning = warnings.showwarning
    run = NucleiRunLog(tmp_path / 'run.log', 'StarDist', 2, stream=stream)

    def execute():
        with run:
            run.progress.begin_image('affected.tif')
            run.progress.phase('Predicting nuclei')
            warnings.warn('Unexpected model diagnostic', RuntimeWarning)
            if exception:
                raise exception('interrupted')
            run.progress.finish_image()
            run.finish('INCOMPLETE')

    if exception:
        with pytest.raises(exception):
            execute()
    else:
        execute()
    assert warnings.showwarning is original_showwarning
    assert logging.getLogger().handlers == root_handlers
    assert not run.logger.handlers
    assert not run.progress._thread.is_alive()
    text = (tmp_path / 'run.log').read_text()
    output = stream.getvalue()
    assert 'Unexpected model diagnostic' in output and 'affected.tif' in output
    assert 'Unexpected model diagnostic' in text
    assert '100.0%' not in output
    if exception is KeyboardInterrupt:
        assert 'CANCELLED' in text and 'Traceback' not in text
    elif exception is OSError:
        assert 'FAILED' in text and 'Traceback' in text
        assert 'Traceback' not in output


def make_source(root, name, count=1):
    source = root / name / 'foci_assay' / 'Nuclei'
    source.mkdir(parents=True)
    image = np.arange(256, dtype=np.uint8).reshape(16, 16)
    for i in range(count):
        imsave(source / f'cell_{i}.tif', image, check_contrast=False)
    return source


def test_many_images_have_compact_redirected_output_and_isolated_detailed_logs(
        tmp_path, monkeypatch, capsys):
    first, second = make_source(tmp_path, 'first', 25), make_source(tmp_path, 'second', 2)
    model = MagicMock()
    labels = np.zeros((16, 16), dtype=np.uint16)
    labels[3:9, 4:10] = 1
    model.predict_instances.return_value = (labels, {})
    stardist = MagicMock()
    stardist.from_pretrained.return_value = model
    monkeypatch.setattr(mod, 'StarDist2D', stardist)
    monkeypatch.setattr(mod, 'normalize', lambda image: image)
    before = list(logging.getLogger().handlers)
    outputs = mod.find_nuclei([str(first), str(second)])
    output = capsys.readouterr().out
    assert '\r' not in output and '\033' not in output
    assert 'cell_0.tif' not in output
    assert len(output.splitlines()) < 25
    assert 'completed=25' in output and 'completed=2' in output
    assert '25 low-contrast label-mask notice(s)' in output
    assert stardist.from_pretrained.call_count == 1
    assert logging.getLogger().handlers == before
    for path, count in zip(outputs, (25, 2)):
        journal = (Path(path).parent / '2_log.log').read_text()
        assert not (Path(path) / '2_log.log').exists()
        assert journal.count('IMAGE_FINISHED') == count
        assert journal.count('is a low contrast image') == count
        assert '"prob_thresh": 0.7' in journal and '"nms_thresh": 0.9' in journal
        assert 'dtype=uint8 | bits=8' in journal
        assert 'dtype=uint16 | bits=16' in journal
        assert 'SAVED | path=' in journal and 'bytes=' in journal
        assert 'FINISHED' in journal and 'elapsed=' in journal
        result = imread(Path(path) / 'cell_0_StarDist_processed.tif')
        assert result.dtype == np.uint16
        np.testing.assert_array_equal(result, labels)
    assert str(second) not in (Path(outputs[0]).parent / '2_log.log').read_text()
    assert str(first) not in (Path(outputs[1]).parent / '2_log.log').read_text()
    assert model.predict_instances.call_args.kwargs == {'nms_thresh': .9, 'prob_thresh': .7}


def test_reuse_checks_are_compact_and_logged_without_modifying_saved_run(tmp_path, capsys):
    source = make_source(tmp_path, 'source', 3)
    saved = source.parent / 'Nuclei_StarDist_mask_processed_20260929_120000'
    saved.mkdir()
    for image in source.glob('*.tif'):
        imsave(saved / (image.stem + '_StarDist_processed.tif'),
               np.ones((16, 16), dtype=np.uint16), check_contrast=False)
    before = {path.name: path.read_bytes() for path in saved.iterdir()}
    candidates = mod.discover_stardist_folders(str(source))
    assert candidates[0]['usable'] and candidates[0]['legacy']
    assert before == {path.name: path.read_bytes() for path in saved.iterdir()}
    output = capsys.readouterr().out
    assert 'cell_0.tif' not in output
    journal = (source.parent / '2_log.log').read_text()
    assert journal.count('VALIDATE_IMAGE') == 3
    assert 'dtype=uint8' in journal and 'dtype=uint16' in journal
    assert 'reusable=1' in output


def fake_imagej(monkeypatch, fail_morphology=False):
    ij, window = MagicMock(), MagicMock()
    for image in (ij.openImage.return_value, window.getImage.return_value):
        image.getWidth.return_value = image.getHeight.return_value = 16
        image.getBitDepth.return_value = 16 if image is ij.openImage.return_value else 8
    ij.saveAs.side_effect = lambda image, fmt, path: Path(path).write_bytes(b'final-mask')
    monkeypatch.setattr(mod, 'initialize_imagej', MagicMock())
    monkeypatch.setattr(mod, 'jimport', lambda name: ij if name == 'ij.IJ' else window)

    class Export:
        def __init__(self, output, *args):
            self.output = Path(output)
            self.failed = False

        def add_image(self, image, filename, mask_name):
            if fail_morphology:
                raise OSError('QC save failed')
            qc = self.output / 'Morphology_QC'
            qc.mkdir(exist_ok=True)
            for suffix in ('_ids.tif', '_ids.png'):
                (qc / (Path(mask_name).stem + suffix)).write_bytes(b'qc')

        def record_failure(self, *args):
            self.failed = True

        def save(self):
            for name in ('Nuclei_Morphology.xlsx', 'Nuclei_Morphology.csv',
                         'Nuclei_Images.csv', 'Nuclei_Run_Info.csv'):
                (self.output / name).write_bytes(b'table')
            return 'incomplete' if self.failed else 'complete'

    monkeypatch.setattr(mod, 'NucleiMorphologyExport', Export)
    return ij


@pytest.mark.parametrize('fail_morphology', [False, True])
def test_imagej_summary_distinguishes_masks_and_morphology_and_keeps_commands(
        tmp_path, monkeypatch, capsys, fail_morphology):
    source = make_source(tmp_path, 'imagej', 2)
    (source / '2_log.log').write_text('previous journal')
    ij = fake_imagej(monkeypatch, fail_morphology)
    success = mod.process_nuclei([str(source)], 2000)
    assert success is not fail_morphology
    output = capsys.readouterr().out
    assert 'Processing file:' not in output and 'Processed image saved:' not in output
    assert "Skipping '2_log.log'" not in output
    assert 'masks=complete' in output
    assert ('morphology=incomplete' if fail_morphology else 'morphology=complete') in output
    if fail_morphology:
        assert 'INCOMPLETE' in output and 'cell_0.tif' in output
        assert 'Traceback' not in output
    else:
        assert 'cell_0.tif' not in output
    commands = [call.args[1:] for call in ij.run.call_args_list if len(call.args) > 1]
    assert commands == [('8-bit', ''), ('Convert to Mask', ''), ('Watershed', ''),
                        ('Analyze Particles...', 'size=2000-Infinity pixel show=Masks')] * 2
    assert all(call.args[1:] == (1, 255) for call in ij.setThreshold.call_args_list)
    folder = next(source.parent.glob('Final_Nuclei_Mask_*'))
    metadata = json.loads((folder / 'nuclei_run.json').read_text())
    assert metadata['status'] == 'complete'
    assert metadata['morphology_status'] == ('incomplete' if fail_morphology else 'complete')
    journal = (folder.parent / '2_log.log').read_text()
    assert not (folder / 'nuclei_log.log').exists()
    assert 'width=16 | height=16 | bits=16' in journal
    assert 'width=16 | height=16 | bits=8' in journal
    assert ('Traceback' in journal) is fail_morphology
    assert f"status={'INCOMPLETE' if fail_morphology else 'COMPLETE'}" in journal


def test_main_reports_incomplete_imagej_result(tmp_path, monkeypatch, capsys):
    source = str(make_source(tmp_path, 'main'))
    monkeypatch.setattr(mod, 'validate_folders', lambda path: [source])
    monkeypatch.setattr(mod, 'select_stardist_sources', lambda paths: {source: 'reused'})
    monkeypatch.setattr(mod, 'process_nuclei', lambda paths, size: False)
    mod.main('input.json', 2000)
    assert 'Step 2: Nuclei processing INCOMPLETE' in capsys.readouterr().out


def main_input(root, sources):
    path = root / 'inputs.json'
    path.write_text(json.dumps({'paths_to_files': [str(source.parent.parent) for source in sources]}))
    return str(path)


def fake_stardist(monkeypatch):
    stardist = MagicMock()
    labels = np.zeros((16, 16), dtype=np.uint16)
    labels[3:9, 4:10] = 1
    stardist.from_pretrained.return_value.predict_instances.return_value = (labels, {})
    monkeypatch.setattr(mod, 'StarDist2D', stardist)
    monkeypatch.setattr(mod, 'normalize', lambda image: image)
    return stardist


def test_whole_command_joins_stages_and_archives_only_at_next_run(tmp_path, monkeypatch):
    source = make_source(tmp_path, 'dataset', 2)
    manifest = main_input(tmp_path, [source])
    current = source.parent / '2_log.log'
    current.write_text('previous run')
    first_log = source.parent / '1_log.log'
    first_log.write_text('first program current')
    archive = source.parent / 'logs'
    archive.mkdir()
    first_archive = archive / '1_log_20260101_000000.log'
    first_archive.write_text('first program archive')
    old_validation = source.parent.parent / '2_val_log.log'
    old_validation.write_text('old validation journal')
    legacy = source.parent / 'Nuclei_StarDist_mask_processed_20250101_010101'
    legacy.mkdir()
    (legacy / '2_log.log').write_text('old StarDist journal')
    stardist = fake_stardist(monkeypatch)
    fake_imagej(monkeypatch)

    mod.main(manifest, 2000)
    first_run = current.read_bytes()
    text = first_run.decode()
    assert text.count('RUN_STARTED |') == text.count('RUN_FINISHED |') == 1
    for stage in ('validation', 'reuse_check', 'selection', 'stardist', 'stardist_check', 'imagej'):
        assert f'STAGE_STARTED | stage={stage} |' in text
    assert 'status=COMPLETE' in text.split('RUN_FINISHED |')[1]
    archived = list(archive.glob('2_log_*.log'))
    assert len(archived) == 1 and archived[0].read_text() == 'previous run'
    saved = [path for path in source.parent.glob('Nuclei_StarDist_mask_processed_*') if path != legacy][0]
    preserved = {path.name: path.read_bytes() for path in saved.iterdir()}
    assert '2_log.log' not in preserved
    final_folder = next(source.parent.glob('Final_Nuclei_Mask_*'))
    assert not (final_folder / 'nuclei_log.log').exists()

    # A second CLI invocation reuses the masks and archives the entire first run.
    monkeypatch.setattr('builtins.input', lambda prompt: '')
    mod.main(manifest, 1000)
    second_run = current.read_text()
    assert second_run.count('RUN_STARTED |') == second_run.count('RUN_FINISHED |') == 1
    assert 'STARDIST_REUSED' in second_run and str(saved) in second_run
    assert 'STAGE_STARTED | stage=stardist |' not in second_run
    assert 'particle_size_pixels_squared=1000' in second_run
    assert stardist.from_pretrained.call_count == 1
    archived = list(archive.glob('2_log_*.log'))
    assert len(archived) == 2
    assert first_run in [path.read_bytes() for path in archived]
    assert preserved == {path.name: path.read_bytes() for path in saved.iterdir()}
    assert old_validation.read_text() == 'old validation journal'
    assert (legacy / '2_log.log').read_text() == 'old StarDist journal'
    assert first_log.read_text() == 'first program current'
    assert first_archive.read_text() == 'first program archive'


def test_rotation_uses_utc_and_preserves_archive_name_collisions(tmp_path, monkeypatch):
    current = tmp_path / 'foci_assay' / '2_log.log'
    current.parent.mkdir()
    current.write_text('old current')
    os.utime(current, (1700000000.125, 1700000000.125))
    archive = current.parent / 'logs'
    archive.mkdir()
    collision = archive / '2_log_20231114_221320_125000.log'
    collision.write_text('already archived')
    monkeypatch.setattr(time, 'time', lambda: 1700000000.125)
    # A local-time formatter would produce the wrong date even in a UTC test host.
    monkeypatch.setattr(time, 'localtime', lambda seconds=None: time.gmtime(0))
    with NucleiLogSession(), NucleiRunLog(current, 'Validation', quiet=True):
        pass
    assert collision.read_text() == 'already archived'
    assert sorted(path.read_text() for path in archive.iterdir()) == ['already archived', 'old current']
    assert current.read_text().startswith('2023-11-14 22:13:20,125Z |')


@pytest.mark.parametrize('exception', [KeyboardInterrupt, OSError])
def test_abort_records_active_and_pending_folders_and_archives_on_retry(
        tmp_path, monkeypatch, exception):
    sources = [make_source(tmp_path, name) for name in ('first', 'second')]
    manifest = main_input(tmp_path, sources)
    stardist = fake_stardist(monkeypatch)
    fake_imagej(monkeypatch)
    model = stardist.from_pretrained.return_value
    model.predict_instances.side_effect = exception('inference interrupted')
    if exception is KeyboardInterrupt:
        assert mod.main(manifest, 2000) == 130
    else:
        with pytest.raises(OSError, match='inference interrupted'):
            mod.main(manifest, 2000)
    mod.initialize_imagej.assert_not_called()
    status = 'CANCELLED' if exception is KeyboardInterrupt else 'FAILED'
    records = [(source.parent / '2_log.log').read_text() for source in sources]
    assert f'status={status}' in records[0].split('RUN_FINISHED |')[1]
    assert ('Traceback' in records[0]) is (exception is OSError)
    pending_status = 'CANCELLED' if exception is KeyboardInterrupt else 'INCOMPLETE'
    assert f'status={pending_status}' in records[1].split('RUN_FINISHED |')[1]
    assert 'BATCH_INTERRUPTED' in records[1]
    assert str(sources[1]) not in records[0] and str(sources[0]) not in records[1]

    model.predict_instances.side_effect = None
    mod.main(manifest, 2000)
    for source, previous in zip(sources, records):
        archived = list((source.parent / 'logs').glob('2_log_*.log'))
        assert len(archived) == 1 and archived[0].read_text() == previous
        current = (source.parent / '2_log.log').read_text()
        assert current.count('RUN_STARTED |') == 1
        assert 'status=COMPLETE' in current.split('RUN_FINISHED |')[1]


def test_later_folder_failure_keeps_completed_folder_status_and_its_own_diagnostics(
        tmp_path, monkeypatch):
    sources = [make_source(tmp_path, name) for name in ('first', 'second', 'third')]
    manifest = main_input(tmp_path, sources)
    fake_stardist(monkeypatch)
    ij = fake_imagej(monkeypatch)
    image = ij.openImage.return_value

    def open_image(path):
        if '/second/' in path:
            raise OSError('second folder failed to open')
        return image

    ij.openImage.side_effect = open_image
    with pytest.raises(OSError, match='second folder'):
        mod.main(manifest, 2000)
    logs = [(source.parent / '2_log.log').read_text() for source in sources]
    for journal, status in zip(logs, ('COMPLETE', 'FAILED', 'INCOMPLETE')):
        assert f'status={status}' in journal.split('RUN_FINISHED |')[1]
        assert journal.count('RUN_STARTED |') == 1
    assert 'Traceback' in logs[1]
    assert 'second folder failed to open' not in logs[0] + logs[2]
