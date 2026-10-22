"""Image-level FieldSignal endpoints; pooled object sizes are descriptive only."""

from collections import defaultdict

import marker_report_data as shared
import numpy as np
from marker_report_plots import prepare_plot_style

# Physical and pixel-area endpoints must never share a plot or statistical test.
METRICS = [
    ('FieldSignal', 'FieldSignal_RawIntDen', 'FieldSignal raw integrated intensity', 'RawIntDen (sum of pixel values)', False),
    ('FieldSignal', 'FieldSignal_IntDen', 'FieldSignal integrated intensity', 'IntDen (a.u. × {area})', True),
    ('FieldSignal', 'FieldSignal_RawIntDen_per_nucleus', 'FieldSignal RawIntDen per nucleus', 'RawIntDen / nucleus', False),
    ('FieldSignal', 'FieldSignal_IntDen_per_nucleus', 'FieldSignal IntDen per nucleus', 'IntDen / nucleus (a.u. × {area})', True),
    ('FieldSignal', 'FieldSignal_RawIntDen_per_nuclear_area', 'FieldSignal RawIntDen per nuclear area', 'RawIntDen / nuclear area (a.u. / {area})', True),
    ('FieldSignal', 'FieldSignal_IntDen_per_nuclear_area', 'FieldSignal IntDen per nuclear area', 'IntDen / nuclear area (a.u.)', False),
    ('FieldSignal', 'FieldSignal_Area', 'FieldSignal-positive area', 'Area ({area})', True),
    ('FieldSignal', 'FieldSignal_Area_per_nucleus', 'FieldSignal area per nucleus', 'Positive area / nucleus ({area})', True),
    ('FieldSignal', 'FieldSignal_Area_per_nuclear_area', 'FieldSignal area per nuclear area', 'Positive area / nuclear area (ratio)', False),
    ('FieldSignal', 'FieldSignal_Coverage_percent', 'FieldSignal coverage', 'Positive area / field area (%)', False),
    ('FieldSignal', 'FieldSignal_Object_count', 'FieldSignal-positive objects per image', 'Objects per image', False),
    ('FieldSignal objects', 'FieldSignal_Median_object_area', 'Median FieldSignal object area per image', 'Object area ({area})', True),
]


def configure_metrics(data):
    images = data['images']
    units = [unit for unit in ('um2', 'px2') if any(row['Area_unit'] == unit for row in images)]
    # Preserve the planned display even if current processing exclusions remove every image.
    if not units:
        units = data['metadata'].get('Area_units', ['px2'])
    specs, plots, object_metrics = [], [], []
    for category, base, title, label, split in METRICS:
        for unit in units if split else [None]:
            field = base + '_' + unit if split else base
            ylabel = label.format(area='µm²' if unit == 'um2' else 'px²')
            if split:
                for row in images:
                    row[field] = row[base] if row['Area_unit'] == unit else None
            specs.append((category, data['metadata']['Marker_ID'], field, ylabel))
            if base == 'FieldSignal_Median_object_area':
                object_metrics.append((field, unit, ylabel))
            else:
                name = 'Nuclei_count' if base == shared.COUNT else field
                plots.append((name, field, title.replace('FieldSignal', data['metadata']['Marker_name']), ylabel, 'image'))
    data.update(metric_specs=specs, image_metric_fields=[s[2] for s in specs],
                custom_plot_specs=plots, object_area_metrics=object_metrics)
    return data


def prepare(data, template, stats_unit, sheet, min_nuclei):
    for row in data['images']:
        row['Well'] = shared.image_well(row)
    shared.annotate(data, template, [], stats_unit, sheet)
    mapping = {row['Mask_name']: row for row in data['images']}
    for obj in data['objects']:
        image = mapping[obj['Mask_name']]
        if obj['Image_name'] != image['Image_name'] or obj['Well'] != image['Well']:
            raise ValueError('FieldSignal object identity differs from image identity.')
        obj['Group'] = image['Group']
    data['min_nuclei'] = min_nuclei
    configure_metrics(data)
    data['plot_style'] = prepare_plot_style(data, min_nuclei)
    shared.filter_images(data, min_nuclei)
    included = {row['Mask_name'] for row in data['images']}
    data['objects'] = [row for row in data['objects'] if row['Mask_name'] in included]
    settings = data['metadata']
    limits = settings['Threshold']
    threshold = f'SUM threshold: [{limits["lower"]:g}, ' + ('inf' if limits['upper_unbounded'] else f'{limits["upper"]:g}') + '].'
    policy = 'all visible nuclei' if settings['Include_border_nuclei'] else 'non-border nuclei'
    base_note = (f'{threshold} Full-field FieldSignal mask; no background subtraction or watershed. '
                 f'Normalization: {policy}; area uses the same IDs. '
                 f'Image filter: >= {min_nuclei} non-border nuclei.' if min_nuclei else
                 f'{threshold} Full-field FieldSignal mask; no background subtraction or watershed. '
                 f'Normalization: {policy}; area uses the same IDs. No image-count filter.')
    stats_note = ('Descriptive only.' if stats_unit is None else
                  f'Two-sided Welch + Holm over all planned image endpoints per comparison block; test unit: {stats_unit}. '
                  'Images/wells are not automatically independent biological replicates.')
    data['plot_captions'], data['plot_notes'] = {}, {}
    for name, _, _, _, _ in data['custom_plot_specs']:
        diagnostic = name == 'Nuclei_count_all'
        caption = ('One grey point = one image; shape = technical well within condition. '
                   'Fill = condition; box = median and IQR. ' + base_note + ' ' + stats_note)
        if diagnostic:
            caption = (f'All validated images before filtering; red edge: < {min_nuclei} non-border nuclei. '
                       'One grey point = one image; shape = well within condition. Diagnostic only; no tests. ' + threshold)
        data['plot_captions'][name] = caption
        data['plot_notes'][name] = (caption + ' RawIntDen and IntDen differ only by pixel area when calibration is constant. '
                                   'No statistical independence is claimed for these paired endpoints.')
    return aggregate(data)


def aggregate(data):
    image_values, well_values, summary = [], [], []
    for category, marker, field, unit in data['metric_specs']:
        wells = defaultdict(list)
        for image in data['images']:
            value = image.get(field)
            row = {'Category': category, 'Marker': marker, 'Metric': field, 'Unit': unit,
                   'Group': image['Group'], 'Well': image['Well'], 'Image_name': image['Image_name'],
                   'Mask_name': image['Mask_name'], 'N_nuclei': image[shared.COUNT],
                   'N_values': int(value is not None), 'Value': value}
            image_values.append(row)
            wells[image['Well']].append(row)
        for well in data['design']:
            if well['Well'] not in data['eligible_wells'][marker]:
                continue
            rows = wells[well['Well']]
            values = [row['Value'] for row in rows if row['Value'] is not None]
            well_values.append({'Category': category, 'Marker': marker, 'Metric': field, 'Unit': unit,
                                'Group': well['Group'], 'Well': well['Well'], 'N_images': len(rows),
                                'N_images_used': len(values), 'N_nuclei': sum(row['N_nuclei'] for row in rows),
                                'N_values': len(values), 'Value': float(np.mean(values)) if values else None})
        for group in data['groups']:
            rows = [r for r in image_values if r['Metric'] == field and r['Group'] == group and r['Value'] is not None]
            values = [row['Value'] for row in rows]
            summary.append({'Category': category, 'Metric': field, 'Unit': unit, 'Group': group,
                            'Observation': 'image', 'N_images': len(values), 'N_wells': len({r['Well'] for r in rows}),
                            'Mean': float(np.mean(values)) if values else None,
                            'Median': float(np.median(values)) if values else None,
                            'IQR': float(np.percentile(values, 75) - np.percentile(values, 25)) if values else None})
    data.update(image_values=image_values, well_values=well_values, summary=summary)
    return data
