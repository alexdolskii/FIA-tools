"""Original-channel SUM projection and lossless FieldSignal diagnostic exports."""

from pathlib import Path

import numpy as np
import spatial_calibration as spatial
import tifffile
from nuclear_intensity_imagej import ImageJEngine as NuclearEngine
from PIL import Image, ImageDraw
from scipy import ndimage


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


def save_images(folder, projection, mask, labels, nuclei_ids, calibration, objects):
    folder.mkdir(parents=True, exist_ok=False)
    save_tiff(folder / 'FieldSignal_SUM32.tif', projection, calibration)
    save_tiff(folder / 'FieldSignal_mask.tif', mask, calibration)
    # uint32 preserves object IDs even when a field has more than 65535 objects.
    save_tiff(folder / 'FieldSignal_objects.tif', labels, calibration, imagej=False)
    save_tiff(folder / 'nuclei_ids.tif', nuclei_ids.astype(np.uint16), calibration)
    low, high = np.percentile(projection, (1, 99.8))
    display = np.clip((projection.astype(np.float64) - low) / (high - low or 1), 0, 1)
    rgb = np.repeat((display * 255).astype(np.uint8)[..., None], 3, axis=2)
    edge = (labels > 0) & (ndimage.minimum_filter(labels, size=3, mode='constant') != labels)
    rgb[edge] = (255, 80, 40)
    preview = Image.fromarray(rgb)
    draw = ImageDraw.Draw(preview)
    for row in objects:
        draw.text((round(row['Centroid_X_px']), round(row['Centroid_Y_px'])),
                  str(row['Object_ID']), fill='yellow', stroke_width=1, stroke_fill='black')
    preview.save(folder / 'objects_QC.png')
    return {p.name: spatial.digest(p) for p in sorted(folder.iterdir())}
