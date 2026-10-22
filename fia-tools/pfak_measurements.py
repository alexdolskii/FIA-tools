"""Whole-field pFAK measurements from one inclusive SUM32 threshold mask.

No background subtraction, rescaling, morphology, hole filling or watershed.
Objects are 8-connected positive regions, including regions touching the field
boundary. Nuclear boundary policy changes denominators only.
"""

import math

import numpy as np
import spatial_calibration as spatial
from scipy import ndimage


def threshold_settings(values):
    """Record requested and effective float32 limits, as in UMA Area assay."""
    if not 1 <= len(values) <= 2:
        raise ValueError('Supply -t LOWER [UPPER].')
    requested = [float(value) for value in values]
    lower = requested[0]
    upper = requested[1] if len(requested) == 2 else math.inf
    maximum = float(np.finfo(np.float32).max)
    if not math.isfinite(lower) or lower < 0 or lower > maximum:
        raise ValueError('Lower threshold must be finite, nonnegative and within float32 range.')
    if math.isnan(upper) or upper < lower or (math.isfinite(upper) and upper > maximum):
        raise ValueError('Upper threshold must be >= lower and within float32 range, or inf.')
    return {'requested_lower': lower, 'requested_upper': upper if math.isfinite(upper) else None,
            'lower': float(np.float32(lower)),
            'upper': float(np.float32(upper)) if math.isfinite(upper) else maximum,
            'upper_unbounded': not math.isfinite(upper)}


def nuclear_population(labels, include_border=False):
    """Use the existing final IDs, including the visible area of border IDs."""
    labels = np.asarray(labels)
    if labels.ndim != 2 or not labels.size or labels.dtype.kind not in 'iu' or labels.min() < 0:
        raise ValueError('Nucleus IDs must be a nonempty, nonnegative 2D integer array.')
    ids, areas = np.unique(labels, return_counts=True)
    area_by_id = {int(i): int(a) for i, a in zip(ids, areas) if i}
    border = set(map(int, np.unique(np.concatenate(
        (labels[0], labels[-1], labels[:, 0], labels[:, -1]))))) - {0}
    selected = set(area_by_id) if include_border else set(area_by_id) - border
    return {'Nuclei_count_total': len(area_by_id), 'Border_nuclei_count': len(border),
            'Non_border_nuclei_count': len(area_by_id) - len(border),
            'Normalization_nuclei_count': len(selected),
            'Nuclear_area_total_px2': sum(area_by_id.values()),
            'Normalization_nuclear_area_px2': sum(area_by_id[i] for i in selected),
            'Border_nuclei_fraction': len(border) / len(area_by_id) if area_by_id else None,
            'Include_border_nuclei': bool(include_border)}, area_by_id, border


def measure_projection(projection, nuclei_ids, limits, calibration, include_border=False):
    """Measure mask geometry and original SUM pixels, never binary intensities."""
    projection = np.asarray(projection)
    if projection.dtype != np.float32 or projection.ndim != 2 or not projection.size:
        raise ValueError('Expected a native-size float32 SUM projection.')
    if not np.isfinite(projection).all():
        raise ValueError('SUM projection contains non-finite intensities or overflow.')
    if projection.shape != np.shape(nuclei_ids):
        raise ValueError('SUM projection and nucleus ID map dimensions differ; resizing is disabled.')
    spatial.validate_record(calibration)
    counts, _, _ = nuclear_population(nuclei_ids, include_border)
    pixel_area = (float(calibration['Pixel_size_X_um']) * float(calibration['Pixel_size_Y_um'])
                  if spatial.calibrated(calibration) else 1.0)
    area_unit = 'um2' if spatial.calibrated(calibration) else 'px2'
    positive = (projection >= limits['lower']) & (projection <= limits['upper'])
    object_ids, count = ndimage.label(positive, structure=np.ones((3, 3), dtype=bool))
    sizes = np.bincount(object_ids.ravel(), minlength=count + 1)
    sums = np.bincount(object_ids.ravel(), weights=projection.ravel(), minlength=count + 1)
    edge_ids = set(map(int, np.unique(np.concatenate(
        (object_ids[0], object_ids[-1], object_ids[:, 0], object_ids[:, -1]))))) - {0}
    centroids = ndimage.center_of_mass(positive, object_ids, range(1, count + 1)) if count else []
    objects = [{'Object_ID': i, 'Area_px2': int(sizes[i]),
                'Area_um2': float(sizes[i] * pixel_area) if area_unit == 'um2' else None,
                'Area': float(sizes[i] * pixel_area), 'Area_unit': area_unit,
                'RawIntDen': float(sums[i]), 'IntDen': float(sums[i] * pixel_area),
                'Touches_border': i in edge_ids,
                'Centroid_X_px': float(centroids[i - 1][1]), 'Centroid_Y_px': float(centroids[i - 1][0])}
               for i in range(1, count + 1)]
    n_positive = int(positive.sum())
    raw = float(projection[positive].sum(dtype=np.float64))
    area = n_positive * pixel_area
    nuclear_area = counts['Normalization_nuclear_area_px2'] * pixel_area
    intensity = raw * pixel_area
    image = {**spatial.columns(calibration), **counts, 'Area_unit': area_unit,
             'Normalization_nuclear_area': nuclear_area,
             'Normalization_mean_nuclear_area': nuclear_area / counts['Normalization_nuclei_count']
             if counts['Normalization_nuclei_count'] else None,
             'pFAK_Positive_pixels': n_positive, 'pFAK_Area': area,
             'pFAK_Coverage_percent': 100.0 * n_positive / projection.size,
             'pFAK_RawIntDen': raw, 'pFAK_IntDen': intensity,
             'pFAK_Object_count': count, 'pFAK_Border_object_count': len(edge_ids),
             'pFAK_Median_object_area': float(np.median(sizes[1:]) * pixel_area) if count else None,
             'Projection_Min': float(projection.min()), 'Projection_Max': float(projection.max())}
    for field in ('pFAK_RawIntDen', 'pFAK_IntDen', 'pFAK_Area'):
        image[field + '_per_nucleus'] = (image[field] / counts['Normalization_nuclei_count']
                                         if counts['Normalization_nuclei_count'] else None)
        image[field + '_per_nuclear_area'] = image[field] / nuclear_area if nuclear_area else None
    if not math.isclose(sum(row['RawIntDen'] for row in objects), raw, rel_tol=1e-12, abs_tol=1e-8):
        raise ValueError('Object intensities do not reconcile with the whole positive mask.')
    return image, objects, positive.astype(np.uint8) * 255, object_ids.astype(np.uint32)
