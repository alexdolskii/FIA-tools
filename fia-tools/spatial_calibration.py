"""Per-image spatial calibration; unknown scales never acquire physical units."""

from assay_layout import ASSAY_DIR
from run_resources import temporary_path

import hashlib
import json
import math
from pathlib import Path
from xml.etree import ElementTree

CALIBRATION_COLUMNS = [
    'Pixel_size_X_um', 'Pixel_size_Y_um', 'Calibration_status',
    'Calibration_source', 'Calibration_note',
]
LENGTH_FIELDS = ('Perimeter_px', 'Major_axis_px', 'Minor_axis_px',
                 'Feret_max_px', 'Feret_min_px', 'Equivalent_diameter_px')
PHYSICAL_FIELDS = {'Area_px2': 'Area_um2', **{
    field: field.removesuffix('_px') + '_um' for field in LENGTH_FIELDS}}
SHAPE_FIELDS = ('Circularity', 'Aspect_ratio', 'Solidity', 'Roundness', 'Eccentricity')
CALIBRATED_SHAPES = {field: field + '_calibrated' for field in SHAPE_FIELDS}
EXTRA_METRICS = [*PHYSICAL_FIELDS.values(), *CALIBRATED_SHAPES.values()]
MANIFEST = 'spatial_calibration.json'
UNIT_TO_UM = {
    'um': 1.0, 'micron': 1.0, 'microns': 1.0, 'micrometer': 1.0,
    'micrometers': 1.0, 'micrometre': 1.0, 'micrometres': 1.0,
    'nm': 0.001, 'nanometer': 0.001, 'nanometre': 0.001,
    'mm': 1000.0, 'millimeter': 1000.0, 'millimetre': 1000.0,
    'cm': 10000.0, 'm': 1000000.0,
}


def uncalibrated(note='Physical XY calibration is unavailable.', source=''):
    return dict(Pixel_size_X_um=None, Pixel_size_Y_um=None,
                Calibration_status='uncalibrated', Calibration_source=str(source),
                Calibration_note=note)


def normalize(x, y, unit, source='', y_unit=None):
    """Normalize explicit physical units, rejecting default pixel scales and bad values."""
    def convert(value, units):
        key = (str(units).strip().lower().replace('µ', 'u').replace('μ', 'u')
               .replace('\\u00b5', 'u').replace('\\u03bc', 'u'))
        result = float(value) * UNIT_TO_UM[key]
        if not math.isfinite(result) or result <= 0:
            raise ValueError('Pixel size must be finite and positive')
        return result
    try:
        sx, sy = convert(x, unit), convert(y, unit if y_unit is None else y_unit)
    except (ValueError, TypeError, KeyError, OverflowError):
        return uncalibrated('Missing/invalid XY sizes or unsupported physical units.', source)
    return dict(Pixel_size_X_um=sx, Pixel_size_Y_um=sy,
                Calibration_status='calibrated', Calibration_source=str(source),
                Calibration_note='')


def calibrated(record):
    return record.get('Calibration_status') == 'calibrated'


def columns(record):
    return {field: record.get(field) for field in CALIBRATION_COLUMNS}


def area_um2(area, calibration):
    if not calibrated(calibration) or area is None:
        return None
    return float(area) * float(calibration['Pixel_size_X_um']) * float(calibration['Pixel_size_Y_um'])


def bioformats_calibration(reader):
    """Read OME lengths from a reader initialized with an OME metadata store."""
    store = reader.getMetadataStore()
    series = int(reader.getSeries())
    x, y = store.getPixelsPhysicalSizeX(series), store.getPixelsPhysicalSizeY(series)
    if x is None or y is None:
        return uncalibrated(source='Bio-Formats OME metadata')
    return normalize(float(x.value().doubleValue()), float(y.value().doubleValue()),
                     str(x.unit().getSymbol()), 'Bio-Formats OME metadata',
                     str(y.unit().getSymbol()))


def tiff_calibration(path):
    """Use OME or explicit ImageJ units; generic TIFF print-resolution tags are ignored."""
    import tifffile
    with tifffile.TiffFile(path) as image:
        page = image.pages[0]
        shape = (int(page.imagelength), int(page.imagewidth))
        if image.ome_metadata:
            root = ElementTree.fromstring(image.ome_metadata)
            pixels = root.find('.//{*}Pixels')
            if pixels is not None:
                # OME defines micrometers as the default physical-size unit.
                cal = normalize(pixels.get('PhysicalSizeX'), pixels.get('PhysicalSizeY'),
                                pixels.get('PhysicalSizeXUnit', 'um'), 'OME-TIFF metadata',
                                pixels.get('PhysicalSizeYUnit', 'um'))
                return cal, shape
        metadata = image.imagej_metadata or {}
        if metadata.get('unit'):
            def spacing(name):
                tag = page.tags.get(name)
                if tag is None:
                    return None
                numerator, denominator = tag.value
                return denominator / numerator if numerator else None
            return normalize(spacing('XResolution'), spacing('YResolution'),
                             metadata['unit'], 'ImageJ TIFF metadata'), shape
        return uncalibrated(source='TIFF metadata'), shape


def read_source(path):
    """Read metadata only, without importing the image pixels or changing their scale."""
    path = Path(path)
    if path.suffix.lower() in ('.tif', '.tiff'):
        return tiff_calibration(path)
    from scyjava import jimport
    reader = jimport('loci.formats.ImageReader')()
    try:
        reader.setGroupFiles(False)
        reader.setMetadataStore(jimport('loci.formats.MetadataTools').createOMEXMLMetadata())
        reader.setId(str(path))
        if int(reader.getSeriesCount()) != 1:
            return uncalibrated('Multiple source series; calibration is ambiguous.'), None
        return bioformats_calibration(reader), (int(reader.getSizeY()), int(reader.getSizeX()))
    finally:
        reader.close()


def imagej_calibration(imp, calibration, jimport):
    """Attach spatial units to an output image without resampling or changing its pixels."""
    cal = jimport('ij.measure.Calibration')()
    if calibrated(calibration):
        cal.pixelWidth = float(calibration['Pixel_size_X_um'])
        cal.pixelHeight = float(calibration['Pixel_size_Y_um'])
        cal.setUnit('um')
    imp.setIgnoreGlobalCalibration(True)
    imp.setCalibration(cal)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def save_snapshot(path, calibration, shape, original=''):
    """Bind a calibration snapshot to the exact generated TIFF, not just its name."""
    path = Path(path)
    manifest = path.parent / MANIFEST
    data = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
    data[path.name] = {**columns(calibration), 'Shape_YX': list(shape),
                       'Image_SHA256': digest(path), 'Original_file': str(original)}
    temporary = temporary_path(manifest, manifest.with_name('.' + MANIFEST + '.tmp'))
    temporary.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    temporary.replace(manifest)


def snapshot_calibration(path, shape):
    path = Path(path)
    manifest = path.parent / MANIFEST
    if not manifest.exists():
        return None
    data = json.loads(manifest.read_text(encoding='utf-8'))
    record = data.get(path.name)
    if record is None:
        return None
    if record.get('Shape_YX') != list(shape) or record.get('Image_SHA256') != digest(path):
        raise ValueError(f'Spatial calibration snapshot no longer matches {path.name}')
    validate_record(record)
    return columns(record)


def projection_calibration(path, shape, allow_bioformats=True):
    """Recover legacy native-size runs only when a unique original has identical XY size."""
    path = Path(path)
    snapshot = snapshot_calibration(path, shape)
    if snapshot is not None:
        return snapshot
    assay = next((parent for parent in path.parents if parent.name == ASSAY_DIR), path.parent.parent)
    stem = path.stem.removesuffix('_nuclei_projection').removesuffix('_foci_projection')
    text = read_text_metadata(assay / 'image_metadata.txt').get(stem, {})
    if (text.get('XY processing') == 'native dimensions; no resizing'
            and (text.get('Height'), text.get('Width')) == tuple(shape)):
        return normalize(text.get('Pixel Width'), text.get('Pixel Height'), text.get('Unit'),
                         f'Native-size preparation metadata: {assay / "image_metadata.txt"}')
    originals = [p for p in assay.parent.iterdir() if not p.name.startswith('.')
                 and p.is_file() and p.suffix.lower() in ('.nd2', '.tif', '.tiff')
                 and p.stem == stem] if assay.parent.is_dir() else []
    if len(originals) != 1:
        return uncalibrated('No unique original or saved calibration snapshot.')
    if originals[0].suffix.lower() == '.nd2' and not allow_bioformats:
        # StarDist runs before ImageJ initialization. Defer legacy ND2 metadata
        # to the measurement stage instead of starting a JVM without Fiji jars.
        return None
    try:
        cal, original_shape = read_source(originals[0])
    except Exception as error:
        return uncalibrated(f'Original calibration could not be read: {error}', originals[0])
    if original_shape != tuple(shape):
        return uncalibrated('Original and processed XY dimensions differ; original scale was not applied.', originals[0])
    return {**cal, 'Calibration_source': f'{cal["Calibration_source"]}: {originals[0]}'}


def read_text_metadata(path):
    """Read legacy preparation metadata, including integer and exponential numbers."""
    path = Path(path)
    if not path.exists():
        return {}
    result, current = {}, None
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        key, separator, value = line.strip().partition(':')
        if not separator:
            continue
        value = value.strip()
        if key == 'Image Name':
            stem = Path(value).stem
            if stem in result:
                raise ValueError(f'Ambiguous image metadata for {stem}')
            current = result.setdefault(stem, {})
        elif current is not None:
            if key in ('Pixel Width', 'Pixel Height', 'Pixel Depth', 'Width', 'Height'):
                try:
                    current[key] = float(value)
                except ValueError:
                    current[key] = None
            else:
                current[key] = value
    return result


def validate_record(row):
    """Validate archived calibration and physical area without requiring the original drive."""
    status = row.get('Calibration_status')
    if status in (None, ''):
        return
    if status not in ('calibrated', 'uncalibrated'):
        raise ValueError('Invalid Calibration_status')
    sx, sy = row.get('Pixel_size_X_um'), row.get('Pixel_size_Y_um')
    if calibrated(row):
        if not calibrated(normalize(sx, sy, 'um')):
            raise ValueError('Invalid physical pixel sizes')
        if 'Area_px2' in row:
            physical = row.get('Area_um2')
            if physical in (None, '') or not math.isclose(
                    float(physical), area_um2(row['Area_px2'], row), rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError('Physical area disagrees with pixel area and calibration')
    elif sx not in (None, '') or sy not in (None, '') or row.get('Area_um2') not in (None, ''):
        raise ValueError('Uncalibrated image contains physical measurements')


def compatible(left, right):
    """Reject conflicting physical scales; legacy tables may lack calibration entirely."""
    if calibrated(left) and calibrated(right):
        return all(math.isclose(float(left[key]), float(right[key]), rel_tol=1e-9)
                   for key in ('Pixel_size_X_um', 'Pixel_size_Y_um'))
    return True
