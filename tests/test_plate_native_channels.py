"""Native ImageJ/Bio-Formats check for per-well MAX/SUM channel routing."""

import numpy as np
import tifffile
from test_nuclear_intensity import engine as engine  # noqa: PLC0414 - pytest fixture re-export
from test_plate_design_workflow import scheme


def test_native_per_well_assignment_preserves_uint16_values_and_sum32(tmp_path, engine):
    design = scheme(tmp_path/'experiment')
    marker = design.markers('Nuclear intensity')[0]
    for well in ('C01', 'H04'):
        count = sum(row is not None for row in design.wells[well]['channels'])
        pixels = np.zeros((3, count, 20, 30), dtype=np.uint16)
        for c in range(count):
            for z in range(3):
                pixels[z, c] = (c+1)*1000 + z*500
        channel = design.channel(well, 'Nuclear intensity', marker['id'])
        pixels[:, channel-1, 5:7, 8:10] = 0
        path = design.path.parent/f'field_Well{well}.ome.tif'
        tifffile.imwrite(path, pixels, ome=True, metadata={'axes': 'ZCYX'}, photometric='minisblack')
        info = engine.inspect(path, 'tiff-stack', channel)
        design.validate_channels(well, info['Channels'])
        for projection, expected in [('max', pixels[:, channel-1].max(axis=0)),
                                     ('sum', pixels[:, channel-1].astype(np.float32).sum(axis=0))]:
            image = engine.marker(path, 'tiff-stack', channel, info, projection_method=projection)
            try:
                # Existing engine uses lossless float32 for uint16, including MAX.
                actual = np.asarray(image.getProcessor().getPixels(), dtype=np.float32).reshape(20, 30)
                assert np.array_equal(actual, expected)
                assert image.getBitDepth() == 32
                assert actual.max() > 255 and actual[5, 8] == 0
            finally:
                image.close()
