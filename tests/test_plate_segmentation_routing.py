"""Current-format preparation, segmentation provenance and exact Foci routing."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock
import numpy as np
import pytest
import tifffile
import plate_design as plate
import collect_marker_intensity_results as collect
from test_plate_design_workflow import scheme, dataset


def test_current_preparation_passes_to_stardist_with_scheme_snapshot(tmp_path, monkeypatch):
    stage = sys.modules['nuclei_mask_generation']
    design = scheme(tmp_path/'experiment')
    folder = design.path.parent/'fia_assay/Nuclei'
    folder.mkdir(parents=True)
    source = design.path.parent/'field_WellA01.nd2'
    source.write_bytes(b'original')
    name = source.stem+'_nuclei_projection.tif'
    pixels = np.arange(256, dtype=np.uint8).reshape(16,16)
    tifffile.imwrite(folder/name, pixels)
    plate.save_snapshot(folder, design)
    (folder/plate.PREPARATION).write_text(json.dumps({'Status':'COMPLETE',
        'Sources':{source.name:plate.file_digest(source)},'Files':{name:plate.file_digest(folder/name)}}))
    model = MagicMock()
    model.predict_instances.return_value = ((pixels > 100).astype(np.uint16), {})
    monkeypatch.setattr(stage.StarDist2D, 'from_pretrained', lambda _: model)
    monkeypatch.setattr(stage, 'normalize', lambda x: x.astype(float)/255)
    output = Path(stage.find_nuclei([str(folder)])[0])
    assert plate.validate_snapshot(output, design).scope_hash('Nuclei') == design.scope_hash('Nuclei')
    assert stage.inspect_stardist_folder(output, folder, stage.source_fingerprints(folder))['usable']
    source.write_bytes(b'changed original')
    with pytest.raises(ValueError, match='original changed'):
        stage.source_fingerprints(folder)


def test_foci_uses_only_assigned_wells_and_explicit_intersections(tmp_path):
    stage = sys.modules['foci_quantification']
    design, run, _ = dataset(tmp_path/'experiment', wells=('A01','F01','G01'))
    root = design.path.parent
    pixels = np.zeros((20,30), dtype=np.uint8)
    pixels[2:4,2:4] = 255
    for marker in design.markers('Foci'):
        prepared = root/'fia_assay/markers'/marker['id']
        masks = root/'fia_assay/Foci_Masks'/f"{marker['id']}_20261022_120000"
        prepared.mkdir(parents=True)
        masks.mkdir(parents=True)
        files, outputs, sources = {}, {}, {}
        for well in ('F01','G01'):
            source = root/f'field_Well{well}_Point{well}_0001.nd2'
            name = source.stem+'_foci_projection.tif'
            tifffile.imwrite(prepared/name, pixels)
            tifffile.imwrite(masks/('processed_'+name), pixels)
            files[name] = plate.file_digest(prepared/name)
            outputs['processed_'+name] = plate.file_digest(masks/('processed_'+name))
            sources[source.name] = plate.file_digest(source)
        for folder in (prepared,masks):
            plate.save_snapshot(folder, design)
        (prepared/plate.PREPARATION).write_text(json.dumps({'Status':'COMPLETE','Files':files,'Sources':sources}))
        (masks/'foci_run.json').write_text(json.dumps({'Status':'COMPLETE','Marker_ID':marker['id'],
                                                    'Inputs':files,'Masks':outputs,'Threshold':150}))
        tifffile.imwrite(masks/'processed_unrelated_WellF02_foci_projection.tif', pixels)
    config = tmp_path/'input.json'
    config.write_text(json.dumps({'paths_to_files':[str(root)]}))
    assert stage.main_summarize_res(str(config), njobs=1) == 0
    output, = (root/'fia_assay/foci_analysis').glob('Results_*')
    state = json.loads((output/'foci_quantification_run.json').read_text())
    assert state['Status'] == 'SUCCESS' and state['Images'] == 2
    rows = collect.csv_snapshot(output/'all_results_with_coloc_universal.csv',{},[])[1]
    assert {r['Well'] for r in rows} == {'F01','G01'}
    intersections = collect.csv_snapshot(output/'Requested_Intersections.csv',{},[])[1]
    assert len(intersections) == 5
    assert [r['Original_channels'] for r in intersections if r['Well']=='G01'] == ['C2+C3+C4']
    assert all((output/r['Mask']).is_file() for r in intersections)
    assert (root/'fia_assay/4_foci_quantification.log').is_file()


def test_required_mask_shape_mismatch_fails_instead_of_partial_measurement(tmp_path):
    stage = sys.modules['foci_quantification']
    nucleus = tmp_path/'image_WellF01_nuclei_projection_StarDist_processed_processed.tif'
    foci = tmp_path/'marker.tif'
    tifffile.imwrite(nucleus, np.zeros((20,30),dtype=np.uint8))
    tifffile.imwrite(foci, np.zeros((19,30),dtype=np.uint8))
    with pytest.raises(ValueError, match='dimensions differ'):
        stage.process_nuclei_image(str(nucleus), {'marker':str(foci)}, {}, str(tmp_path), True,
                                   requested_intersections=[])
