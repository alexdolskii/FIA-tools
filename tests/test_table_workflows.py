"""CLI location compatibility, full-run journals, and truthful phase progress."""

import io
import json
import os
import shutil
import warnings
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
import collect_marker_intensity_results as collect
import marker_intensity_report as report
import marker_report_data as inputs
import marker_report_plots as plots
from table_workflow import TableJournal, TableProgress
from test_collect_marker_intensity_results import create_morphology, create_intensity
from test_marker_intensity_report import collection, annotated


def manifest(tmp_path, roots):
    path = tmp_path / 'input_paths.json'
    path.write_text(json.dumps({'paths_to_files': [str(root) for root in roots]}))
    return path


@pytest.fixture
def table_only_report(monkeypatch):
    # The existing plotting suite renders real PNG/PDF files; these tests isolate
    # CLI/journal failures while retaining real input validation and Excel export.
    def render(data, spec, selected, points, comparisons, limits, high, span, note, path):
        from PIL import Image
        Image.new('RGB', (10, 10), 'white').save(path)
        path.with_suffix('.pdf').write_bytes(b'PDF rendering tested in the plot suite')
    monkeypatch.setattr(plots, 'render_figure', render)


def test_collect_all_json_roots_rotates_once_for_multiple_runs(tmp_path, monkeypatch, capsys):
    roots = [tmp_path / 'first', tmp_path / 'second']
    for root in roots:
        for stamp in ('20260924_090000', '20260924_120000'):
            morph = create_morphology(root, stamp=stamp)
            create_intensity(morph, stamp=stamp)
    path = manifest(tmp_path, roots + roots[:1])
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        return '2' if 'Nucleus-run' in prompt else 'all'
    monkeypatch.setattr('builtins.input', answer)
    snapshots = {}
    for root in roots:
        assay = root / 'fia_assay'
        (assay / 'logs').mkdir()
        (assay / 'logs' / '1_log_previous.log').write_text('keep')
    for invocation in range(2):
        assert collect.main(path) == 0
        for root in roots:
            assay = root / 'fia_assay'
            log = assay / '4_collect_marker_intensity.log'
            current = log.read_text()
            assert current.count('RUN_STARTED |') == current.count('RUN_FINISHED |') == 1
            assert current.count('RESULT |') == 2
            assert 'status=COMPLETE' in current
            assert f'input_folder={root}' in current
            assert f'input_folder={roots[1] if root == roots[0] else roots[0]}' not in current
            archives = list((assay / 'logs').glob('4_collect_marker_intensity_*.log'))
            assert len(archives) == invocation
            if invocation:
                assert archives[0].read_text() == snapshots[root]
            snapshots[root] = current
            assert len(list(assay.glob(collect.OUTPUT_PREFIX + '*'))) == 2 * (invocation + 1)
            assert not list(root.glob(collect.OUTPUT_PREFIX + '*'))
            assert (assay / 'logs' / '1_log_previous.log').read_text() == 'keep'
    assert len(prompts) == 4 and not any('experiments' in p for p in prompts)
    output = capsys.readouterr().out
    assert 'Combining images' in output and '\033' not in output


@pytest.mark.parametrize('command,filename', [
    ('collect', '4_collect_marker_intensity.log'), ('report', '5_marker_intensity_report.log')])
def test_early_cancel_archives_current_log_without_overwriting_collision(tmp_path, monkeypatch, command, filename):
    path = collection(tmp_path)
    root, assay = path.parent.parent, path.parent
    current = assay / filename
    current.write_text('previous run')
    previous_time = 1000000000
    os.utime(current, (previous_time, previous_time))
    stamp = datetime.fromtimestamp(previous_time, timezone.utc)
    archive = assay / 'logs'
    archive.mkdir(exist_ok=True)
    collision = archive / f'{current.stem}_{stamp:%Y%m%d_%H%M%S_%f}.log'
    collision.write_text('existing archive')
    config = manifest(tmp_path, [root])
    monkeypatch.setattr('builtins.input', lambda _: 'q')
    if command == 'collect':
        assert collect.main(config) == 130  # Single morphology auto-selects; marker prompt remains.
    else:
        assert report.main(['-i', str(config), '--collections', 'ask']) == 130
    assert 'status=CANCELLED' in current.read_text()
    assert 'Traceback' not in current.read_text()
    assert collision.read_text() == 'existing archive'
    assert sorted(p.read_text() for p in archive.glob(current.stem + '_*.log')) == ['existing archive', 'previous run']
    assert not list(assay.glob(report.OUTPUT_PREFIX + '*'))


def test_report_discovers_only_assay_collections_and_writes_inside_assay(
        tmp_path, monkeypatch, table_only_report):
    path = collection(tmp_path)
    root, assay = path.parent.parent, path.parent
    older = assay / (collect.OUTPUT_PREFIX + '20000101_000000')
    newer = assay / (collect.OUTPUT_PREFIX + '20990101_000000')
    shutil.copytree(path, older)
    shutil.copytree(path, newer)
    ignored = root / (collect.OUTPUT_PREFIX + '20991231_235959')
    shutil.copytree(path, ignored)
    old_assay = root / 'foci_assay'
    old_assay.mkdir()
    shutil.copytree(path, old_assay / ignored.name)
    assert report.choose_collections([root], 'latest') == ([newer], [])
    assert report.choose_collections([root], 'all') == ([older, path, newer], [])
    config = manifest(tmp_path, [root])
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('Single report marker must be automatic'))
    assert report.main(['-i', str(config), '--stats-unit', 'nucleus', '--min-nuclei', '20']) == 0
    output, = assay.glob(report.OUTPUT_PREFIX + '*')
    status = json.loads((output / 'report_status.json').read_text())
    assert status['Collection'] == str(newer)
    assert status['Statistics_unit'] == 'nucleus' and status['Min_nuclei'] == 20
    assert status['Images_excluded'] == 1 and status['Images'] == 0
    assert not list(root.glob(report.OUTPUT_PREFIX + '*')) and not (output / 'report.log').exists()
    assert ignored.is_dir() and (old_assay / ignored.name).is_dir()
    log = (assay / '5_marker_intensity_report.log').read_text()
    for token in ('min_nuclei=20', 'IMAGE_EXCLUDED', 'INPUT_VERIFIED', 'REPORT_VERIFIED', 'status=COMPLETE'):
        assert token in log


def test_reports_all_collections_share_one_journal_and_keep_partial_batch_status(
        tmp_path, monkeypatch, table_only_report):
    path = collection(tmp_path)
    root, assay = path.parent.parent, path.parent
    second = assay / (collect.OUTPUT_PREFIX + '20990101_000000')
    shutil.copytree(path, second)
    missing = tmp_path / 'unavailable'
    config = manifest(tmp_path, [root, missing])
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('No selection prompt expected'))
    assert report.main(['-i', str(config), '--collections', 'all']) == 1
    assert len(list(assay.glob(report.OUTPUT_PREFIX + '*'))) == 2
    assert all(json.loads(p.read_text())['Status'] == 'SUCCESS'
               for p in assay.glob(report.OUTPUT_PREFIX + '*/report_status.json'))
    text = (assay / '5_marker_intensity_report.log').read_text()
    assert text.count('RUN_STARTED |') == text.count('RUN_FINISHED |') == 1
    assert text.count('RESULT |') == 2 and 'SKIPPED_EXPERIMENT' in text
    assert not missing.exists()
    assert not list((assay / 'logs').glob('5_marker_intensity_report_*.log'))


def test_report_failure_stays_in_own_journal_and_next_experiment_finishes(
        tmp_path, monkeypatch, table_only_report):
    first, second = collection(tmp_path / 'first'), collection(tmp_path / 'second')
    (first / 'Nuclei_Morphology.csv').write_text('corrupt')
    config = manifest(tmp_path, [first.parent.parent, second.parent.parent])
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('No prompt expected'))
    assert report.main(['-i', str(config)]) == 1
    failed_log = (first.parent / '5_marker_intensity_report.log').read_text()
    good_log = (second.parent / '5_marker_intensity_report.log').read_text()
    assert 'Archived spreadsheet changed' in failed_log and 'Traceback' in failed_log
    assert 'Archived spreadsheet changed' not in good_log and 'Traceback' not in good_log
    assert 'status=COMPLETE' in good_log


def test_log_failure_prevents_success(tmp_path, monkeypatch):
    morph = create_morphology(tmp_path / 'sample')
    config = manifest(tmp_path, [morph[2].parent])
    # A directory at the journal path makes file creation fail without affecting result writing.
    (morph[0].parent / '4_collect_marker_intensity.log').mkdir()
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('Morphology-only must not prompt'))
    assert collect.main(config) == 1
    assert len(list(morph[0].parent.glob(collect.OUTPUT_PREFIX + '*'))) == 1


def test_scoped_warning_logging_and_completed_folder_survives_later_cancel(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    audit = TableJournal('5_marker_intensity_report.log')
    for root in (first, second):
        audit.register(root)
        audit.plan(root, 'one report')
    before = warnings.showwarning
    stream = io.StringIO()
    with TableProgress(stream=stream) as progress:
        with audit.stage(first, 'first stage', progress):
            progress.phase('Checking records', 2)
            warnings.warn('first experiment warning', UserWarning)
            progress.advance('WellA2.nd2')
        assert warnings.showwarning is before and progress.logger is None
    audit.result(first, 'one report', 'SUCCESS')
    audit.status = 'CANCELLED'
    audit.result(second, 'one report', 'CANCELLED')
    assert audit.finish()
    a = (first / 'fia_assay' / audit.filename).read_text()
    b = (second / 'fia_assay' / audit.filename).read_text()
    assert 'first experiment warning' in a and 'first experiment warning' not in b
    assert 'RUN_FINISHED' in a and 'status=COMPLETE' in a
    assert 'status=CANCELLED' in b and 'Traceback' not in b
    assert all(record['handler'].stream is None for record in audit.folders.values())


def test_progress_does_not_change_aggregation_or_log_one_line_per_record():
    expected = annotated()
    actual = deepcopy(expected)
    stream = io.StringIO()
    with TableProgress(stream=stream) as progress:
        progress.group('Report 1/1')
        inputs.aggregate(actual, progress)
        assert progress.done == progress.phase_total
    assert actual == expected
    assert stream.getvalue().count('\n') == 2 and '\033' not in stream.getvalue()


def test_plot_progress_advances_only_after_both_exports(tmp_path, monkeypatch):
    data = annotated()
    data['statistics'] = []
    progress = TableProgress(stream=io.StringIO())
    def render(data, spec, selected, points, comparisons, limits, high, span, note, path):
        if progress.done == 1:
            raise OSError('PDF could not be saved')
        path.write_bytes(b'PNG fixture')
        path.with_suffix('.pdf').write_bytes(b'PDF fixture')
    monkeypatch.setattr(plots, 'render_figure', render)
    with pytest.raises(OSError, match='PDF'):
        plots.render_plots(data, tmp_path / 'Plots', progress)
    assert progress.done == 1 and progress.phase_total == 2


def test_interactive_progress_percent_and_indeterminate_saving(monkeypatch):
    class TTY(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setenv('TERM', 'xterm')
    stream = TTY()
    progress = TableProgress(stream)
    progress.group('Report 1/2')
    progress.phase('Images', 4)
    progress.advance('B03:0002')
    progress._render(force=True)
    assert '1/4 (25%)' in stream.getvalue() and 'B03:0002' in stream.getvalue()
    stream.seek(0)
    stream.truncate()
    progress.phase('Saving Excel')
    assert 'Saving Excel' in stream.getvalue() and '%' not in stream.getvalue()
