"""Panelled distributions retain every non-border nucleus, independently of the test unit."""

import math

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import spatial_calibration as spatial
from marker_report_data import COUNT, metric_value, report_specs
from matplotlib.font_manager import FontProperties, fontManager
from matplotlib.textpath import TextPath

PLOT_COLUMNS = ['Plot', 'Metric', 'Observation', 'Group', 'Well', 'Image_name', 'Mask_name',
                'Nucleus_ID', 'Value']
LABEL_COLUMNS = ['Panel', 'Block', 'Figure_context', 'Panel_title', 'Group', 'Display_label']
FONT_SIZES = {'title': 20, 'panel': 16, 'axis': 14, 'sample': 12, 'note': 12}
PNG_DPI = 300


def plot_font():
    installed = {font.name for font in fontManager.ttflist}
    return next(font for font in ('Arial', 'Liberation Sans', 'DejaVu Sans') if font in installed)


def wrap_label(text, width_points, size, weight='normal'):
    """Prefer whole-word wraps; split oversized tokens without losing their characters."""
    font = FontProperties(family=plot_font(), size=size, weight=weight)
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split():
            candidate = f'{line} {word}'.strip()
            if line and TextPath((0, 0), candidate, prop=font).get_extents().width > width_points:
                lines.append(line)
                line = word
            else:
                line = candidate
            while len(line) > 1 and TextPath((0, 0), line, prop=font).get_extents().width > width_points:
                low, high = 1, len(line)
                while low + 1 < high:
                    middle = (low + high) // 2
                    if TextPath((0, 0), line[:middle], prop=font).get_extents().width <= width_points:
                        low = middle
                    else:
                        high = middle
                lines.append(line[:low])
                line = line[low:]
        lines.append(line)
    return '\n'.join(lines)


def panel_letter(index):
    """Use stable spreadsheet-style letters, including batches with more than 26 blocks."""
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def plot_specs(data):
    """Always plot the primary endpoints; add morphology only after a significant adjusted test."""
    specs = [('Nuclei_count', COUNT, 'Non-border nuclei per image', 'Nuclei per image', 'image')]
    specs += [(marker + '_Integrated_density', marker + '_Marker_RawIntDen',
               'Nuclear integrated density', 'Integrated density (sum of pixel values)', 'nucleus')
              for marker in data['markers']]
    significant = set()
    if data['stats_unit'] is not None:
        significant = {row['Metric'] for row in data.get('statistics', [])
                       if row['Category'] == 'Morphology' and row['Status'] == 'TESTED'
                       and row['P_Holm'] is not None and 0 <= row['P_Holm'] < 0.05}
    for category, _, field, unit in report_specs(data, []):
        if category == 'Morphology' and field in significant:
            label = field.removesuffix('_px2').removesuffix('_px').removesuffix('_um2').removesuffix('_um').replace('_', ' ').capitalize()
            display_unit = {'px2': 'px²', 'um2': 'µm²', 'um': 'µm'}.get(unit, unit)
            ylabel = label + f' ({display_unit})'
            specs.append(('Morphology_' + field, field, 'Nuclear morphology: ' + label, ylabel, 'nucleus'))
    return specs


def plot_rows(data):
    rows = []
    for name, field, _, _, observation in plot_specs(data):
        population = data['images'] if observation == 'image' else data['nuclei']
        for row in population:
            value = metric_value(row, field)
            if value is None:
                continue
            rows.append({'Plot': name, 'Metric': field, 'Observation': observation, 'Group': row['Group'],
                         'Well': row['Well'], 'Image_name': row['Image_name'], 'Mask_name': row['Mask_name'],
                         'Nucleus_ID': row.get('Nucleus_ID') if observation == 'nucleus' else None,
                         'Value': value})
    return rows


def p_label(row):
    p = row['P_Holm']
    if p is None:
        return 'Not tested'
    return '****' if p < 0.0001 else '***' if p < 0.001 else '**' if p < 0.01 else '*' if p < 0.05 else 'ns'


def short_labels(groups):
    """Move only a shared whole-word prefix into a title; never change group identities."""
    tokens = [group.split() for group in groups]
    shared = 0
    for words in zip(*tokens):
        if len(set(words)) != 1:
            break
        shared += 1
    shared = min(shared, min(map(len, tokens)) - 1)
    labels = {group: ' '.join(words[shared:]) for group, words in zip(groups, tokens)}
    if shared <= 0 or len(set(labels.values())) != len(groups):
        return '', {group: group for group in groups}
    return ' '.join(tokens[0][:shared]), labels


def plot_panels(data):
    """Use existing comparison blocks, including empty groups, without splitting test families."""
    blocks = {color: list(groups) for color, groups in data.get('blocks', {}).items()}
    if not blocks:
        colors = {}
        for row in data['design']:
            colors.setdefault(row['Group'], set()).add(row.get('Color', ''))
        # Descriptive reports may lack colors; ambiguous fills must not duplicate observations.
        if all(len(colors.get(group, set())) == 1 for group in data['groups']):
            for group in data['groups']:
                color = next(iter(colors[group]))
                blocks.setdefault(color, []).append(group)
    assigned = {group for groups in blocks.values() for group in groups}
    remaining = [group for group in data['groups'] if group not in assigned]
    if remaining:
        blocks.setdefault('', []).extend(remaining)
    context, _ = short_labels(data['groups'])
    panels = []
    for index, (color, members) in enumerate(blocks.items(), 1):
        groups = [group for group in data['groups'] if group in members]
        prefix, labels = short_labels(groups)
        heading = prefix
        if context and prefix.startswith(context):
            heading = prefix[len(context):].strip()
        panels.append({'id': f'Block_{index:02d}', 'block': color, 'groups': groups,
                       'title': heading or f'Block {index}', 'labels': labels, 'context': context})
    # Keep any cross-panel comparison visible if an externally prepared design is incomplete.
    membership = {group: p['id'] for p in panels for group in p['groups']}
    if any(membership.get(row['Control']) != membership.get(row['Treatment'])
           for row in data['statistics']):
        prefix, labels = short_labels(data['groups'])
        return [{'id': 'Block_01', 'block': '', 'groups': data['groups'], 'title': 'All conditions',
                 'labels': labels, 'context': prefix}]
    return panels


def draw_distribution(ax, values, position, observation, random):
    """Draw all nucleus values as a density; individual dots are reserved for image counts."""
    if not values:
        return
    variable = max(values) > min(values)
    if observation == 'nucleus' and len(values) >= 5 and variable:
        violin = ax.violinplot([values], positions=[position], widths=0.8, points=200,
                              bw_method='scott', showextrema=False)
        for body in violin['bodies']:
            body.set_facecolor('#86b6c9')
            body.set_edgecolor('#34677f')
            body.set_alpha(0.75)
    if len(values) >= 2 and variable:
        ax.boxplot([values], positions=[position], widths=0.16 if observation == 'nucleus' else 0.5,
                   showfliers=False, patch_artist=True,
                   boxprops={'facecolor': '#f5f9fb', 'edgecolor': '#344f60', 'zorder': 3},
                   medianprops={'color': '#182f3d', 'linewidth': 1.6, 'zorder': 4}, manage_ticks=False)
        if observation == 'nucleus':
            # The full observed range remains visible without drawing outlier dots.
            ax.vlines(position, min(values), max(values), color='#34677f', alpha=0.5, linewidth=0.7)
    elif observation == 'nucleus':
        ax.hlines(values[0], position - 0.15, position + 0.15, color='#182f3d', linewidth=2)
    if observation == 'image':
        ax.scatter(position + random.uniform(-0.17, 0.17, len(values)), values,
                   s=28, alpha=0.48, c='#225f83', linewidths=0, zorder=4)


def plot_note(data, name, observation, comparisons):
    description = ('One point = one image; all image points shown.' if observation == 'image' else
                   'Violin: all usable nuclei, no individual dots; equal maximum widths; Scott smoothing. '
                   'Density stays within the observed range. Fewer than 5 nuclei: box/range; constant or single value: line.')
    effective = 'well' if data['stats_unit'] == 'well' else observation
    inference = ('Statistics disabled.' if data['stats_unit'] is None else
                 f'Test unit: {effective}. Two-sided Welch; p adjusted by Holm across all metrics and selected markers per color block.')
    image_filter = data.get('image_exclusion_rule', 'Image count filtering disabled.')
    note = (description + ' Border nuclei excluded. ' + image_filter + ' '
            'Linear Y; shared limits across panels of this metric. '
            'Box: median and IQR; whiskers: 1.5 IQR. Labels: usable observations and contributing wells. ' + inference)
    if data['stats_unit'] == 'well':
        note += ' Well values: mean of image means (count: mean nuclei per image).'
    elif data['stats_unit'] == 'nucleus':
        note += ' Nucleus/image tests are exploratory; within-well dependence is not modeled.'
    if any(row['P_Holm'] is None for row in comparisons):
        note += ' Not tested: see Statistics for the reason.'
    if name.startswith('Morphology_'):
        note += ' Shown because at least one morphology comparison has Holm-adjusted p < 0.05.'
    return note


def render_figure(data, spec, panels, points, comparisons, limits, high, span, note, path):
    """Keep the existing overview and panels, applying the same style to PNG and vector PDF."""
    with plt.rc_context({'font.family': plot_font(), 'font.size': FONT_SIZES['axis'], 'pdf.fonttype': 42}):
        _render_figure(data, spec, panels, points, comparisons, limits, high, span, path)


def short_plot_note(data, observation, field=None):
    population = 'Non-border nuclei.' if observation == 'nucleus' else 'One point = one image; non-border nuclei counted.'
    threshold = data.get('min_nuclei', 0)
    filtering = f' Images: ≥{threshold} non-border nuclei.' if threshold else ' No image-count filter.'
    if data['stats_unit'] is None:
        inference = ' Descriptive only.'
    else:
        unit = 'well' if data['stats_unit'] == 'well' else observation
        inference = f' Welch + Holm; test unit: {unit}. Symbols and methods: report tables.'
    cohort = ''
    if field in spatial.PHYSICAL_FIELDS or field in spatial.PHYSICAL_FIELDS.values():
        label = 'Calibrated' if field in spatial.PHYSICAL_FIELDS.values() else 'Uncalibrated'
        images = sum(row['Metric'] == field and row['Value'] is not None for row in data.get('image_values', []))
        cohort = f' {label} images only; {images} contributing images in this metric.'
    return population + filtering + ' Box: median and IQR.' + inference + cohort


def _render_figure(data, spec, panels, points, comparisons, limits, high, span, path):
    name, field, title, ylabel, observation = spec
    columns = min(2, len(panels))
    rows = math.ceil(len(panels) / columns)
    panel_width = max(7.2, 1.85 * max(len(p['groups']) for p in panels))
    width = columns * panel_width
    caption = wrap_label(short_plot_note(data, observation, field), (width - 0.6) * 72, FONT_SIZES['note'])
    subtitle = ' · '.join(part for part in (panels[0]['context'],
                         name.removesuffix('_Integrated_density') if name.endswith('_Integrated_density') else '') if part)
    heading = wrap_label(title, (width - 0.6) * 72, FONT_SIZES['title'], 'bold')
    subtitle = wrap_label(subtitle, (width - 0.6) * 72, FONT_SIZES['panel'])
    wrapped = {}
    label_widths = {}
    for panel in panels:
        # Reserve room for Y ticks and margins before assigning each condition its width.
        label_widths[panel['id']] = (panel_width - 1.3) * 72 / (len(panel['groups']) + 0.2) * 0.9
        wrapped[panel['id']] = {group: wrap_label(label, label_widths[panel['id']], FONT_SIZES['axis'])
                                for group, label in panel['labels'].items()}
    label_lines = max(label.count('\n') + 1 for labels in wrapped.values() for label in labels.values())
    levels = max(sum(r['Control'] in p['groups'] and r['Treatment'] in p['groups']
                     for r in comparisons) for p in panels)
    title_height = 0.36 * (heading.count('\n') + 1) + (0.28 * (subtitle.count('\n') + 1) if subtitle else 0)
    caption_height = 0.21 * (caption.count('\n') + 1) + 0.25
    plot_height = rows * (3.7 + 0.26 * label_lines + 0.3 * levels + 0.45)
    fig = plt.figure(figsize=(width, title_height + plot_height + caption_height + 0.5),
                     layout='constrained')
    try:
        outer = fig.add_gridspec(3, 1, height_ratios=[title_height, plot_height, caption_height])
        header = fig.add_subplot(outer[0])
        header.set_axis_off()
        header.text(0.5, 1, heading, ha='center', va='top', fontsize=FONT_SIZES['title'],
                    fontweight='bold', transform=header.transAxes)
        if subtitle:
            header.text(0.5, 0, subtitle, ha='center', va='bottom', fontsize=FONT_SIZES['panel'], transform=header.transAxes)
        grid = outer[1].subgridspec(rows, columns)
        if len(panels) > 1:
            fig.supylabel(ylabel, fontsize=FONT_SIZES['axis'])
        for index, panel in enumerate(panels):
            ax = fig.add_subplot(grid[index // columns, index % columns])
            ax.set_label(panel['id'])
            random = np.random.default_rng(20260923)
            labels = []
            panel_lines = max(label.count('\n') + 1 for label in wrapped[panel['id']].values())
            for position, group in enumerate(panel['groups'], 1):
                records = [row for row in points if row['Group'] == group]
                values = [row['Value'] for row in records]
                draw_distribution(ax, values, position, observation, random)
                unit = 'nuclei' if observation == 'nucleus' else 'images'
                wells = len({row['Well'] for row in records})
                labels.append(wrapped[panel['id']][group])
                sample = f'n={len(values):,} {unit} · {wells} wells'
                sample_font = FontProperties(family=plot_font(), size=FONT_SIZES['sample'])
                if TextPath((0, 0), sample, prop=sample_font).get_extents().width > label_widths[panel['id']]:
                    sample = f'n={len(values):,} {unit}\n{wells} wells'
                text = ax.annotate(sample, (position, 0), xycoords=ax.get_xaxis_transform(),
                                   xytext=(0, -(12 + panel_lines * FONT_SIZES['axis'] * 1.2)),
                                   textcoords='offset points', ha='center', va='top',
                                   fontsize=FONT_SIZES['sample'], annotation_clip=False)
                text.set_gid('sample_size')
            local = [r for r in comparisons if r['Control'] in panel['groups'] and r['Treatment'] in panel['groups']]
            for level, row in enumerate(local):
                left, right = sorted(panel['groups'].index(g) + 1 for g in (row['Control'], row['Treatment']))
                bottom = high + span * (0.09 + level * 0.11)
                top = bottom + span * 0.025
                ax.plot([left, left, right, right], [bottom, top, top, bottom], color='#4b6371', linewidth=0.8)
                text = ax.text((left + right) / 2, top + span * 0.008, p_label(row), ha='center', va='bottom',
                               fontsize=FONT_SIZES['axis'], fontweight='bold')
                text.set_gid('significance')
            ax.set_ylim(*limits)
            ax.set_xlim(0.4, len(panel['groups']) + 0.6)
            ax.set_xticks(range(1, len(panel['groups']) + 1), labels, fontsize=FONT_SIZES['axis'])
            ax.tick_params(axis='x', pad=6)
            ax.tick_params(axis='y', labelsize=FONT_SIZES['axis'])
            ax.ticklabel_format(axis='y', style='sci', scilimits=(-4, 5), useMathText=True)
            ax.yaxis.get_offset_text().set_fontsize(FONT_SIZES['sample'])
            letter = panel_letter(int(panel['id'].removeprefix('Block_')))
            panel_title = wrap_label(f'{letter}  {panel["title"]}', (panel_width - 1.3) * 72,
                                     FONT_SIZES['panel'], 'bold')
            ax.set_title(panel_title, fontsize=FONT_SIZES['panel'], fontweight='bold', pad=12)
            if len(panels) == 1:
                ax.set_ylabel(ylabel, fontsize=FONT_SIZES['axis'])
            ax.spines[['top', 'right']].set_visible(False)
            ax.grid(axis='y', alpha=0.2)
            ax.set_axisbelow(True)
            if not any(row['Group'] in panel['groups'] for row in points):
                ax.text(0.5, 0.5, 'No usable measurements', transform=ax.transAxes, ha='center')
        footer = fig.add_subplot(outer[2])
        footer.set_axis_off()
        footer.text(0, 1, caption, fontsize=FONT_SIZES['note'], va='top', transform=footer.transAxes)
        footer.text(0, 0, 'Run: ' + data['run_id'], fontsize=FONT_SIZES['note'], va='bottom', transform=footer.transAxes)
        fig.savefig(path, dpi=PNG_DPI)
        fig.savefig(path.with_suffix('.pdf'))
    finally:
        plt.close(fig)


def render_plots(data, folder):
    folder.mkdir(exist_ok=True)
    data['plot_data'] = plot_rows(data)
    panels = plot_panels(data)
    data['plot_labels'] = [dict(zip(LABEL_COLUMNS, (p['id'], p['block'], p['context'], p['title'], group, p['labels'][group])))
                           for p in panels for group in p['groups']]
    plots = []
    for spec in plot_specs(data):
        name, field, _, _, observation = spec
        points = [row for row in data['plot_data'] if row['Plot'] == name]
        comparisons = [row for row in data['statistics'] if row['Metric'] == field]
        values = [row['Value'] for row in points]
        low, high = (min(values), max(values)) if values else (0, 1)
        # Annotation spacing must also cover a zero-based axis when observations are nearly constant.
        span = max(high - low, high - min(0, low), abs(high) * 0.15) or 1
        levels = max(sum(r['Control'] in p['groups'] and r['Treatment'] in p['groups']
                         for r in comparisons) for p in panels)
        limits = min(0, low - span * 0.05), high + span * (0.2 + levels * 0.11)
        note = plot_note(data, name, observation, comparisons)
        views = [(name, 'overview' if len(panels) > 1 else 'panel', panels)]
        if len(panels) > 1:
            views += [(name + '__' + p['id'], 'panel', [p]) for p in panels]
        for filename, view, selected in views:
            path = folder / (filename + '.png')
            render_figure(data, spec, selected, points, comparisons, limits, high, span, note, path)
            groups = {group for p in selected for group in p['groups']}
            count = sum(row['Group'] in groups for row in points)
            plots.append({'Plot': name, 'Metric': field, 'Observation': observation, 'Points': count,
                          'View': view, 'Panels': ', '.join(p['id'] for p in selected),
                          'Rendered_points': count if observation == 'image' else 0,
                          'File': str(path.relative_to(folder.parent)),
                          'PDF_file': str(path.with_suffix('.pdf').relative_to(folder.parent)),
                          'Caption': note, 'Display_caption': short_plot_note(data, observation, field),
                          'Font': plot_font(), 'PNG_DPI': PNG_DPI})
    data['plots'] = plots
    return data
