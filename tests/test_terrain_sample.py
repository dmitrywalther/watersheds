import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.transform import from_origin

from services import terrain_sample


class TerrainSampleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'terrain.tif'
        self.transform = from_origin(1000000, 2001000, 500, 500)
        self.values = np.array([[-25.5, 0, 10.25, 900], [100, -9999, np.nan, np.inf]], dtype=np.float32)
        self.write()

    def write(self, *, mask=None, external=False, crs='EPSG:3395', count=1):
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=not external), rasterio.open(
                self.path, 'w', driver='GTiff', width=self.values.shape[1],
                height=self.values.shape[0], count=count, dtype=self.values.dtype,
                nodata=-9999, crs=crs, transform=self.transform) as dst:
            for band in range(1, count + 1):
                dst.write(self.values, band)
            if mask is not None:
                dst.write_mask(mask)

    def sample(self, row=0, col=0, layer='elevation', path=None):
        x, y = self.transform * (col + 0.5, row + 0.5)
        longitude, latitude = Transformer.from_crs('EPSG:3395', 'EPSG:4326', always_xy=True).transform(x, y)
        return terrain_sample.sample_terrain_value(latitude, longitude, layer, self.path if path is None else path)

    def test_native_value_is_exact_and_preserves_negative_elevation(self):
        for row, col in ((0, 0), (0, 1), (0, 2), (0, 3), (1, 0)):
            with self.subTest(row=row, col=col):
                result = self.sample(row, col)
                self.assertEqual(result, {
                    'status': 'ok', 'value': float(self.values[row, col]), 'unit': 'm',
                    'source_label': 'GMTED2010 30″', 'reason': None,
                })
                json.dumps(result, allow_nan=False)

    def test_nodata_nonfinite_and_outside_extent_are_no_data(self):
        for row, col in ((1, 1), (1, 2), (1, 3), (-1, 0), (0, 4)):
            with self.subTest(row=row, col=col):
                result = self.sample(row, col)
                self.assertEqual(result['status'], 'no_data')
                self.assertIsNone(result['value'])
                self.assertTrue(result['reason'])

    def test_slope_range_and_degrees(self):
        self.values = np.array([[0, 0.125, 90, -0.01, 90.01]], dtype=np.float32)
        self.write()
        for col in range(5):
            with self.subTest(col=col):
                result = self.sample(col=col, layer='slope')
                self.assertEqual(result['unit'], 'degrees')
                self.assertEqual(result['status'], 'ok' if col < 3 else 'no_data')
                self.assertEqual(result['value'], float(self.values[0, col]) if col < 3 else None)

    def test_internal_and_external_masks_are_honored(self):
        mask = np.full(self.values.shape, 255, dtype=np.uint8)
        mask[0, 0] = 0
        for external in (False, True):
            with self.subTest(external=external):
                self.write(mask=mask, external=external)
                self.assertEqual(self.sample()['status'], 'no_data')
                self.assertEqual(self.sample(col=2)['value'], 10.25)
                self.assertEqual(self.sample(row=1, col=1)['status'], 'no_data')

    def test_cloud_raster_and_cloud_mask_are_not_opened(self):
        for mask_cloud in (False, True):
            with self.subTest(mask_cloud=mask_cloud):
                if mask_cloud:
                    self.path.with_name(self.path.name + '.msk').write_bytes(b'cloud')
                cloud_check = (lambda path: path.name.endswith('.msk')) if mask_cloud else (lambda path: True)
                with patch.object(terrain_sample, 'is_cloud_placeholder', side_effect=cloud_check), \
                        patch.object(rasterio, 'open', side_effect=AssertionError('Do not hydrate files')):
                    result = self.sample()
                self.assertEqual(result['status'], 'unavailable')
                self.assertIn('облаке', result['reason'])

    def test_missing_corrupt_missing_crs_and_multiband_are_unavailable(self):
        self.assertEqual(self.sample(path=self.path.with_name('missing.tif'))['status'], 'unavailable')
        self.path.write_bytes(b'not a raster')
        self.assertEqual(self.sample()['status'], 'unavailable')
        self.write(crs=None)
        self.assertEqual(self.sample()['status'], 'unavailable')
        self.write(count=2)
        self.assertEqual(self.sample()['status'], 'unavailable')

    def test_invalid_external_mask_is_unavailable(self):
        self.write(mask=np.full(self.values.shape, 255, dtype=np.uint8), external=True)
        mask_path = self.path.with_name(self.path.name + '.msk')
        with rasterio.open(mask_path, 'w', driver='GTiff', width=1, height=1, count=1, dtype='uint8',
                           transform=self.transform) as dst:
            dst.write(np.ones((1, 1), dtype=np.uint8), 1)
            dst.update_tags(INTERNAL_MASK_FLAGS_1='2')
        self.assertEqual(self.sample()['status'], 'unavailable')

    def test_only_one_native_pixel_is_read_even_with_overviews_and_mask(self):
        self.values = np.arange(256 * 256, dtype=np.float32).reshape(256, 256)
        self.write(mask=np.full(self.values.shape, 255, dtype=np.uint8), external=True)
        with rasterio.open(self.path, 'r+') as dst:
            dst.build_overviews([2, 4], rasterio.enums.Resampling.average)
        reads = []
        real_open = rasterio.open

        class Tracker:
            def __init__(inner, dataset):
                inner.dataset = dataset
            def __getattr__(inner, name):
                return getattr(inner.dataset, name)
            def __enter__(inner):
                return inner
            def __exit__(inner, *args):
                return inner.dataset.__exit__(*args)
            def read(inner, *args, **kwargs):
                reads.append(kwargs)
                return inner.dataset.read(*args, **kwargs)
            def read_masks(inner, *args, **kwargs):
                reads.append(kwargs)
                return inner.dataset.read_masks(*args, **kwargs)

        with patch.object(rasterio, 'open', side_effect=lambda *args, **kwargs: Tracker(real_open(*args, **kwargs))):
            result = self.sample(row=123, col=97)
        self.assertEqual(result['value'], float(self.values[123, 97]))
        self.assertEqual(len(reads), 3)
        for kwargs in reads:
            self.assertEqual(kwargs['window'].width, 1)
            self.assertEqual(kwargs['window'].height, 1)
            self.assertEqual(kwargs['window'].col_off, 97)
            self.assertEqual(kwargs['window'].row_off, 123)
            self.assertNotIn('out_shape', kwargs)

    def test_invalid_coordinates_and_layer_raise_before_reading(self):
        with patch.object(rasterio, 'open', side_effect=AssertionError('Invalid request must not open raster')):
            for latitude, longitude, layer in ((86, 30, 'elevation'), (0, 181, 'elevation'),
                    (float('nan'), 30, 'slope'), (0, float('inf'), 'slope'),
                    (0, 0, '../secret.tif'), (None, 0, 'elevation')):
                with self.subTest(latitude=latitude, longitude=longitude, layer=layer):
                    with self.assertRaises(ValueError):
                        terrain_sample.sample_terrain_value(latitude, longitude, layer, self.path)

    def test_geographic_crs_and_configured_paths(self):
        self.transform = from_origin(30, 60, 0.5, 0.5)
        self.write(crs='EPSG:4326')
        with patch.object(terrain_sample, 'ELEVATION_RASTER', self.path), \
                patch.object(terrain_sample, 'SLOPE_RASTER', self.path):
            self.assertEqual(terrain_sample.sample_terrain_value(59.75, 30.25, 'elevation')['value'], -25.5)
            self.assertEqual(terrain_sample.sample_terrain_value(59.75, 31.25, 'slope')['value'], 10.25)


if __name__ == '__main__':
    unittest.main()
