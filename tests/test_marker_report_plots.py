"""Plot regressions: readable panels, complete distributions and unchanged inference."""

from copy import deepcopy
from itertools import pairwise
from zipfile import ZipFile

import collect_marker_intensity_results as collector
import marker_intensity_report as report
import marker_report_data as inputs
import marker_report_plots as plots
import matplotlib.pyplot as plt
import numpy as np
import pytest
from marker_report_statistics import calculate_statistics
from matplotlib.collections import PathCollection, PolyCollection
from matplotlib.figure import Figure

MARKER = 'Foci_1_Channel_2'


def four_blocks(unit):
    data = {'images': [], 'nuclei': [], 'groups': [], 'design': [], 'blocks': {}, 'markers': [MARKER],
            'stats_unit': unit, 'run_id': 'Final_Nuclei_Mask_20260929_100000'}
    fields = [field for _, _, field, _ in inputs.metric_specs([MARKER])[1:]]
    for block, context in enumerate(('P4+8i old serum', 'P4+8i new serum',
                                     'P4+13i old serum', 'P4+13i new serum')):
        color = f'color-{block}'
        data['blocks'][color] = {}
        for treatment in ('dmso', 'tgfb inhibitor', 'citric acid', 'tgfb ligand'):
            group = 'BK near (tert-blast) ' + context + ' ' + treatment
            data['groups'].append(group)
            data['blocks'][color][group] = treatment == 'dmso'
            for repeat in range(2):
                well = f'{chr(65 + block)}{len(data["blocks"][color]) * 2 + repeat:02d}'
                identity = {'Group': group, 'Well': well, 'Image_name': f'field_Well{well}.tif', 'Mask_name': well}
                data['design'].append({'Group': group, 'Well': well, 'Color': color})
                values = np.arange(1, 21, dtype=float) + repeat
                image = dict(identity, **{inputs.COUNT: len(values)})
                for field in fields:
                    for stat, value in zip(collector.STATS, (np.mean(values), np.median(values),
                                                           np.percentile(values, 75) - np.percentile(values, 25))):
                        image[field + '_' + stat] = float(value)
                data['images'].append(image)
                for index, value in enumerate(values):
                    data['nuclei'].append(dict(identity, Nucleus_ID=index + 1,
                                               **{field: float(value) for field in fields}))
    if unit is None:
        data['blocks'] = {}
    inputs.aggregate(data)
    return calculate_statistics(data)


@pytest.mark.parametrize('unit', ['nucleus', 'well', None])
def test_four_panels_keep_statistics_observations_and_readable_labels(tmp_path, monkeypatch, unit):
    data = four_blocks(unit)
    before = deepcopy(data)
    snapshots = []
    original_save = Figure.savefig

    def inspect(fig, filename, **kwargs):
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        axes = [ax for ax in fig.axes if ax.get_ylabel()]
        for ax in axes:
            labels = ax.get_xticklabels()
            boxes = [label.get_window_extent(renderer) for label in labels]
            assert all(left.x1 < right.x0 for left, right in pairwise(boxes))
            assert all('n=' in label.get_text() and '2 wells' in label.get_text() for label in labels)
            assert ax.get_yscale() == 'linear'
            dots = sum(isinstance(c, PathCollection) for c in ax.collections)
            assert dots == (4 if filename.name.startswith('Nuclei_count') else 0)
            for label in labels:
                assert not label.get_window_extent(renderer).overlaps(fig.axes[-1].get_window_extent(renderer))
        snapshots.append((filename.name, [ax.get_ylim() for ax in axes],
                          [t.get_text() for ax in axes for t in ax.texts if 'p=' in t.get_text() or t.get_text() == 'Not tested']))
        return original_save(fig, filename, **kwargs)

    monkeypatch.setattr(Figure, 'savefig', inspect)
    plots.render_plots(data, tmp_path / 'Plots')
    assert all(data[key] == value for key, value in before.items())
    assert data['plot_data'] == plots.plot_rows(before)
    assert len(data['plots']) == 10
    assert len(data['plot_labels']) == 16
    assert {r['Display_label'] for r in data['plot_labels']} == {'dmso', 'tgfb inhibitor', 'citric acid', 'tgfb ligand'}
    assert {r['Figure_context'] for r in data['plot_labels']} == {'BK near (tert-blast)'}
    assert {r['Group'] for r in data['plot_labels']} == set(data['groups'])
    for metric in ('Nuclei_count', MARKER + '_Integrated_density'):
        selected = [item for item in snapshots if item[0].startswith(metric)]
        assert len({limits for _, axes, _ in selected for limits in axes}) == 1
        assert len(selected[0][1]) == 4
        assert len(selected[0][2]) == (0 if unit is None else 12)
        assert sum(len(item[2]) for item in selected[1:]) == len(selected[0][2])
    tables = {'Plot_Info': report.as_table(data['plots']), 'Plot_Labels': report.as_table(data['plot_labels'])}
    report.write_workbook(tmp_path, tables, data['plots'])
    with ZipFile(tmp_path / report.WORKBOOK) as archive:
        assert len([name for name in archive.namelist() if name.startswith('xl/media/')]) == 10


@pytest.mark.parametrize('values', [[], [7], [7] * 100, [1, 2, 3, 9], list(range(5000)) + [100000]])
def test_nucleus_density_has_no_dots_and_keeps_extremes(values):
    fig, ax = plt.subplots()
    try:
        plots.draw_distribution(ax, values, 1, 'nucleus', np.random.default_rng(0))
        assert not any(isinstance(c, PathCollection) for c in ax.collections)
        bodies = [c for c in ax.collections if isinstance(c, PolyCollection)]
        assert bool(bodies) == (len(values) >= 5 and max(values) > min(values))
        if bodies:
            vertices = bodies[0].get_paths()[0].vertices
            assert vertices[:, 1].min() == min(values)
            assert vertices[:, 1].max() == max(values)
            median = [line for line in ax.lines if np.all(np.asarray(line.get_ydata()) == np.median(values))]
            assert any(line.get_zorder() > ax.patches[0].get_zorder() for line in median)
        if values:
            assert ax.dataLim.ymin == min(values)
            assert ax.dataLim.ymax == max(values)
    finally:
        plt.close(fig)


def test_display_shortening_never_merges_conditions():
    groups = ['control', 'control treated', 'control  treated']
    _, labels = plots.short_labels(groups)
    assert len(set(labels.values())) == len(groups)
    assert labels == {group: group for group in groups}
    data = four_blocks(None)
    data['design'].append(dict(data['design'][0], Color='conflicting color'))
    panels = plots.plot_panels(data)
    assert len(panels) == 1 and panels[0]['groups'] == data['groups']


def test_descriptive_template_retains_direct_colors_without_requiring_controls(tmp_path):
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill
    from test_marker_intensity_report import plate

    path = plate(tmp_path / 'plate.xlsx')
    book = load_workbook(path)
    book.active['C2'].font = Font()
    book.active['D2'].fill = PatternFill('solid', fgColor='ABC123')
    book.save(path)
    _, design = inputs.read_template(path, {}, statistics=False)
    assert len({row['Color'] for row in design}) == 2
    with pytest.raises(inputs.ValidationError, match='one control'):
        # Color extraction itself remains independent of the statistical control validation.
        inputs.annotate({'images': [], 'nuclei': [], 'markers': [], 'files': {}}, path, [], 'nucleus')
