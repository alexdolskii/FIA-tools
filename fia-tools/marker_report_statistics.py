"""Welch comparisons with one planned Holm family per plate-map color block."""

import math
import warnings

import numpy as np
from marker_report_data import COUNT, metric_specs
from scipy.stats import ttest_ind

STAT_COLUMNS = [
    'Block', 'Category', 'Marker', 'Metric', 'Unit', 'Control', 'Treatment',
    'Requested_unit', 'Effective_unit', 'Method', 'N_control', 'N_treatment',
    'Control_images', 'Treatment_images', 'Control_wells', 'Treatment_wells',
    'Control_nuclei', 'Treatment_nuclei', 'Mean_control', 'Mean_treatment',
    'Difference_treatment_minus_control', 'CI95_low', 'CI95_high', 'T_statistic',
    'Degrees_of_freedom', 'P_raw', 'P_Holm', 'Family_size_planned', 'Family_tests_available',
    'Significant_Holm_0_05', 'Status', 'Reason',
]


def observations(data, field, group):
    """Return the explicitly selected experimental unit, without pooling wells."""
    if data['stats_unit'] == 'well':
        rows = [r for r in data['well_values'] if r['Metric'] == field and r['Group'] == group]
        return [r['Value'] for r in rows if r['Value'] is not None], 'well'
    population = data['images'] if field == COUNT else data['nuclei']
    return [r[field] for r in population if r['Group'] == group and r[field] is not None], (
        'image' if field == COUNT else 'nucleus')


def contribution_counts(data, field, group):
    images = [r for r in data['image_values']
              if r['Metric'] == field and r['Group'] == group and r['Value'] is not None]
    return {'images': len(images), 'wells': len({r['Well'] for r in images}),
            'nuclei': sum(r['N_nuclei'] if field == COUNT else r['N_values'] for r in images)}


def holm_adjust(p_values, planned_size):
    """Unavailable planned comparisons still count toward the family size."""
    if planned_size < len(p_values) or any(not math.isfinite(p) or not 0 <= p <= 1 for p in p_values):
        raise ValueError('Invalid Holm family')
    adjusted, previous = [None] * len(p_values), 0.0
    for rank, index in enumerate(sorted(range(len(p_values)), key=p_values.__getitem__)):
        previous = max(previous, min(1.0, p_values[index] * (planned_size - rank)))
        adjusted[index] = previous
    return adjusted


def comparison(data, color, spec, control, treatment, family_size):
    category, marker, field, unit = spec
    control_values, effective = observations(data, field, control)
    treatment_values, _ = observations(data, field, treatment)
    row = dict.fromkeys(STAT_COLUMNS)
    row.update(Block=color, Category=category, Marker=marker, Metric=field, Unit=unit,
               Control=control, Treatment=treatment, Requested_unit=data['stats_unit'],
               Effective_unit=effective, Method='Welch t-test, two-sided; Holm per color block',
               N_control=len(control_values), N_treatment=len(treatment_values),
               Family_size_planned=family_size, Status='NOT_TESTED', Reason='')
    for prefix, group, values in (('Control', control, control_values), ('Treatment', treatment, treatment_values)):
        row.update({prefix + '_' + key: value for key, value in contribution_counts(data, field, group).items()})
        row['Mean_' + prefix.lower()] = float(np.mean(values)) if values else None
    if control_values and treatment_values:
        row['Difference_treatment_minus_control'] = row['Mean_treatment'] - row['Mean_control']
    if min(len(control_values), len(treatment_values)) < 2:
        row['Reason'] = 'At least two usable observations per group are required for the effective unit.'
        return row
    arrays = [np.asarray(values, dtype=float) for values in (control_values, treatment_values)]
    if any(not np.isfinite(values).all() for values in arrays):
        row['Reason'] = 'Non-finite observations.'
        return row
    if all(np.ptp(values) == 0 for values in arrays):
        row['Reason'] = 'Both groups have zero variance; a Welch standard error is undefined.'
        return row
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        result = ttest_ind(arrays[1], arrays[0], equal_var=False, alternative='two-sided')
        interval = result.confidence_interval(confidence_level=0.95)
    numbers = (result.statistic, result.pvalue, result.df, interval.low, interval.high)
    if not all(math.isfinite(float(value)) for value in numbers):
        row['Reason'] = 'The Welch calculation did not produce finite statistics.'
        return row
    row.update(T_statistic=float(result.statistic), P_raw=float(result.pvalue),
               Degrees_of_freedom=float(result.df), CI95_low=float(interval.low), CI95_high=float(interval.high),
               Status='TESTED', Reason='')
    return row


def calculate_statistics(data):
    if data['stats_unit'] is None:
        data['statistics'] = []
        return data
    specs, rows = metric_specs(data['markers']), []
    for color, groups in data['blocks'].items():
        control = next(group for group, is_control in groups.items() if is_control)
        treatments = [group for group in groups if group != control]
        family_size = len(specs) * len(treatments)
        family = [comparison(data, color, spec, control, treatment, family_size)
                  for spec in specs for treatment in treatments]
        tested = [row for row in family if row['P_raw'] is not None]
        adjusted = holm_adjust([row['P_raw'] for row in tested], family_size)
        for row, p_value in zip(tested, adjusted):
            row['P_Holm'] = p_value
            row['Significant_Holm_0_05'] = p_value < 0.05
        for row in family:
            row['Family_tests_available'] = len(tested)
        rows.extend(family)
    data['statistics'] = rows
    return data
