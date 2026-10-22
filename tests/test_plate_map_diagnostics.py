"""Raw-folder scheme errors remain actionable before any report calculations."""
import json
import pytest
from openpyxl import Workbook
import marker_intensity_report as report
import marker_report_data as inputs
from test_collect_marker_intensity_results import sheet_rows
from test_marker_intensity_report import collection, plate


@pytest.mark.parametrize('problem,code', [('missing','MISSING_PLATE_MAP'),('invalid','INVALID_PLATE_MAP'),
    ('ambiguous','AMBIGUOUS_PLATE_MAP'),('explicit_missing','MISSING_PLATE_MAP'),('explicit_invalid','INVALID_PLATE_MAP')])
def test_direct_report_writes_actionable_diagnostics(tmp_path, monkeypatch, capsys, problem, code):
    path = collection(tmp_path)
    root = path.parent.parent
    layout = root/'FIA_scheme.xlsx'
    options = {}
    if problem in ('missing', 'invalid'):
        layout.unlink()
    if problem == 'invalid':
        Workbook().save(layout)
    elif problem == 'ambiguous':
        plate(root/'FIA_second.xlsx')
    elif problem.startswith('explicit_'):
        target = tmp_path/'explicit.xlsx'
        if problem == 'explicit_invalid':
            target.write_text('Not an Excel workbook')
        options['template'] = target
    monkeypatch.setattr(inputs, 'aggregate', lambda *a, **kw: pytest.fail('No calculations on invalid scheme'))
    ok, output = report.create_report(path, root, markers='all', **options)
    assert not ok
    status = json.loads((output/'report_status.json').read_text())
    assert status['Error_code'] == code and status['Stage'] == 'plate-map validation'
    assert 'rerun' in status['Required_action']
    diagnostics, = sheet_rows(output/'Report_Diagnostics.xlsx', 'Diagnostics')
    assert diagnostics['Error_code'] == code
    assert not (output/'Plots').exists()
    assert 'Traceback' not in capsys.readouterr().out


def test_cli_preflight_logs_each_invalid_experiment_without_report_outputs(tmp_path, monkeypatch, capsys):
    paths = [collection(tmp_path/name) for name in ('first','second')]
    for path in paths:
        (path.parent.parent/'FIA_scheme.xlsx').unlink()
    config = tmp_path/'input.json'
    config.write_text(json.dumps({'paths_to_files':[str(p.parent.parent) for p in paths]}))
    monkeypatch.setattr(inputs, 'aggregate', lambda *a, **kw: pytest.fail('No calculations'))
    assert report.main(['-i',str(config)]) == 1
    for path in paths:
        log = (path.parent/'5_marker_intensity_report.log').read_text()
        assert 'INPUT_VALIDATION_FAILED' in log and 'FIA*.xlsx' in log and 'status=FAILED' in log
        assert not list(path.parent.glob(report.OUTPUT_PREFIX+'*'))
    assert 'FIA*.xlsx' in capsys.readouterr().out


def test_raw_folder_discovery_ignores_uma_hidden_lock_and_results(tmp_path):
    path = collection(tmp_path)
    root = path.parent.parent
    plate(root/'UMA.xlsx')
    plate(root/'._FIA_copy.xlsx')
    plate(root/'~$FIA_copy.xlsx')
    assert inputs.select_template(root).name == 'FIA_scheme.xlsx'
    with pytest.raises(inputs.PlateMapError):
        inputs.select_template(root, root/'FIA_scheme.xlsx', 'Wrong sheet')
