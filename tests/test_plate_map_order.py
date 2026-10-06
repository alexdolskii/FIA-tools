"""Explicit display order must preserve plate identities, populations and inference."""

import json
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

import marker_intensity_report as report
import marker_report_data as inputs
import marker_report_plots as plots
import pytest
from marker_report_statistics import calculate_statistics
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill
from test_collect_marker_intensity_results import sheet_rows
from test_marker_intensity_report import MARKER, annotated, collection, plate


def add_order(path, rows, headers=('Order', 'Group'), columns=(15, 16)):
    book = load_workbook(path)
    sheet = book.active
    for column, header in zip(columns, headers):
        sheet.cell(1, column, header)
    for index, values in enumerate(rows, 2):
        for column, value in zip(columns, values):
            sheet.cell(index, column, value)
    book.save(path)
    book.close()
    return path


def read_design(path, unit='nucleus', data=None):
    data = data or {'files': {}, 'images': [], 'nuclei': [], 'markers': [MARKER]}
    data.setdefault('files', {})
    return inputs.annotate(data, path, [MARKER], unit)


@pytest.mark.parametrize('headers,columns', [(('Order', 'Group'), (15, 16)),
                                          ((' GROUPS ', 'ORDER'), (20, 18))])
def test_numeric_order_controls_axes_even_with_control_last(tmp_path, headers, columns):
    rows = [('30', 'Control'), (10, 'Treatment')]
    if headers[0].strip() == 'GROUPS':
        rows = [(name, rank) for rank, name in rows]
    path = add_order(plate(tmp_path / 'plate.xlsx'), rows, headers, columns)
    data = read_design(path)
    assert data['groups'] == ['Treatment', 'Control']
    assert [r['Order'] for r in data['group_order']] == [10, 30]
    assert data['group_order_source'] == 'order_table'
    assert [r['Well'] for r in data['design']] == ['A02', 'A03']
    assert list(data['blocks'].values()) == [{'Treatment': False, 'Control': True}]
    style = plots.prepare_plot_style(data)
    assert style['panels'][0]['groups'] == ['Treatment', 'Control']
    control = next(r for r in style['conditions'] if r['Group'] == 'Control')
    assert control['X_position'] == 2 and control['Condition_color'] == '#B5B1D8'


@pytest.mark.parametrize('empty_headers', [False, True])
def test_old_or_empty_order_table_keeps_plate_order(tmp_path, empty_headers):
    path = plate(tmp_path / 'plate.xlsx')
    if empty_headers:
        add_order(path, [(None, None), (' ', ' ')])
    data = read_design(path)
    assert data['groups'] == ['Control', 'Treatment']
    assert data['group_order_source'] == 'plate_grid'


@pytest.mark.parametrize('rows,reason', [
    ([(1, 'Control'), (1, 'Treatment')], 'O3: repeated Order'),
    ([(1, 'Control'), (2, 'Control')], 'P3: repeated Group'),
    ([(1, 'control'), (2, 'Treatment')], 'P2: unknown Group'),
    ([(1, 'Control')], 'incomplete'),
    ([(None, 'Control')], 'O2/P2: fill both'),
    ([(1, None)], 'O2/P2: fill both'),
    ([(0, 'Control'), (2, 'Treatment')], 'O2: Order must'),
    ([(-1, 'Control'), (2, 'Treatment')], 'O2: Order must'),
    ([(1.5, 'Control'), (2, 'Treatment')], 'O2: Order must'),
    ([(True, 'Control'), (2, 'Treatment')], 'O2: Order must'),
    ([('first', 'Control'), (2, 'Treatment')], 'O2: Order must'),
    ([('=1', 'Control'), (2, 'Treatment')], 'O2: Order must'),
    ([(1, '="Control"'), (2, 'Treatment')], 'P2: Group must be literal'),
])
def test_bad_explicit_order_is_actionable_before_calculation(tmp_path, rows, reason):
    path = add_order(plate(tmp_path / 'plate.xlsx'), rows)
    with pytest.raises(inputs.ValidationError, match=reason):
        read_design(path)
    with pytest.raises(inputs.PlateMapError) as error:
        inputs.select_template(tmp_path, path)
    assert error.value.code == 'INVALID_PLATE_MAP' and 'Order/Group' in error.value.action


@pytest.mark.parametrize('change,reason', [('single', 'expected one'), ('duplicate', 'expected one'),
                                        ('merged', 'Do not merge')])
def test_ambiguous_headers_or_merged_table_are_rejected(tmp_path, change, reason):
    path = add_order(plate(tmp_path / 'plate.xlsx'), [(1, 'Control'), (2, 'Treatment')])
    book = load_workbook(path)
    sheet = book.active
    if change == 'single':
        sheet['P1'] = None
    elif change == 'duplicate':
        sheet['Q1'] = 'Groups'
    else:
        sheet.merge_cells('O2:O3')
    book.save(path)
    book.close()
    with pytest.raises(inputs.ValidationError, match=reason):
        read_design(path)


@pytest.mark.parametrize('unit', [None, 'nucleus', 'well'])
def test_multiple_panels_follow_first_rank_without_changing_blocks(tmp_path, unit):
    groups = {'A02': ('A control', True), 'A03': ('A treatment', False),
              'B02': ('B control', True), 'B03': ('B treatment', False)}
    path = plate(tmp_path / 'plate.xlsx', groups)
    book = load_workbook(path)
    for addr in ('C3', 'D3'):
        book.active[addr].fill = PatternFill('solid', fgColor='CCBBAA')
    book.save(path)
    book.close()
    add_order(path, [(40, 'A control'), ('20', 'A treatment'),
                     (30, 'B control'), (10, 'B treatment')])
    data = read_design(path, unit)
    panels = plots.plot_panels(data)
    assert [p['groups'] for p in panels] == [['B treatment', 'B control'], ['A treatment', 'A control']]
    assert len({p['block'] for p in panels}) == 2


def test_order_survives_filtering_and_preserves_statistics(tmp_path):
    base = annotated()
    groups = {r['Well']: (r['Group'], r['Group'] == 'Control') for r in base['design']}
    groups['A06'] = ('No images', False)
    path = plate(tmp_path / 'plate.xlsx', groups)
    original = read_design(path, data=deepcopy(base))
    add_order(path, [(30, 'Control'), (20, 'Treatment'), (10, 'No images')])
    ordered = read_design(path, data=deepcopy(base))
    key = lambda r: (r['Block'], r['Metric'], r['Control'], r['Treatment'])
    for data in (original, ordered):
        inputs.aggregate(data)
        calculate_statistics(data)
    assert any(r['Status'] == 'TESTED' for r in original['statistics'])
    assert sorted(original['statistics'], key=key) == sorted(ordered['statistics'], key=key)
    for data in (original, ordered):
        data['plot_style'] = plots.prepare_plot_style(data, 3)
        inputs.filter_images(data, 3)
        inputs.aggregate(data)
        calculate_statistics(data)
    assert sorted(original['statistics'], key=key) == sorted(ordered['statistics'], key=key)
    assert original['images'] == ordered['images'] and original['nuclei'] == ordered['nuclei']
    assert ordered['plot_style']['panels'][0]['groups'] == ['No images', 'Treatment', 'Control']
    assert ordered['counts_by_group']['Treatment']['Images'] == 0
    assert [r['Group'] for r in ordered['summary'][:3]] == ordered['groups']


def test_explicit_order_is_saved_in_complete_report(tmp_path):
    path = collection(tmp_path)
    add_order(path / 'arbitrary layout.xlsx', [(2, 'Control'), (1, 'Treatment')])
    ok, output = report.create_report(path, path.parent.parent, markers=[MARKER],
                                     stats_unit='nucleus', min_nuclei=1, plot_format='both')
    assert ok
    assert [r['Group'] for r in sheet_rows(output / report.WORKBOOK, 'Group_Order')] == ['Treatment', 'Control']
    labels = sheet_rows(output / report.WORKBOOK, 'Plot_Labels')
    assert [(r['Group'], r['X_position']) for r in labels] == [('Treatment', 1), ('Control', 2)]
    style = json.loads((output / 'plot_style.json').read_text())
    assert style['Condition_order'] == ['Treatment', 'Control']
    assert style['Group_order'][0]['Order_cell'] == 'O3'
    status = json.loads((output / 'report_status.json').read_text())
    assert status['Condition_order_source'] == 'order_table'
    assert (output / 'Group_Order.csv').is_file()
    log = (path.parent / '5_marker_intensity_report.log').read_text()
    assert 'CONDITION_ORDER | source=order_table' in log
    assert len(list((output / 'Plots').glob('*.pdf'))) == len(list((output / 'Plots').glob('*.png'))) > 0


def test_incomplete_order_is_recorded_without_starting_calculations(tmp_path, monkeypatch, capsys):
    path = collection(tmp_path)
    add_order(path / 'arbitrary layout.xlsx', [(1, 'Control')])
    monkeypatch.setattr(inputs, 'aggregate', lambda *a, **kw: pytest.fail('No calculations on invalid order'))
    ok, output = report.create_report(path, path.parent.parent, markers=[MARKER], stats_unit='nucleus')
    assert not ok
    status = json.loads((output / 'report_status.json').read_text())
    assert status['Error_code'] == 'INVALID_PLATE_MAP' and status['Stage'] == 'plate-map validation'
    assert 'incomplete' in status['Plate_map_details'] and 'Treatment' in status['Plate_map_details']
    assert 'incomplete' in capsys.readouterr().out
    assert 'incomplete' in (path.parent / '5_marker_intensity_report.log').read_text()
    assert not (output / 'Plots').exists()


def test_distributed_template_is_blank_and_usable(tmp_path):
    template = Path(__file__).resolve().parents[1] / 'templates' / 'FIA_96_well_plate_template.xlsx'
    book = load_workbook(template)
    sheet = book['Plate Map']
    assert book.sheetnames == ['Plate Map', 'Instructions'] and sheet.freeze_panes == 'B2'
    assert all(cell.value is None and not cell.font.bold for row in sheet['B2:M9'] for cell in row)
    assert all(cell.value is None for row in sheet['O2:P97'] for cell in row)
    assert all(area.min_col > 13 for cf in sheet.conditional_formatting for area in cf.sqref.ranges)
    assert len(sheet.data_validations.dataValidation) == 2
    sheet['C2'] = 'Vehicle'
    sheet['D2'] = 'Drug'
    sheet['C2'].font = Font(bold=True)
    sheet['O2'], sheet['P2'] = 2, 'Vehicle'
    sheet['O3'], sheet['P3'] = 1, 'Drug'
    filled = tmp_path / 'filled.xlsx'
    book.save(filled)
    book.close()
    assert read_design(filled)['groups'] == ['Drug', 'Vehicle']
    with ZipFile(template) as archive:
        content = b'\n'.join(archive.read(n) for n in archive.namelist() if n.endswith('.xml'))
    assert b'Bkar_' not in content and b'upcoming' not in content
