"""Exercise simplified prompts and complete text journals with a mocked image reader."""

import io
import json
import logging
import os
import shutil
import warnings
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
import nuclear_intensity as app
from intensity_run_log import IntensityJournal
from terminal_progress import CompactProgress
from test_nuclear_intensity import make_experiment


@pytest.fixture
def setup_batch(tmp_path, monkeypatch):
    def create(count=2, multiple=True):
        roots, old_runs, new_runs = [], [], []
        for index in range(count):
            parent = tmp_path / f'dataset{index + 1}'
            parent.mkdir()
            _, raw, run, _, labels = make_experiment(parent)
            # Format dispatch is under test; the reader below is explicitly mocked.
            raw.rename(raw.with_suffix('.nd2'))
            roots.append(raw.parent)
            old_runs.append(run)
            if multiple:
                newer = run.with_name('Final_Nuclei_Mask_20260923_130000')
                shutil.copytree(run, newer)
                new_runs.append(newer)
        manifest = tmp_path / 'batch.json'
        manifest.write_text(json.dumps({'paths_to_files': [str(root) for root in roots]}))
        engine = MagicMock(version='mock', bioformats_version='mock')
        engine.inspect.return_value = {'Width_px': labels.shape[1], 'Height_px': labels.shape[0],
                                       'Z_planes': 3, 'Pixel_type': 'uint16'}
        engine.labels.return_value = np.zeros_like(labels)
        engine.populations.return_value = ([], [], [])
        engine.measure.return_value = ([], {key: 0 for key in app.COUNTS})
        factory = MagicMock(return_value=engine)
        monkeypatch.setattr(app, 'ImageJEngine', factory)
        return manifest, roots, old_runs, new_runs, engine, factory
    return create


def journal(root):
    return root / 'foci_assay' / '3_nuclei_intensity.log'


@pytest.mark.parametrize('answers, which, measurements', [
    ([''], 'new', 2), (['2'], 'all', 4), (['3', '2,4'], 'old', 2),
])
def test_two_json_folders_one_marker_nd2_need_only_mask_choices(
        setup_batch, monkeypatch, answers, which, measurements):
    manifest, roots, older, newer, engine, factory = setup_batch()
    choices, prompts = iter(answers), []
    def answer(prompt):
        assert factory.call_count == 0  # Plan selection before expensive validation.
        prompts.append(prompt)
        return next(choices)
    monkeypatch.setattr('builtins.input', answer)
    inspected = []
    original = app.inspect_run
    def inspect(*args, **kwargs):
        inspected.append(args[1])
        return original(*args, **kwargs)
    monkeypatch.setattr(app, 'inspect_run', inspect)
    assert app.main(manifest) == 0
    assert len(prompts) == len(answers)
    assert all('run' in prompt.lower() for prompt in prompts)
    assert engine.measure.call_count == measurements
    expected = newer if which == 'new' else older if which == 'old' else older + newer
    assert set(inspected) == set(expected)
    for root in roots:
        text = journal(root).read_text()
        assert text.count('RUN_STARTED |') == text.count('RUN_FINISHED |') == 1
        assert 'status=COMPLETE' in text.split('RUN_FINISHED |')[1]
        assert 'INPUT_TYPE | nd2 | selection=automatic' in text
        assert 'original_channel=2' in text and "'Pixel_type': 'uint16'" in text
        assert 'TABLES_SAVED' in text and 'elapsed=' in text
        assert not list((root / 'foci_assay').glob('Nuclear_Intensity_*/intensity.log'))
    assert str(roots[1]) not in journal(roots[0]).read_text()
    assert str(roots[0]) not in journal(roots[1]).read_text()


def test_only_one_candidate_requires_no_questions(setup_batch, monkeypatch):
    manifest, _, _, _, engine, _ = setup_batch(multiple=False)
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('No question is needed'))
    assert app.main(manifest) == 0
    assert engine.measure.call_count == 2


def test_latest_falls_back_after_full_validation_rejects_newer_ids(setup_batch, monkeypatch):
    manifest, roots, older, newer, engine, _ = setup_batch(count=1)
    next((newer[0] / 'Morphology_QC').glob('*_ids.tif')).unlink()
    monkeypatch.setattr('builtins.input', lambda _: '')
    assert app.main(manifest) == 0
    output = next((roots[0] / 'foci_assay').glob('Nuclear_Intensity_*'))
    assert json.loads((output / 'intensity_run.json').read_text())['Nuclei_run'] == str(older[0])
    text = journal(roots[0]).read_text()
    assert 'UNAVAILABLE' in text and str(newer[0]) in text and 'READY' in text
    assert engine.measure.call_count == 1


def test_metadata_change_after_manual_selection_is_rejected(setup_batch, monkeypatch):
    manifest, roots, _, newer, engine, _ = setup_batch(count=1)
    answers = iter(['3', '1'])
    def answer(prompt):
        choice = next(answers)
        if choice == '1':
            path = newer[0] / 'nuclei_run.json'
            content = json.loads(path.read_text())
            content['particle_size_pixels_squared'] = 9000
            path.write_text(json.dumps(content))
        return choice
    monkeypatch.setattr('builtins.input', answer)
    assert app.main(manifest) == 1
    engine.measure.assert_not_called()
    assert 'metadata changed after selection' in journal(roots[0]).read_text()


def test_reruns_archive_once_and_preserve_other_logs_and_old_result_logs(setup_batch, monkeypatch):
    manifest, roots, _, _, _, _ = setup_batch(count=1)
    root = roots[0]
    assay = root / 'foci_assay'
    for name in ('1_log.log', '2_log.log'):
        (assay / name).write_text(name)
    old_output = assay / 'Nuclear_Intensity_old'
    old_output.mkdir()
    (old_output / 'intensity.log').write_text('legacy text log')
    current = journal(root)
    current.write_text('old current journal')
    os.utime(current, (1700000000.125, 1700000000.125))
    archive = assay / 'logs'
    archive.mkdir()
    collision = archive / '3_nuclei_intensity_20231114_221320_125000.log'
    collision.write_text('existing archive')
    monkeypatch.setattr('builtins.input', lambda _: '2')
    assert app.main(manifest) == 0
    first = current.read_bytes()
    assert len(list(archive.glob('3_nuclei_intensity_*.log'))) == 2
    assert current.read_text().count('RUN_STARTED |') == 1
    assert current.read_text().count('RESULT |') == 2
    assert app.main(manifest) == 0
    archived = list(archive.glob('3_nuclei_intensity_*.log'))
    assert len(archived) == 3 and first in [path.read_bytes() for path in archived]
    assert collision.read_text() == 'existing archive'
    assert (old_output / 'intensity.log').read_text() == 'legacy text log'
    assert all((assay / name).read_text() == name for name in ('1_log.log', '2_log.log'))


@pytest.mark.parametrize('stop', ['q', KeyboardInterrupt(), EOFError()])
def test_cancel_mask_prompt_is_logged_before_imagej_or_results(setup_batch, monkeypatch, stop):
    manifest, roots, _, _, _, factory = setup_batch()
    def answer(prompt):
        if isinstance(stop, BaseException):
            raise stop
        return stop
    monkeypatch.setattr('builtins.input', answer)
    assert app.main(manifest) == 130
    factory.assert_not_called()
    for root in roots:
        text = journal(root).read_text()
        assert 'status=CANCELLED' in text and 'Traceback' not in text
        assert not list((root / 'foci_assay').glob('Nuclear_Intensity_*'))


def test_initialization_failure_is_logged_for_all_inputs(setup_batch, monkeypatch):
    manifest, roots, _, _, _, factory = setup_batch(multiple=False)
    factory.side_effect = OSError('JVM unavailable')
    assert app.main(manifest) == 1
    for root in roots:
        text = journal(root).read_text()
        assert 'JVM unavailable' in text and 'Traceback' in text and 'status=FAILED' in text


def test_missing_selected_marker_makes_batch_incomplete_without_substitution(setup_batch):
    manifest, roots, _, _, engine, _ = setup_batch(multiple=False)
    next((roots[1] / 'foci_assay' / 'Foci' / 'Foci_1_Channel_2').glob('*.tif')).unlink()
    assert app.main(manifest) == 1
    assert engine.measure.call_count == 1
    assert 'status=COMPLETE' in journal(roots[0]).read_text().split('RUN_FINISHED |')[1]
    text = journal(roots[1]).read_text()
    assert 'Selected marker folder is missing' in text and 'status=INCOMPLETE' in text


def test_missing_json_folder_is_recorded_and_prevents_overall_success(setup_batch, tmp_path):
    manifest, roots, _, _, _, _ = setup_batch(count=1, multiple=False)
    missing = tmp_path / 'unavailable'
    manifest.write_text(json.dumps({'paths_to_files': [str(roots[0]), str(missing)]}))
    assert app.main(manifest) == 1
    assert not missing.exists()
    output = next((roots[0] / 'foci_assay').glob('Nuclear_Intensity_*'))
    metadata = json.loads((output / 'batch.json').read_text())
    assert metadata['status'] == 'incomplete' and len(metadata['skipped_experiments']) == 1
    assert 'SKIPPED_EXPERIMENT' in journal(roots[0]).read_text()


def test_tiff_and_mixed_formats_keep_explicit_type_question(monkeypatch):
    prompts = []
    monkeypatch.setattr('builtins.input', lambda prompt: prompts.append(prompt) or '2')
    for raw in ([Path('image.tif')], [Path('image.nd2'), Path('image.tiff')]):
        assert app.select_input_mode([{'raw': raw}]) == 'tiff-stack'
    assert len(prompts) == 2
    assert app.select_input_mode([{'raw': [Path('image.nd2')]}], 'tiff-2d') == 'tiff-2d'
    assert len(prompts) == 2  # Explicit CLI mode is never silently overridden.


def test_stage_restores_handlers_and_warnings_and_filters_only_reader_percentages(tmp_path):
    audit = IntensityJournal()
    audit.register(tmp_path)
    stream = io.StringIO()
    root_handlers = list(logging.getLogger().handlers)
    previous_warning = warnings.showwarning
    with CompactProgress(stream=stream) as progress:
        with pytest.raises(OSError), audit.stage(tmp_path, 'Test', progress) as logger:
            handlers = list(logger.handlers)
            logger.info("Bio-Formats: Parsing block 'ImageDataSeq' 50%")
            warnings.warn('Reader warning retained', RuntimeWarning)
            raise OSError('simulated failure')
        assert progress.logger is None
    assert warnings.showwarning is previous_warning
    assert logging.getLogger().handlers == root_handlers
    assert not logger.handlers
    assert all(handler.stream is None for handler in handlers if isinstance(handler, logging.FileHandler))
    text = journal(tmp_path).read_text()
    assert 'Parsing block' not in text and 'Reader warning retained' in text
    assert 'Traceback' in text and 'Traceback' not in stream.getvalue()
