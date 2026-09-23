"""Native-pixel ImageJ measurements using Bio-Formats source planes."""

from contextlib import contextmanager
from pathlib import Path

import numpy as np
from scyjava import jimport

METRICS = {
    "Area_px2": "Area", "Marker_Mean": "Mean", "Marker_Median": "Median",
    "Marker_StdDev": "StdDev", "Marker_Min": "Min", "Marker_Max": "Max",
    "Marker_RawIntDen": "RawIntDen",
}


class ImageJEngine:
    """Keep source intensities and geometry separate from display rendering."""

    def __init__(self, initialize=True):
        if initialize:
            import imagej
            self.gateway = imagej.init('sc.fiji:fiji', mode='headless')
        self.version = str(jimport('ij.IJ').getVersion())
        self.bioformats_version = str(jimport('loci.formats.FormatTools').VERSION)

    @contextmanager
    def reader(self, path):
        reader = jimport('loci.formats.ImageReader')()
        try:
            # A source file is one analysis unit; never group neighboring files.
            reader.setGroupFiles(False)
            reader.setId(str(path))
            yield reader
        finally:
            reader.close()

    def _info(self, reader):
        tools = jimport('loci.formats.FormatTools')
        return {
            'Width_px': int(reader.getSizeX()),
            'Height_px': int(reader.getSizeY()),
            'Channels': int(reader.getSizeC()),
            'Z_planes': int(reader.getSizeZ()),
            'Timepoints': int(reader.getSizeT()),
            'Series_count': int(reader.getSeriesCount()),
            'RGB': bool(reader.isRGB()),
            'Pixel_type': str(tools.getPixelTypeString(reader.getPixelType())),
            'Dimension_order': str(reader.getDimensionOrder()),
            'Little_endian': bool(reader.isLittleEndian()),
        }

    def inspect(self, path, mode, channel):
        with self.reader(path) as reader:
            info = self._info(reader)
        self.validate_info(info, mode, channel)
        return info

    @staticmethod
    def validate_info(info, mode, channel):
        if info['Series_count'] != 1 or info['Timepoints'] != 1:
            raise ValueError('Multiple series/timepoints are not supported; export '
                             'each field/timepoint as a separate multichannel file.')
        if info['RGB']:
            raise ValueError('RGB input is unsupported; use separate grayscale channels.')
        if channel < 1 or channel > info['Channels']:
            raise ValueError(f"Channel {channel} is outside 1-{info['Channels']}.")
        if mode == 'tiff-2d' and info['Z_planes'] != 1:
            raise ValueError('The 2D input mode requires exactly one Z plane.')
        if info['Pixel_type'] not in ('uint8', 'int8', 'uint16', 'int16', 'float'):
            raise ValueError('Unsupported pixel type for lossless float32 conversion: '
                             + info['Pixel_type'])

    def marker(self, path, mode, channel, expected_info):
        """Read one channel without autoscaling; use ImageJ MAX over all Z planes."""
        import jpype
        ImagePlus = jimport('ij.ImagePlus')
        FloatProcessor = jimport('ij.process.FloatProcessor')
        stack = jimport('ij.ImageStack')(expected_info['Width_px'],
                                        expected_info['Height_px'])
        with self.reader(path) as reader:
            info = self._info(reader)
            self.validate_info(info, mode, channel)
            if info != expected_info:
                raise ValueError('Source dimensions/type changed after selection.')
            dtype = {'uint8': 'u1', 'int8': 'i1', 'uint16': 'u2',
                     'int16': 'i2', 'float': 'f4'}[info['Pixel_type']]
            dtype = np.dtype(('<' if info['Little_endian'] else '>') + dtype)
            for z in range(info['Z_planes']):
                plane = reader.openBytes(reader.getIndex(z, channel - 1, 0))
                raw = np.asarray(plane).astype(np.uint8).tobytes()
                pixels = np.frombuffer(raw, dtype=dtype).astype(np.float32)
                if pixels.size != info['Width_px'] * info['Height_px']:
                    raise ValueError('Unexpected Bio-Formats plane dimensions.')
                if not np.isfinite(pixels).all():
                    raise ValueError('Source channel contains NaN or infinite values.')
                stack.addSlice(FloatProcessor(info['Width_px'], info['Height_px'],
                               jpype.JArray(jpype.JFloat)(pixels)))
        source = ImagePlus('Original marker channel', stack)
        projected = None
        try:
            if info['Z_planes'] > 1:
                projector = jimport('ij.plugin.ZProjector')(source)
                projector.setMethod(projector.MAX_METHOD)
                projector.setStartSlice(1)
                projector.setStopSlice(info['Z_planes'])
                projector.doProjection()
                projected = projector.getProjection()
                processor = projected.getProcessor().duplicate()
            else:
                processor = source.getProcessor().duplicate()
            result = ImagePlus('Marker MAX' if info['Z_planes'] > 1 else 'Marker',
                               processor)
            result.setIgnoreGlobalCalibration(True)
            result.setCalibration(jimport('ij.measure.Calibration')())
            result.getProcessor().resetThreshold()
            return result
        finally:
            source.close()
            if projected is not None:
                projected.close()

    def labels(self, mask_path, labels_path, info):
        """Validate existing final-mask IDs without resegmentation or renumbering."""
        IJ = jimport('ij.IJ')
        mask, ids = None, None
        try:
            mask = IJ.openImage(str(mask_path))
            ids = IJ.openImage(str(labels_path))
            if mask is None or ids is None:
                raise ValueError('Final mask or Morphology_QC ID map is unreadable.')
            for imp in (mask, ids):
                if (int(imp.getWidth()), int(imp.getHeight())) != (
                        info['Width_px'], info['Height_px']):
                    raise ValueError('Source and mask XY dimensions differ. Regenerate '
                                     'stages 1 and 2 at native size; resizing is disabled.')
                if int(imp.getStackSize()) != 1:
                    raise ValueError('Masks and ID maps must be single 2D images.')
            if int(mask.getBitDepth()) != 8 or int(ids.getBitDepth()) != 16:
                raise ValueError('Expected an 8-bit final mask and 16-bit ID map.')
            shape = (info['Height_px'], info['Width_px'])
            raw = np.asarray(mask.getProcessor().getPixels()).astype(np.uint8).reshape(shape)
            labels = np.asarray(ids.getProcessor().getPixels()).astype(np.uint16).reshape(shape)
            if not np.isin(raw, (0, 255)).all():
                raise ValueError('Final mask is not binary.')
            foreground = int(mask.getProcessor().getBestIndex(jimport('java.awt.Color').black))
            if foreground not in (0, 255) or not np.array_equal(labels > 0, raw == foreground):
                raise ValueError('ID-map foreground does not match the final mask.')
            return labels
        finally:
            for imp in (mask, ids):
                if imp is not None:
                    imp.close()

    @staticmethod
    def populations(labels):
        ids = {int(value) for value in np.unique(labels)} - {0}
        border = {int(value) for value in np.concatenate(
            (labels[0], labels[-1], labels[:, 0], labels[:, -1]))} - {0}
        return sorted(ids), sorted(border), sorted(ids - border)

    def measure(self, marker, labels, folder):
        """Measure explicit ID ROIs, including zero-valued marker pixels."""
        import jpype
        ImagePlus = jimport('ij.ImagePlus')
        ByteProcessor = jimport('ij.process.ByteProcessor')
        FileSaver = jimport('ij.io.FileSaver')
        flags = jimport('ij.measure.Measurements')
        ResultsTable = jimport('ij.measure.ResultsTable')
        Analyzer = jimport('ij.plugin.filter.Analyzer')
        selector = jimport('ij.plugin.filter.ThresholdToSelection')()
        RoiEncoder = jimport('ij.io.RoiEncoder')
        Color = jimport('java.awt.Color')
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=False)
        if (int(marker.getHeight()), int(marker.getWidth())) != labels.shape:
            raise ValueError('Marker and ID map must have identical XY dimensions.')
        marker.deleteRoi()
        marker.setIgnoreGlobalCalibration(True)
        marker.setCalibration(jimport('ij.measure.Calibration')())
        marker.getProcessor().resetThreshold()
        if not FileSaver(marker).saveAsTiff(str(folder / 'marker.tif')):
            raise OSError('Could not save marker TIFF.')
        marker.getProcessor().resetMinAndMax()
        preview = ImagePlus('Nuclear intensity QC', marker.getProcessor().convertToRGB())
        canvas = preview.getProcessor()
        canvas.setColor(Color.red)
        canvas.setFont(jimport('java.awt.Font')('SansSerif', 1, 12))
        all_ids, border_ids, eligible_ids = self.populations(labels)
        rows = []
        try:
            for nucleus_id in eligible_ids:
                binary = (labels == nucleus_id).astype(np.uint8) * 255
                processor = ByteProcessor(labels.shape[1], labels.shape[0],
                    jpype.JArray(jpype.JByte)(binary.ravel().view(np.int8)), None)
                processor.setThreshold(255, 255, processor.NO_LUT_UPDATE)
                roi = selector.convert(processor)
                if roi is None:
                    raise ValueError(f'No ROI for nucleus {nucleus_id}.')
                roi.setName(f'nucleus_{nucleus_id}')
                marker.setRoi(roi)
                table = ResultsTable()
                measurements = (flags.AREA | flags.MEAN | flags.MEDIAN | flags.STD_DEV
                                | flags.MIN_MAX | flags.INTEGRATED_DENSITY)
                Analyzer(marker, measurements, table).measure()
                row = {'Nucleus_ID': nucleus_id}
                row.update({name: float(table.getValue(heading, 0))
                            for name, heading in METRICS.items()})
                if not all(np.isfinite(row[name]) for name in METRICS):
                    raise ValueError(f'Non-finite ImageJ measurement for nucleus {nucleus_id}.')
                if row['Area_px2'] != int(np.count_nonzero(binary)):
                    raise ValueError(f'ROI area differs from ID {nucleus_id} pixel count.')
                row['Area_px2'] = int(row['Area_px2'])
                mask_name, roi_name = f'nucleus_{nucleus_id}_mask.tif', f'nucleus_{nucleus_id}.roi'
                processor.resetThreshold()
                binary_image = ImagePlus(f'Nucleus {nucleus_id}', processor)
                try:
                    if not FileSaver(binary_image).saveAsTiff(str(folder / mask_name)):
                        raise OSError('Could not save individual nucleus mask.')
                finally:
                    binary_image.close()
                if not RoiEncoder.save(roi, str(folder / roi_name)):
                    raise OSError('Could not save ImageJ ROI.')
                canvas.draw(roi)
                y, x = np.nonzero(binary)
                canvas.drawString(str(nucleus_id), int(x.mean()), int(y.mean()))
                rows.append({**row, 'Nucleus_mask': mask_name, 'ROI': roi_name})
            if not FileSaver(preview).saveAsPng(str(folder / 'numbered_nuclei.png')):
                raise OSError('Could not save numbered QC image.')
        finally:
            marker.deleteRoi()
            preview.close()
        counts = {'Nuclei_count_total': len(all_ids), 'Border_nuclei_count': len(border_ids),
                  'Non_border_nuclei_count': len(eligible_ids),
                  'Nuclei_measured_count': len(rows), 'Failed_nuclei_count': 0}
        return rows, counts
