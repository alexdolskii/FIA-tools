"""Versioned, verified FieldSignal tables shared by analysis and image-free reports."""

import hashlib
import json
import math
from pathlib import Path

import collect_marker_intensity_results as collect
import image_exclusions as exclusions
import plate_design as plate
import spatial_calibration as spatial
from marker_intensity_report import write_workbook
from run_resources import temporary_path

PREFIX = 'FIA_Field_Signal_Results_'
WORKBOOK = 'Field_Signal_Measurements.xlsx'
IMAGE_PATH_COLUMNS = ['SUM32_file', 'Mask_file', 'Objects_file', 'Nuclei_IDs_file']
IMAGE_IDENTIFIERS = ['Run_ID', 'Dataset_path', 'Image_name', 'Mask_name', 'Well', 'Source_file',
                     'Marker_ID', 'Marker_name', 'Method', 'Source_SHA256', 'Nuclei_run_ID', 'Mask_SHA256', 'ID_map_SHA256', 'Channel',
                     'Z_planes', 'Z_step_um', 'Width_px', 'Height_px', 'Pixel_type', 'Projection',
                     'Threshold_lower', 'Threshold_upper', 'Particle_size_px2', 'Image_folder'] + IMAGE_PATH_COLUMNS
IMAGE_METRICS = [
    'Nuclei_count_total', 'Border_nuclei_count', 'Non_border_nuclei_count',
    'Normalization_nuclei_count', 'Nuclear_area_total_px2', 'Normalization_nuclear_area_px2',
    'Normalization_nuclear_area', 'Normalization_mean_nuclear_area', 'Border_nuclei_fraction',
    'Include_border_nuclei', 'Area_unit', 'FieldSignal_Positive_pixels', 'FieldSignal_Area',
    'FieldSignal_Coverage_percent', 'FieldSignal_RawIntDen', 'FieldSignal_IntDen',
    'FieldSignal_RawIntDen_per_nucleus', 'FieldSignal_IntDen_per_nucleus',
    'FieldSignal_RawIntDen_per_nuclear_area', 'FieldSignal_IntDen_per_nuclear_area',
    'FieldSignal_Area_per_nucleus', 'FieldSignal_Area_per_nuclear_area',
    'FieldSignal_Object_count', 'FieldSignal_Border_object_count', 'FieldSignal_Median_object_area',
    'Projection_Min', 'Projection_Max',
]
IMAGE_COLUMNS = IMAGE_IDENTIFIERS + spatial.CALIBRATION_COLUMNS + IMAGE_METRICS
OBJECT_COLUMNS = ['Image_name', 'Mask_name', 'Well', 'Object_ID', 'Area_px2', 'Area_um2', 'Area',
                  'Area_unit', 'RawIntDen', 'IntDen', 'Touches_border', 'Centroid_X_px', 'Centroid_Y_px']
FAILURE_COLUMNS = ['Image_name', 'Mask_name', 'Stage', 'Error']
BOOL_COLUMNS = {'Include_border_nuclei', 'Touches_border'}
TEXT_COLUMNS = set(IMAGE_IDENTIFIERS) - {'Channel', 'Z_planes', 'Z_step_um', 'Width_px', 'Height_px',
                                      'Threshold_lower', 'Threshold_upper', 'Particle_size_px2'}
TEXT_COLUMNS |= {'Calibration_status', 'Calibration_source', 'Calibration_note', 'Area_unit'}


def write_json(path, data):
    path = Path(path)
    temporary = temporary_path(path, path.with_name('.' + path.name + '.tmp'))
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def save_tables(output, images, objects, failures, parameters):
    tables = {'Images': (IMAGE_COLUMNS, images), 'Objects': (OBJECT_COLUMNS, objects),
              'Failed_Images': (FAILURE_COLUMNS, failures),
              'Run_Info': (['Parameter', 'Value'], [
                  {'Parameter': key, 'Value': json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}
                  for key, value in parameters.items()])}
    for title, (columns, rows) in tables.items():
        collect.write_csv(output / (title + '.csv'), columns, rows)
    write_workbook(output, tables, [], WORKBOOK)
    exported = {title: collect.csv_snapshot(output / (title + '.csv'), {}, columns)
                for title, (columns, _) in tables.items()}
    collect.verify_workbook(output / WORKBOOK, {}, exported)
    return {name: spatial.digest(output / name)
            for name in [*(title + '.csv' for title in tables), WORKBOOK]}


def typed_rows(rows):
    result = []
    for row in rows:
        parsed = {}
        for key, value in row.items():
            if key in BOOL_COLUMNS:
                if value not in ('True', 'False'):
                    raise ValueError(f'Invalid boolean {key}: {value}')
                parsed[key] = value == 'True'
            elif key in TEXT_COLUMNS:
                parsed[key] = value
            elif value == '':
                parsed[key] = None
            else:
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError(f'Non-finite FieldSignal table value: {key}')
                parsed[key] = int(number) if number.is_integer() else number
        result.append(parsed)
    return result


def load_run(path):
    """Reports require validated tables, not the original drive or a JVM."""
    path = Path(path)
    files = {}
    metadata = collect.json_snapshot(path / 'field_signal_run.json', files)
    if (metadata.get('Schema_version') not in (2, 3) or metadata.get('Run_ID') != path.name
            or metadata.get('Status') not in ('SUCCESS', 'PARTIAL')):
        raise ValueError('Not a completed FieldSignal run with usable results.')
    required = {'Images.csv', 'Objects.csv', 'Failed_Images.csv', 'Run_Info.csv', WORKBOOK}
    if set(metadata.get('Table_SHA256', {})) != required:
        raise ValueError('Missing FieldSignal table provenance.')
    for name, digest in metadata['Table_SHA256'].items():
        content = collect.snapshot(path / name, files)
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f'FieldSignal input changed after analysis: {name}')
    columns = [c for c in IMAGE_COLUMNS if metadata['Schema_version'] == 3 or c not in IMAGE_PATH_COLUMNS]
    images = typed_rows(collect.csv_snapshot(path / 'Images.csv', files, columns)[1])
    objects = typed_rows(collect.csv_snapshot(path / 'Objects.csv', files, OBJECT_COLUMNS)[1])
    failures = collect.csv_snapshot(path / 'Failed_Images.csv', files, FAILURE_COLUMNS)[1]
    if len(images) != metadata['Images_measured'] or len(failures) != metadata['Images_failed']:
        raise ValueError('FieldSignal status and table counts disagree.')
    design = plate.snapshot_design(path)
    collect.snapshot(path / plate.SNAPSHOT, files)
    collect.snapshot(path / 'plate_design.xlsx', files)
    if metadata.get('Scope_SHA256') != design.scope_hash('Field signal', metadata['Marker_ID']):
        raise ValueError('Field signal routing differs from its scheme snapshot')
    by_image = {}
    for row in images:
        key = row['Mask_name']
        well = plate.well_from_name(row['Image_name'])
        if (row['Marker_ID'] != metadata['Marker_ID'] or row['Well'] != well
                or row['Channel'] != design.channel(well, 'Field signal', metadata['Marker_ID'])):
            raise ValueError('Field signal row differs from its per-well marker assignment')
        if key in by_image or row['Run_ID'] != path.name:
            raise ValueError('Duplicate FieldSignal image or mismatched run ID.')
        by_image[key] = []
    seen = set()
    for row in objects:
        key = row['Mask_name'], row['Object_ID']
        if key in seen or row['Mask_name'] not in by_image or row['Object_ID'] < 1:
            raise ValueError('Duplicate or orphan FieldSignal object.')
        seen.add(key)
        by_image[row['Mask_name']].append(row)
    for row in images:
        items = by_image[row['Mask_name']]
        if len(items) != row['FieldSignal_Object_count']:
            raise ValueError('FieldSignal object count disagrees with image table.')
        for total, field in [('FieldSignal_Positive_pixels', 'Area_px2'), ('FieldSignal_Area', 'Area'),
                             ('FieldSignal_RawIntDen', 'RawIntDen'), ('FieldSignal_IntDen', 'IntDen')]:
            if not math.isclose(sum(item[field] for item in items), row[total], rel_tol=1e-9, abs_tol=1e-8):
                raise ValueError(f'FieldSignal object/image totals disagree: {total}')
    records = exclusions.load_exclusions(path, files)
    images = [row for row in images if exclusions.image_key(row['Image_name']) not in records]
    retained = {row['Mask_name'] for row in images}
    objects = [row for row in objects if row['Mask_name'] in retained]
    return {'path': path, 'files': files, 'metadata': metadata, 'images': images, 'objects': objects,
            'failures': failures, 'processing_exclusions': records, 'nuclei': [], 'markers': [],
            'report_kind': 'pfak', 'run_id': path.name, 'notes': [],
            'image_columns': IMAGE_COLUMNS, 'particle_size': metadata['Particle_size_px2']}


def profile_key(metadata):
    limits = metadata['Threshold']
    return (metadata['Marker_ID'], metadata['Scope_SHA256'], limits['lower'], limits['upper'], metadata['Nuclei_run_ID'],
            metadata['Include_border_nuclei'])
