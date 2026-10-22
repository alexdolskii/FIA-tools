"""pFAK mask/intensity semantics, image-level statistics and portable reports."""

import json
import os
import subprocess
import sys
from unittest.mock import Mock
from zipfile import ZipFile

import collect_marker_intensity_results as collect
import image_exclusions as exclusions
import numpy as np
import pfak_analysis as analysis
import pfak_io as io
import pfak_report as report
import pfak_report_data as report_data
import pytest
import spatial_calibration as spatial
import test_nuclear_intensity as native_tests
import tifffile
from cli_arguments import parse_arguments
from marker_report_statistics import calculate_statistics, observations
from pfak_imagej import save_images
from pfak_measurements import measure_projection, threshold_settings
from test_collect_marker_intensity_results import create_morphology, sheet_rows
from test_marker_intensity_report import plate


def sample():
    projection = np.zeros((12, 15), dtype=np.float32)
    projection[0, 1:4] = [5, 10, 15]
    projection[5:8, 7:10] = 20
    projection[6, 8] = 0  # A hole must remain a hole.
    projection[8, 10] = 25  # Diagonal 8-connectivity, without watershed.
    projection[10, 2] = 50  # Excluded by upper bound.
    labels = np.zeros(projection.shape, dtype=np.uint16)
    labels[0:2, 7:10] = 7
    labels[3:5, 1:3] = 2
    labels[8:10, 12:14] = 9
    return projection, labels


def test_inclusive_mask_raw_and_integrated_density_without_rescaling():
    projection, nuclei = sample()
    calibration = spatial.normalize(.25, .5, 'um')
    row, objects, mask, ids = measure_projection(projection, nuclei, threshold_settings([5, 25]), calibration)
    expected = (projection >= 5) & (projection <= 25)
    assert mask.dtype == np.uint8 and np.array_equal(mask, expected * 255)
    assert ids.dtype == np.uint32 and np.array_equal(ids > 0, expected)
    assert row['pFAK_Object_count'] == 2
    assert row['pFAK_Positive_pixels'] == 12 and row['pFAK_Area'] == 1.5
    assert row['pFAK_RawIntDen'] == 215 and row['pFAK_IntDen'] == 215 * .125
    assert [o['Area_px2'] for o in objects] == [3, 9]
    assert [o['Touches_border'] for o in objects] == [True, False]
    assert row['Normalization_nuclei_count'] == 2 and row['Normalization_nuclear_area'] == 1
    assert row['pFAK_RawIntDen_per_nucleus'] == 107.5
    assert row['pFAK_IntDen_per_nuclear_area'] == 26.875
    assert row['pFAK_Area_per_nucleus'] == .75
    assert row['pFAK_Area_per_nuclear_area'] == 1.5
    assert projection.max() == 50


def test_border_option_changes_only_both_denominators():
    projection, nuclei = sample()
    args = (projection, nuclei, threshold_settings([5, 25]), spatial.uncalibrated())
    without, _, mask1, ids1 = measure_projection(*args)
    with_border, _, mask2, ids2 = measure_projection(*args, include_border=True)
    assert without['Normalization_nuclei_count'] == 2 and with_border['Normalization_nuclei_count'] == 3
    assert without['Normalization_nuclear_area_px2'] == 8 and with_border['Normalization_nuclear_area_px2'] == 14
    assert with_border['pFAK_RawIntDen_per_nucleus'] == pytest.approx(215 / 3)
    assert with_border['pFAK_IntDen_per_nuclear_area'] == pytest.approx(215 / 14)
    assert np.array_equal(mask1, mask2) and np.array_equal(ids1, ids2)
    for field in ('pFAK_Area', 'pFAK_RawIntDen', 'pFAK_IntDen', 'pFAK_Object_count'):
        assert without[field] == with_border[field]
    assert without['Non_border_nuclei_count'] == with_border['Non_border_nuclei_count'] == 2


def test_empty_mask_and_zero_denominators_have_distinct_meanings():
    projection, ids = sample()
    row, objects, mask, _ = measure_projection(projection, ids * 0, threshold_settings([100]), spatial.uncalibrated())
    assert row['pFAK_RawIntDen'] == row['pFAK_IntDen'] == row['pFAK_Area'] == row['pFAK_Object_count'] == 0
    assert objects == [] and not mask.any() and row['pFAK_Median_object_area'] is None
    for metric in ('pFAK_RawIntDen', 'pFAK_IntDen', 'pFAK_Area'):
        assert row[metric + '_per_nucleus'] is None and row[metric + '_per_nuclear_area'] is None


@pytest.mark.parametrize('values', [[], [1, 2, 3], [-1], [float('nan')], [float('inf')], [1, 0], [1, float('nan')], [1e40]])
def test_invalid_thresholds(values):
    with pytest.raises(ValueError):
        threshold_settings(values)


def test_float32_threshold_precision_and_unbounded_upper():
    limits = threshold_settings([.1, float('inf')])
    assert limits['requested_lower'] == .1 and limits['lower'] == float(np.float32(.1))
    assert limits['upper_unbounded'] and limits['requested_upper'] is None
    assert limits['upper'] == np.finfo(np.float32).max


def test_lossless_saved_projection_mask_ids_and_calibration(tmp_path):
    projection, nuclei = sample()
    projection[1, 1] = 120000
    calibration = spatial.normalize(.4, .7, 'um')
    _, objects, mask, ids = measure_projection(projection, nuclei, threshold_settings([5]), calibration)
    folder = tmp_path / 'image'
    saved = save_images(folder, projection, mask, ids, nuclei, calibration, objects)
    assert len(saved) == 5
    assert np.array_equal(tifffile.imread(folder / 'pFAK_SUM32.tif'), projection)
    assert np.array_equal(tifffile.imread(folder / 'pFAK_mask.tif'), mask)
    assert np.array_equal(tifffile.imread(folder / 'pFAK_objects.tif'), ids)
    assert spatial.compatible(spatial.tiff_calibration(folder / 'pFAK_SUM32.tif')[0], calibration)


class FakeEngine:
    """Replace external file decoding only; real masks, measurements and exports run."""
    version = 'test ImageJ'
    bioformats_version = 'test BioFormats'
    gateway = None

    def __init__(self):
        self.read = []

    def inspect(self, path, mode, channel):
        assert channel == 3 and mode == 'nd2'
        self.read.append(path.name)
        return {**spatial.normalize(.2, .3, 'um'), 'Width_px': 30, 'Height_px': 20,
                'Z_planes': 3, 'Z_step_um': .5, 'Pixel_type': 'uint16'}

    def labels(self, mask, ids, info):
        return tifffile.imread(ids)

    def projection(self, path, channel, info):
        result = np.zeros((20, 30), dtype=np.float32)
        result[2:4, 2:5] = 100000
        result[6:8, 18:21] = 250000
        result[0, 0] = 120000
        return result


def experiment(tmp_path):
    run, export, raw = create_morphology(tmp_path / 'experiment')
    ids = np.zeros((20, 30), dtype=np.uint16)
    ids[0:2, 2:5] = 7
    ids[4:7, 7:11] = 2
    ids[12:16, 20:25] = 5
    mask = run / export.images[0]['Mask_name']
    qc = run / 'Morphology_QC'
    qc.mkdir()
    tifffile.imwrite(mask, np.where(ids, 0, 255).astype(np.uint8))
    tifffile.imwrite(qc / (mask.stem + '_ids.tif'), ids)
    manifest = tmp_path / 'input.json'
    manifest.write_text(json.dumps({'paths_to_files': [str(raw.parent)]}))
    return run, raw, manifest


@pytest.fixture
def pfak_run(tmp_path):
    run, raw, manifest = experiment(tmp_path)
    engine = FakeEngine()
    before = raw.read_bytes()
    assert analysis.main(['-i', str(manifest), '--channel', '3', '-t', '100000', '200000'], lambda: engine) == 0
    assert raw.read_bytes() == before and engine.read == [raw.name]
    output = next(run.parent.glob(io.PREFIX + '*'))
    return output, run, raw, manifest


def test_analysis_preserves_provenance_and_both_intensity_metrics(pfak_run):
    output, run, _raw, _ = pfak_run
    data = io.load_run(output)
    assert data['metadata']['Status'] == 'SUCCESS'
    row = data['images'][0]
    assert row['pFAK_RawIntDen'] == 720000 and row['pFAK_IntDen'] == pytest.approx(43200)
    assert row['pFAK_Object_count'] == 2 and row['pFAK_Positive_pixels'] == 7
    assert row['Normalization_nuclei_count'] == 2 and row['Normalization_nuclear_area_px2'] == 32
    assert row['pFAK_RawIntDen_per_nucleus'] == 360000
    assert row['pFAK_IntDen_per_nuclear_area'] == pytest.approx(22500)
    assert row['Channel'] == 3 and row['Projection'] == 'SUM'
    assert (run.parent / analysis.JOURNAL).exists()
    assert data['metadata']['Include_border_nuclei'] is False
    with (output / 'Images.csv').open('a') as handle:
        handle.write('\n')
    with pytest.raises(ValueError, match='changed after analysis'):
        io.load_run(output)


def test_failed_missing_mask_is_not_a_zero_measurement_and_other_images_continue(tmp_path):
    run, raw, manifest = experiment(tmp_path)
    (raw.parent / 'field_WellA3.nd2').write_bytes(b'new image without nuclei preparation')
    assert analysis.main(['-i', str(manifest), '--channel', '3', '-t', '100000'], FakeEngine) == 1
    output = next(run.parent.glob(io.PREFIX + '*'))
    data = io.load_run(output)
    assert data['metadata']['Status'] == 'PARTIAL'
    assert len(data['images']) == len(data['failures']) == 1
    assert data['failures'][0]['Image_name'] == 'field_WellA3.nd2'
    assert data['images'][0]['Image_name'] == raw.name


def test_real_pdf_report_embeds_all_graphs_and_objects_without_png_exports(pfak_run):
    source, run, raw, _ = pfak_run
    plate(run.parent / 'layout.xlsx')
    ok, output = report.create_report(source, raw.parent, min_nuclei=2, stats_unit='image')
    assert ok, (output / 'report_status.json').read_text()
    pdfs = list((output / 'Plots').glob('*.pdf'))
    assert len(pdfs) == 14 and all(p.read_bytes().startswith(b'%PDF') for p in pdfs)
    assert (output / 'Plots' / 'pFAK_Object_area_um2.pdf').is_file()
    assert not list(output.rglob('*.png'))
    with ZipFile(output / report.WORKBOOK) as archive:
        assert len([name for name in archive.namelist() if name.startswith('xl/media/')]) == len(pdfs)
    rows = sheet_rows(output / report.WORKBOOK, 'Objects')
    assert len(rows) == 2 and rows[0]['Group'] == 'Control'
    plot_rows = sheet_rows(output / report.WORKBOOK, 'Plot_Data')
    assert len([r for r in plot_rows if r['Observation'] == 'object']) == 2
    stats = sheet_rows(output / report.WORKBOOK, 'Statistics')
    assert len(stats) == 13 and all(r['Effective_unit'] == 'image' for r in stats)
    assert all(r['Family_size_planned'] == 13 for r in stats)
    assert all(r['N_control'] <= 1 for r in stats)
    exported = {path.stem: collect.csv_snapshot(path, {}, [])[0:2]
                for path in output.glob('*.csv')}
    collect.verify_workbook(output / report.WORKBOOK, {}, exported)


def test_minimum_filter_order_style_and_object_population(pfak_run):
    source, run, _raw, _ = pfak_run
    template = plate(run.parent / 'layout.xlsx')
    data = report_data.prepare(io.load_run(source), template, None, None, 3)
    assert data['images'] == data['objects'] == []
    assert len(data['calibration_images_before_filter']) == 1
    assert data['plot_style']['images'][0]['Point_edgecolor'] == '#D62728'
    assert len(data['metric_specs']) == 13
    assert data['groups'] == ['Control', 'Treatment']


def test_later_processing_exclusion_removes_old_pfak_results(pfak_run):
    source, run, raw, _ = pfak_run
    exclusions.exclude_image(run.parent, raw, 'INFERENCE', 'failed in later segmentation')
    data = io.load_run(source)
    assert data['images'] == data['objects'] == []
    assert len(data['processing_exclusions']) == 1
    template = plate(run.parent / 'layout.xlsx')
    report_data.prepare(data, template, None, None, 0)
    assert data['metadata']['Area_units'] == ['um2']
    assert any('IntDen_um2' in spec[2] for spec in data['metric_specs'])


def test_missing_excel_produces_actionable_report_diagnostics(pfak_run):
    source, _, raw, _ = pfak_run
    ok, output = report.create_report(source, raw.parent)
    assert not ok
    status = json.loads((output / 'report_status.json').read_text())
    assert status['Error_code'] == 'MISSING_PLATE_MAP'
    assert 'fia_assay' in status['Required_action']


def test_statistics_use_images_or_equal_image_weight_wells_not_objects(pfak_run):
    source, run, _, _ = pfak_run
    template = plate(run.parent / 'layout.xlsx', {'A02': ('Control', True), 'A03': ('Control', True), 'A04': ('Treatment', False)})
    data = io.load_run(source)
    original = data['images'][0]
    data['images'], data['objects'] = [], []
    for index, (well, value) in enumerate([('A02', 1), ('A02', 9), ('A03', 20), ('A04', 10), ('A04', 30)]):
        row = dict(original, Well=well, Image_name=f'field{index}_Well{well}.nd2', Mask_name=f'mask_{index}', pFAK_RawIntDen=value)
        data['images'].append(row)
    report_data.prepare(data, template, 'image', None, 0)
    values, unit = observations(data, 'pFAK_RawIntDen', 'Control')
    assert values == [1, 9, 20] and unit == 'image'
    data['stats_unit'] = 'well'
    values, unit = observations(data, 'pFAK_RawIntDen', 'Control')
    assert values == [5, 20] and unit == 'well'
    calculate_statistics(data)
    assert all(row['N_control'] == 2 for row in data['statistics'])


@pytest.mark.parametrize('command', ['quantify_pfak', 'fia_pfak_report'])
def test_help_needs_no_imaging_runtime_and_no_storage(tmp_path, command):
    script = f'import __init__ as app; app.{command}()'
    env = dict(os.environ, FIA_RUNTIME_HOME=str(tmp_path / 'runtime'))
    result = subprocess.run([sys.executable, '-c', script, '--help'], capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'runtime').exists()
    assert '-I' not in result.stdout


def test_cli_rejects_bad_limits_channels_and_object_statistics():
    for args in (['-t', '2', '1'], ['-t', '2', '--channel', '0'], ['-t', 'nan'], ['-t', '1', '2', '3']):
        with pytest.raises(SystemExit):
            parse_arguments('quantify_pfak', ['-i', 'input.json', *args])
    with pytest.raises(SystemExit):
        parse_arguments('fia_pfak_report', ['-i', 'input.json', '--stats-unit', 'object'])


@pytest.fixture
def native_engine(monkeypatch):
    return native_tests.engine.__wrapped__(monkeypatch)


def test_native_sum_channel_calibration_and_imagej_intdens(tmp_path, native_engine):
    import jpype
    from pfak_imagej import ImageJEngine

    pixels = np.zeros((3, 3, 6, 7), dtype=np.uint16)
    pixels[:, 0] = 65535  # An unrelated bright channel must not contribute.
    pixels[:, 2] = np.array([10000, 40000, 60000], dtype=np.uint16)[:, None, None]
    pixels[:, 2, 1, 1] = 0
    pixels[:, 2, 0, 0] = 60000
    source = tmp_path / 'three_channels.ome.tif'
    tifffile.imwrite(source, pixels, ome=True, photometric='minisblack', metadata={
        'axes': 'ZCYX', 'PhysicalSizeX': .25, 'PhysicalSizeXUnit': 'µm',
        'PhysicalSizeY': .5, 'PhysicalSizeYUnit': 'µm',
        'PhysicalSizeZ': .6, 'PhysicalSizeZUnit': 'µm'})
    native = ImageJEngine(initialize=False)
    info = native.inspect(source, 'tiff-stack', 3)
    projection = native.projection(source, 3, info)
    assert projection.dtype == np.float32
    assert np.array_equal(projection, pixels[:, 2].astype(np.float32).sum(axis=0))
    assert info['Z_step_um'] == .6 and projection.max() == 180000
    ids = np.zeros(projection.shape, dtype=np.uint16)
    ids[2:4, 2:4] = 1
    row, _, mask, _ = measure_projection(projection, ids, threshold_settings([100000, 125000]), info)
    assert row['pFAK_Positive_pixels'] == 40 and row['pFAK_RawIntDen'] == 4400000

    J = jpype.JClass
    image = J('ij.ImagePlus')('SUM', J('ij.process.FloatProcessor')(
        7, 6, jpype.JArray(jpype.JFloat)(projection.ravel())))
    try:
        binary = J('ij.process.ByteProcessor')(7, 6, jpype.JArray(jpype.JByte)(mask.ravel().view(np.int8)), None)
        binary.setThreshold(255, 255, binary.NO_LUT_UPDATE)
        image.setRoi(J('ij.plugin.filter.ThresholdToSelection')().convert(binary))
        spatial.imagej_calibration(image, info, J)
        table = J('ij.measure.ResultsTable')()
        flags = J('ij.measure.Measurements')
        J('ij.plugin.filter.Analyzer')(image, flags.AREA | flags.INTEGRATED_DENSITY, table).measure()
        assert row['pFAK_RawIntDen'] == table.getValue('RawIntDen', 0)
        assert row['pFAK_IntDen'] == table.getValue('IntDen', 0)
        assert row['pFAK_Area'] == table.getValue('Area', 0)
    finally:
        image.close()


def test_latest_keeps_threshold_and_border_profiles_separate_and_archives_logs(pfak_run):
    first, run, raw, manifest = pfak_run
    common = ['-i', str(manifest), '--channel', '3']
    for options in (['-t', '100000', '200000'],
                    ['-t', '100000', '200000', '--include-border-nuclei'],
                    ['-t', '110000']):
        assert analysis.main(common + options, FakeEngine) == 0
    found = report.discover_runs(raw.parent, 'latest', Mock(), Mock())
    assert len(found) == 3
    assert first not in {item['path'] for item in found}
    assert len({item['profile'] for item in found}) == 3
    assert len(report.discover_runs(raw.parent, 'all', Mock(), Mock())) == 4
    assert len(list((run.parent / 'logs').glob('3_pfak_analysis_*.log'))) == 3


def test_physical_and_uncalibrated_area_endpoints_never_pool(pfak_run):
    source, run, _, _ = pfak_run
    data = io.load_run(source)
    original = data['images'][0]
    row = {**original, **spatial.uncalibrated(), 'Area_unit': 'px2',
           'Image_name': 'second_WellA2.nd2', 'Mask_name': 'second_mask',
           'pFAK_Area': 7, 'pFAK_IntDen': 720000, 'pFAK_Area_per_nucleus': 3.5}
    data['images'].append(row)
    template = plate(run.parent / 'layout.xlsx')
    report_data.prepare(data, template, 'image', None, 0)
    for field, expected in [('pFAK_Area_um2', [.42]), ('pFAK_Area_px2', [7]),
                            ('pFAK_IntDen_um2', [43200]), ('pFAK_IntDen_px2', [720000])]:
        values, unit = observations(data, field, 'Control')
        assert values == pytest.approx(expected) and unit == 'image'
    assert len(observations(data, 'pFAK_RawIntDen', 'Control')[0]) == 2
