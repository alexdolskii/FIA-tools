"""Paired image-median and pooled-object-size panels in one overview per unit."""

from io import BytesIO

import matplotlib.pyplot as plt
import plot_palette as palette
from marker_report_plots import (
    PNG_DPI,
    draw_distribution,
    draw_image_points,
    p_label,
    plot_font,
    wrap_label,
)


def render_object_areas(data, folder, plot_format, progress=None):
    styles = {(row['Panel'], row['Group']): row for row in data['plot_style']['conditions']}
    image_styles = {palette.image_identity(row): row for row in data['plot_style']['images']}
    panels = data['plot_style']['panels']
    for field, unit, ylabel in data['object_area_metrics']:
        name = 'pFAK_Object_area_' + unit
        points = [{**row, 'Value': row[field], **image_styles[palette.image_identity(row)]}
                  for row in data['images'] if row.get(field) is not None]
        objects = [row for row in data['objects'] if row['Area_unit'] == unit]
        comparisons = [row for row in data['statistics'] if row['Metric'] == field]
        high = max([row['Area'] for row in objects], default=1)
        span = high or 1
        levels = max((sum(r['Control'] in p['groups'] and r['Treatment'] in p['groups'] for r in comparisons)
                      for p in panels), default=0)
        limits = (0, high + span * (0.2 + levels * .12))
        panel_width = max(7.2, 1.85 * max(len(p['groups']) for p in panels))
        labels = {p['id']: {g: wrap_label(p['labels'][g], (panel_width - 1.3) * 72 / len(p['groups']), 12)
                            for g in p['groups']} for p in panels}
        label_lines = max(s.count('\n') + 1 for values in labels.values() for s in values.values())
        height = 2 + len(panels) * (5.2 + .22 * label_lines + .3 * levels)
        with plt.rc_context({'font.family': plot_font(), 'font.size': 12, 'pdf.fonttype': 42,
                             'figure.facecolor': 'white', 'axes.facecolor': 'white'}):
            fig, axes = plt.subplots(len(panels), 2, figsize=(2 * panel_width, height), squeeze=False,
                                     layout='constrained')
            try:
                for index, panel in enumerate(panels):
                    for column, observation in enumerate(('image', 'object')):
                        ax = axes[index, column]
                        for position, group in enumerate(panel['groups'], 1):
                            records = [row for row in (points if observation == 'image' else objects) if row['Group'] == group]
                            values = [row['Value' if observation == 'image' else 'Area'] for row in records]
                            draw_distribution(ax, values, position, 'image' if observation == 'image' else 'nucleus',
                                              styles[panel['id'], group])
                            if observation == 'image':
                                draw_image_points(ax, records)
                            image_count = len({r['Mask_name'] for r in records})
                            well_count = len({r['Well'] for r in records})
                            label = (f'n={len(values)} images; {well_count} wells' if observation == 'image' else
                                     f'n={len(values)} objects\n{image_count} images; {well_count} wells')
                            ax.annotate(label, (position, 0), xycoords=ax.get_xaxis_transform(),
                                        xytext=(0, -(16 + 15 * label_lines)), textcoords='offset points',
                                        ha='center', va='top', fontsize=10, annotation_clip=False)
                        if column == 0:
                            local = [r for r in comparisons if r['Control'] in panel['groups'] and r['Treatment'] in panel['groups']]
                            for level, row in enumerate(local):
                                left, right = sorted(panel['groups'].index(g) + 1 for g in (row['Control'], row['Treatment']))
                                bottom = high + span * (.06 + level * .12)
                                top = bottom + span * .025
                                ax.plot([left, left, right, right], [bottom, top, top, bottom], color='#4b6371', lw=.8)
                                ax.text((left + right) / 2, top + .008 * span, p_label(row), ha='center', fontweight='bold')
                        ax.set_xticks(range(1, len(panel['groups']) + 1), [labels[panel['id']][g] for g in panel['groups']])
                        ax.set_xlim(.4, len(panel['groups']) + .6)
                        ax.set_ylim(*limits)
                        ax.set_ylabel(ylabel)
                        title = 'Median per image' if column == 0 else 'All objects: descriptive distribution'
                        ax.set_title(wrap_label(panel['title'] + ' — ' + title, (panel_width - 1) * 72, 14), fontsize=14)
                        ax.spines[['top', 'right']].set_visible(False)
                        ax.grid(axis='y', alpha=.2)
                        ax.set_axisbelow(True)
                thresholds = data['metadata']['Threshold']
                upper = 'inf' if thresholds['upper_unbounded'] else f'{thresholds["upper"]:g}'
                note = (f'SUM threshold [{thresholds["lower"]:g}, {upper}]; min-nuclei {data["min_nuclei"]}. '
                        '8-connected 2D objects; no watershed; border objects included. '
                        'Left: one grey point per image; well shapes. Right: pooled objects; no object-level tests. '
                        'Images with more objects contribute more to the pooled distribution. '
                        'Zero objects: image median undefined, not zero.')
                fig.suptitle('pFAK-positive object area\n' + wrap_label(note, (2 * panel_width - 1) * 72, 11), fontsize=11)
                path = folder / name
                if plot_format in ('pdf', 'both'):
                    fig.savefig(path.with_suffix('.pdf'))
                if plot_format in ('png', 'both'):
                    fig.savefig(path.with_suffix('.png'), dpi=PNG_DPI)
                else:
                    with BytesIO() as preview:
                        fig.savefig(preview, format='png', dpi=PNG_DPI)
                        data['plot_previews'][name] = preview.getvalue()
            finally:
                plt.close(fig)
        for observation, records in [('image', points), ('object', objects)]:
            for row in records:
                point = {'Plot': name, 'Metric': field if observation == 'image' else 'Area_' + unit,
                         'Observation': observation, 'Group': row['Group'], 'Well': row['Well'],
                         'Image_name': row['Image_name'], 'Mask_name': row['Mask_name'],
                         'Nucleus_ID': None, 'Object_ID': row.get('Object_ID'),
                         'Value': row['Value'] if observation == 'image' else row['Area']}
                point.update({key: row.get(key) if observation == 'image' else None for key in palette.POINT_COLUMNS})
                data['plot_data'].append(point)
        data['plots'].append({'Plot': name, 'Metric': field, 'Observation': 'image median and descriptive objects',
                              'Points': len(points), 'Object_values': len(objects), 'View': 'paired overview',
                              'Panels': ', '.join(p['id'] for p in panels), 'Population': 'retained',
                              'Y_min': limits[0], 'Y_max': limits[1], 'Rendered_points': len(points),
                              'File': 'Plots/' + name + ('.pdf' if plot_format == 'pdf' else '.png'),
                              'PNG_file': 'Plots/' + name + '.png' if plot_format != 'pdf' else '',
                              'PDF_file': 'Plots/' + name + '.pdf' if plot_format != 'png' else '',
                              'Caption': note, 'Display_caption': note, 'Font': plot_font(),
                              'PNG_DPI': PNG_DPI if plot_format != 'pdf' else None})
        if progress:
            progress.advance(name)
