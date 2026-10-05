"""Condition roles, complete plate designs and stable per-image presentation."""

from copy import deepcopy

import marker_report_data as inputs
import marker_report_plots as plots
import matplotlib.pyplot as plt
import numpy as np
import plot_palette as palette
import pytest
from marker_report_statistics import calculate_statistics
from matplotlib import colors
from matplotlib.collections import PathCollection, PolyCollection
from matplotlib.figure import Figure
from test_marker_intensity_report import annotated


def test_control_role_overrides_name_and_position_and_restarts_each_block():
    groups = ['Control', 'Vehicle', 'Dose 1']
    styles = palette.condition_styles(groups, 'Vehicle')
    assert [r['Group'] for r in styles] == groups
    assert [r['Condition_color'] for r in styles] == ['#004F46', '#B5B1D8', '#F37420']
    assert [r['Is_control'] for r in styles] == [False, True, False]
    assert [r['Median_color'] for r in styles] == ['#FFFFFF', '#111314', '#111314']
    other = palette.condition_styles(['Reference', 'Compound'], 'Reference')
    assert [r['Condition_color'] for r in other] == ['#B5B1D8', '#004F46']


@pytest.mark.parametrize('count', [1, 2, 8, 9, 16])
def test_unmarked_control_allows_ordinary_lavender_and_never_infers_role(count):
    groups = ['Control'] + [f'Dose {index}' for index in range(count - 1)]
    styles = palette.condition_styles(groups)
    assert not any(r['Is_control'] or r['Control_identified'] for r in styles)
    assert styles[0]['Condition_color'] == '#004F46'
    if count > 1:
        assert styles[1]['Condition_color'] == '#B5B1D8'
    if count == 8:
        assert [r['Condition_color'] for r in styles] == [c for _, c in palette.PALETTE]
    if count >= 9:
        assert styles[-1]['Condition_color'] == '#BFD3D1'
    assert len({r['Condition_color'] for r in styles}) == count


def test_nine_annotated_conditions_include_empty_groups_in_tint_selection():
    data = annotated()
    for index in range(7):
        group = f'No images {index}'
        data['groups'].append(group)
        data['design'].append({'Group': group, 'Well': f'B{index + 1:02}'})
        data['blocks']['one color'][group] = False
    before = deepcopy(data)
    style = plots.prepare_plot_style(data, 20)
    assert len(style['conditions']) == 9
    assert {r['Palette_mode'] for r in style['conditions']} == {'green_tints'}
    assert style['conditions'][0]['Condition_color'] == '#B5B1D8'
    assert style['conditions'][1]['Condition_color'] == '#004F46'
    assert style['conditions'][-1]['Condition_color'] == '#BFD3D1'
    assert data == before


def test_well_shapes_include_missing_wells_and_do_not_repeat_after_twelve():
    data = annotated()
    data['design'] = [{'Group': group, 'Well': f'{letter}{i:02}'}
                      for group, letter in [('Control', 'A'), ('Treatment', 'B')] for i in range(1, 14)]
    # This style-only fixture has no measurements; markers still cover the plate design.
    data['images'] = []
    style = plots.prepare_plot_style(data)
    first = [r for r in style['wells'] if r['Group'] == 'Control']
    second = [r for r in style['wells'] if r['Group'] == 'Treatment']
    assert len({r['Marker'] for r in first}) == 13
    assert first[0]['Marker'] == second[0]['Marker'] == 'o'
    assert first[1]['Marker'] == second[1]['Marker'] == 's'
    assert first[-1]['Marker'] == second[-1]['Marker'] == '$13$'


def test_descriptive_missing_or_ambiguous_roles_warn_and_use_categorical_lavender():
    data = annotated()
    data['blocks'] = {}
    style = plots.prepare_plot_style(data)
    assert style['warnings'] and style['conditions'][0]['Condition_color'] == '#004F46'
    assert style['conditions'][1]['Condition_color'] == '#B5B1D8'
    assert not any(r['Is_control'] for r in style['conditions'])
    for row in data['design']:
        row['Is_control'] = True
    ambiguous = plots.prepare_plot_style(data)
    assert ambiguous['warnings'] and not any(r['Is_control'] for r in ambiguous['conditions'])


def test_count_diagnostic_keeps_offsets_shapes_and_limits_without_changing_tests(tmp_path, monkeypatch):
    data = annotated([('A02', []), ('A02', [1]), ('A03', [2, 4]),
                      ('A04', [3]), ('A04', [4, 6]), ('A05', [5, 7, 9])])
    data['groups'].append('No images')
    data['design'].append({'Group': 'No images', 'Well': 'A06'})
    data['blocks']['one color']['No images'] = False
    data['particle_size'] = 200
    data['plot_style'] = plots.prepare_plot_style(data, 2)
    planned = deepcopy(data['plot_style'])
    inputs.filter_images(data, 2)
    inputs.aggregate(data)
    calculate_statistics(data)
    analytical = {key: deepcopy(data[key]) for key in ('nuclei', 'images', 'summary', 'statistics')}
    snapshots = {}
    original_save = Figure.savefig

    def inspect(fig, path, **kwargs):
        axes = [ax for ax in fig.axes if ax.get_label().startswith('Block_')]
        assert len(axes) == 1
        ax = axes[0]
        dots = [c for c in ax.collections if isinstance(c, PathCollection)]
        if path.name.startswith('Nuclei_count'):
            snapshots[path.stem] = {'limits': ax.get_ylim(), 'dots': sum(len(c.get_offsets()) for c in dots),
                                   'tests': sum(t.get_gid() == 'significance' for t in ax.texts)}
            for dot in dots:
                assert np.allclose(dot.get_facecolors(), colors.to_rgba(palette.POINT_FILL))
                assert dot.get_zorder() > max(line.get_zorder() for line in ax.lines)
            edge_colors = {colors.to_hex(c).upper() for dot in dots for c in dot.get_edgecolors()}
            assert (palette.LOW_NUCLEI_EDGE in edge_colors) == (path.stem == 'Nuclei_count_all')
        else:
            assert not dots
        if path.stem == 'Nuclei_count_all':
            assert [colors.to_hex(p.get_facecolor()).upper() for p in ax.patches] == ['#B5B1D8', '#004F46']
        return original_save(fig, path, **kwargs)

    monkeypatch.setattr(Figure, 'savefig', inspect)
    plots.render_plots(data, tmp_path / 'Plots', plot_format='png')
    assert snapshots['Nuclei_count_all']['limits'] == snapshots['Nuclei_count']['limits']
    assert snapshots['Nuclei_count_all']['dots'] == 6
    assert snapshots['Nuclei_count']['dots'] == 3
    assert snapshots['Nuclei_count_all']['tests'] == 0
    assert snapshots['Nuclei_count']['tests'] == 2
    assert all(data[key] == value for key, value in analytical.items())
    assert data['plot_style'] == planned
    rows = {name: {row['Mask_name']: row for row in data['plot_data'] if row['Plot'] == name}
            for name in ('Nuclei_count_all', 'Nuclei_count')}
    for identity, row in rows['Nuclei_count'].items():
        previous = rows['Nuclei_count_all'][identity]
        assert all(row[key] == previous[key] for key in palette.POINT_COLUMNS)
        assert not row['Below_min_nuclei']
    assert rows['Nuclei_count_all']['mask_2']['Value'] == 2
    assert rows['Nuclei_count_all']['mask_2']['Point_edgecolor'] == palette.INK
    info = next(row for row in data['plots'] if row['Plot'] == 'Nuclei_count_all')
    assert 'no tests' in info['Display_caption'] and '200 px²' in info['Display_caption']


def test_violin_and_inner_box_use_exact_fill_and_preserve_quartiles():
    values = [1, 3, 4, 8, 9, 17, 40]
    style = palette.condition_styles(['Treatment'])[0]
    fig, ax = plt.subplots()
    try:
        plots.draw_distribution(ax, values, 1, 'nucleus', style)
        violin = next(c for c in ax.collections if isinstance(c, PolyCollection))
        assert colors.to_hex(violin.get_facecolors()[0]).upper() == '#004F46'
        assert colors.to_hex(ax.patches[0].get_facecolor()).upper() == '#004F46'
        assert colors.to_hex(ax.patches[0].get_edgecolor()).upper() == '#FFFFFF'
        assert not any(isinstance(c, PathCollection) for c in ax.collections)
        vertices = ax.patches[0].get_path().vertices
        assert (vertices[:, 1].min(), vertices[:, 1].max()) == tuple(np.percentile(values, [25, 75]))
        median = next(line for line in ax.lines if line.get_color() == '#FFFFFF')
        assert set(median.get_ydata()) == {np.median(values)}
    finally:
        plt.close(fig)
