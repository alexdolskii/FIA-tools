"""Deterministic report styles; no filtering, aggregation or statistical decisions."""

import hashlib
import json

STYLE_VERSION = 1
BACKGROUND = '#FFFFFF'
INK = '#111314'
POINT_FILL = '#D0D0D0'
LOW_NUCLEI_EDGE = '#D62728'
PALETTE = (
    ('Dusky Green', '#004F46'),
    ('Grayish Lavender A', '#B5B1D8'),
    ('Orange', '#F37420'),
    ('Deep Indigo', '#051230'),
    ('Dull Blue Violet', '#80719E'),
    ('Ivory Buff', '#EBD3A2'),
    ('Violet', '#4F4086'),
    ('Verditter Blue', '#6FB5A8'),
)
WELL_MARKERS = (
    ('o', 'circle'), ('s', 'square'), ('^', 'triangle up'), ('v', 'triangle down'),
    ('D', 'diamond'), ('P', 'filled plus'), ('X', 'filled X'), ('<', 'triangle left'),
    ('>', 'triangle right'), ('h', 'hexagon'), ('p', 'pentagon'), ('*', 'star'),
)
POINT_COLUMNS = ['Marker', 'Marker_name', 'Point_facecolor', 'Point_edgecolor',
                 'X_offset', 'X_position', 'Below_min_nuclei', 'Used_for_analysis']
CONDITION_COLUMNS = ['Condition_color', 'Color_name', 'Median_color', 'Is_control',
                     'Control_identified', 'Palette_mode', 'X_position']


def luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def contrast(first, second):
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


def median_color(fill):
    return max((INK, BACKGROUND), key=lambda color: contrast(fill, color))


def green_tints(count):
    """Even sRGB mixing with 0..75% white; this sequence encodes no quantity."""
    base = [int(PALETTE[0][1][i:i + 2], 16) for i in (1, 3, 5)]
    result = []
    for index in range(count):
        white = 0.75 * index / (count - 1) if count > 1 else 0
        color = '#' + ''.join(f'{round(c * (1 - white) + 255 * white):02X}' for c in base)
        name = 'Dusky Green' if not white else f'Dusky Green tint ({white:.6g} white)'
        result.append((name, color))
    return result


def condition_styles(groups, control=None):
    """Keep axis order. Without a control lavender is an ordinary second color."""
    has_control = control is not None
    if has_control and control not in groups:
        raise ValueError('Control must belong to the panel')
    if len(groups) <= len(PALETTE):
        colors = [color for color in PALETTE if color != PALETTE[1]] if has_control else list(PALETTE)
        mode = 'categorical'
    else:
        colors = green_tints(len(groups) - 1)
        if not has_control:
            colors.insert(1, PALETTE[1])
        mode = 'green_tints'
    colors = iter(colors)
    result = []
    for index, group in enumerate(groups, 1):
        name, fill = PALETTE[1] if has_control and group == control else next(colors)
        result.append({'Group': group, 'Condition_color': fill, 'Color_name': name,
                       'Median_color': median_color(fill), 'Is_control': has_control and group == control,
                       'Control_identified': has_control, 'Palette_mode': mode, 'X_position': index})
    return result


def panel_control(panel, design, blocks):
    """Only explicit plate roles qualify; a name such as 'Control' is irrelevant."""
    members = blocks.get(panel['block'], {})
    roles = {group: ({bool(members[group])} if group in members else
                     {bool(row.get('Is_control', False)) for row in design if row['Group'] == group})
             for group in panel['groups']}
    controls = [group for group, values in roles.items() if values == {True}]
    if len(controls) == 1 and all(len(values) <= 1 for values in roles.values()):
        return controls[0]
    return None


def image_identity(row):
    return row['Group'], row['Well'], row['Image_name'], row['Mask_name']


def point_offset(row):
    """Stable by image identity, independently of population order or filtering."""
    identity = json.dumps(image_identity(row), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    number = int.from_bytes(hashlib.sha256(identity).digest()[:8], 'big')
    return (number / (2 ** 64 - 1) - 0.5) * 0.34


def build_style(panels, design, blocks, images, min_nuclei):
    """Build the full style manifest from the plate design and pre-filter images."""
    conditions, wells, warnings = [], [], []
    for panel in panels:
        control = panel_control(panel, design, blocks)
        if control is None:
            warnings.append(f'{panel["id"]}: no unambiguous control is marked; colors are categorical. '
                            'Lavender does not designate a control in this panel.')
        conditions.extend({'Panel': panel['id'], **item} for item in condition_styles(panel['groups'], control))
        for group in panel['groups']:
            # Include wells without images so a later absence cannot reassign symbols.
            names = sorted({row['Well'] for row in design + images if row['Group'] == group})
            for index, well in enumerate(names, 1):
                marker, name = WELL_MARKERS[index - 1] if index <= len(WELL_MARKERS) else (f'${index}$', f'number {index}')
                wells.append({'Group': group, 'Well': well, 'Well_index': index,
                              'Marker': marker, 'Marker_name': name})
    by_group = {row['Group']: row for row in conditions}
    by_well = {(row['Group'], row['Well']): row for row in wells}
    image_styles = []
    for image in images:
        well = by_well[image['Group'], image['Well']]
        low = image['Non_border_nuclei_count'] < min_nuclei
        offset = point_offset(image)
        image_styles.append({key: image[key] for key in ('Group', 'Well', 'Image_name', 'Mask_name')} | {
            'Marker': well['Marker'], 'Marker_name': well['Marker_name'],
            'Point_facecolor': POINT_FILL, 'Point_edgecolor': LOW_NUCLEI_EDGE if low else INK,
            'X_offset': offset, 'X_position': by_group[image['Group']]['X_position'] + offset,
            'Below_min_nuclei': low, 'Used_for_analysis': not low})
    return {'Style_version': STYLE_VERSION, 'Color_space': 'sRGB', 'Background': BACKGROUND,
            'Source': 'User-selected Wada colors (Elwyn sRGB reconstruction); adapted green tints. '
                      'Not an original numbered historical combination.',
            'Palette': [{'Color_name': name, 'HEX': color} for name, color in PALETTE],
            'Control_policy': 'Explicit plate roles only. Without an unambiguous control, lavender is an ordinary color.',
            'Tint_policy': 'Nine or more conditions: even sRGB mixing of Dusky Green with 0..75% white. '
                           'No effect, dose or significance encoding. Without a control the second color is ordinary lavender.',
            'Well_policy': 'Shapes restart within each condition; sorted annotated well IDs, then numeric markers after 12.',
            'Point_policy': 'One point per image. Grey fill; red edge only below Min_nuclei. Nucleus violins have no dots.',
            'Min_nuclei': min_nuclei, 'panels': panels, 'conditions': conditions, 'wells': wells,
            'images': image_styles, 'warnings': warnings}
