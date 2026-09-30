"""Panelled distributions retain every non-border nucleus, independently of the test unit."""

import math
import textwrap

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from marker_report_data import COUNT, metric_specs

PLOT_COLUMNS = ['Plot', 'Metric', 'Observation', 'Group', 'Well', 'Image_name', 'Mask_name',
                'Nucleus_ID', 'Value']
LABEL_COLUMNS = ['Panel', 'Block', 'Figure_context', 'Panel_title', 'Group', 'Display_label']


def plot_specs(data):
    """Always plot the primary endpoints; add morphology only after a significant adjusted test."""
    specs = [('Nuclei_count', COUNT, 'Non-border nuclei per image', 'Nuclei per image', 'image')]
    specs += [(marker + '_Integrated_density', marker + '_Marker_RawIntDen',
               marker + ': nuclear integrated density', 'Raw integrated density (sum of pixel values)', 'nucleus')
              for marker in data['markers']]
    significant = set()
    if data['stats_unit'] is not None:
        significant = {row['Metric'] for row in data.get('statistics', [])
                       if row['Category'] == 'Morphology' and row['Status'] == 'TESTED'
                       and row['P_Holm'] is not None and 0 <= row['P_Holm'] < 0.05}
    for category, _, field, unit in metric_specs([]):
        if category == 'Morphology' and field in significant:
            label = field.removesuffix('_px2').removesuffix('_px').replace('_', ' ').capitalize()
            ylabel = label + (' (px²)' if unit == 'px2' else f' ({unit})')
            specs.append(('Morphology_' + field, field, 'Nuclear morphology: ' + label, ylabel, 'nucleus'))
    return specs


def plot_rows(data):
    rows = []
    for name, field, _, _, observation in plot_specs(data):
        population = data['images'] if observation == 'image' else data['nuclei']
        for row in population:
            if row[field] is None:
                continue
            rows.append({'Plot': name, 'Metric': field, 'Observation': observation, 'Group': row['Group'],
                         'Well': row['Well'], 'Image_name': row['Image_name'], 'Mask_name': row['Mask_name'],
                         'Nucleus_ID': row.get('Nucleus_ID') if observation == 'nucleus' else None,
                         'Value': row[field]})
    return rows


def p_label(row):
    p = row['P_Holm']
    if p is None:
        return 'Not tested'
    stars = '****' if p < 0.0001 else '***' if p < 0.001 else '**' if p < 0.01 else '*' if p < 0.05 else 'ns'
    return f'{stars}  p={p:.3g}'


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
    note = (description + ' Border nuclei excluded. Images with zero non-border nuclei excluded before analysis. '
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
    """Render every selected panel, growing figure height rather than shrinking panels."""
    _, _, title, ylabel, observation = spec
    columns = min(2, len(panels))
    rows = math.ceil(len(panels) / columns)
    width = columns * max(7.2, 1.65 * max(len(p['groups']) for p in panels))
    caption = textwrap.fill(note, int(width * 13))
    heading = '\n'.join(textwrap.fill(part, int(width * 10))
                        for part in (title, panels[0]['context'], data['run_id']) if part)
    label_lines = max(textwrap.fill(label, 18).count('\n') + 3
                      for panel in panels for label in panel['labels'].values())
    levels = max(sum(r['Control'] in p['groups'] and r['Treatment'] in p['groups']
                     for r in comparisons) for p in panels)
    title_height = 0.3 * (heading.count('\n') + 1)
    caption_height = 0.17 * (caption.count('\n') + 1)
    plot_height = rows * (4.5 + 0.2 * label_lines + 0.23 * levels)
    fig = plt.figure(figsize=(width, title_height + plot_height + caption_height + 0.5),
                     layout='constrained')
    try:
        outer = fig.add_gridspec(3, 1, height_ratios=[title_height, plot_height, caption_height])
        header = fig.add_subplot(outer[0])
        header.set_axis_off()
        header.text(0.5, 0.5, heading, ha='center', va='center', fontsize=13, transform=header.transAxes)
        grid = outer[1].subgridspec(rows, columns)
        for index, panel in enumerate(panels):
            ax = fig.add_subplot(grid[index // columns, index % columns])
            random = np.random.default_rng(20260923)
            labels = []
            for position, group in enumerate(panel['groups'], 1):
                records = [row for row in points if row['Group'] == group]
                values = [row['Value'] for row in records]
                draw_distribution(ax, values, position, observation, random)
                unit = 'nuclei' if observation == 'nucleus' else 'images'
                wells = len({row['Well'] for row in records})
                labels.append(textwrap.fill(panel['labels'][group], 18)
                              + f'\nn={len(values):,} {unit}\n{wells} wells')
            local = [r for r in comparisons if r['Control'] in panel['groups'] and r['Treatment'] in panel['groups']]
            for level, row in enumerate(local):
                left, right = sorted(panel['groups'].index(g) + 1 for g in (row['Control'], row['Treatment']))
                bottom = high + span * (0.09 + level * 0.11)
                top = bottom + span * 0.025
                ax.plot([left, left, right, right], [bottom, top, top, bottom], color='#4b6371', linewidth=0.8)
                ax.text((left + right) / 2, top + span * 0.008, p_label(row), ha='center', va='bottom', fontsize=10)
            ax.set_ylim(*limits)
            ax.set_xlim(0.4, len(panel['groups']) + 0.6)
            ax.set_xticks(range(1, len(panel['groups']) + 1), labels, fontsize=10)
            ax.set_title(textwrap.fill(panel['title'], max(40, int(width / columns * 10))), fontsize=12, pad=12)
            ax.set_ylabel(ylabel, fontsize=10)
            ax.spines[['top', 'right']].set_visible(False)
            ax.grid(axis='y', alpha=0.2)
            ax.set_axisbelow(True)
            if not any(row['Group'] in panel['groups'] for row in points):
                ax.text(0.5, 0.5, 'No usable measurements', transform=ax.transAxes, ha='center')
        footer = fig.add_subplot(outer[2])
        footer.set_axis_off()
        footer.text(0, 0.5, caption, fontsize=9, va='center', transform=footer.transAxes)
        fig.savefig(path, dpi=160)
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
        span = max(high - low, abs(high) * 0.15) or 1
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
                          'File': str(path.relative_to(folder.parent)), 'Caption': note})
    data['plots'] = plots
    return data
