"""Boxplots retain every non-border nucleus, independently of the test unit."""

import textwrap

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from marker_report_data import COUNT, metric_specs

PLOT_COLUMNS = ['Plot', 'Metric', 'Observation', 'Group', 'Well', 'Image_name', 'Mask_name',
                'Nucleus_ID', 'Value']


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


def render_plots(data, folder):
    folder.mkdir(exist_ok=True)
    data['plot_data'] = plot_rows(data)
    plots = []
    for name, field, title, ylabel, observation in plot_specs(data):
        points = [row for row in data['plot_data'] if row['Plot'] == name]
        comparisons = [row for row in data['statistics'] if row['Metric'] == field]
        width = max(9, 1.1 * len(data['groups']))
        fig, ax = plt.subplots(figsize=(width, 7 + 0.25 * len(comparisons)))
        try:
            random = np.random.default_rng(20260923)
            labels = []
            for position, group in enumerate(data['groups'], 1):
                records = [row for row in points if row['Group'] == group]
                values = [row['Value'] for row in records]
                if len(values) >= 2:
                    ax.boxplot([values], positions=[position], widths=0.55, showfliers=False,
                               patch_artist=True, boxprops={'facecolor': '#dbe7ed', 'edgecolor': '#4b6371'},
                               medianprops={'color': '#182f3d', 'linewidth': 1.5}, manage_ticks=False)
                if values:
                    ax.scatter(position + random.uniform(-0.17, 0.17, len(values)), values,
                               s=12 if observation == 'nucleus' else 28, alpha=0.48,
                               c='#225f83', linewidths=0, zorder=3)
                count = data['counts_by_group'][group]
                labels.append(textwrap.fill(group, 20) + f'\nn={len(values)} {observation}(s); {count["Wells"]} well(s)')
            values = [row['Value'] for row in points]
            low, high = (min(values), max(values)) if values else (0, 1)
            span = max(high - low, abs(high) * 0.15, 1)
            for level, row in enumerate(comparisons):
                left, right = sorted(data['groups'].index(g) + 1 for g in (row['Control'], row['Treatment']))
                bottom = high + span * (0.09 + level * 0.11)
                top = bottom + span * 0.025
                ax.plot([left, left, right, right], [bottom, top, top, bottom], color='#4b6371', linewidth=0.8)
                ax.text((left + right) / 2, top + span * 0.008, p_label(row), ha='center', va='bottom', fontsize=9)
            ax.set_ylim(min(0, low - span * 0.05), high + span * (0.2 + len(comparisons) * 0.11))
            ax.set_xlim(0.4, len(data['groups']) + 0.6)
            ax.set_xticks(range(1, len(data['groups']) + 1), labels, fontsize=9)
            ax.set_title(title + '\n' + data['run_id'], fontsize=12)
            ax.set_ylabel(ylabel)
            ax.spines[['top', 'right']].set_visible(False)
            ax.grid(axis='y', alpha=0.2)
            ax.set_axisbelow(True)
            if not values:
                ax.text(0.5, 0.5, 'No usable measurements', transform=ax.transAxes, ha='center')
            effective = 'well' if data['stats_unit'] == 'well' else observation
            inference = ('Statistics disabled.' if data['stats_unit'] is None else
                         f'Test unit: {effective}. Two-sided Welch; p adjusted by Holm across all metrics and selected markers per color block.')
            note = (f'One point = one {observation}; all points shown. Border nuclei excluded. '
                    'Box: median and IQR; whiskers: 1.5 IQR. ' + inference)
            if data['stats_unit'] == 'well':
                note += ' Well values: mean of image means (count: mean nuclei per image).'
            elif data['stats_unit'] == 'nucleus':
                note += ' Nucleus/image tests are exploratory; within-well dependence is not modeled.'
            if any(row['P_Holm'] is None for row in comparisons):
                note += ' Not tested: see Statistics for the reason.'
            if name.startswith('Morphology_'):
                note += ' Shown because at least one morphology comparison has Holm-adjusted p < 0.05.'
            caption = textwrap.fill(note, max(90, int(width * 14)))
            bottom_margin = 0.07 + 0.022 * (caption.count('\n') + 1)
            fig.text(0.04, 0.02, caption, fontsize=8, va='bottom')
            fig.tight_layout(rect=(0.01, bottom_margin, 0.99, 0.98))
            path = folder / (name + '.png')
            fig.savefig(path, dpi=160)
            plots.append({'Plot': name, 'Metric': field, 'Observation': observation, 'Points': len(points),
                          'File': str(path.relative_to(folder.parent)), 'Caption': note})
        finally:
            plt.close(fig)
    data['plots'] = plots
    return data
