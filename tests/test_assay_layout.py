"""The prepared-channel producer and both analysis branches share one fresh layout."""

import json
import shutil
import sys

import collect_marker_intensity_results as collector
import marker_intensity_report as report
import nuclear_intensity as intensity
import pytest
from test_select_channels_logging import run, source_folder
from test_select_channels_logging import runtime  # noqa: F401 - shared pytest fixture

nuclei = sys.modules['nuclei_mask_generation']
foci = sys.modules['foci_mask_generation']
quantify = sys.modules['foci_quantification']


def manifest(tmp_path, root):
    path = tmp_path / 'input.json'
    path.write_text(json.dumps({'paths_to_files': [str(root)]}))
    return str(path)


def test_prepared_channels_are_found_by_nuclei_foci_and_intensity(
        tmp_path, monkeypatch, runtime, capsys):
    root = source_folder(tmp_path)
    config = manifest(tmp_path, root)
    run(monkeypatch, [root], input_json=config)
    assay = root / 'fia_assay'
    marker = assay / 'markers' / 'Foci_1_Channel_2'
    assert (marker / 'good_foci_projection.tif').is_file()
    assert not (root / 'foci_assay').exists() and not (assay / 'Foci').exists()
    assert nuclei.validate_folders(config) == [str(assay / 'Nuclei')]
    stardist = assay / 'Nuclei_StarDist_mask_processed_20261001_120000'
    stardist.mkdir()
    shutil.copy2(assay / 'Nuclei' / 'good_nuclei_projection.tif', stardist / 'mask.tif')
    # Hidden directories and Apple sidecars cannot change discovery counts.
    (marker / '._good.tif').touch()
    (assay / 'markers' / '.hidden').mkdir()
    info = foci.validate_folders(config)[str(root)]
    assert info['fia_assay_folder'] == str(assay)
    assert info['foci_folder'] == str(assay / 'markers')
    assert info['foci_files'] == [str((marker / 'good_foci_projection.tif').relative_to(assay / 'markers'))]
    assert "Number of files in 'markers': 1" in capsys.readouterr().out
    experiments = intensity.discover(config)
    catalog = intensity.discover_markers(experiments)
    assert len(catalog) == 1 and catalog[0]['channel'] == 2
    assert catalog[0]['folders'][root]['path'] == marker
    assert catalog[0]['folders'][root]['image_count'] == 1
    assert collector.scan_source(root)['markers'] == {'Foci_1_Channel_2'}
    assert quantify.validate_folders(config)[str(root)]['fia_assay_folder'] == str(assay)
    # All active journals for these steps belong to the same assay directory.
    assert (assay / '1_log.log').is_file() and (assay / '2_log.log').is_file()


@pytest.mark.parametrize('old_parent,old_markers', [('foci_assay', 'Foci'), ('fia_assay', 'Foci')])
def test_old_marker_locations_are_not_discovered(tmp_path, old_parent, old_markers):
    root = source_folder(tmp_path)
    channel = root / old_parent / old_markers / 'Foci_1_Channel_2'
    channel.mkdir(parents=True)
    saved = channel / 'image.tif'
    saved.write_bytes(b'old result, never read as pixels')
    config = manifest(tmp_path, root)
    assert intensity.discover_markers(intensity.discover(config)) == []
    assert collector.scan_source(root)['markers'] == set()
    assert report.discover_collections(root) == []
    assert saved.read_bytes() == b'old result, never read as pixels'


def test_old_assay_masks_do_not_enter_either_analysis_branch(tmp_path):
    root = source_folder(tmp_path)
    old = root / 'foci_assay'
    (old / 'Final_Nuclei_Mask_20261001_120000').mkdir(parents=True)
    (old / 'Foci_Masks' / 'Foci_1_Channel_2_20261001_120000').mkdir(parents=True)
    config = manifest(tmp_path, root)
    assert intensity.discover(config)[0]['runs'] == []
    assert collector.scan_source(root)['morphology'] == []
    assert quantify.validate_folders(config) == {}
    assert 'foci_folder' not in foci.validate_folders(config).get(str(root), {})
