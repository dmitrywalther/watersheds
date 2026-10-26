import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.shutil import copy as raster_copy
from rasterio.transform import from_origin

from scripts.inspect_cog_layout import inspect_cog_layout


class CogLayoutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def fixture(self, *, bigtiff='YES', compression='DEFLATE', cog=True):
        source = self.folder / 'source.tif'
        values = (np.arange(1024 * 1024).reshape(1024, 1024) % 128).astype('float32')
        values[:64, :64] = -9999
        with rasterio.Env(GDAL_PAM_ENABLED='NO'):
            with rasterio.open(source, 'w', driver='GTiff', width=1024, height=1024,
                               count=1, dtype='float32', nodata=-9999,
                               crs='EPSG:3395', transform=from_origin(0, 10000, 3, 3)) as dst:
                dst.write(values, 1)
            if not cog:
                return source
            output = self.folder / f'{bigtiff}-{compression}.tif'
            raster_copy(source, output, driver='COG', BIGTIFF=bigtiff,
                        BLOCKSIZE=128, COMPRESS=compression, PREDICTOR='FLOATING_POINT',
                        OVERVIEWS='IGNORE_EXISTING', RESAMPLING='NEAREST')
        return output

    def test_actual_cogs_pass_both_tiff_formats_and_codecs(self):
        for bigtiff in ('YES', 'NO'):
            for compression in ('DEFLATE', 'ZSTD'):
                with self.subTest(bigtiff=bigtiff, compression=compression):
                    report = inspect_cog_layout(self.fixture(bigtiff=bigtiff,
                                                             compression=compression))
                    self.assertTrue(report['checks_passed'])
                    self.assertEqual(report['format'], 'BigTIFF' if bigtiff == 'YES' else 'TIFF')
                    self.assertEqual(len(report['directories']), 4)
                    self.assertIn('not full COG certification', report['scope'])

    def test_ifd_reference_past_end_is_rejected(self):
        path = self.fixture()
        with path.open('r+b') as output:
            output.seek(8)
            output.write(struct.pack('<Q', path.stat().st_size + 1))
        with self.assertRaisesRegex(ValueError, 'outside file'):
            inspect_cog_layout(path)

    def test_swapped_tile_offsets_are_rejected(self):
        path = self.fixture()
        # The fixture is little-endian BigTIFF. Mutate only its tile-offset
        # table, leaving raster bytes untouched: row-major order must fail.
        with path.open('r+b') as output:
            output.seek(8)
            ifd = struct.unpack('<Q', output.read(8))[0]
            output.seek(ifd)
            count = struct.unpack('<Q', output.read(8))[0]
            for _ in range(count):
                entry = output.read(20)
                tag, dtype, values, location = struct.unpack('<HHQQ', entry)
                if tag == 324:
                    self.assertGreater(values, 1)
                    size = {4: 4, 16: 8}[dtype]
                    output.seek(location)
                    first, second = output.read(size), output.read(size)
                    output.seek(location)
                    output.write(second + first)
                    break
            else:
                self.fail('Fixture has no tile-offset table')
        with self.assertRaisesRegex(ValueError, 'row-major order'):
            inspect_cog_layout(path)

    def test_striped_tiff_is_outside_supported_cog_layout(self):
        path = self.fixture(cog=False)
        with self.assertRaisesRegex(ValueError, 'tag 322'):
            inspect_cog_layout(path)


if __name__ == '__main__':
    unittest.main()
