"""Original-channel SUM projection and lossless FieldSignal diagnostic exports."""

from pathlib import Path

import numpy as np
import spatial_calibration as spatial
import tifffile
from nuclear_intensity_imagej import ImageJEngine as NuclearEngine


class ImageJEngine(NuclearEngine):
    def _info(self, reader):
        info = super()._info(reader)
        value = reader.getMetadataStore().getPixelsPhysicalSizeZ(int(reader.getSeries()))
        step = (spatial.normalize(float(value.value().doubleValue()), float(value.value().doubleValue()),
                                 str(value.unit().getSymbol())) if value is not None else {})
        info['Z_step_um'] = step.get('Pixel_size_X_um')
        return info

    def projection(self, source, channel, info):
        mode = 'nd2' if Path(source).suffix.lower() == '.nd2' else 'tiff-stack'
        image = self.marker(source, mode, channel, info, projection_method='sum')
        try:
            return np.asarray(image.getProcessor().getPixels(), dtype=np.float32).reshape(
                info['Height_px'], info['Width_px']).copy()
        finally:
            image.close()


def save_tiff(path, pixels, calibration, imagej=True):
    """Write and reopen exact numeric pixels; XY units never alter intensities."""
    options = {'metadata': {'axes': 'YX'}}
    if spatial.calibrated(calibration):
        options['resolution'] = (1 / float(calibration['Pixel_size_X_um']),
                                 1 / float(calibration['Pixel_size_Y_um']))
        options['metadata']['unit'] = 'um'
    else:
        options['metadata']['unit'] = 'pixel'
    tifffile.imwrite(path, pixels, imagej=imagej, photometric='minisblack', **options)
    actual = tifffile.imread(path)
    if actual.dtype != pixels.dtype or not np.array_equal(actual, pixels):
        raise OSError(f'Saved TIFF differs from measured pixels: {path}')
    if imagej:
        saved, shape = spatial.tiff_calibration(path)
        if shape != pixels.shape or not spatial.compatible(saved, calibration):
            raise OSError(f'Saved TIFF calibration differs: {path}')


def image_paths(folder, source_name):
    """Paths for one unambiguously matched source, grouped by artifact type."""
    filename = Path(source_name).stem + '.tif'
    return {field: Path(folder) / kind / filename for field, kind in (
        ('SUM32_file', 'FieldSignal_SUM32'), ('Mask_file', 'FieldSignal_mask'),
        ('Objects_file', 'FieldSignal_objects'), ('Nuclei_IDs_file', 'nuclei_ids'))}


def save_images(folder, source_name, projection, mask, labels, nuclei_ids, calibration):
    paths = image_paths(folder, source_name)
    for path in paths.values():
        if path.exists() or path.is_symlink():
            raise FileExistsError(f'Image artifact already exists: {path}')
    created = []
    try:
        for field, pixels in (('SUM32_file', projection), ('Mask_file', mask),
                              ('Objects_file', labels), ('Nuclei_IDs_file', nuclei_ids.astype(np.uint16))):
            path = paths[field]
            path.parent.mkdir(parents=True, exist_ok=True)
            created.append(path)
            # uint32 preserves object IDs even when a field has more than 65535 objects.
            save_tiff(path, pixels, calibration, imagej=field != 'Objects_file')
        return {path.relative_to(folder).as_posix(): spatial.digest(path) for path in created}
    except BaseException:
        # Shared folders also contain successful images: remove only this image's files.
        for path in created:
            path.unlink(missing_ok=True)
        raise
