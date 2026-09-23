"""Collector checks using the actual morphology/intensity spreadsheet exporters."""

import csv
import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import collect_marker_intensity_results as collector
import nuclear_intensity as intensity
import nuclei_morphology as morphology
import numpy as np
import pytest
from openpyxl import load_workbook


def create_morphology(root, stamp='20260924_090000', particle_size=2, empty=False):
    root.mkdir(parents=True, exist_ok=True)
    raw = root / 'field, WellA2.nd2'
    raw.write_bytes(b'not an image: the collector must never read pixels')
    run = root / 'foci_assay' / f'Final_Nuclei_Mask_{stamp}'
    run.mkdir(parents=True)
    source_name = raw.stem + '_nuclei_projection_StarDist_processed.tif'
    mask = Path(source_name).stem + '_processed.tif'
    export = morphology.NucleiMorphologyExport(run, run.parent / 'StarDist_source', particle_size, '1.54t')
    identifiers = export.identifiers(source_name, mask)
    for nucleus_id, area in ([] if empty else [(2, 12), (5, 20)]):
        values = {metric: 0.5 for metric in morphology.METRICS}
        export.nuclei.append({**identifiers, **values, 'Area_px2': area, 'Nucleus_ID': nucleus_id,
                              'Orientation_deg': 0, 'Centroid_X_px': 10, 'Centroid_Y_px': 20,
                              'Touches_border': False, 'Label_map': 'Morphology_QC/ids.tif',
                              'Numbered_image': 'Morphology_QC/ids.png'})
    summary = {**identifiers, 'Status': 'complete', 'Error': '',
               'Nuclei_count_total': 0 if empty else 3, 'Border_nuclei_count': 0 if empty else 1,
               'Non_border_nuclei_count': len(export.nuclei)}
    for metric in morphology.METRICS:
        values = [row[metric] for row in export.nuclei]
        for stat, value in (
                ('Mean', float(np.mean(values)) if values else None),
                ('Median', float(np.median(values)) if values else None),
                ('IQR', float(np.percentile(values, 75) - np.percentile(values, 25)) if values else None)):
            summary[f'{metric}_{stat}'] = value
    export.images.append(summary)
    assert export.save() == 'complete'
    metadata = {'schema_version': 1, 'status': 'complete', 'morphology_status': 'complete',
                'particle_size_pixels_squared': particle_size, 'processed_files': [source_name], 'skipped_files': []}
    (run / 'nuclei_run.json').write_text(json.dumps(metadata))
    return run, export, raw


def create_intensity(morph, marker='Foci_1_Channel_2', stamp='20260924_100000',
                     ids=None, areas=None, state='complete', legacy=False, fingerprints=None):
    run, export, raw = morph
    channel = int(marker.rsplit('_', 1)[1])
    name = f'Nuclear_Intensity_{stamp}' if legacy else f'Nuclear_Intensity_{marker}_{stamp}'
    output = run.parent / name
    output.mkdir()
    rows = []
    mask = export.images[0]['Mask_name']
    identifiers = {'Dataset': raw.parent.name, 'Dataset_path': str(raw.parent),
                   'Image_name': raw.name, 'Source_file': str(raw), 'Mask_file': str(run / mask),
                   'ID_map': str(run / 'Morphology_QC' / 'ids.tif'), 'Nuclei_run_ID': run.name,
                   'Intensity_run_ID': name, 'Particle_size_px2': export.particle_size,
                   'StarDist_source': str(export.source), 'Marker_folder': marker,
                   'Marker_folder_path': str(run.parent / 'Foci' / marker), 'Marker_channel': channel,
                   'Input_type': 'nd2', 'Projection': 'MAX over all Z planes', 'Z_planes': 3,
                   'Width_px': 1031, 'Height_px': 37, 'Pixel_type': 'uint16'}
    for index, source in enumerate(export.nuclei):
        measurements = {field: channel * 10.0 + index for field in collector.INTENSITY_METRICS}
        rows.append({**identifiers, **measurements, 'Area_px2': areas[index] if areas else source['Area_px2'],
                     'Nucleus_ID': ids[index] if ids else source['Nucleus_ID'],
                     'Nucleus_mask': f'image_0001/nucleus_{source["Nucleus_ID"]}_mask.tif',
                     'ROI': 'image_0001/nucleus.roi', 'Marker_image': 'image_0001/marker.tif',
                     'Numbered_image': 'image_0001/numbered_nuclei.png'})
    summary = {**identifiers, 'Status': 'complete', 'Error': '', **intensity.summarize(rows),
               **{key: export.images[0][key] for key in collector.COUNTS},
               'Nuclei_measured_count': len(rows), 'Failed_nuclei_count': 0}
    metadata = {'Schema_version': 1 if legacy else 2, 'Status': state,
                'Nuclei_run': str(run), 'Intensity_run': str(output),
                'Particle_size_px2': export.particle_size, 'Marker_channel': channel}
    if not legacy:
        metadata.update(Marker_folder=marker, Marker_folder_path=identifiers['Marker_folder_path'])
    if fingerprints is not None:
        metadata['Source_fingerprints'] = fingerprints
    intensity.save_tables(output, rows, [summary], metadata)
    (output / 'intensity_run.json').write_text(json.dumps(metadata))
    return output


def sheet_rows(path, sheet):
    book = load_workbook(path, read_only=True, data_only=False)
    try:
        values = iter(book[sheet].values)
        headers = next(values)
        return [dict(zip(headers, tuple(row) + (None,) * (len(headers) - len(row)))) for row in values]
    finally:
        book.close()


def collect(morph, markers):
    context = collector.scan_source(morph[0].parent.parent)
    bundle = next(bundle for bundle in context['morphology'] if bundle['run'] == morph[0])
    return collector.collect_one(bundle, context, markers, 'input_paths.json')


def test_complete_collection_has_only_spreadsheets_and_exact_source_copies(tmp_path):
    morph = create_morphology(tmp_path / 'experiment, A')
    first = create_intensity(morph)
    second = create_intensity(morph, 'Foci_2_Channel_3')
    before = {path: path.read_bytes() for path in morph[0].parent.rglob('*') if path.is_file()}
    ok, output = collect(morph, ['Foci_1_Channel_2', 'Foci_2_Channel_3'])
    assert ok and output.parent == morph[2].parent and output.name.startswith(collector.OUTPUT_PREFIX)
    assert len(list(output.iterdir())) == 15
    assert all(path.is_file() and path.suffix in ('.csv', '.xlsx') for path in output.iterdir())
    for filename in ('Nuclei_Morphology.xlsx', 'Nuclei_Morphology.csv', 'Nuclei_Images.csv', 'Nuclei_Run_Info.csv'):
        assert (output / filename).read_bytes() == before[morph[0] / filename]
    for marker, source in [('Foci_1_Channel_2', first), ('Foci_2_Channel_3', second)]:
        for path in source.iterdir():
            if path.suffix in ('.csv', '.xlsx'):
                assert (output / collector.copy_name(path, marker)).read_bytes() == before[path]
    nuclei = sheet_rows(output / collector.COMBINED_NAME, 'Nuclei')
    images = sheet_rows(output / collector.COMBINED_NAME, 'Images')
    assert [row['Nucleus_ID'] for row in nuclei] == [2, 5]
    assert [row['Area_px2'] for row in nuclei] == [12, 20]
    assert nuclei[0]['Image_name'] == morph[2].name
    assert nuclei[0]['Morphology_Image_name'] == morph[1].nuclei[0]['Image_name']
    assert nuclei[0]['Foci_1_Channel_2_Marker_Mean'] == 20
    assert nuclei[0]['Foci_2_Channel_3_Marker_Mean'] == 30
    assert images[0]['Nuclei_count_total'] == 3 and images[0]['Non_border_nuclei_count'] == 2
    assert 'Condition' not in nuclei[0] and 'Biological_replicate' not in nuclei[0]
    with (output / 'FIA_Marker_Intensity_Nuclei.csv').open(encoding='utf-8-sig', newline='') as handle:
        assert next(csv.DictReader(handle))['Image_name'] == 'field, WellA2.nd2'
    assert all(path.read_bytes() == data for path, data in before.items())
    assert not list(output.parent.glob('.fia_marker_collection_*'))


def test_repeat_collection_keeps_previous_output(tmp_path):
    morph = create_morphology(tmp_path / 'experiment')
    create_intensity(morph)
    _, first = collect(morph, ['Foci_1_Channel_2'])
    saved = {p.name: p.read_bytes() for p in first.iterdir()}
    _, second = collect(morph, ['Foci_1_Channel_2'])
    assert second != first
    assert {p.name: p.read_bytes() for p in first.iterdir()} == saved


def test_missing_marker_has_blank_values_and_explicit_status(tmp_path):
    morph = create_morphology(tmp_path / 'experiment')
    create_intensity(morph)
    ok, output = collect(morph, ['Foci_1_Channel_2', 'Foci_2_Channel_3'])
    assert ok
    rows = sheet_rows(output / collector.COMBINED_NAME, 'Nuclei')
    assert rows[0]['Foci_2_Channel_3_Marker_Mean'] is None
    assert rows[0]['Foci_2_Channel_3_Availability'] == 'MISSING'
    info = sheet_rows(output / collector.COMBINED_NAME, 'Collection_Info')
    assert any(row['Status'] == 'SUCCESS_WITH_MISSING_INTENSITY' for row in info)
    assert not list(output.glob('Nuclear_Intensity*Foci_2_Channel_3*'))


def test_zero_nuclei_is_a_valid_result(tmp_path):
    morph = create_morphology(tmp_path / 'empty', empty=True)
    create_intensity(morph)
    ok, output = collect(morph, ['Foci_1_Channel_2'])
    assert ok
    assert sheet_rows(output / collector.COMBINED_NAME, 'Nuclei') == []
    row = sheet_rows(output / collector.COMBINED_NAME, 'Images')[0]
    assert row['Non_border_nuclei_count'] == 0
    assert row['Foci_1_Channel_2_Marker_Mean_Mean'] is None


def test_long_run_metadata_preserves_full_csv_despite_excel_cell_limit(tmp_path):
    morph = create_morphology(tmp_path / 'experiment')
    fingerprints = [{'source': 'a' * 300, 'size': index} for index in range(1000)]
    source = create_intensity(morph, fingerprints=fingerprints)
    original = (source / 'Nuclear_Intensity_Run_Info.csv').read_bytes()
    assert len(original) > 131072
    ok, output = collect(morph, ['Foci_1_Channel_2'])
    assert ok
    assert (output / 'Nuclear_Intensity_Run_Info_Foci_1_Channel_2.csv').read_bytes() == original
    assert (output / 'Nuclear_Intensity_Foci_1_Channel_2.xlsx').read_bytes() == (source / 'Nuclear_Intensity.xlsx').read_bytes()
    value = next(row['Value'] for row in sheet_rows(source / 'Nuclear_Intensity.xlsx', 'Run_Info')
                 if row['Parameter'] == 'Source_fingerprints')
    assert len(value) == 32767


@pytest.mark.parametrize('change, message', [({'ids': [2, 99]}, 'ID sets'), ({'areas': [13, 20]}, 'area differs')])
def test_latest_cross_analysis_conflict_never_falls_back_to_older_matching_data(tmp_path, change, message):
    morph = create_morphology(tmp_path / 'experiment')
    create_intensity(morph)
    create_intensity(morph, stamp='20260924_110000', **change)
    ok, output = collect(morph, ['Foci_1_Channel_2'])
    assert not ok
    assert [path.name for path in output.iterdir()] == ['Collection_Report.xlsx']
    info = sheet_rows(output / 'Collection_Report.xlsx', 'Collection_Info')
    assert any(message in str(row['Details']) for row in info)


def test_incomplete_latest_run_can_use_older_complete_run(tmp_path):
    morph = create_morphology(tmp_path / 'experiment')
    complete = create_intensity(morph)
    create_intensity(morph, stamp='20260924_110000', state='running')
    ok, output = collect(morph, ['Foci_1_Channel_2'])
    assert ok
    row = sheet_rows(output / collector.COMBINED_NAME, 'Nuclei')[0]
    assert row['Foci_1_Channel_2_Intensity_run_ID'] == complete.name


def test_wrong_nucleus_run_is_never_selected(tmp_path):
    old = create_morphology(tmp_path / 'experiment')
    new = create_morphology(old[2].parent, stamp='20260924_120000', particle_size=3)
    create_intensity(old, stamp='20260924_140000')
    expected = create_intensity(new, stamp='20260924_130000')
    ok, output = collect(new, ['Foci_1_Channel_2'])
    assert ok
    row = sheet_rows(output / collector.COMBINED_NAME, 'Nuclei')[0]
    assert row['Nuclei_run_ID'] == new[0].name
    assert row['Foci_1_Channel_2_Intensity_run_ID'] == expected.name


def test_stale_workbook_and_duplicate_nucleus_are_rejected(tmp_path):
    morph = create_morphology(tmp_path / 'experiment')
    csv_path = morph[0] / 'Nuclei_Morphology.csv'
    csv_path.write_bytes(csv_path.read_bytes().replace(b',12,', b',13,'))
    with pytest.raises(collector.ValidationError, match='differs from CSV'):
        collector.load_morphology(morph[0])
    morph[1].nuclei.append(dict(morph[1].nuclei[0]))
    morph[1].save()
    with pytest.raises(collector.ValidationError, match='Duplicate nucleus'):
        collector.load_morphology(morph[0])


def test_source_changes_before_publication_only_produce_diagnostic_workbook(tmp_path, monkeypatch):
    morph = create_morphology(tmp_path / 'experiment')
    create_intensity(morph)
    original = collector.write_workbook
    def change_source(path, tables):
        original(path, tables)
        if path.name == collector.COMBINED_NAME:
            source = morph[0] / 'Nuclei_Images.csv'
            source.write_bytes(source.read_bytes() + b'\n')
    monkeypatch.setattr(collector, 'write_workbook', change_source)
    ok, output = collect(morph, ['Foci_1_Channel_2'])
    assert not ok
    assert [p.name for p in output.iterdir()] == ['Collection_Report.xlsx']
    info = sheet_rows(output / 'Collection_Report.xlsx', 'Collection_Info')
    assert not any(row['Status'] in ('SUCCESS', 'SUCCESS_WITH_MISSING_INTENSITY', 'COPIED') for row in info)


def test_legacy_intensity_uses_explicit_channel_label(tmp_path):
    morph = create_morphology(tmp_path / 'experiment')
    create_intensity(morph, legacy=True)
    ok, output = collect(morph, ['Channel_2'])
    assert ok
    assert (output / 'Nuclear_Intensity_Channel_2.xlsx').exists()
    assert sheet_rows(output / collector.COMBINED_NAME, 'Nuclei')[0]['Channel_2_Marker_Mean'] == 20


def test_hidden_and_foci_results_are_not_collected(tmp_path):
    morph = create_morphology(tmp_path / 'experiment')
    create_intensity(morph)
    for name in ('.Final_Nuclei_Mask_20260924_180000', '._Nuclear_Intensity_Foci_1_Channel_2_20260924_190000',
                 'Foci_Masks', 'Foci_1_Channel_2_20260924_200000'):
        folder = morph[0].parent / name
        folder.mkdir()
        (folder / 'bad.csv').write_bytes(b'not a result')
    context = collector.scan_source(morph[2].parent)
    assert len(context['morphology']) == 1 and len(context['intensity']) == 1
    assert context['checks'] == []


def test_main_selects_all_nucleus_runs_into_separate_folders(tmp_path, monkeypatch):
    first = create_morphology(tmp_path / 'experiment')
    second = create_morphology(first[2].parent, stamp='20260924_120000', particle_size=3)
    create_intensity(first)
    create_intensity(second, stamp='20260924_130000')
    manifest = tmp_path / 'input_paths.json'
    manifest.write_text(json.dumps({'paths_to_files': [str(first[2].parent)]}))
    answers = iter(['all', '2', 'all'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    assert collector.main(manifest) == 0
    outputs = list(first[2].parent.glob(collector.OUTPUT_PREFIX + '*'))
    assert len(outputs) == 2
    assert {sheet_rows(output / collector.COMBINED_NAME, 'Nuclei')[0]['Nuclei_run_ID'] for output in outputs} == {
        first[0].name, second[0].name}


def test_module_import_never_loads_an_imagej_engine():
    code = f'''
import importlib.abc
import sys
class BlockImaging(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in ('imagej', 'scyjava', 'jpype', 'nuclear_intensity', 'nuclei_morphology'):
            raise RuntimeError('Unexpected imaging dependency: ' + fullname)
sys.meta_path.insert(0, BlockImaging())
sys.path.insert(0, {str(Path(collector.__file__).parent)!r})
import collect_marker_intensity_results
'''
    subprocess.run([sys.executable, '-c', code], check=True)


@pytest.fixture
def appledouble_filesystem(monkeypatch):
    """Model paired sidecar rename/unlink behavior independently of the host OS."""
    original_replace, original_unlink = Path.replace, os.unlink
    original_scandir, original_workbook = os.scandir, collector.write_workbook
    events = {'moves': [], 'missing_unlinks': []}

    def save_with_sidecars(path, tables):
        original_workbook(path, tables)
        for spreadsheet in path.parent.iterdir():
            if not spreadsheet.name.startswith('.') and spreadsheet.is_file():
                spreadsheet.with_name('._' + spreadsheet.name).write_bytes(b'AppleDouble test metadata')
        (path.parent / '.DS_Store').write_bytes(b'Finder test metadata')
        (path.parent / 'unrelated.csv').write_text('Not part of the collection')

    def replace_with_sidecar(path, target):
        events['moves'].append(path.name)
        result = original_replace(path, target)
        sidecar = path.with_name('._' + path.name)
        if not path.name.startswith('._') and sidecar.exists():
            original_replace(sidecar, Path(target).with_name('._' + Path(target).name))
        return result

    def unlink_with_sidecar(path, *, dir_fd=None):
        try:
            result = original_unlink(path, dir_fd=dir_fd)
        except FileNotFoundError:
            events['missing_unlinks'].append(Path(path).name)
            raise
        path = Path(path)
        if not path.name.startswith('._'):
            try:
                original_unlink(str(path.with_name('._' + path.name)), dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        return result

    class PairedScan:
        def __init__(self, path):
            with original_scandir(path) as scan:
                self.entries = sorted(scan, key=lambda entry: (
                    entry.name.removeprefix('._'), entry.name.startswith('._')))

        def __enter__(self):
            return iter(self.entries)

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(collector, 'write_workbook', save_with_sidecars)
    monkeypatch.setattr(Path, 'replace', replace_with_sidecar)
    monkeypatch.setattr(os, 'unlink', unlink_with_sidecar)
    monkeypatch.setattr(os, 'scandir', PairedScan)
    return events


def test_collection_ignores_sidecars_created_during_export(tmp_path, appledouble_filesystem):
    morph = create_morphology(tmp_path / 'experiment')
    create_intensity(morph)
    appledouble_filesystem['moves'].clear()
    ok, output = collect(morph, ['Foci_1_Channel_2'])
    assert ok
    events = appledouble_filesystem
    assert all(not name.startswith('.') for name in events['moves'])
    assert events['moves'][-1] == collector.COMBINED_NAME
    assert len(events['moves']) == 11
    assert not (output / 'unrelated.csv').exists()
    assert not (output / '.DS_Store').exists()
    assert (output / 'Nuclei_Images.csv').read_bytes() == (morph[0] / 'Nuclei_Images.csv').read_bytes()
    assert len(sheet_rows(output / collector.COMBINED_NAME, 'Nuclei')) == 2
    assert not list(output.parent.glob('.fia_marker_collection_*'))


def test_failed_transfer_cleans_disappearing_sidecars_and_reports_original_error(
        tmp_path, monkeypatch, appledouble_filesystem):
    morph = create_morphology(tmp_path / 'experiment')
    create_intensity(morph)
    previous = morph[2].parent / (collector.OUTPUT_PREFIX + '20000101_000000')
    previous.mkdir()
    (previous / 'preserved.csv').write_bytes(b'previous result')
    paired_replace = Path.replace

    def fail_real_spreadsheet(path, target):
        if path.name == 'Nuclei_Images.csv':
            raise OSError(errno.EIO, 'Simulated spreadsheet write failure', str(path))
        return paired_replace(path, target)

    monkeypatch.setattr(Path, 'replace', fail_real_spreadsheet)
    ok, output = collect(morph, ['Foci_1_Channel_2'])
    assert not ok
    assert '._Nuclei_Morphology.csv' in appledouble_filesystem['missing_unlinks']
    assert [path.name for path in output.iterdir() if not path.name.startswith('.')] == ['Collection_Report.xlsx']
    rows = sheet_rows(output / 'Collection_Report.xlsx', 'Collection_Info')
    status, = [row for row in rows if row['Category'] == 'Collection' and row['Item'] == 'Status']
    assert status['Status'] == 'IO_FAILED'
    assert 'Simulated spreadsheet write failure' in status['Details']
    assert set(output.parent.glob(collector.OUTPUT_PREFIX + '*')) == {previous, output}
    assert (previous / 'preserved.csv').read_bytes() == b'previous result'
    assert not list(output.parent.glob('.fia_marker_collection_*'))


def test_missing_expected_spreadsheet_is_not_silently_ignored(tmp_path):
    staging = tmp_path / 'staging'
    staging.mkdir()
    (staging / collector.COMBINED_NAME).write_bytes(b'test workbook')
    with pytest.raises(FileNotFoundError, match='Nuclei_Images.csv'):
        collector.publish_staging(staging, tmp_path, collector.COMBINED_NAME,
                                  ['Nuclei_Images.csv', collector.COMBINED_NAME])
    assert not list(tmp_path.glob(collector.OUTPUT_PREFIX + '*'))
    assert (staging / collector.COMBINED_NAME).exists()


@pytest.mark.parametrize('cleanup_error', [PermissionError('denied'), FileNotFoundError('missing real file')])
def test_cleanup_errors_do_not_mask_the_original_publication_failure(tmp_path, monkeypatch, capsys, cleanup_error):
    staging = tmp_path / 'staging'
    staging.mkdir()
    (staging / collector.COMBINED_NAME).write_bytes(b'test workbook')
    publication_error = OSError(errno.ENOSPC, 'No space for the spreadsheet')

    def fail_transfer(path, target):
        raise publication_error

    def fail_cleanup(path, onerror):
        onerror(os.unlink, str(path / 'real.csv'), (type(cleanup_error), cleanup_error, None))

    monkeypatch.setattr(Path, 'replace', fail_transfer)
    monkeypatch.setattr(collector.shutil, 'rmtree', fail_cleanup)
    with pytest.raises(OSError) as caught:
        collector.publish_staging(staging, tmp_path, collector.COMBINED_NAME, [collector.COMBINED_NAME])
    assert caught.value is publication_error
    assert 'Could not fully remove incomplete output' in capsys.readouterr().out
