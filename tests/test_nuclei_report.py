"""Nuclei-only preparation and direct reporting without any marker dependency."""

import hashlib
from plate_helpers import design
import json
import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import image_exclusions as exclusions
import marker_report_data as inputs
import nuclei_morphology as morphology
import nuclei_report as report
import pytest
import runtime_diagnostics
import test_select_channels_logging as preparation_tests
import test_table_workflows as workflow_tests
from marker_report_plots import plot_specs
from marker_report_statistics import calculate_statistics
from test_collect_marker_intensity_results import collect, create_morphology, sheet_rows
from test_marker_intensity_report import annotated, plate
from test_select_channels_logging import journal, mod, source_folder
from test_table_workflows import manifest

runtime = preparation_tests.runtime
table_only_report = workflow_tests.table_only_report


@pytest.mark.parametrize('file_type,filename', [('1', 'good_WellA02.nd2'), ('2', 'good_WellA02.tiff'), ('3', 'good_WellA02.tif')])
def test_only_selected_nuclei_channel_is_prepared(tmp_path, monkeypatch, runtime, file_type, filename):
    root = source_folder(tmp_path, filenames=(filename,))
    design(root, channels=['Ignore', 'Nuclei: DAPI', 'Ignore'])
    answers = iter([file_type if file_type != '1' else '2'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    assert mod.process_image([str(root)], nuclei_only=True) == ['SUCCESS']
    assert runtime.saveAs.call_count == 1
    assert not (root / 'fia_assay' / 'markers').exists()
    log = journal(root)
    assert 'nuclei_C=2' in log and 'output_files=1' in log
    assert 'marker_channels=' not in log and 'marker_projection=' not in log
    assert 'native_XY_preserved=True' in log
    nuclei = root / 'fia_assay' / 'Nuclei'
    snapshot = json.loads((nuclei / 'spatial_calibration.json').read_text())
    record = next(iter(snapshot.values()))
    assert record['Shape_YX'] == [10, 17]
    assert record['Pixel_size_X_um'] == .2 and record['Pixel_size_Y_um'] == .3


def test_real_pdf_report_ignores_unrelated_marker_files_and_preserves_measurements(tmp_path):
    run, export, raw = create_morphology(tmp_path / 'experiment')
    plate(run.parent / 'plate.xlsx')
    # These deliberately broken files must never be discovered or opened.
    for dirname in ('markers', 'Nuclear_Intensity_NuclearIntensity_Marker_1_783c3a1993a9_20260924_100000',
                    'FIA_Marker_Intensity_Combined_Results_20260924_110000'):
        directory = run.parent / dirname
        directory.mkdir()
        (directory / 'intensity_run.json').write_text('broken JSON')
        (directory / 'invalid.xlsx').write_bytes(b'broken workbook')
    before = {path: path.read_bytes() for path in raw.parent.rglob('*') if path.is_file()}
    ok, output = report.create_report(run, raw.parent)
    assert ok
    assert output.parent == run.parent and output.name.startswith(report.OUTPUT_PREFIX)
    assert {p.name for p in output.glob('*.xlsx')} == {report.WORKBOOK, 'plate_design.xlsx'}
    expected = {'Nuclei_count', 'Morphology_Area_px2', 'Morphology_Aspect_ratio',
                'Morphology_Circularity', 'Morphology_Solidity'}
    pdfs = list((output / 'Plots').glob('*.pdf'))
    assert {p.stem for p in pdfs} == expected
    assert all(p.read_bytes().startswith(b'%PDF') for p in pdfs)
    assert not list(output.rglob('*.png'))
    book = output / report.WORKBOOK
    with ZipFile(book) as archive:
        assert len([name for name in archive.namelist() if name.startswith('xl/media/')]) == 5
    nuclei = sheet_rows(book, 'Nuclei')
    assert [row['Nucleus_ID'] for row in nuclei] == [2, 5]
    for row, original in zip(nuclei, export.nuclei):
        assert all(row[field] == original[field] for field in morphology.METRICS)
    assert len(sheet_rows(book, 'Morphology_By_Condition')) == 12
    assert sheet_rows(book, 'Statistics') == []
    image_points = [row for row in sheet_rows(book, 'Plot_Data') if row['Observation'] == 'image']
    assert image_points[0]['Value'] == 2 and image_points[0]['Marker']
    metadata = json.loads((output / 'report_status.json').read_text())
    assert metadata['Report_kind'] == 'nuclei' and metadata['Nuclei_run'] == str(run)
    assert 'Markers' not in metadata and 'Collection' not in metadata
    assert not any('Marker_' in column for row in nuclei for column in row)
    assert all(path.read_bytes() == content for path, content in before.items())
    diagnostics = runtime_diagnostics.experiment_diagnostics(raw.parent, 1000, [], set())
    current = next(row for row in diagnostics['journals'] if Path(row['path']).name == report.JOURNAL)
    assert 'status=COMPLETE' in current['last_status_line']


@pytest.mark.parametrize('minimum,retained,red', [(0, 1, False), (2, 1, False), (3, 0, True)])
def test_filter_is_inclusive_and_keeps_diagnostic_count(tmp_path, table_only_report, minimum, retained, red):
    run, _, raw = create_morphology(tmp_path / 'experiment')
    plate(run.parent / 'plate.xlsx')
    ok, output = report.create_report(run, raw.parent, min_nuclei=minimum, stats_unit='nucleus', plot_format='both')
    assert ok
    book = output / report.WORKBOOK
    assert len(sheet_rows(book, 'Images')) == retained
    assert len(list((output / 'Plots').glob('*.pdf'))) == (6 if minimum else 5)
    assert len(list((output / 'Plots').glob('*.png'))) == (6 if minimum else 5)
    stats = sheet_rows(book, 'Statistics')
    assert len(stats) == 13 and all(row['Family_size_planned'] == 13 for row in stats)
    assert all('Marker' not in row for row in stats)
    points = sheet_rows(book, 'Plot_Data')
    diagnostic = [row for row in points if row['Plot'] == 'Nuclei_count_all']
    if minimum:
        assert len(diagnostic) == 1
        assert (diagnostic[0]['Point_edgecolor'] == '#D62728') is red
    if not retained:
        assert len(points) == 1  # No analytical points fabricated for filtered data.
        assert sheet_rows(book, 'Nuclei') == []


def test_real_zero_nuclei_and_failed_processing_have_different_meanings(tmp_path, table_only_report):
    run, _, raw = create_morphology(tmp_path / 'experiment', empty=True)
    plate(run.parent / 'plate.xlsx')
    ok, first = report.create_report(run, raw.parent)
    assert ok
    assert sheet_rows(first / report.WORKBOOK, 'Plot_Data')[0]['Value'] == 0
    # The same image's later processing failure must remove the old zero observation.
    exclusions.write_snapshot(run, {})
    exclusions.exclude_image(run.parent, raw, 'INFERENCE', 'native failure')
    ok, second = report.create_report(run, raw.parent)
    assert ok and first != second
    book = second / report.WORKBOOK
    assert sheet_rows(book, 'Images') == sheet_rows(book, 'Plot_Data') == []
    assert len(sheet_rows(book, 'Processing_Exclusions')) == 1
    sources = sheet_rows(book, 'Source_Files')
    snapshots = [row for row in sources if Path(row['Source']).name == 'excluded_images.json']
    assert len(snapshots) == 2 and snapshots[0]['Archived_copy'] != snapshots[1]['Archived_copy']
    for row in snapshots:
        assert hashlib.sha256((second / row['Archived_copy']).read_bytes()).hexdigest() == row['SHA256']


@pytest.mark.parametrize('unit', ['nucleus', 'well'])
def test_statistics_match_existing_morphology_only_route(tmp_path, unit):
    morph = create_morphology(tmp_path / 'experiment')
    ok, collection = collect(morph, [])
    assert ok
    template = plate(morph[0].parent / 'plate.xlsx')
    datasets = [inputs.load_nuclei_run(morph[0]), inputs.load_collection(collection)]
    datasets[1]['report_kind'] = 'nuclei'
    for data in datasets:
        inputs.annotate(data, template, [], unit)
        inputs.filter_images(data, 0)
        inputs.aggregate(data)
        calculate_statistics(data)
    assert datasets[0]['statistics'] == datasets[1]['statistics']
    assert len(datasets[0]['statistics']) == 13
    assert len(plot_specs(datasets[0])) == 5
    assert len(plot_specs(datasets[1])) == 5  # Existing marker-report policy stays intact.
    # Nonconstant populations also exercise tested p-values and well aggregation.
    direct, collected = annotated(), annotated()
    for data in (direct, collected):
        data['markers'] = []
        data['stats_unit'] = unit
        inputs.aggregate(data)
    direct['report_kind'] = 'nuclei'
    calculate_statistics(direct)
    calculate_statistics(collected)
    assert direct['statistics'] == collected['statistics']
    assert any(row['P_Holm'] is not None for row in direct['statistics'])


def test_batch_selects_latest_valid_runs_and_rotates_journals(tmp_path, monkeypatch, table_only_report):
    roots = [tmp_path / 'first', tmp_path / 'second']
    latest = []
    for root in roots:
        create_morphology(root)
        latest.append(create_morphology(root, stamp='20260925_090000')[0])
        incomplete = create_morphology(root, stamp='20260926_090000')[0]
        (incomplete / 'nuclei_run.json').write_text('{"status": "failed"}')
        plate(incomplete.parent / 'plate.xlsx')
    path = manifest(tmp_path, roots)
    monkeypatch.setattr('builtins.input', lambda prompt: pytest.fail(f'Unexpected prompt: {prompt}'))
    assert report.main(['-i', str(path)]) == 0
    for root, run in zip(roots, latest):
        outputs = list((root / 'fia_assay').glob(report.OUTPUT_PREFIX + '*'))
        assert len(outputs) == 1
        status = json.loads((outputs[0] / 'report_status.json').read_text())
        assert status['Nuclei_run'] == str(run)
        log = (root / 'fia_assay' / report.JOURNAL).read_text()
        assert 'SOURCE_REJECTED' in log and 'status=COMPLETE' in log
    assert report.main(['-i', str(path), '--nuclei-runs', 'all']) == 0
    for root in roots:
        assay = root / 'fia_assay'
        assert len(list(assay.glob(report.OUTPUT_PREFIX + '*'))) == 3
        assert len(list((assay / 'logs').glob('3_nuclei_report_*.log'))) == 1
    monkeypatch.setattr('builtins.input', lambda prompt: 'q')
    assert report.main(['-i', str(path), '--nuclei-runs', 'ask']) == 130


def test_missing_plate_map_stops_batch_before_reports(tmp_path, table_only_report, capsys):
    roots = [tmp_path/'missing_layout', tmp_path/'ready']
    for root in roots:
        create_morphology(root)
    (roots[0]/'FIA_scheme.xlsx').unlink()
    path = manifest(tmp_path, roots)
    assert report.main(['-i',str(path)]) == 1
    for root in roots:
        assert not list((root/'fia_assay').glob(report.OUTPUT_PREFIX+'*'))
        assert 'status=FAILED' in (root/'fia_assay/3_nuclei_report.log').read_text()
    assert 'FIA*.xlsx' in capsys.readouterr().out


def test_nuclei_report_import_and_help_need_no_imaging_runtime(tmp_path):
    env = dict(os.environ, FIA_RUNTIME_HOME=str(tmp_path / 'runtime'))
    command = 'import nuclei_report, sys; assert not any(x in sys.modules for x in ("imagej", "scyjava", "stardist", "tensorflow")); nuclei_report.main(["--help"])'
    result = subprocess.run([sys.executable, '-c', command], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--nuclei-runs' in result.stdout and '--markers' not in result.stdout
    assert not (tmp_path / 'runtime').exists()
