"""Native crash isolation and durable exclusions across the FIA analysis chain."""

import json
import os
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import collect_marker_intensity_results as collect
import image_exclusions as exclusions
import marker_intensity_report as report
import marker_report_data as report_data
import nuclear_intensity as intensity
import numpy as np
import pytest
import tifffile
from stardist_worker import ImageInferenceError, IsolatedStarDist, checked_normalize
from test_collect_marker_intensity_results import (
    create_intensity,
    create_morphology,
    sheet_rows,
)
from test_marker_intensity_report import MARKER, plate
from test_nuclear_intensity import make_experiment, marker_for

stage2 = sys.modules['nuclei_mask_generation']


def normalize(image):
    low, high = np.percentile(image, (3, 99.8))
    return (image.astype(np.float32) - low) / (high - low + 1e-20)


def test_degenerate_percentiles_are_excluded_but_real_zero_images_remain_valid():
    image = np.zeros((512, 512), dtype=np.uint8)
    np.testing.assert_array_equal(checked_normalize(image, normalize), np.zeros_like(image))
    image[0, 0] = 255
    with pytest.raises(ImageInferenceError, match='Degenerate normalization'):
        checked_normalize(image, normalize)
    ordinary = np.arange(256, dtype=np.uint8).reshape(16, 16)
    np.testing.assert_array_equal(checked_normalize(ordinary, normalize), normalize(ordinary))


def test_nonfinite_normalized_input_cannot_reach_model():
    image = np.arange(256, dtype=np.uint8).reshape(16, 16)
    with pytest.raises(ImageInferenceError, match='NaN or infinity'):
        checked_normalize(image, lambda x: np.full(x.shape, np.nan))


@pytest.mark.skipif(os.name == 'nt', reason='POSIX abort signal fixture')
def test_native_abort_does_not_kill_parent_and_worker_restarts(tmp_path):
    worker = tmp_path / 'fake_worker.py'
    worker.write_text('''import json, os, sys, resource
import numpy as np
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
print(json.dumps({'status': 'ready'}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    image = np.load(request['input'])
    if image[0, 0] == 99:
        print('ClipperLib: Coordinate outside allowed range', file=sys.stderr, flush=True)
        os.abort()
    np.save(request['output'], np.ones(image.shape, dtype=np.uint16))
    print(json.dumps({'status': 'ok'}), flush=True)
''')
    model = IsolatedStarDist('fake', [sys.executable, str(worker)])
    try:
        model.predict_instances(np.ones((8, 8)), nms_thresh=.9, prob_thresh=.7)
        first_pid = model.process.pid
        with pytest.raises(ImageInferenceError, match='Coordinate outside allowed range'):
            model.predict_instances(np.full((8, 8), 99), nms_thresh=.9, prob_thresh=.7)
        assert model.process is None
        labels, _ = model.predict_instances(np.ones((8, 8)), nms_thresh=.9, prob_thresh=.7)
        assert model.process.pid != first_pid
        assert labels.dtype == np.uint16 and np.all(labels == 1)
    finally:
        model.close()
    assert not model.folder.exists()


def test_skipped_image_is_durable_and_reuse_accepts_only_accounted_masks(tmp_path, monkeypatch):
    source = tmp_path / 'experiment' / 'fia_assay' / 'Nuclei'
    source.mkdir(parents=True)
    good = np.arange(256, dtype=np.uint8).reshape(16, 16)
    bad = np.zeros((512, 512), dtype=np.uint8)
    bad[0, 0] = 255
    for name, image in [('bad', bad), ('good', good)]:
        tifffile.imwrite(source / f'{name}_nuclei_projection.tif', image)
    model = MagicMock()
    model.predict_instances.return_value = (np.ones(good.shape, dtype=np.uint16), {})
    monkeypatch.setattr(stage2, 'normalize', normalize)
    monkeypatch.setattr(stage2.StarDist2D, 'from_pretrained', lambda name: model)
    run = Path(stage2.find_nuclei([str(source)])[0])
    metadata = json.loads((run / stage2.STARDIST_METADATA).read_text())
    assert metadata['status'] == 'complete_with_exclusions'
    assert metadata['excluded_files'] == ['bad_nuclei_projection.tif']
    assert model.predict_instances.call_count == 1
    assert (source.parent / 'logs' / '2_excluded_images.log').is_file()
    assert stage2.inspect_stardist_folder(run, source, stage2.source_fingerprints(source))['usable']
    # Fixing/replacing a file does not silently undo the experiment's exclusion.
    tifffile.imwrite(source / 'bad_nuclei_projection.tif', good)
    second = Path(stage2.find_nuclei([str(source)])[0])
    assert model.predict_instances.call_count == 2
    assert not (second / 'bad_nuclei_projection_StarDist_processed.tif').exists()
    assert len((source.parent / 'logs' / '2_excluded_images.log').read_text().splitlines()) == 1


def test_inference_exception_continues_and_closes_model(tmp_path, monkeypatch):
    source = tmp_path / 'experiment' / 'fia_assay' / 'Nuclei'
    source.mkdir(parents=True)
    image = np.arange(256, dtype=np.uint8).reshape(16, 16)
    for name in ['a', 'b']:
        tifffile.imwrite(source / f'{name}_nuclei_projection.tif', image)
    model = MagicMock()
    model.predict_instances.side_effect = [ImageInferenceError('native crash'), (image.astype(np.uint16), {})]
    monkeypatch.setattr(stage2, 'normalize', normalize)
    monkeypatch.setattr(stage2.StarDist2D, 'from_pretrained', lambda name: model)
    run = Path(stage2.find_nuclei([str(source)])[0])
    assert len(list(run.glob('*.tif'))) == 1
    assert len(exclusions.load_exclusions(run)) == 1
    model.close.assert_called_once()


def test_model_initialization_failure_never_blacklists_inputs(tmp_path, monkeypatch):
    source = tmp_path / 'experiment' / 'fia_assay' / 'Nuclei'
    source.mkdir(parents=True)
    tifffile.imwrite(source / 'good_nuclei_projection.tif', np.arange(256, dtype=np.uint8).reshape(16, 16))
    monkeypatch.setattr(stage2, 'normalize', normalize)
    factory = MagicMock(side_effect=RuntimeError('Cannot load model weights'))
    monkeypatch.setattr(stage2.StarDist2D, 'from_pretrained', factory)
    with pytest.raises(RuntimeError, match='Cannot load model weights'):
        stage2.find_nuclei([str(source)])
    assert not (source.parent / exclusions.MANIFEST).exists()


def test_worker_io_failure_stops_without_excluding_image(tmp_path, monkeypatch):
    source = tmp_path / 'experiment' / 'fia_assay' / 'Nuclei'
    source.mkdir(parents=True)
    tifffile.imwrite(source / 'good_nuclei_projection.tif', np.arange(256, dtype=np.uint8).reshape(16, 16))
    worker = tmp_path / 'unwritable_worker.py'
    worker.write_text('''import json, sys
print(json.dumps({'status': 'ready'}), flush=True)
for line in sys.stdin:
    print(json.dumps({'status': 'fatal', 'error': 'saving mask: OSError: No space left on device'}), flush=True)
''')
    model = IsolatedStarDist('fake', [sys.executable, str(worker)])
    monkeypatch.setattr(stage2, 'normalize', normalize)
    monkeypatch.setattr(stage2.StarDist2D, 'from_pretrained', lambda name: model)
    with pytest.raises(RuntimeError, match='No space left on device') as caught:
        stage2.find_nuclei([str(source)])
    assert not isinstance(caught.value, ImageInferenceError)
    assert not (source.parent / exclusions.MANIFEST).exists()
    assert not model.folder.exists()


def test_registry_is_exact_image_scoped_and_malformed_registry_fails_closed(tmp_path):
    assay = tmp_path / 'one' / 'fia_assay'
    assay.mkdir(parents=True)
    source = assay / 'stamp_WellA02_PointA02_0000_Seq0001_nuclei_projection.tif'
    source.write_bytes(b'source')
    records = exclusions.exclude_image(assay, source, 'INFERENCE', 'failed')
    key = 'stamp_WellA02_PointA02_0000_Seq0001'
    for name in [key + '.nd2', key + '_nuclei_projection_StarDist_processed_processed.tif',
                 'processed_' + key + '_foci_projection.tif']:
        assert exclusions.image_key(name) in records
    assert exclusions.image_key(key.replace('0000', '0002') + '.nd2') not in records
    assert not exclusions.load_exclusions(tmp_path / 'two')
    (assay / exclusions.MANIFEST).write_text('{broken')
    with pytest.raises(ValueError):
        exclusions.load_exclusions(assay)


def test_intensity_does_not_open_excluded_original_even_with_old_final_mask(tmp_path):
    manifest, raw, run, _, _ = make_experiment(tmp_path)
    exclusions.exclude_image(run.parent, raw, 'INFERENCE', 'failed')
    experiment = intensity.discover(manifest)[0]
    engine = MagicMock()
    result = intensity.inspect_run(experiment, run, 'tiff-stack', marker_for(run), engine, {})
    assert result['pairs'] == []
    engine.inspect.assert_not_called()
    engine.labels.assert_not_called()


def two_images(tmp_path):
    morph = create_morphology(tmp_path / 'experiment')
    run, export, raw = morph
    output = create_intensity(morph)
    tables, _, _ = collect.read_tables(output, 'intensity', {})
    meta = json.loads((output / 'intensity_run.json').read_text())
    second = raw.with_name(raw.name.replace('WellA2', 'WellA3'))
    second.write_bytes(b'second source')
    # Duplicate valid exported measurements while changing every image identifier.
    def clone(row):
        return {key: ('A3' if key == 'Well' else value.replace('WellA2', 'WellA3')) if isinstance(value, str) else value
                for key, value in deepcopy(row).items()}
    export.images += [clone(row) for row in export.images]
    export.nuclei += [clone(row) for row in export.nuclei]
    export.save()
    manifest = json.loads((run / 'nuclei_run.json').read_text())
    manifest['processed_files'] += [name.replace('WellA2', 'WellA3') for name in manifest['processed_files']]
    (run / 'nuclei_run.json').write_text(json.dumps(manifest))
    intensity.save_tables(output, tables['Nuclei'][1] + [clone(row) for row in tables['Nuclei'][1]],
                          tables['Images'][1] + [clone(row) for row in tables['Images'][1]], meta)
    return morph, second


@pytest.mark.parametrize('exclude_before_collection', [True, False])
def test_collection_and_report_exclude_existing_measurements(tmp_path, exclude_before_collection):
    morph, second = two_images(tmp_path)
    run, _, raw = morph
    if exclude_before_collection:
        exclusions.exclude_image(run.parent, raw, 'INFERENCE', 'failed later')
    context = collect.scan_source(raw.parent)
    bundle = context['morphology'][0]
    ok, output = collect.collect_one(bundle, context, [MARKER], 'input_paths.json')
    assert ok
    if not exclude_before_collection:
        exclusions.exclude_image(run.parent, raw, 'INFERENCE', 'failed later')
    data = report_data.load_collection(output)
    assert [row['Image_name'] for row in data['images']] == [second.name]
    assert len(data['nuclei']) == 2
    assert len(data['processing_exclusions']) == 1
    template = plate(output / 'plate.xlsx')
    report_data.annotate(data, template, [MARKER], None)
    report_data.filter_images(data, 0)
    assert data['excluded_images'] == []  # Processing failures are not min-nuclei exclusions.
    assert next(row for row in data['image_filter_summary'] if row['Group'] == 'Control')['Processing_excluded'] == 1
    if not exclude_before_collection:
        ok, result = report.create_report(output, raw.parent, markers=[MARKER], stats_unit='nucleus', min_nuclei=1)
        assert ok
        status = json.loads((result / 'report_status.json').read_text())
        assert status['Images'] == 1 and status['Processing_excluded'] == 1
        assert status['Images_excluded'] == 0
        plotted = sheet_rows(result / report.WORKBOOK, 'Plot_Data')
        assert all(row.get('Image_name') != raw.name for row in plotted)
        audit = sheet_rows(result / report.WORKBOOK, 'Processing_Exclusions')
        assert audit[0]['Image_key'] == raw.stem and audit[0]['Group'] == 'Control'
    if exclude_before_collection:
        archive = tmp_path / 'portable_archive'
        shutil.copytree(output, archive)
        shutil.rmtree(raw.parent)
        portable = report_data.load_collection(archive)
        assert len(portable['images']) == 1 and len(portable['processing_exclusions']) == 1


def test_exclusion_snapshot_tampering_is_rejected(tmp_path):
    morph, _ = two_images(tmp_path)
    run, _, raw = morph
    exclusions.exclude_image(run.parent, raw, 'INFERENCE', 'failed')
    context = collect.scan_source(raw.parent)
    ok, output = collect.collect_one(context['morphology'][0], context, [MARKER], 'input_paths.json')
    assert ok
    exclusions.write_manifest(output / exclusions.MANIFEST, {})
    with pytest.raises(collect.ValidationError, match='snapshot'):
        report_data.load_collection(output)


def test_all_images_excluded_from_old_collection_are_unknown_not_zero(tmp_path):
    morph, second = two_images(tmp_path)
    run, _, raw = morph
    context = collect.scan_source(raw.parent)
    ok, output = collect.collect_one(context['morphology'][0], context, [MARKER], 'input_paths.json')
    assert ok
    for path in [raw, second]:
        exclusions.exclude_image(run.parent, path, 'INFERENCE', 'failed later')
    plate(output / 'plate.xlsx')
    ok, result = report.create_report(output, raw.parent, markers=[MARKER], stats_unit='nucleus')
    assert ok
    assert sheet_rows(result / report.WORKBOOK, 'Images') == []
    assert sheet_rows(result / report.WORKBOOK, 'Plot_Data') == []
    assert len(sheet_rows(result / report.WORKBOOK, 'Processing_Exclusions')) == 2


def test_foci_paths_exclude_old_nuclei_and_marker_masks(tmp_path, monkeypatch):
    root = tmp_path / 'experiment'
    run = root / 'fia_assay' / 'Final_Nuclei_Mask_20261009_120000'
    run.mkdir(parents=True)
    marker = run.parent / 'Foci_Masks' / 'Foci_1_Channel_2_20261009_120000'
    marker.mkdir(parents=True)
    for key in ['bad', 'good']:
        (run / f'{key}_nuclei_projection_StarDist_processed_processed.tif').write_bytes(b'mask')
        (marker / f'processed_{key}_foci_projection.tif').write_bytes(b'mask')
    raw = root / 'bad.nd2'
    raw.write_bytes(b'raw')
    exclusions.exclude_image(run.parent, raw, 'INFERENCE', 'failed')
    foci = sys.modules['foci_quantification']
    monkeypatch.setattr(foci, 'get_nuclei_mask_folder', lambda path: str(run))
    monkeypatch.setattr(foci, 'get_latest_foci_folders', lambda path: {'Foci_1_Channel_2': str(marker)})
    paths, channels = foci.gather_paths_and_channels(str(root))
    assert len(paths) == 1 and Path(paths[0]).name.startswith('good_')
    assert set(channels) == {'good'}
    generation = sys.modules['foci_mask_generation']
    prepared = run.parent / 'markers' / 'Foci_1_Channel_2'
    prepared.mkdir(parents=True)
    (prepared / 'bad_foci_projection.tif').write_bytes(b'bad')
    initialize = MagicMock()
    monkeypatch.setattr(generation, 'initialize_imagej', initialize)
    generation.filter_foci({'fia_assay_folder': str(run.parent), 'foci_folder': str(prepared.parent)},
                           'Foci_1_Channel_2', 20)
    initialize.assert_not_called()
