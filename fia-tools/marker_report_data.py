"""Validate collected FIA spreadsheets and annotate nuclei from a plate map."""

import hashlib
import io
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from zipfile import BadZipFile

import collect_marker_intensity_results as collect
import image_exclusions as exclusions
import numpy as np
import plate_design as plate
import spatial_calibration as spatial
from openpyxl import load_workbook

ValidationError = collect.ValidationError
COLLECTION_PATTERN = re.compile(r'^FIA_Marker_Intensity_Combined_Results_(\d{8}_\d{6})$')
MARKER_PATTERN = re.compile(r'^(?:(?:Foci_[1-9][0-9]*_)?Channel_([1-9][0-9]*)|NuclearIntensity_[A-Za-z0-9_]+_[0-9a-f]{12})$')
WELL_PATTERN = re.compile(r'Well([A-H])(0?[1-9]|1[0-2])(?!\d)', re.IGNORECASE)
COUNT = 'Non_border_nuclei_count'
EXCLUDED_IMAGE_COLUMNS = ['Image_name', 'Source_file', 'Mask_name', 'Well', 'Group',
                          *collect.COUNTS, 'Min_nuclei', 'Reason']
IMAGE_VALUE_COLUMNS = ['Category', 'Marker', 'Metric', 'Unit', 'Group', 'Well', 'Image_name',
                       'Mask_name', 'N_nuclei', 'N_values', 'Value']


class PlateMapError(ValidationError):
    """An actionable layout problem, distinct from an internal report failure."""

    def __init__(self, code, message, location, action, details=''):
        self.code = code
        self.location = str(location)
        self.action = action
        self.details = details
        text = f'{message}\nLocation: {self.location}\nAction: {action}'
        if details:
            text += f'\nDetails: {details}'
        super().__init__(text)


def workbook_rows(path, sheet, files):
    try:
        book = load_workbook(io.BytesIO(collect.snapshot(path, files)), read_only=True, data_only=False)
        try:
            values = iter(book[sheet].values)
            columns = list(next(values))
            if not columns or len(set(columns)) != len(columns) or any(not x for x in columns):
                raise ValidationError(f'Invalid {sheet} headers')
            return [dict(zip(columns, tuple(row) + (None,) * (len(columns) - len(row)))) for row in values]
        finally:
            book.close()
    except Exception as error:
        raise ValidationError(f'Cannot read {path.name}/{sheet}: {error}') from error


def collection_info(path, files):
    rows = workbook_rows(path / collect.COMBINED_NAME, 'Collection_Info', files)
    status = [row['Status'] for row in rows if row.get('Category') == 'Collection' and row.get('Item') == 'Status']
    if len(status) != 1 or status[0] not in ('SUCCESS', 'SUCCESS_WITH_MISSING_INTENSITY'):
        raise ValidationError('Collection must have one successful completion status')
    return rows


def _unique(rows, key, label):
    result = {}
    for row in rows:
        identity = key(row)
        if identity in result:
            raise ValidationError(f'Duplicate {label}: {identity}')
        result[identity] = row
    return result


def _nucleus_key(row):
    return row['Mask_name'], collect.number(row['Nucleus_ID'], 'Nucleus_ID', integer=True)


def _check_values(expected, actual, mapping=None):
    mapping = mapping or {}
    for field, value in expected.items():
        target = mapping.get(field, field)
        if target not in actual or not collect.matching_cell(value, actual[target]):
            raise ValidationError(f'Combined/source table mismatch in {target}')


def _read_intensity(path, marker, files, copied):
    names = [f'Nuclear_Intensity_{marker}.csv', f'Nuclear_Intensity_Images_{marker}.csv',
             f'Nuclear_Intensity_Run_Info_{marker}.csv', f'Nuclear_Intensity_{marker}.xlsx']
    if any(name not in copied for name in names):
        raise ValidationError(f'Missing collector source record for {marker}')
    identity = ['Dataset_path', 'Nuclei_run_ID', 'Intensity_run_ID', 'Mask_file', 'Image_name',
                'Source_file', 'Marker_channel', 'Particle_size_px2']
    required = [identity + ['Nucleus_ID', 'Area_px2'] + list(collect.INTENSITY_METRICS),
                identity + ['Status', 'Error', 'Nuclei_measured_count', 'Failed_nuclei_count'] + list(collect.COUNTS)
                + [f'{metric}_{stat}' for metric in ('Area_px2', *collect.INTENSITY_METRICS) for stat in collect.STATS],
                ['Parameter', 'Value']]
    tables = {sheet: collect.csv_snapshot(path / name, files, fields)
              for sheet, name, fields in zip(('Nuclei', 'Images', 'Run_Info'), names, required)}
    collect.verify_workbook(path / names[3], files, tables)
    info_rows = _unique(tables['Run_Info'][1], lambda row: row['Parameter'], 'run parameter')
    meta = {key: row['Value'] for key, row in info_rows.items()}
    if meta.get('Status') != 'complete':
        raise ValidationError(f'Incomplete intensity: {marker}')
    meta['Schema_version'] = collect.number(meta.get('Schema_version'), 'Schema_version', integer=True)
    if meta['Schema_version'] not in (1, 2, 3, 4):
        raise ValidationError('Unsupported intensity schema')
    if meta['Schema_version'] == 4:
        channel = None
        meta['Channels_by_well'] = json.loads(meta['Channels_by_well'])
        if meta.get('Marker_folder') != marker or meta.get('Analysis_method') != 'Nuclear intensity':
            raise ValidationError('Marker identity differs from metadata')
    else:
        channel = int(MARKER_PATTERN.fullmatch(marker)[1])
        if (collect.number(meta.get('Marker_channel'), 'Marker_channel', integer=True) != channel
                or (meta['Schema_version'] in (2, 3) and meta.get('Marker_folder') != marker)):
            raise ValidationError('Marker identity differs from its metadata')
    run = Path(collect.basename(meta.get('Intensity_run', '')))
    if not collect.INTENSITY_PATTERN.fullmatch(run.name):
        raise ValidationError('Invalid intensity run reference')
    bundle = {'run': run, 'metadata': meta, 'marker': marker, 'channel': channel, 'tables': tables}
    collect.validate_rows(bundle, 'intensity')
    return bundle


def load_collection(path, progress=None):
    """Use archived tables only; original images and original drives are not required."""
    path = Path(path)
    files = {}
    scheme = plate.snapshot_design(path)
    collect.snapshot(path / plate.SNAPSHOT, files)
    collect.snapshot(path / 'plate_design.xlsx', files)
    info = collection_info(path, files)
    stored_exclusions = exclusions.read_manifest(path / exclusions.MANIFEST, files)
    recorded_exclusions = [row for row in info if row.get('Category') == 'Processing exclusions'
                           and row.get('Item') == 'Manifest']
    if recorded_exclusions:
        content = files.get(path / exclusions.MANIFEST, b'')
        if len(recorded_exclusions) != 1 or hashlib.sha256(content).hexdigest() != recorded_exclusions[0].get('SHA256'):
            raise ValidationError('Processing exclusion snapshot is missing or changed since collection')
    copied_rows = [r for r in info if r.get('Category') == 'Copied spreadsheet' and r.get('Status') == 'COPIED']
    copied = _unique(copied_rows, lambda row: row['Item'], 'copied spreadsheet')
    if progress:
        progress.phase('Verifying archived spreadsheets', len(copied))
    for name, row in copied.items():
        if Path(name).name != name or name.startswith('.') or Path(name).suffix not in ('.csv', '.xlsx'):
            raise ValidationError('Invalid archived spreadsheet name')
        content = collect.snapshot(path / name, files)
        if hashlib.sha256(content).hexdigest() != row.get('SHA256'):
            raise ValidationError(f'Archived spreadsheet changed since collection: {name}')
        if progress:
            progress.advance(name)
    if progress:
        progress.phase('Validating morphology and combined tables')
    morph_names = {'Nuclei_Morphology.csv', 'Nuclei_Images.csv', 'Nuclei_Run_Info.csv', 'Nuclei_Morphology.xlsx'}
    if not morph_names <= set(copied):
        raise ValidationError('Collection lacks the four morphology source records')
    tables, meta, _ = collect.read_tables(path, 'morphology', files)
    run_id = meta.get('Run_ID', '')
    if not collect.NUCLEI_PATTERN.fullmatch(run_id):
        raise ValidationError('Invalid nuclei run reference')
    morphology = {'run': Path(run_id), 'metadata': {'particle_size_pixels_squared': meta.get('Particle_size_px2')},
                  'tables': tables}
    collect.validate_rows(morphology, 'morphology')
    exclusions.filter_bundle(morphology, stored_exclusions)
    combined = {
        'Nuclei': collect.csv_snapshot(path / 'FIA_Marker_Intensity_Nuclei.csv', files,
                                      ['Mask_name', 'Nucleus_ID', 'Nuclei_run_ID', 'Image_name', 'Source_file']),
        'Images': collect.csv_snapshot(path / 'FIA_Marker_Intensity_Images.csv', files,
                                      ['Mask_name', 'Nuclei_run_ID', 'Image_name', 'Source_file']),
    }
    collect.verify_workbook(path / collect.COMBINED_NAME, files, combined)
    merged_nuclei = _unique(combined['Nuclei'][1], _nucleus_key, 'combined nucleus')
    merged_images = _unique(combined['Images'][1], lambda row: row['Mask_name'], 'combined image')
    if set(merged_nuclei) != set(morphology['nuclei']) or set(merged_images) != set(morphology['images']):
        raise ValidationError('Combined and morphology populations differ')
    for sheet, merged, source in (('Nuclei', merged_nuclei, morphology['nuclei']),
                                   ('Images', merged_images, morphology['images'])):
        for key, row in source.items():
            _check_values(collect.typed_morphology(row), merged[key], {'Image_name': 'Morphology_Image_name'})
    marker_lists = [{col.removesuffix('_Availability') for col in combined[sheet][0] if col.endswith('_Availability')}
                    for sheet in ('Nuclei', 'Images')]
    if marker_lists[0] != marker_lists[1] or any(not MARKER_PATTERN.fullmatch(m) for m in marker_lists[0]):
        raise ValidationError('Inconsistent marker columns')
    markers = sorted(marker_lists[0])
    bundles, availability = {}, {}
    if progress:
        progress.phase('Validating marker tables', len(markers))
    for marker in markers:
        states = {row.get(marker + '_Availability') for sheet in combined.values() for row in sheet[1]}
        if not states <= {'AVAILABLE', 'MISSING', 'NOT_APPLICABLE'} or {'AVAILABLE', 'MISSING'} <= states:
            raise ValidationError(f'Inconsistent availability: {marker}')
        availability[marker] = 'AVAILABLE' if 'AVAILABLE' in states else 'MISSING'
        for _, rows in combined.values():
            for row in rows:
                expected = (availability[marker] if scheme.assigned(plate.well_from_name(row['Mask_name']), 'Nuclear intensity', marker)
                            else 'NOT_APPLICABLE')
                if row[marker + '_Availability'] != expected:
                    raise ValidationError(f'{marker}: availability differs from the scheme')
                applicable = expected != 'NOT_APPLICABLE'
                channel = collect.number(row.get(marker + '_Source_channel'), 'Source_channel', integer=True, blank=True)
                if channel != (scheme.channel(plate.well_from_name(row['Mask_name']), 'Nuclear intensity', marker)
                               if applicable else None):
                    raise ValidationError(f'{marker}: exported source channel differs from the scheme')
                if expected != 'AVAILABLE' and any(value not in (None, '') for col, value in row.items()
                       if col.startswith(marker + '_') and col not in {
                           marker + '_Availability', marker + '_Marker_name', marker + '_Source_channel'}):
                    raise ValidationError('Unmeasured marker contains values')
        if availability[marker] == 'MISSING':
            for _, rows in combined.values():
                if any(value for row in rows for col, value in row.items()
                       if col.startswith(marker + '_') and col not in {marker + '_Availability', marker + '_Marker_name', marker + '_Source_channel'}):
                    raise ValidationError('Missing marker contains measurements')
            if progress:
                progress.advance(marker)
            continue
        bundle = _read_intensity(path, marker, files, copied)
        exclusions.filter_bundle(bundle, stored_exclusions)
        if bundle['metadata'].get('Schema_version') == 4:
            expected = {w: scheme.channel(w, 'Nuclear intensity', marker) for w in scheme.wells
                        if scheme.assigned(w, 'Nuclear intensity', marker)}
            if bundle['metadata']['Channels_by_well'] != expected:
                raise ValidationError('Archived marker routing differs from collection scheme')
        collect.validate_compatibility(morphology, bundle)
        bundles[marker] = bundle
        for sheet, merged, source in (('Nuclei', merged_nuclei, bundle['nuclei']),
                                      ('Images', merged_images, bundle['images'])):
            metrics = list(collect.INTENSITY_METRICS) if sheet == 'Nuclei' else [
                f'{metric}_{stat}' for metric in collect.INTENSITY_METRICS for stat in collect.STATS]
            if sheet == 'Images':
                metrics += ['Nuclei_measured_count', 'Failed_nuclei_count']
            for key, row in source.items():
                expected = {'Image_name': row['Image_name'], 'Source_file': row['Source_file'],
                            marker + '_Intensity_run_ID': bundle['run'].name}
                expected.update({marker + '_' + metric: collect.number(row[metric], metric, blank=True) for metric in metrics})
                _check_values(expected, merged[key])
        if progress:
            progress.advance(marker)
    images, nuclei = [], []
    if progress:
        progress.phase('Checking image records', len(merged_images))
    for row in merged_images.values():
        record = collect.typed_morphology(row)
        for marker in markers:
            field = marker + '_Source_channel'
            record[field] = collect.number(row.get(field), field, integer=True, blank=True)
        record['Well'] = image_well(record)
        images.append(record)
        if progress:
            progress.advance(record['Image_name'])
    image_map = {row['Mask_name']: row for row in images}
    if progress:
        progress.phase('Checking nucleus records', len(merged_nuclei))
    for key, row in merged_nuclei.items():
        record = collect.typed_morphology(row)
        image = image_map[key[0]]
        if record['Image_name'] != image['Image_name'] or image_well(record) != image['Well']:
            raise ValidationError('Nucleus/image identity mismatch')
        record['Well'] = image['Well']
        for marker in markers:
            channel_field = marker + '_Source_channel'
            record[channel_field] = collect.number(row.get(channel_field), channel_field, integer=True, blank=True)
            for metric in collect.INTENSITY_METRICS:
                field = marker + '_' + metric
                record[field] = collect.number(row.get(field), field, blank=row[marker + '_Availability'] != 'AVAILABLE')
        nuclei.append(record)
        if progress:
            progress.advance(record['Image_name'])
    active_exclusions = exclusions.load_exclusions(path, files)
    images = [row for row in images if exclusions.image_key(row['Mask_name']) not in active_exclusions]
    nuclei = [row for row in nuclei if exclusions.image_key(row['Mask_name']) not in active_exclusions]
    return {'path': path, 'files': files, 'info': info, 'images': images, 'nuclei': nuclei,
            'image_columns': combined['Images'][0] + ['Group'],
            'processing_exclusions': active_exclusions,
            'markers': markers, 'availability': availability, 'bundles': bundles, 'report_kind': 'marker',
            'run_id': run_id, 'particle_size': morphology['particle_size'], 'notes': []}


def load_nuclei_run(path, progress=None):
    """Read validated morphology directly; never discover or open marker results."""
    path = Path(path)
    if path.is_symlink() or not path.is_dir() or not collect.NUCLEI_PATTERN.fullmatch(path.name):
        raise ValidationError(f'Not a regular completed-nuclei directory: {path}')
    bundle = collect.load_morphology(path, allow_empty=True)
    for source in (path / plate.SNAPSHOT, path / 'plate_design.xlsx'):
        collect.snapshot(source, bundle['files'])
    bundle['root'] = path.parent.parent
    checks = []
    tables = collect.combine(bundle, {}, [], checks, progress)
    images, nuclei = tables['Images'][1], tables['Nuclei'][1]
    for row in images + nuclei:
        row['Well'] = image_well(row)
    return {'path': path, 'files': bundle['files'], 'info': checks,
            'images': images, 'nuclei': nuclei,
            'image_columns': tables['Images'][0] + ['Group'], 'nuclei_columns': tables['Nuclei'][0],
            'processing_exclusions': bundle['processing_exclusions'],
            'markers': [], 'availability': {}, 'bundles': {}, 'report_kind': 'nuclei',
            'run_id': path.name, 'particle_size': bundle['particle_size'],
            'notes': [row['Details'] for row in checks]}


def image_well(row):
    value = str(row.get('Well', ''))
    match = re.fullmatch(r'([A-H])(0?[1-9]|1[0-2])', value, re.IGNORECASE)
    if not match:
        raise ValidationError(f'Invalid or missing well: {value!r}')
    well = f'{match[1].upper()}{int(match[2]):02d}'
    name = row.get('Image_name') or row['Mask_name']
    matches = WELL_PATTERN.findall(name)
    if len(matches) != 1 or f'{matches[0][0].upper()}{int(matches[0][1]):02d}' != well:
        raise ValidationError(f'Well differs from filename: {name}')
    points = re.findall(r'Point([A-H])(0?[1-9]|1[0-2])(?!\d)', name, re.IGNORECASE)
    if any(f'{letter.upper()}{int(column):02d}' != well for letter, column in points):
        raise ValidationError(f'Well/Point tokens disagree: {name}')
    return well


def marker_catalog(data):
    """Prefer named marker runs over legacy channel-only exports; never pool aliases."""
    markers, notes = list(data['markers']), []
    for marker in data['markers']:
        if not marker.startswith('Channel_'):
            continue
        channel = MARKER_PATTERN.fullmatch(marker)[1]
        named = [m for m in markers if m.startswith('Foci_') and MARKER_PATTERN.fullmatch(m)[1] == channel]
        if named:
            markers.remove(marker)
            notes.append(f'{marker} excluded: use named marker result(s) {", ".join(named)}; no legacy fallback.')
    return markers, notes


def _color(cell):
    color = cell.fill.fgColor
    if cell.fill.patternType != 'solid' or color.type not in ('rgb', 'theme', 'indexed'):
        raise ValidationError(f'{cell.coordinate}: use a direct solid fill')
    return f'{color.type}:{color.value}:tint={color.tint}'


def read_template(path, files, sheet_name=None, statistics=False):
    if sheet_name not in (None, 'Plate Map'):
        raise ValidationError('The current FIA scheme uses the named Plate Map and Channels sheets')
    try:
        scheme = plate.read_design(path)
    except ValueError as error:
        raise ValidationError(str(error)) from error
    files[Path(path)] = scheme.content
    return 'Plate Map', scheme.report_rows()


def find_template(collection, sheet_name=None, location_label='raw-image folder'):
    path = None
    try:
        path = plate.find_design(collection)
        read_template(path, {}, sheet_name)
        return path
    except (OSError, ValueError, KeyError, BadZipFile) as error:
        candidates = [p for p in Path(collection).glob('FIA*.xlsx') if p.is_file() and not p.name.startswith('._')]
        code = ('MISSING_PLATE_MAP' if path is None and not candidates else
                'AMBIGUOUS_PLATE_MAP' if path is None and len(candidates) > 1 else 'INVALID_PLATE_MAP')
        raise PlateMapError(code, str(error), collection,
                            'Place one completed FIA*.xlsx scheme beside the raw images and rerun.',
                            details=str(error)) from error


def select_template(collection, template=None, sheet_name=None, location_label='raw-image folder'):
    if template is None:
        return find_template(Path(collection), sheet_name, location_label)
    path = Path(template).expanduser().absolute()
    try:
        read_template(path, {}, sheet_name)
    except (OSError, ValueError, KeyError, BadZipFile) as error:
        code = 'MISSING_PLATE_MAP' if isinstance(error, FileNotFoundError) else 'INVALID_PLATE_MAP'
        raise PlateMapError(code, str(error), path, 'Use the current FIA template with sheet Plate Map and rerun.',
                            details=str(error)) from error
    return path


def annotate(data, template, markers, stats_unit, sheet_name=None):
    allowed = (None, 'image', 'well') if data.get('report_kind') == 'pfak' else (None, 'nucleus', 'well')
    if stats_unit not in allowed:
        raise ValidationError(f'Statistics unit must be {allowed[1]}, well, or omitted')
    if len(set(markers)) != len(markers) or not set(markers) <= set(marker_catalog(data)[0]):
        raise ValidationError('Select unique marker folders from the available catalog')
    sheet, design = read_template(template, data['files'], sheet_name, statistics=stats_unit is not None)
    scheme = plate.read_design(template)
    if markers and not set(markers) <= {m['id'] for m in scheme.markers('Nuclear intensity')}:
        raise ValidationError('Selected marker is not assigned to Nuclear intensity in this scheme')
    unannotated = {row['Well'] for row in data['images'] if row['Well'] not in scheme.wells}
    if unannotated:
        raise ValidationError('Unannotated imaged well: ' + ', '.join(sorted(unannotated)))
    data['plate_design'] = scheme
    data['layout_panels'] = scheme.panels
    data['marker_labels'] = {m['id']: m['name'] for method in ('Nuclear intensity', 'Field signal')
                             for m in scheme.markers(method)}
    data['eligible_wells'] = {m['id']: set(m['channels']) for method in ('Nuclear intensity', 'Field signal')
                              for m in scheme.markers(method)}
    if data.get('report_kind') == 'pfak':
        ids = [data['metadata']['Marker_ID']]
    else:
        ids = markers
    if ids:
        allowed_wells = set().union(*(data['eligible_wells'].get(m, set()) for m in ids))
        data['images'] = [row for row in data['images'] if row['Well'] in allowed_wells]
        retained = {row['Mask_name'] for row in data['images']}
        data['nuclei'] = [row for row in data['nuclei'] if row['Mask_name'] in retained]
    mapping = {row['Well']: row['Group'] for row in design}
    processing_rows = []
    for row in data.get('processing_exclusions', {}).values():
        matches = WELL_PATTERN.findall(row['Image_key'])
        well = f'{matches[0][0].upper()}{int(matches[0][1]):02d}' if len(matches) == 1 else ''
        if ids and well not in allowed_wells:
            continue
        processing_rows.append({**row, 'Well': well, 'Group': mapping.get(well, '')})
    data['processing_exclusion_rows'] = processing_rows
    for row in data['nuclei'] + data['images']:
        if row['Well'] not in mapping:
            raise ValidationError(f'Unannotated imaged well: {row["Well"]}')
        row['Group'] = mapping[row['Well']]
    annotations = {row['Group']: row for row in design}
    groups = sorted(annotations, key=lambda group: annotations[group]['Group_order'])
    group_order = [{'Group': group, 'Order': annotations[group]['Group_order'],
                    'Source': annotations[group]['Order_source'],
                    'Order_cell': annotations[group]['Order_cell'],
                    'Group_cell': annotations[group]['Order_group_cell']} for group in groups]
    blocks, roles = {}, {}
    if stats_unit is not None:
        for row in design:
            identity = row['Color'], row['Is_control']
            if row['Group'] in roles and roles[row['Group']] != identity:
                raise ValidationError(f'Inconsistent fill or control role: {row["Group"]}')
            roles[row['Group']] = identity
        for group in groups:
            color, control = roles[group]
            blocks.setdefault(color, {})[group] = control
        for color, members in blocks.items():
            if sum(members.values()) != 1 or len(members) < 2:
                raise ValidationError(f'{color}: each comparison block needs one control condition and a treatment')
    # Remove excluded marker aliases from the report tables, while retaining archived sources.
    excluded = set(data['markers']) - set(markers)
    for sheet_rows in (data['images'], data['nuclei']):
        for row in sheet_rows:
            for field in list(row):
                if any(field.startswith(marker + '_') for marker in excluded):
                    del row[field]
    data.update(markers=markers, template=Path(template), template_sheet=sheet,
                design=design, groups=groups, blocks=blocks, stats_unit=stats_unit,
                group_order=group_order, group_order_source=group_order[0]['Source'])
    return data


def filter_images(data, min_nuclei=0):
    """Optionally filter validated images by their minimum non-border nucleus count."""
    if not isinstance(min_nuclei, int) or isinstance(min_nuclei, bool) or min_nuclei < 0:
        raise ValidationError('min_nuclei must be a nonnegative integer')
    data['min_nuclei'] = min_nuclei
    data['image_exclusion_rule'] = (
        f'Images with Non_border_nuclei_count < {min_nuclei} excluded before analysis.'
        if min_nuclei else 'Image count filtering disabled; zero-count images retained.')
    images = data['images']
    data['calibration_images_before_filter'] = images
    data['image_columns'] = list(dict.fromkeys(key for row in images for key in row)) or data.get(
        'image_columns', ['Image_name', 'Mask_name', 'Well', 'Group', *collect.COUNTS])
    data['images_before_filter'] = len(images)
    excluded = {row['Mask_name'] for row in images if row[COUNT] < min_nuclei}
    data['excluded_images'] = [
        {**{key: row.get(key) for key in EXCLUDED_IMAGE_COLUMNS[:-1]},
         'Min_nuclei': min_nuclei,
         'Reason': f'Non-border nuclei count {row[COUNT]} is below minimum {min_nuclei}'}
        for row in images if row['Mask_name'] in excluded
    ]
    data['image_filter_summary'] = []
    for group in data['groups']:
        original = [row for row in images if row['Group'] == group]
        retained = [row for row in original if row['Mask_name'] not in excluded]
        data['image_filter_summary'].append({
            'Group': group, 'Images_total': len(original),
            'Processing_excluded': sum(row.get('Group') == group for row in data.get('processing_exclusion_rows', [])),
            'Images_excluded': len(original) - len(retained), 'Images_used': len(retained),
            'Wells_with_images': len({row['Well'] for row in original}),
            'Wells_used': len({row['Well'] for row in retained}),
        })
    data['images'] = [row for row in images if row['Mask_name'] not in excluded]
    data['nuclei'] = [row for row in data['nuclei'] if row['Mask_name'] not in excluded]
    return data


def metric_specs(markers, images=None):
    metrics = [('Nuclei', '', COUNT, 'Nuclei per image')]
    has_physical = any(spatial.calibrated(row) for row in (images or []))
    has_pixels = images is None or not images or any(not spatial.calibrated(row) for row in images)
    for field in collect.MORPH_METRICS:
        units = 'px2' if field == 'Area_px2' else ('px' if field.endswith('_px') else 'dimensionless')
        if field in spatial.PHYSICAL_FIELDS:
            if has_physical:
                metrics.append(('Morphology', '', spatial.PHYSICAL_FIELDS[field], 'um2' if field == 'Area_px2' else 'um'))
            if has_pixels:
                metrics.append(('Morphology', '', field, units))
        else:
            metrics.append(('Morphology', '', field, units))
    for marker in markers:
        metrics.extend(('Intensity', marker, marker + '_' + field,
                        'summed raw pixel values' if field == 'Marker_RawIntDen' else 'raw intensity')
                       for field in collect.INTENSITY_METRICS)
    return metrics


def report_specs(data, markers=None):
    if 'metric_specs' in data:
        return data['metric_specs']
    specs = metric_specs(data['markers'] if markers is None else markers,
                         data['images'] or data.get('calibration_images_before_filter', []))
    return [s for s in specs if s[0] == 'Intensity'] if data.get('report_kind') == 'marker' else specs


def metric_eligible(row, field):
    if field in spatial.PHYSICAL_FIELDS:
        return not spatial.calibrated(row)
    if field in spatial.PHYSICAL_FIELDS.values():
        return spatial.calibrated(row)
    return True


def measurement_field(row, field):
    if spatial.calibrated(row) and field in spatial.CALIBRATED_SHAPES:
        return spatial.CALIBRATED_SHAPES[field]
    return field


def metric_value(row, field):
    """Never put a calibrated image in a pixel-unit plot or statistical comparison."""
    return row.get(measurement_field(row, field)) if metric_eligible(row, field) else None


def aggregate(data, progress=None):
    """Keep nuclei as plot observations; compute equal-image-weight well means."""
    by_mask = defaultdict(list)
    for nucleus in data['nuclei']:
        by_mask[nucleus['Mask_name']].append(nucleus)
    image_values, well_values, summary = [], [], []
    specs = report_specs(data)
    if progress:
        progress.phase('Aggregating image metrics', len(specs) * len(data['images']))
    for category, marker, field, unit in specs:
        per_well = defaultdict(list)
        for image in data['images']:
            if marker and image['Well'] not in data.get('eligible_wells', {}).get(marker, {image['Well']}):
                continue
            if not metric_eligible(image, field):
                if progress:
                    progress.advance(image['Image_name'])
                continue
            values = ([image[COUNT]] if field == COUNT else
                      [metric_value(row, field) for row in by_mask[image['Mask_name']]
                       if metric_value(row, field) is not None])
            record = {'Category': category, 'Marker': marker, 'Metric': field, 'Unit': unit,
                      'Group': image['Group'], 'Well': image['Well'], 'Image_name': image['Image_name'],
                      'Mask_name': image['Mask_name'], 'N_nuclei': image[COUNT],
                      'N_values': len(values), 'Value': float(np.mean(values)) if values else None}
            image_values.append(record)
            per_well[image['Well']].append(record)
            if field != COUNT and values:
                # Existing image summaries are independently reconciled with individual nuclei.
                for stat, value in zip(collect.STATS, (np.mean(values), np.median(values),
                                      np.percentile(values, 75) - np.percentile(values, 25))):
                    source_field = measurement_field(image, field)
                    recorded = collect.number(image.get(source_field + '_' + stat), source_field + '_' + stat, blank=True)
                    if recorded is None or not math.isclose(recorded, float(value), rel_tol=1e-9, abs_tol=1e-9):
                        raise ValidationError(f'Image summary disagrees with nuclei: {field}/{stat}')
            if progress:
                progress.advance(image['Image_name'])
        for annotation in data['design']:
            if marker and annotation['Well'] not in data.get('eligible_wells', {}).get(marker, {annotation['Well']}):
                continue
            records = per_well[annotation['Well']]
            valid = [row['Value'] for row in records if row['Value'] is not None]
            well_values.append({'Category': category, 'Marker': marker, 'Metric': field, 'Unit': unit,
                                'Group': annotation['Group'], 'Well': annotation['Well'],
                                'N_images': len(records), 'N_images_used': len(valid),
                                'N_nuclei': sum(row['N_nuclei'] for row in records),
                                'N_values': sum(row['N_values'] for row in records),
                                'Value': float(np.mean(valid)) if valid else None})
        for group in data['groups']:
            population = data['images'] if field == COUNT else data['nuclei']
            values = [metric_value(row, field) for row in population
                      if row['Group'] == group and metric_value(row, field) is not None]
            summary.append({'Category': category, 'Marker': marker, 'Metric': field, 'Group': group,
                            'Unit': unit, 'Observation': 'image' if field == COUNT else 'nucleus',
                            'N': len(values), 'Mean': float(np.mean(values)) if values else None,
                            'Median': float(np.median(values)) if values else None,
                            'IQR': float(np.percentile(values, 75) - np.percentile(values, 25)) if values else None})
    data.update(image_values=image_values, well_values=well_values, summary=summary)
    data['calibration_summary'] = []
    for group in data['groups']:
        for status in ('calibrated', 'uncalibrated'):
            rows = [row for row in data['images'] if row['Group'] == group
                    and spatial.calibrated(row) == (status == 'calibrated')]
            data['calibration_summary'].append({
                'Group': group, 'Calibration_status': status,
                'Area_unit': 'um2' if status == 'calibrated' else 'px2',
                'Length_unit': 'um' if status == 'calibrated' else 'px',
                'Images': len(rows), 'Wells': len({row['Well'] for row in rows}),
                'Non_border_nuclei': sum(row[COUNT] for row in rows),
            })
    data['counts_by_group'] = {group: {'Nuclei': sum(r['Group'] == group for r in data['nuclei']),
                                      'Images': sum(r['Group'] == group for r in data['images']),
                                      'Wells': len({r['Well'] for r in data['images'] if r['Group'] == group})}
                               for group in data['groups']}
    return data
