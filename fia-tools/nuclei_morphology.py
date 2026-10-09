"""Export pixel and calibrated nuclear morphology without modifying final masks."""

import csv
import math
import re
from pathlib import Path

import image_exclusions as exclusions
import numpy as np
import spatial_calibration as spatial
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from run_resources import temporary_path
from scyjava import jimport

METRICS = [
    "Area_px2", "Perimeter_px", "Circularity", "Aspect_ratio", "Solidity",
    "Major_axis_px", "Minor_axis_px", "Feret_max_px", "Feret_min_px",
    "Equivalent_diameter_px", "Roundness", "Eccentricity",
]
EXPORT_METRICS = METRICS + spatial.EXTRA_METRICS
IDENTIFIERS = [
    "Dataset", "Dataset_path", "Image_name", "Mask_name", "Well",
    "Run_ID", "Particle_size_px2", "StarDist_source",
]
NUCLEI_COLUMNS = IDENTIFIERS + spatial.CALIBRATION_COLUMNS + ["Nucleus_ID"] + EXPORT_METRICS + [
    "Orientation_deg", "Centroid_X_px", "Centroid_Y_px", "Touches_border",
    "Orientation_calibrated_deg", "Centroid_X_um", "Centroid_Y_um",
    "Label_map", "Numbered_image",
]
IMAGE_COLUMNS = IDENTIFIERS + spatial.CALIBRATION_COLUMNS + [
    "Status", "Error", "Nuclei_count_total", "Border_nuclei_count",
    "Non_border_nuclei_count",
] + [f"{metric}_{stat}" for metric in EXPORT_METRICS for stat in ("Mean", "Median", "IQR")]


def add_physical_measurements(records, labels_image, calibration):
    """Scale square-pixel results; measure calibrated ROIs for anisotropic pixels."""
    for row in records:
        row.update(dict.fromkeys(spatial.EXTRA_METRICS + [
            'Orientation_calibrated_deg', 'Centroid_X_um', 'Centroid_Y_um']))
    if not spatial.calibrated(calibration):
        return
    sx, sy = calibration['Pixel_size_X_um'], calibration['Pixel_size_Y_um']
    isotropic = math.isclose(sx, sy, rel_tol=1e-12)
    labels = processor_pixels(labels_image) if not isotropic else None
    for row in records:
        row['Area_um2'] = spatial.area_um2(row['Area_px2'], calibration)
        row['Equivalent_diameter_um'] = math.sqrt(4 * row['Area_um2'] / math.pi)
        for axis, scale in (('X', sx), ('Y', sy)):
            value = row.get(f'Centroid_{axis}_px')
            row[f'Centroid_{axis}_um'] = value * scale if value is not None else None
        for field, target in spatial.CALIBRATED_SHAPES.items():
            row[target] = row.get(field)
        if isotropic:
            for field in spatial.LENGTH_FIELDS:
                value = row.get(field)
                row[spatial.PHYSICAL_FIELDS[field]] = value * sx if value is not None else None
            row['Orientation_calibrated_deg'] = row.get('Orientation_deg')
            continue
        # ImageJ ROI perimeter and Feret calculations respect independent XY scales.
        import jpype
        binary = (labels == row['Nucleus_ID']).astype(np.uint8) * 255
        processor = jimport('ij.process.ByteProcessor')(
            labels.shape[1], labels.shape[0],
            jpype.JArray(jpype.JByte)(binary.ravel().view(np.int8)), None)
        processor.setThreshold(255, 255, processor.NO_LUT_UPDATE)
        roi = jimport('ij.plugin.filter.ThresholdToSelection')().convert(processor)
        image = jimport('ij.ImagePlus')('Calibrated nucleus geometry', processor)
        try:
            spatial.imagej_calibration(image, calibration, jimport)
            image.setRoi(roi)
            row['Perimeter_um'] = float(roi.getLength())
            feret = roi.getFeretValues()
            row['Feret_max_um'], row['Feret_min_um'] = float(feret[0]), float(feret[2])
        finally:
            image.close()
        # Affinely transform the ImageJ fitted ellipse, not the raster image.
        major, minor, angle = (row.get(key) for key in
                               ('Major_axis_px', 'Minor_axis_px', 'Orientation_deg'))
        if all(value is not None for value in (major, minor, angle)):
            theta = math.radians(angle)
            rotation = np.array([[math.cos(theta), -math.sin(theta)],
                                 [math.sin(theta), math.cos(theta)]])
            axes, lengths, _ = np.linalg.svd(np.diag([sx, sy]) @ rotation @ np.diag([major, minor]))
            row['Major_axis_um'], row['Minor_axis_um'] = map(float, lengths)
            row['Orientation_calibrated_deg'] = math.degrees(math.atan2(axes[1, 0], axes[0, 0])) % 180
            row['Aspect_ratio_calibrated'] = float(lengths[0] / lengths[1]) if lengths[1] > 0 else None
            row['Roundness_calibrated'] = 4 * row['Area_um2'] / (math.pi * lengths[0] ** 2) if lengths[0] > 0 else None
            row['Eccentricity_calibrated'] = math.sqrt(max(0, 1 - (lengths[1] / lengths[0]) ** 2)) if lengths[0] > 0 else None
        else:
            for field in ('Aspect_ratio', 'Roundness', 'Eccentricity'):
                row[spatial.CALIBRATED_SHAPES[field]] = None
        row['Circularity_calibrated'] = min(1.0, 4 * math.pi * row['Area_um2'] / row['Perimeter_um'] ** 2) if row['Perimeter_um'] > 0 else None


def processor_pixels(imp):
    """Copy raw pixels, independent of the ImageJ display LUT."""
    dtype = np.uint8 if int(imp.getBitDepth()) == 8 else np.uint16
    return np.asarray(imp.getProcessor().getPixels()).astype(dtype).reshape(
        int(imp.getHeight()), int(imp.getWidth()))


def measure_final_mask(mask):
    """Measure a duplicate with ImageJ and return rows plus an ID label image.

    The caller supplies the unchanged `show=Masks` output of ParticleAnalyzer.
    These masks display particles in black, including when the LUT is inverted.
    No watershed, size filter or exclusion of border objects is applied here.
    The caller owns the returned label image and must close it.
    """
    ImagePlus = jimport("ij.ImagePlus")
    Calibration = jimport("ij.measure.Calibration")
    Measurements = jimport("ij.measure.Measurements")
    ResultsTable = jimport("ij.measure.ResultsTable")
    ParticleAnalyzer = jimport("ij.plugin.filter.ParticleAnalyzer")
    ImageProcessor = jimport("ij.process.ImageProcessor")
    Color = jimport("java.awt.Color")

    if int(mask.getBitDepth()) != 8 or int(mask.getStackSize()) != 1:
        raise ValueError("Morphology requires a single 8-bit final mask.")
    original = processor_pixels(mask)
    if not np.isin(original, (0, 255)).all():
        raise ValueError("The final nuclei mask is not binary.")
    foreground = int(mask.getProcessor().getBestIndex(Color.black))
    if foreground not in (0, 255):
        raise ValueError("Unrecognized final-mask LUT.")

    duplicate = ImagePlus("Nuclei morphology", mask.getProcessor().duplicate())
    duplicate.setIgnoreGlobalCalibration(True)
    duplicate.setCalibration(Calibration())
    duplicate.getProcessor().setThreshold(
        foreground, foreground, ImageProcessor.NO_LUT_UPDATE)
    table = ResultsTable()
    flags = (Measurements.AREA | Measurements.PERIMETER | Measurements.ELLIPSE
             | Measurements.SHAPE_DESCRIPTORS | Measurements.FERET
             | Measurements.CENTROID)
    analyzer = ParticleAnalyzer(
        ParticleAnalyzer.SHOW_ROI_MASKS, flags, table, 0.0, float("inf"))
    analyzer.setHideOutputImage(True)
    labels_image = None
    try:
        if not analyzer.analyze(duplicate):
            raise RuntimeError("ImageJ could not measure the final nuclei mask.")
        count = int(table.size())
        if count > 65534:
            raise ValueError("Too many nuclei for unique ImageJ count-mask IDs.")
        labels_image = analyzer.getOutputImage()
        if labels_image is None:
            raise RuntimeError("ImageJ did not return a nucleus ID map.")
        labels = processor_pixels(labels_image)
        if not np.array_equal(labels > 0, original == foreground):
            raise ValueError("Measured objects do not match the saved final mask.")
        sizes = np.bincount(labels.ravel(), minlength=count + 1)
        if len(sizes) != count + 1 or np.any(sizes[1:] == 0):
            raise ValueError("Nucleus IDs do not match the measurement rows.")
        border_ids = set(np.concatenate(
            (labels[0], labels[-1], labels[:, 0], labels[:, -1])))

        def value(heading, row):
            number = float(table.getValue(heading, row))
            return number if math.isfinite(number) else None

        records = []
        for index in range(count):
            area = value("Area", index)
            if area is None or not math.isclose(area, int(sizes[index + 1])):
                raise ValueError("ImageJ area does not match the ID-map pixel count.")
            major, minor = value("Major", index), value("Minor", index)
            eccentricity = None
            if major is not None and major > 0 and minor is not None:
                eccentricity = math.sqrt(max(0.0, 1.0 - (minor / major) ** 2))
            records.append({
                "Nucleus_ID": index + 1,
                "Area_px2": int(sizes[index + 1]),
                "Perimeter_px": value("Perim.", index),
                "Circularity": value("Circ.", index),
                "Aspect_ratio": value("AR", index),
                "Solidity": value("Solidity", index),
                "Major_axis_px": major,
                "Minor_axis_px": minor,
                "Feret_max_px": value("Feret", index),
                "Feret_min_px": value("MinFeret", index),
                "Equivalent_diameter_px": math.sqrt(4.0 * area / math.pi),
                "Roundness": value("Round", index),
                "Eccentricity": eccentricity,
                "Orientation_deg": value("Angle", index),
                "Centroid_X_px": value("X", index),
                "Centroid_Y_px": value("Y", index),
                "Touches_border": index + 1 in border_ids,
            })
        return records, labels_image
    except Exception:
        if labels_image is not None:
            labels_image.close()
        raise
    finally:
        duplicate.close()


def save_numbered_image(mask, records, path):
    """Draw IDs on a separate RGB image, leaving the binary mask unchanged."""
    ImagePlus = jimport("ij.ImagePlus")
    FileSaver = jimport("ij.io.FileSaver")
    Color = jimport("java.awt.Color")
    JavaFont = jimport("java.awt.Font")
    processor = mask.getProcessor().convertToRGB()
    processor.setFont(JavaFont("SansSerif", JavaFont.BOLD, 12))
    processor.setColor(Color.red)
    for row in records:
        text = str(row["Nucleus_ID"])
        width = int(processor.getStringWidth(text))
        x = round(row["Centroid_X_px"]) - width // 2
        y = round(row["Centroid_Y_px"])
        x = max(0, min(x, int(mask.getWidth()) - width))
        y = min(int(mask.getHeight()) - 1, max(12, y))
        processor.drawString(text, x, y)
    preview = ImagePlus("Nucleus IDs", processor)
    try:
        if not FileSaver(preview).saveAsPng(str(path)):
            raise OSError(f"Could not save numbered image: {path}")
    finally:
        preview.close()


class NucleiMorphologyExport:
    """Accumulate per-nucleus measurements and per-image quality information."""

    def __init__(self, output_folder, stardist_folder, particle_size, ij_version):
        self.output = Path(output_folder)
        self.source = Path(stardist_folder).resolve()
        self.particle_size = particle_size
        self.ij_version = str(ij_version)
        self.nuclei = []
        self.images = []

    def identifiers(self, filename, mask_name=""):
        source = Path(filename)
        stem = source.stem.removesuffix("_StarDist_processed")
        image_name = stem + source.suffix
        well = re.search(r"(?:^|[_\s-])Well([A-P])(0?[1-9]|1[0-9]|2[0-4])"
                         r"(?=[_\s.\-]|$)", image_name, re.IGNORECASE)
        dataset = self.output.parent.parent.resolve()
        return {
            "Dataset": dataset.name, "Dataset_path": str(dataset),
            "Image_name": image_name, "Mask_name": mask_name,
            "Well": f"{well[1].upper()}{int(well[2])}" if well else "",
            "Run_ID": self.output.name, "Particle_size_px2": self.particle_size,
            "StarDist_source": str(self.source),
        }

    def add_image(self, mask, filename, mask_name):
        records, label_image = measure_final_mask(mask)
        qc = self.output / "Morphology_QC"
        stem = Path(mask_name).stem
        label_path = qc / f"{stem}_ids.tif"
        preview_path = qc / f"{stem}_ids.png"
        try:
            shape = (int(mask.getHeight()), int(mask.getWidth()))
            calibration = spatial.snapshot_calibration(self.source / filename, shape)
            if calibration is None:
                projection = self.source.parent / 'Nuclei' / (
                    Path(filename).stem.removesuffix('_StarDist_processed') + Path(filename).suffix)
                calibration = spatial.projection_calibration(projection, shape)
            add_physical_measurements(records, label_image, calibration)
            spatial.imagej_calibration(label_image, calibration, jimport)
            qc.mkdir(exist_ok=True)
            FileSaver = jimport("ij.io.FileSaver")
            mask_path = self.output / mask_name
            if mask_path.is_file():
                spatial.imagej_calibration(mask, calibration, jimport)
                if not FileSaver(mask).saveAsTiff(str(mask_path)):
                    raise OSError(f'Could not save calibrated final mask: {mask_path}')
                spatial.save_snapshot(mask_path, calibration, shape)
            if not FileSaver(label_image).saveAsTiff(str(label_path)):
                raise OSError(f"Could not save nucleus ID map: {label_path}")
            save_numbered_image(mask, records, preview_path)
        finally:
            label_image.close()
        # Exclude edge-touching nuclei from every measurement table and summary.
        # Keep their IDs in QC images so the unchanged final mask remains traceable.
        included_records = [row for row in records if not row["Touches_border"]]
        identifiers = {**self.identifiers(filename, mask_name), **calibration}
        self.nuclei.extend({
            **identifiers, **row,
            "Label_map": str(label_path.relative_to(self.output)),
            "Numbered_image": str(preview_path.relative_to(self.output)),
        } for row in included_records)
        summary = {
            **identifiers, "Status": "complete", "Error": "",
            "Nuclei_count_total": len(records),
            "Border_nuclei_count": len(records) - len(included_records),
            "Non_border_nuclei_count": len(included_records),
        }
        # Orientation is axial and is deliberately not summarized by linear statistics.
        for metric in EXPORT_METRICS:
            values = [row[metric] for row in included_records if row[metric] is not None]
            summary[f"{metric}_Mean"] = float(np.mean(values)) if values else None
            summary[f"{metric}_Median"] = float(np.median(values)) if values else None
            summary[f"{metric}_IQR"] = (
                float(np.percentile(values, 75) - np.percentile(values, 25))
                if values else None)
        self.images.append(summary)

    def record_failure(self, filename, error, mask_name=""):
        self.images.append({**self.identifiers(filename, mask_name),
                            "Status": "failed", "Error": str(error),
                            "Nuclei_count_total": None, "Border_nuclei_count": None,
                            "Non_border_nuclei_count": None})

    def run_info(self):
        return [
            ("Run_ID", self.output.name),
            ("StarDist_source", str(self.source)),
            ("Particle_size_px2", self.particle_size),
            ("Status", "complete" if self.images and all(
                row["Status"] == "complete" for row in self.images) else "incomplete"),
            ("ImageJ_version", self.ij_version),
            ("Measurement_method", "ImageJ ParticleAnalyzer on a duplicate final mask"),
            ("Spatial_calibration_schema", 1),
            ("Area_units", "Area_px2 always retained; Area_um2 when valid per-image XY calibration exists"),
            ("Length_units", "Pixel columns retained; additional _um columns when calibrated"),
            ("Shape_space", "Pixel geometry retained; _calibrated shape columns use physical XY geometry"),
            ("Anisotropic_geometry", "ImageJ calibrated ROI perimeter/Feret; affine transform of the ImageJ fitted ellipse; no image resampling"),
            ("Circularity", "ImageJ: min(1, 4*pi*Area/Perimeter^2)"),
            ("Aspect_ratio", "Major_axis_px / Minor_axis_px"),
            ("Roundness", "4*Area_px2/(pi*Major_axis_px^2)"),
            ("Solidity", "Area / convex hull area (ImageJ definition)"),
            ("Feret_diameters", "Maximum and minimum caliper diameters (ImageJ)"),
            ("Equivalent_diameter_px", "sqrt(4*Area_px2/pi)"),
            ("Eccentricity", "sqrt(1-(Minor_axis_px/Major_axis_px)^2)"),
            ("Orientation_deg", "ImageJ fitted-ellipse angle to the x axis (0-180)"),
            ("Centroid", "ImageJ pixel coordinates; origin at the top left"),
            ("Border_policy", "Nuclei touching any image edge are excluded from "
             "per-nucleus tables and summary metrics; counted separately"),
            ("Summary_population", "Final-mask nuclei that do not touch the image border"),
            ("Non_border_nuclei_count", "Nuclei_count_total - Border_nuclei_count"),
            ("Summary_statistics", "Per-image arithmetic mean, median and IQR; "
             "blank if no non-border nuclei; no hypothesis tests"),
            ("Nucleus_ID", "Local to Image_name and Run_ID; matches Morphology_QC ID map"),
            ("QC_population", "All final-mask nuclei, including border nuclei; "
             "exported Nucleus_ID values may have gaps"),
            ("ID_compatibility", "Not guaranteed to match StarDist or quantify_foci IDs"),
            ("Intensity_and_texture", "Not measured"),
            ("Missing_values", "Blank means unavailable; zero nuclei is recorded explicitly"),
        ]

    def save(self):
        """Write Excel and UTF-8 CSV tables without rounding numeric values."""
        tables = [("Nuclei", NUCLEI_COLUMNS, self.nuclei),
                  ("Images", IMAGE_COLUMNS, self.images),
                  ("Run_Info", ["Parameter", "Value"],
                   [dict(zip(("Parameter", "Value"), pair)) for pair in self.run_info()])]
        excluded = exclusions.load_exclusions(self.output)
        tables.append(('Processing_Exclusions', exclusions.COLUMNS, list(excluded.values())))
        workbook = Workbook()
        workbook.remove(workbook.active)
        for name, columns, records in tables:
            csv_name = "Nuclei_Morphology.csv" if name == "Nuclei" else f"Nuclei_{name}.csv"
            if name == 'Processing_Exclusions':
                csv_name = exclusions.TABLE
            csv_path = self.output / csv_name
            temporary = temporary_path(csv_path, csv_path.with_name("." + csv_name + ".tmp"))
            with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows(records)
            temporary.replace(csv_path)

            sheet = workbook.create_sheet(name)
            sheet.append(columns)
            for record in records:
                sheet.append([record.get(column) for column in columns])
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for cell in sheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="264653")
                sheet.column_dimensions[cell.column_letter].width = min(
                    32, max(16, len(str(cell.value)) + 2))
            for row in sheet.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
                    elif isinstance(cell.value, float):
                        cell.number_format = "0.0000"
        destination = self.output / "Nuclei_Morphology.xlsx"
        temporary = destination.with_name("." + destination.name)
        try:
            workbook.save(temporary)
            temporary.replace(destination)
        finally:
            workbook.close()
        return dict(self.run_info())["Status"]
