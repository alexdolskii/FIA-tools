"""Current-batch collection must never rediscover older or unrelated measurements."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import collect_marker_intensity_results as collect
import marker_report_data
import nuclear_intensity as app
import numpy as np
import pytest
from plate_helpers import M1, M2
from test_collect_marker_intensity_results import create_intensity, create_morphology
from test_nuclear_intensity import make_experiment


def selection(morphology, output, marker=M1, status='complete'):
    return {'source': str(morphology[0]), 'output': str(output), 'marker': marker, 'status': status}


def test_exact_outputs_separate_experiments_and_nucleus_runs_without_questions(tmp_path, monkeypatch):
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('Automatic collection must not prompt'))
    morphs = [create_morphology(tmp_path / 'A'),
              create_morphology(tmp_path / 'A', stamp='20260924_120000'),
              create_morphology(tmp_path / 'B')]
    selections = []
    for index, morph in enumerate(morphs):
        chosen = create_intensity(morph, stamp=f'20260924_1{index}0000')
        selections.append(selection(morph, chosen))
        create_intensity(morph, stamp=f'20260924_1{index}5000')  # Newer is deliberately not selected.
    second = create_intensity(morphs[0], marker=M2)
    selections.append(selection(morphs[0], second, M2))
    results = []
    assert collect.collect_measurement_batch(tmp_path / 'input.json', selections, results)
    assert len(results) == 3 and all(r['status'] == 'complete' for r in results)
    for result, morph in zip(results, morphs):
        output = Path(result['output'])
        data = marker_report_data.load_collection(output)
        assert len(data['nuclei']) == 2
        selected = [s for s in selections if s['source'] == str(morph[0])]
        for item in selected:
            path = Path(item['output']) / 'Nuclear_Intensity.csv'
            assert (output / collect.copy_name(path, item['marker'])).read_bytes() == path.read_bytes()
        assert result['markers'] == [s['marker'] for s in selected]
    log = (morphs[0][0].parent / '4_collect_marker_intensity.log').read_text()
    assert log.count('RUN_STARTED |') == log.count('RUN_FINISHED |') == 1
    assert 'Exact output of the current measurement batch' in log


@pytest.mark.parametrize('failure', ['incomplete', 'invalid_tables'])
def test_bad_requested_marker_cannot_fall_back_but_other_experiments_continue(tmp_path, failure):
    morph = create_morphology(tmp_path / 'A')
    old = create_intensity(morph)
    current = create_intensity(morph, stamp='20260924_110000')
    if failure == 'invalid_tables':
        (current / 'Nuclear_Intensity.csv').unlink()
    another = create_morphology(tmp_path / 'B')
    good = create_intensity(another)
    other_marker = create_intensity(morph, marker=M2)
    results = []
    runs = [selection(morph, current, status='incomplete' if failure == 'incomplete' else 'complete'),
            selection(morph, other_marker, M2),
            selection(another, good)]
    assert collect.collect_measurement_batch(tmp_path / 'input.json', runs, results)
    assert results[0]['status'] == ('blocked' if failure == 'incomplete' else 'failed')
    assert results[1]['status'] == 'complete'
    assert not list(morph[0].parent.glob('FIA_Marker_Intensity_Combined_Results_*/FIA_Marker_Intensity_Combined.xlsx'))
    assert (old / 'Nuclear_Intensity.xlsx').is_file()
    assert (current / 'intensity_run.json').is_file()


def test_measurement_to_collection_with_nonzero_nuclei(tmp_path, monkeypatch):
    manifest, raw, run, pixels, labels = make_experiment(tmp_path)
    engine = MagicMock(version='test', bioformats_version='test')
    engine.inspect.return_value = {'Width_px': labels.shape[1], 'Height_px': labels.shape[0],
                                   'Z_planes': 3, 'Pixel_type': 'uint16', 'Channels': 2}
    engine.labels.return_value = labels
    engine.populations.return_value = ({7, 19, 31}, {7}, {19, 31})
    maximum = pixels[:, 1].max(axis=0)
    rows = []
    for key in (19, 31):
        values = maximum[labels == key]
        rows.append({'Nucleus_ID': key, 'Area_px2': len(values), 'Marker_Mean': float(values.mean()),
                     'Marker_Median': float(np.median(values)), 'Marker_StdDev': float(values.std(ddof=1)),
                     'Marker_Min': int(values.min()), 'Marker_Max': int(values.max()),
                     'Marker_RawIntDen': float(values.sum()), 'Nucleus_mask': f'{key}.tif', 'ROI': f'{key}.roi'})
    engine.measure.return_value = (rows, {'Nuclei_count_total': 3, 'Border_nuclei_count': 1,
                                          'Non_border_nuclei_count': 2, 'Nuclei_measured_count': 2,
                                          'Failed_nuclei_count': 0})
    monkeypatch.setattr(app, 'ImageJEngine', lambda: engine)
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('No choices needed'))
    before = raw.read_bytes()
    assert app.main(manifest, mode='tiff-stack') == 0
    assert raw.read_bytes() == before and engine.measure.call_count == 1
    journal = json.loads(next(run.parent.glob('Nuclear_Intensity_*/batch.json')).read_text())
    assert journal['measurement_status'] == journal['collection_status'] == journal['status'] == 'complete'
    output = Path(journal['collections'][0]['output'])
    data = marker_report_data.load_collection(output)
    assert len(data['nuclei']) == 2
    for row in rows:
        assert row['Marker_RawIntDen'] in [float(n[f'{M1}_Marker_RawIntDen']) for n in data['nuclei']]
