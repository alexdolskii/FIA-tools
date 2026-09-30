"""Physical units, snapshot provenance and disjoint report populations."""


import marker_report_data as reports
import nuclei_morphology as morphology
import numpy as np
import pytest
import spatial_calibration as spatial
import tifffile
from marker_report_plots import plot_rows, plot_specs
from marker_report_statistics import calculate_statistics, observations
from test_collect_marker_intensity_results import (
    collect,
    create_intensity,
    create_morphology,
)
from test_marker_intensity_report import MARKER, RAW, annotated, plate


@pytest.mark.parametrize('unit,factor', [('um', 1), ('µm', 1), ('μm', 1), ('micron', 1),
                                        ('nm', 0.001), ('mm', 1000)])
def test_explicit_units_are_normalized(unit, factor):
    result = spatial.normalize(2, 3, unit)
    assert spatial.calibrated(result)
    assert result['Pixel_size_X_um'] == 2 * factor
    assert spatial.area_um2(10, result) == pytest.approx(60 * factor ** 2)


@pytest.mark.parametrize('x,y,unit', [(1, 1, 'pixel'), (0, 1, 'um'), (-1, 1, 'um'),
                                    (1, None, 'um'), (float('nan'), 1, 'um'),
                                    (1, float('inf'), 'um'), (1, 1, 'inch')])
def test_invalid_or_print_scales_remain_pixels(x, y, unit):
    result = spatial.normalize(x, y, unit)
    assert not spatial.calibrated(result)
    assert spatial.area_um2(12, result) is None


def test_tiff_metadata_and_dpi_are_distinguished(tmp_path):
    pixels = np.zeros((19, 31), np.uint16)
    path = tmp_path / 'imagej.tif'
    tifffile.imwrite(path, pixels, imagej=True, resolution=(4, 2), metadata={'unit': 'um'})
    cal, shape = spatial.read_source(path)
    assert shape == pixels.shape
    assert cal['Pixel_size_X_um'] == 0.25
    assert cal['Pixel_size_Y_um'] == 0.5
    path = tmp_path / 'ome.ome.tif'
    tifffile.imwrite(path, pixels, ome=True, metadata={
        'PhysicalSizeX': 200, 'PhysicalSizeXUnit': 'nm',
        'PhysicalSizeY': 0.4, 'PhysicalSizeYUnit': 'µm'})
    cal, _ = spatial.read_source(path)
    assert cal['Pixel_size_X_um'] == pytest.approx(0.2)
    assert cal['Pixel_size_Y_um'] == pytest.approx(0.4)
    path = tmp_path / 'print.tif'
    tifffile.imwrite(path, pixels, resolution=(300, 300), resolutionunit='INCH')
    assert not spatial.calibrated(spatial.read_source(path)[0])


def test_snapshot_is_bound_to_pixels_and_dimensions(tmp_path):
    path = tmp_path / 'mask.tif'
    tifffile.imwrite(path, np.zeros((12, 17), np.uint8))
    cal = spatial.normalize(0.2, 0.3, 'um', 'test')
    spatial.save_snapshot(path, cal, (12, 17))
    assert spatial.snapshot_calibration(path, (12, 17)) == cal
    with pytest.raises(ValueError, match='no longer matches'):
        spatial.snapshot_calibration(path, (17, 12))
    tifffile.imwrite(path, np.ones((12, 17), np.uint8))
    with pytest.raises(ValueError, match='no longer matches'):
        spatial.snapshot_calibration(path, (12, 17))


def test_native_metadata_exact_identity_and_no_z_requirement(tmp_path):
    assay = tmp_path / 'foci_assay'
    folder = assay / 'Nuclei'
    folder.mkdir(parents=True)
    (assay / 'image_metadata.txt').write_text(
        'Image Name: image1.nd2\n  Width: 17\n  Height: 12\n'
        '  XY processing: native dimensions; no resizing\n'
        '  Pixel Width: 2e2\n  Pixel Height: 400\n  Unit: nm\n')
    assert spatial.projection_calibration(folder / 'image1_nuclei_projection.tif', (12, 17))['Pixel_size_X_um'] == 0.2
    assert not spatial.calibrated(spatial.projection_calibration(folder / 'image10_nuclei_projection.tif', (12, 17)))
    assert not spatial.calibrated(spatial.projection_calibration(folder / 'image1_nuclei_projection.tif', (24, 34)))


def test_isotropic_measurements_preserve_raw_values():
    row = {field: 10.0 for field in morphology.METRICS}
    row.update(Area_px2=400, Centroid_X_px=20, Centroid_Y_px=30, Orientation_deg=40)
    original = dict(row)
    morphology.add_physical_measurements([row], None, spatial.normalize(0.25, 0.25, 'um'))
    assert row['Area_um2'] == 25
    assert row['Perimeter_um'] == 2.5
    assert row['Centroid_X_um'] == 5
    for field, value in original.items():
        assert row[field] == value
    assert row['Circularity_calibrated'] == row['Circularity']


def calibrate_data(data, scales):
    for index, image in enumerate(data['images']):
        cal = spatial.normalize(scales[index], scales[index], 'um') if scales[index] else spatial.uncalibrated()
        image.update(cal)
        rows = [row for row in data['nuclei'] if row['Mask_name'] == image['Mask_name']]
        morphology.add_physical_measurements(rows, None, cal)
        for row in rows:
            row.update(cal)
        for field in spatial.EXTRA_METRICS:
            values = [row[field] for row in rows if row[field] is not None]
            stats = (np.mean(values), np.median(values), np.percentile(values, 75)-np.percentile(values, 25)) if values else (None,) * 3
            for stat, value in zip(('Mean', 'Median', 'IQR'), stats):
                image[field + '_' + stat] = value
    return reports.aggregate(data)


@pytest.mark.parametrize('stats_unit', ['nucleus', 'well'])
def test_mixed_calibration_never_pools_units(stats_unit):
    data = annotated([('A02', [10, 20]), ('A03', [30, 40]),
                      ('A04', [50, 60]), ('A05', [70, 80])])
    data['stats_unit'] = stats_unit
    data = calibrate_data(data, [0.5, None, 0.2, None])
    calculate_statistics(data)
    physical, _ = observations(data, 'Area_um2', 'Control')
    pixels, _ = observations(data, 'Area_px2', 'Control')
    assert physical == ([2.5, 5] if stats_unit == 'nucleus' else [3.75])
    assert pixels == ([30, 40] if stats_unit == 'nucleus' else [35])
    assert observations(data, RAW, 'Control')[0] == ([10, 20, 30, 40] if stats_unit == 'nucleus' else [15, 35])
    assert {row['Mask_name'] for row in data['image_values'] if row['Metric'] == 'Area_um2'} == {'mask_0', 'mask_2'}
    assert {row['Mask_name'] for row in data['image_values'] if row['Metric'] == 'Area_px2'} == {'mask_1', 'mask_3'}
    # Force plot eligibility only to inspect the plotted populations and unit labels.
    for stat in data['statistics']:
        if stat['Metric'] in ('Area_um2', 'Area_px2'):
            stat.update(Status='TESTED', P_Holm=0.01)
    plotted = plot_rows(data)
    assert {row['Mask_name'] for row in plotted if row['Metric'] == 'Area_um2'} == {'mask_0', 'mask_2'}
    assert {row['Mask_name'] for row in plotted if row['Metric'] == 'Area_px2'} == {'mask_1', 'mask_3'}
    assert any('µm²' in row[3] for row in plot_specs(data))


def test_fully_calibrated_has_no_pixel_endpoints_or_duplicate_tests():
    data = calibrate_data(annotated(), [0.5] * 5)
    calculate_statistics(data)
    fields = {row[2] for row in reports.report_specs(data)}
    assert not fields.intersection(spatial.PHYSICAL_FIELDS)
    assert fields.issuperset(spatial.PHYSICAL_FIELDS.values())
    assert len(fields) == 19
    assert len(data['statistics']) == 19
    assert len(data['nuclei']) == 107


def test_physical_area_mismatch_and_conflicting_calibration_rejected():
    cal = spatial.normalize(0.2, 0.4, 'um')
    with pytest.raises(ValueError, match='Physical area'):
        spatial.validate_record({**cal, 'Area_px2': 10, 'Area_um2': 10})
    assert not spatial.compatible(cal, spatial.normalize(0.3, 0.4, 'um'))


def test_calibrated_collection_retains_units_without_originals(tmp_path):
    morph = create_morphology(tmp_path / 'experiment')
    export = morph[1]
    cal = spatial.normalize(0.5, 0.5, 'um', 'test original')
    morphology.add_physical_measurements(export.nuclei, None, cal)
    for row in export.nuclei + export.images:
        row.update(cal)
    for metric in spatial.EXTRA_METRICS:
        values = [row[metric] for row in export.nuclei]
        for stat, value in zip(('Mean', 'Median', 'IQR'),
                               (np.mean(values), np.median(values), np.percentile(values, 75) - np.percentile(values, 25))):
            export.images[0][metric + '_' + stat] = float(value)
    export.save()
    create_intensity(morph)
    ok, path = collect(morph, [MARKER])
    assert ok
    morph[2].unlink()
    data = reports.load_collection(path)
    reports.annotate(data, plate(path / 'plate.xlsx'), [MARKER], 'nucleus')
    reports.aggregate(data)
    assert observations(data, 'Area_um2', 'Control')[0] == [3, 5]
    assert not any(spec[2] == 'Area_px2' for spec in reports.report_specs(data))
    assert data['nuclei'][0]['Area_px2'] == 12


from test_nuclear_intensity import engine as engine  # noqa: E402
from test_nuclear_intensity import make_experiment  # noqa: E402
from test_nuclei_morphology import image_from_pixels  # noqa: E402
from test_nuclei_morphology import imagej_classes as imagej_classes  # noqa: E402


def test_native_anisotropic_geometry_uses_physical_roi(imagej_classes):
    pixels = np.zeros((60, 80), np.uint8)
    pixels[20:40, 20:60] = 255
    mask = image_from_pixels(imagej_classes, pixels, inverted=True)
    labels = None
    try:
        records, labels = morphology.measure_final_mask(mask)
        before = morphology.processor_pixels(mask).copy()
        morphology.add_physical_measurements(records, labels, spatial.normalize(0.25, 0.5, 'um'))
        row = records[0]
        assert row['Area_px2'] == 800
        assert row['Area_um2'] == 100
        assert row['Aspect_ratio'] == pytest.approx(2)
        assert row['Aspect_ratio_calibrated'] == pytest.approx(1)
        assert row['Major_axis_um'] == pytest.approx(row['Minor_axis_um'])
        assert row['Feret_max_um'] == pytest.approx(np.hypot(10, 10))
        assert row['Feret_min_um'] == pytest.approx(10)
        assert row['Perimeter_um'] > 0
        assert row['Circularity_calibrated'] == pytest.approx(min(1, 4*np.pi*100/row['Perimeter_um']**2))
        np.testing.assert_array_equal(before, morphology.processor_pixels(mask))
    finally:
        if labels is not None:
            labels.close()
        mask.close()


def test_native_metadata_and_raw_intensity_are_independent(tmp_path, engine):
    manifest, raw, run, pixels, labels = make_experiment(tmp_path)
    tifffile.imwrite(raw, pixels, ome=True, metadata={
        'axes': 'ZCYX', 'PhysicalSizeX': 250, 'PhysicalSizeXUnit': 'nm',
        'PhysicalSizeY': 0.5, 'PhysicalSizeYUnit': 'µm'}, photometric='minisblack')
    info = engine.inspect(raw, 'tiff-stack', 2)
    assert info['Pixel_size_X_um'] == pytest.approx(0.25)
    assert info['Pixel_size_Y_um'] == pytest.approx(0.5)
    with engine.reader(raw) as reader:
        # Exercise the real OME metadata interface as used by the ND2 reader.
        cal = spatial.bioformats_calibration(reader)
        assert cal['Pixel_size_X_um'] == pytest.approx(0.25)
    marker = engine.marker(raw, 'tiff-stack', 2, info)
    try:
        rows, counts = engine.measure(marker, labels, tmp_path / 'measurement')
        assert counts['Non_border_nuclei_count'] == 2
        for row in rows:
            expected = pixels[:, 1].max(axis=0)[labels == row['Nucleus_ID']]
            assert row['Area_px2'] == len(expected)
            assert row['Marker_RawIntDen'] == pytest.approx(expected.sum())
        saved, shape = spatial.read_source(tmp_path / 'measurement' / 'marker.tif')
        assert spatial.compatible(saved, info)
        assert spatial.calibrated(saved)
        assert shape == labels.shape
    finally:
        marker.close()


def test_native_calibration_survives_nuclei_intensity_and_collection(tmp_path, engine, monkeypatch):
    import collect_marker_intensity_results as collector
    import jpype
    import nuclear_intensity as intensity
    from test_nuclear_intensity import marker_for
    monkeypatch.setattr(morphology, 'jimport', jpype.JClass)
    manifest, raw, run, pixels, _ = make_experiment(tmp_path)
    tifffile.imwrite(raw, pixels, ome=True, metadata={
        'axes': 'ZCYX', 'PhysicalSizeX': 0.25, 'PhysicalSizeY': 0.5}, photometric='minisblack')
    mask_path = next(p for p in run.glob('*.tif') if not p.name.startswith('.'))
    mask = jpype.JClass('ij.IJ').openImage(str(mask_path))
    export = morphology.NucleiMorphologyExport(run, run.parent / 'StarDist_original', 2, engine.version)
    try:
        export.add_image(mask, raw.stem + '_nuclei_projection_StarDist_processed.tif', mask_path.name)
        assert export.save() == 'complete'
    finally:
        mask.close()
    experiment = intensity.discover(manifest)[0]
    record = intensity.inspect_run(experiment, run, 'tiff-stack', marker_for(run), engine, {})
    assert record['eligible'], record['errors']
    output = run.parent / 'Nuclear_Intensity_Foci_1_Channel_2_20260930_180000'
    output.mkdir()
    assert intensity.analyze_run(record, output, 'tiff-stack', engine, output / 'batch.json') == 'complete'
    context = collector.scan_source(raw.parent)
    assert len(context['morphology']) == len(context['intensity']) == 1
    bundle = context['morphology'][0]
    ok, collection = collector.collect_one(bundle, context, [MARKER], str(manifest))
    assert ok
    data = reports.load_collection(collection)
    assert len(data['nuclei']) == 2
    for row in data['nuclei']:
        assert row['Area_um2'] == pytest.approx(row['Area_px2'] * 0.125)
        assert row['Pixel_size_X_um'] == 0.25
        assert row['Pixel_size_Y_um'] == 0.5
    reports.annotate(data, plate(collection / 'plate.xlsx'), [MARKER], 'nucleus')
    reports.aggregate(data)
    assert observations(data, 'Area_um2', 'Control')[0] == [1.5, 2.5]


def test_legacy_nd2_metadata_is_deferred_until_imagej_initialization(tmp_path, monkeypatch):
    folder = tmp_path / 'foci_assay' / 'Nuclei'
    folder.mkdir(parents=True)
    (tmp_path / 'image.nd2').write_bytes(b'original placeholder')
    def unexpected_read(path):
        raise AssertionError('StarDist must not initialize the JVM for legacy ND2 metadata')
    monkeypatch.setattr(spatial, 'read_source', unexpected_read)
    assert spatial.projection_calibration(folder / 'image_nuclei_projection.tif', (20, 30),
                                          allow_bioformats=False) is None
