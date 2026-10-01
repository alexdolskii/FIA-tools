"""Exercise retries and cancellation through the actual FIA prompt call paths."""

import importlib.util
import io
import json
import logging
import sys
from pathlib import Path
from unittest.mock import Mock

import collect_marker_intensity_results as collect
import interactive_input as prompts
import marker_intensity_report as report
import nuclear_intensity as intensity
import pandas as pd
import pytest
from channel_run_log import ChannelRunLog
from terminal_progress import CompactProgress

channels = sys.modules['select_channels']
nuclei = sys.modules['nuclei_mask_generation']
foci = sys.modules['foci_mask_generation']
quantify = sys.modules['foci_quantification']


@pytest.fixture(autouse=True)
def restore_log_handlers():
    root = logging.getLogger()
    previous, level = root.handlers[:], root.level
    yield
    for handler in root.handlers[:]:
        if handler not in previous:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def answer_with(monkeypatch, values):
    reader = Mock(side_effect=values)
    monkeypatch.setattr('builtins.input', reader)
    return reader


def test_channel_settings_retry_each_field_without_restarting(monkeypatch, capsys):
    reader = answer_with(monkeypatch, [
        'abc', '', '1.5', '0', '4', '2',
        'x', '13', '1',
        '-1', '', '2',
        '0', 'abc', '2',
        '13', '3',
    ])
    assert channels.select_settings() == (2, 1, [2, 3])
    questions = [call.args[0] for call in reader.call_args_list]
    assert questions[0] == questions[5]
    assert questions[6] == questions[8]
    assert questions[9] == questions[11]
    assert questions[12] == questions[14]
    assert questions[15] == questions[16]
    assert 'Traceback' not in capsys.readouterr().out


@pytest.mark.parametrize('prefix', [[], ['1'], ['1', '1'], ['1', '1', '1']])
@pytest.mark.parametrize('stop', [' Q ', KeyboardInterrupt(), EOFError()])
def test_all_channel_setting_prompts_can_cancel(monkeypatch, prefix, stop):
    reader = answer_with(monkeypatch, [*prefix, stop])
    with pytest.raises(prompts.CANCELLATION_EXCEPTIONS):
        channels.select_settings()
    assert reader.call_count == len(prefix) + 1


def test_yes_no_retries_typos_before_applying_documented_default(monkeypatch):
    reader = answer_with(monkeypatch, ['maybe', 'yess', ''])
    assert prompts.ask_yes_no('Continue? [y/N; q = cancel]: ', default=False) is False
    assert reader.call_count == 3
    reader = answer_with(monkeypatch, ['', 'noo', ' YES '])
    assert prompts.ask_yes_no('Continue? (yes/no; q = cancel): ') is True
    assert reader.call_count == 3


def test_cancellation_is_printed_and_recorded_without_a_console_handler(tmp_path, capsys):
    path = tmp_path / 'journal.log'
    handler = logging.FileHandler(path)
    handler.setLevel(logging.WARNING)
    logging.getLogger().addHandler(handler)

    @prompts.cancelable
    def main():
        raise prompts.Cancelled()

    assert main() == 130
    assert capsys.readouterr().out == 'Analysis canceled by user.\n'
    assert path.read_text() == 'CANCELLED | Analysis canceled by user.\n'


@pytest.mark.parametrize('choose', [intensity.choose, collect.choose_indices])
def test_multi_selection_retries_format_and_range_errors(monkeypatch, choose):
    reader = answer_with(monkeypatch, ['', 'abc', '1.5', '0', '4', '1,', '3,1,3'])
    assert choose('Select (1-3, all, q): ', 3) == [0, 2]
    assert reader.call_count == 7


@pytest.mark.parametrize('stop', ['q', KeyboardInterrupt(), EOFError()])
def test_channel_journal_records_cancellation_without_failure_or_traceback(tmp_path, stop):
    (tmp_path / 'image.nd2').touch()
    (tmp_path / 'fia_assay').mkdir()
    stream = io.StringIO()
    with pytest.raises(prompts.CANCELLATION_EXCEPTIONS):
        with CompactProgress(1, stream) as progress:
            with ChannelRunLog(tmp_path, None, 'test', 'digest', progress) as audit:
                audit.inventory()
                progress.logger = audit.logger
                audit.start_image('image.nd2')
                if isinstance(stop, str):
                    raise prompts.Cancelled()
                raise stop
    text = (tmp_path / 'fia_assay' / '1_log.log').read_text()
    assert 'IMAGE_CANCELLED' in text and 'RUN_CANCELLED' in text
    assert 'completed=0 | failed=0' in text and 'cancelled=1' in text
    assert 'status=CANCELLED' in text
    assert 'Traceback' not in text and 'FAILED' not in text
    assert 'CANCELLED' in stream.getvalue() and 'INTERRUPTED' not in stream.getvalue()


def test_legacy_confirmation_retries_same_question(monkeypatch, tmp_path):
    source = str(tmp_path / 'fia_assay' / 'Nuclei')
    candidate = {'usable': True, 'legacy': True, 'path': 'saved', 'count': 1}
    monkeypatch.setattr(nuclei, 'discover_stardist_folders', lambda path: [candidate])
    reader = answer_with(monkeypatch, ['', 'maybe', 'yes'])
    assert nuclei.select_stardist_sources([source]) == {source: 'saved'}
    assert reader.call_args_list[1] == reader.call_args_list[2]


@pytest.mark.parametrize('stop', ['q', KeyboardInterrupt(), EOFError()])
def test_legacy_confirmation_can_cancel_before_segmentation(monkeypatch, tmp_path, stop):
    source = str(tmp_path / 'fia_assay' / 'Nuclei')
    candidate = {'usable': True, 'legacy': True, 'path': 'saved', 'count': 1}
    monkeypatch.setattr(nuclei, 'validate_folders', lambda path: [source])
    monkeypatch.setattr(nuclei, 'discover_stardist_folders', lambda path: [candidate])
    process = Mock()
    monkeypatch.setattr(nuclei, 'find_nuclei', process)
    monkeypatch.setattr(nuclei, 'process_nuclei', process)
    answer_with(monkeypatch, ['1', stop])
    assert nuclei.main('input.json', 2500) == 130
    process.assert_not_called()
    journal = (tmp_path / 'fia_assay' / '2_log.log').read_text()
    assert 'RUN_FINISHED' in journal and 'status=CANCELLED' in journal
    assert 'Traceback' not in journal


def foci_folder(tmp_path, monkeypatch):
    folder = tmp_path / 'markers'
    (folder / 'Foci_1_Channel_2').mkdir(parents=True)
    info = {'foci_folder': str(folder)}
    monkeypatch.setattr(foci, 'validate_folders', lambda path: {'sample': info})
    process = Mock()
    monkeypatch.setattr(foci, 'filter_foci', process)
    return info, process


def test_foci_selection_and_confirmation_retry_before_processing(tmp_path, monkeypatch):
    info, process = foci_folder(tmp_path, monkeypatch)
    reader = answer_with(monkeypatch, ['abc', '0', '2', '1', 'yess', '', 'yes'])
    assert foci.main_filter_foci('input.json', 150) is None
    assert reader.call_count == 7
    process.assert_called_once_with(info, 'Foci_1_Channel_2', 150)


@pytest.mark.parametrize('answers', [['q'], ['1', 'q'], ['1', 'no'],
                                     ['1', KeyboardInterrupt()], ['1', EOFError()]])
def test_foci_cancellation_does_not_start_processing(tmp_path, monkeypatch, answers):
    _, process = foci_folder(tmp_path, monkeypatch)
    answer_with(monkeypatch, answers)
    assert foci.main_filter_foci('input.json', 150) == 130
    process.assert_not_called()


@pytest.mark.parametrize('answer,enabled', [('yes', True), ('no', False)])
def test_colocalization_never_interprets_a_typo_as_no(tmp_path, monkeypatch, answer, enabled):
    info = {'fia_assay_folder': str(tmp_path)}
    monkeypatch.setattr(quantify, 'validate_folders', lambda path: {str(tmp_path): info})
    monkeypatch.setattr(quantify, 'extract_metadata', lambda path: {})
    monkeypatch.setattr(quantify, 'gather_paths_and_channels', lambda path: (['image.tif'], {}))
    process = Mock(return_value=pd.DataFrame())
    monkeypatch.setattr(quantify, 'parallel_processing', process)
    reader = answer_with(monkeypatch, ['maybe', 'yes', 'yess', '', answer])
    quantify.main_summarize_res('input.json', njobs=2)
    assert reader.call_count == 5
    assert process.call_args.kwargs['perform_colocalization'] is enabled
    assert process.call_args.kwargs['max_workers'] == 2


@pytest.mark.parametrize('stop', ['q', KeyboardInterrupt(), EOFError()])
def test_colocalization_cancellation_creates_no_results(tmp_path, monkeypatch, stop):
    monkeypatch.setattr(quantify, 'validate_folders', lambda path: {str(tmp_path): {}})
    process = Mock()
    monkeypatch.setattr(quantify, 'parallel_processing', process)
    answer_with(monkeypatch, ['yes', stop])
    assert quantify.main_summarize_res('input.json') == 130
    process.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_intensity_type_retry_and_cancellation_precede_imagej(monkeypatch, tmp_path):
    from test_nuclear_intensity import make_experiment
    manifest, _, _, _, _ = make_experiment(tmp_path)
    engine = Mock()
    monkeypatch.setattr(intensity, 'ImageJEngine', engine)
    reader = answer_with(monkeypatch, ['abc', '0', '4', 'q'])
    assert intensity.main(manifest) == 130
    assert reader.call_count == 4
    engine.assert_not_called()


def test_intensity_mask_run_choice_retries(monkeypatch):
    records = [{'eligible': True, 'dataset': Path('sample'), 'path': Path(f'run{i}'),
                'marker': {'name': 'marker'}} for i in range(2)]
    answer_with(monkeypatch, ['abc', '4', '2'])
    assert intensity.select_runs(records) == records


def test_intensity_mask_run_enter_uses_advertised_latest_default(monkeypatch):
    records = [{'eligible': True, 'dataset': Path('sample'), 'path': Path(f'run{i}'),
                'marker': {'name': 'marker'}} for i in range(2)]
    answer_with(monkeypatch, [''])
    assert intensity.select_runs(records) == [records[-1]]


def test_collection_nucleus_run_choice_retries(monkeypatch):
    bundles = [{'run': Path('run1')}, {'run': Path('run2')}]
    answer_with(monkeypatch, ['abc', '4', '2'])
    assert collect.select_morphologies([{'morphology': bundles}]) == bundles


@pytest.mark.parametrize('stop', ['q', KeyboardInterrupt(), EOFError()])
def test_collection_cancels_before_writing_results(tmp_path, monkeypatch, stop):
    from test_collect_marker_intensity_results import create_morphology
    morph = create_morphology(tmp_path / 'sample')
    manifest = tmp_path / 'input.json'
    manifest.write_text(json.dumps({'paths_to_files': [str(morph[2].parent)]}))
    # Multiple nucleus runs provide a real selection prompt after discovery.
    create_morphology(morph[2].parent, stamp='20260924_120000')
    answer_with(monkeypatch, ['abc', stop])
    assert collect.main(manifest) == 130
    assert not list(morph[0].parent.glob(collect.OUTPUT_PREFIX + '*'))
    assert 'CANCELLED' in (morph[0].parent / '4_collect_marker_intensity.log').read_text()


def test_report_collection_choice_retries_with_explanation(monkeypatch):
    root, collection = Path('sample'), Path('sample/collection')
    monkeypatch.setattr(report, 'discover_collections', lambda path: [(collection, 'run')])
    answer_with(monkeypatch, ['abc', '', '4', ' ALL '])
    assert report.choose_collections([root], 'ask') == ([collection], [])


@pytest.mark.parametrize('stop', ['q', KeyboardInterrupt(), EOFError()])
def test_report_cancels_without_creating_outputs(tmp_path, monkeypatch, stop):
    from test_marker_intensity_report import collection
    path = collection(tmp_path)
    manifest = tmp_path / 'input.json'
    manifest.write_text(json.dumps({'paths_to_files': [str(path.parent.parent)]}))
    create = Mock()
    monkeypatch.setattr(report, 'create_report', create)
    answer_with(monkeypatch, ['abc', stop])
    assert report.main(['-i', str(manifest), '--collections', 'ask']) == 130
    create.assert_not_called()
    assert 'CANCELLED' in (path.parent / '5_marker_intensity_report.log').read_text()


@pytest.mark.parametrize('command', [
    'select_channels', 'generate_nuclei_mask', 'generate_foci_mask', 'quantify_foci',
    'quantify_nuclear_intensity', 'fia_collect_marker_intensity_results',
    'fia_marker_intensity_report',
])
def test_installed_entry_points_preserve_cancellation_status(monkeypatch, command):
    path = Path(__file__).parents[1] / 'fia-tools' / '__init__.py'
    spec = importlib.util.spec_from_file_location('fia_cli_test', path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    script = Mock()
    for method in ('select_channel_name', 'main', 'main_filter_foci', 'main_summarize_res'):
        getattr(script, method).return_value = 130
    monkeypatch.setattr(cli, '_load_script', lambda name: script)
    monkeypatch.setattr(sys, 'argv', [command, '-i', 'input.json'])
    # Dispatch stays in-process for the mocked analysis functions. Real worker
    # exit/cancellation propagation is exercised in test_run_resources.py.
    def supervise(name, arguments):
        assert name == command and arguments == ['-i', 'input.json']
        return getattr(cli, name).__wrapped__()
    monkeypatch.setattr('run_resources.run_command', supervise)
    assert getattr(cli, command)() == 130
