"""Missing experiment layouts must be actionable in terminal and saved diagnostics."""

import json

import pytest
from openpyxl import Workbook

import marker_intensity_report as report
import marker_report_data as inputs
from test_collect_marker_intensity_results import sheet_rows
from test_marker_intensity_report import collection, plate


@pytest.mark.parametrize('problem,code', [
    ('missing', 'MISSING_PLATE_MAP'),
    ('invalid', 'INVALID_PLATE_MAP'),
    ('ambiguous', 'AMBIGUOUS_PLATE_MAP'),
    ('explicit_missing', 'MISSING_PLATE_MAP'),
    ('explicit_invalid', 'INVALID_PLATE_MAP'),
])
def test_layout_problem_is_displayed_and_saved_without_starting_calculations(
        tmp_path, monkeypatch, capsys, problem, code):
    path = collection(tmp_path)
    layout = path / 'arbitrary layout.xlsx'
    options = []
    location = path
    if problem in ('missing', 'invalid'):
        layout.unlink()
    if problem == 'invalid':
        Workbook().save(path / 'unrecognized.xlsx')
    elif problem == 'ambiguous':
        plate(path / 'second layout.xlsx')
    elif problem.startswith('explicit_'):
        location = tmp_path / 'requested layout.xlsx'
        if problem == 'explicit_invalid':
            location.write_text('Not an Excel workbook')
        options = ['--template', str(location)]
    config = tmp_path / 'input.json'
    config.write_text(json.dumps({'paths_to_files': [str(path.parent.parent)]}))
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('Single marker must not prompt'))
    monkeypatch.setattr(inputs, 'aggregate', lambda *a, **kw: pytest.fail('No calculations without a layout'))
    monkeypatch.setattr(report, 'render_plots', lambda *a, **kw: pytest.fail('No plots without a layout'))
    assert report.main(['-i', str(config), '--stats-unit', 'nucleus', '--min-nuclei', '20', *options]) == 1
    output, = path.parent.glob(report.OUTPUT_PREFIX + '*')
    status = json.loads((output / 'report_status.json').read_text())
    assert status['Status'] == 'FAILED' and status['Stage'] == 'plate-map validation'
    assert status['Error_code'] == code and status['Plate_map_location'] == str(location)
    assert status['Required_action'] and 'rerun' in status['Required_action']
    diagnostics, = sheet_rows(output / 'Report_Diagnostics.xlsx', 'Diagnostics')
    for key in ('Error_code', 'Plate_map_location', 'Required_action', 'Error'):
        assert diagnostics[key] == status[key]
    log = (path.parent / '5_marker_intensity_report.log').read_text()
    console = capsys.readouterr().out
    for text in (code, str(location), status['Required_action']):
        assert text in log and text in console
    assert 'Traceback' not in console and 'Traceback' not in log
    assert not (output / report.WORKBOOK).exists() and not (output / 'Plots').exists()
    if problem == 'invalid':
        assert 'unrecognized.xlsx' in console and '96-well grid' in status['Plate_map_details']
        assert 'MISSING_PLATE_MAP' not in console
    if problem == 'ambiguous':
        assert 'arbitrary layout.xlsx' in status['Plate_map_details']
        assert 'second layout.xlsx' in status['Plate_map_details']


def test_two_experiments_without_layout_record_their_own_collection_paths(tmp_path, monkeypatch, capsys):
    paths = [collection(tmp_path / name) for name in ('first', 'second')]
    for path in paths:
        (path / 'arbitrary layout.xlsx').unlink()
    config = tmp_path / 'input.json'
    config.write_text(json.dumps({'paths_to_files': [str(p.parent.parent) for p in paths]}))
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('No prompt expected'))
    assert report.main(['-i', str(config), '--stats-unit', 'nucleus', '--min-nuclei', '20']) == 1
    console = capsys.readouterr().out
    assert console.count('Excel experiment layout (.xlsx) is missing') == 2
    for path in paths:
        log = (path.parent / '5_marker_intensity_report.log').read_text()
        assert f'Location: {path}' in log
        assert f'Location: {paths[1] if path == paths[0] else paths[0]}' not in log
        assert 'MISSING_PLATE_MAP' in log and 'status=FAILED' in log


def test_valid_layout_selection_and_explicit_sheet_are_preserved(tmp_path):
    path = collection(tmp_path)
    layout = path / 'arbitrary layout.xlsx'
    assert inputs.select_template(path) == layout
    assert inputs.select_template(path, str(layout), 'Plate Map') == layout
    with pytest.raises(inputs.PlateMapError) as error:
        inputs.select_template(path, str(layout), 'Wrong sheet')
    assert error.value.code == 'INVALID_PLATE_MAP'
    assert 'Wrong sheet' in error.value.details and '--sheet' in error.value.action


def test_analytical_hidden_and_lock_files_are_not_misreported_as_invalid_layouts(tmp_path):
    path = collection(tmp_path)
    (path / 'arbitrary layout.xlsx').unlink()
    plate(path / '._layout.xlsx')
    plate(path / '~$layout.xlsx')
    (path / 'folder.xlsx').mkdir()
    with pytest.raises(inputs.PlateMapError) as error:
        inputs.find_template(path)
    assert error.value.code == 'MISSING_PLATE_MAP'
