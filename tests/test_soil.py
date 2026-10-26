import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from services import soil


class SoilTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'soil.tif'
        self.transform = from_origin(1000000, 2001000, 250, 250)
        self.values = np.array([[1, 1, 2, 65536], [1, 2, 999, 65536]], dtype=np.uint32)
        self.write()

    def write(self, mask=None, external=False):
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=not external), rasterio.open(
                self.path, 'w', driver='GTiff', width=self.values.shape[1],
                height=self.values.shape[0], count=1, dtype=self.values.dtype,
                nodata=65536, crs='EPSG:3395', transform=self.transform) as dst:
            dst.write(self.values, 1)
            if mask is not None:
                dst.write_mask(mask)

    def basin(self, left=0, top=0, right=4, bottom=2):
        x1, y1 = self.transform * (left, top)
        x2, y2 = self.transform * (right, bottom)
        return gpd.GeoDataFrame(geometry=[box(x1, y2, x2, y1)], crs='EPSG:3395')

    def test_native_categories_exclude_nodata_and_keep_unknown_codes(self):
        result = soil.analyze_soil(self.basin(), self.path)
        self.assertIsInstance(result, dict)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['valid_cells'], 6)
        self.assertEqual(result['total_cells'], 8)
        self.assertEqual(result['coverage_percent'], 75)
        self.assertEqual([(item['code'], item['percent']) for item in result['categories']],
                         [(1, 50), (2, 33.33), (999, 16.67)])
        self.assertEqual(result['categories'][0]['name'], 'Арктические пустынные')
        self.assertEqual(result['categories'][2]['name'], 'Неизвестный класс (код 999)')
        self.assertIsNone(result['reason'])

    def test_reprojects_geographic_basin_before_reading(self):
        expected = soil.analyze_soil(self.basin(), self.path)
        actual = soil.analyze_soil(self.basin().to_crs('EPSG:4326'), self.path)
        self.assertEqual(actual, expected)

    def test_overlapping_polygons_count_each_pixel_once(self):
        basin = self.basin()
        basin = gpd.GeoDataFrame(geometry=[basin.geometry[0], basin.geometry[0]], crs=basin.crs)
        self.assertEqual(soil.analyze_soil(basin, self.path), soil.analyze_soil(self.basin(), self.path))

    def test_partial_coverage_includes_pixel_centres_outside_raster(self):
        result = soil.analyze_soil(self.basin(left=-2), self.path)
        self.assertEqual(result['total_cells'], 12)
        self.assertEqual(result['valid_cells'], 6)
        self.assertEqual(result['coverage_percent'], 50)

    def test_entirely_outside_raster_has_no_data(self):
        result = soil.analyze_soil(self.basin(left=5, right=7), self.path)
        self.assertEqual(result['status'], 'no_data')
        self.assertEqual(result['coverage_percent'], 0)
        self.assertEqual(result['valid_cells'], 0)
        self.assertEqual(result['total_cells'], 4)
        self.assertEqual(result['categories'], [])
        self.assertTrue(result['reason'])

    def test_tiny_polygon_without_cell_centres_does_not_invent_a_class(self):
        result = soil.analyze_soil(self.basin(left=0.05, top=0.05, right=0.1, bottom=0.1), self.path)
        self.assertEqual(result['status'], 'no_data')
        self.assertEqual(result['total_cells'], 0)
        self.assertIsNone(result['coverage_percent'])

    def test_all_nodata_has_explicit_no_data_state(self):
        result = soil.analyze_soil(self.basin(left=3), self.path)
        self.assertEqual(result['status'], 'no_data')
        self.assertEqual(result['coverage_percent'], 0)
        self.assertEqual(result['total_cells'], 2)

    def test_internal_mask_and_nodata_both_excluded(self):
        mask = np.full(self.values.shape, 255, dtype=np.uint8)
        mask[0, 0] = 0
        self.write(mask)
        result = soil.analyze_soil(self.basin(), self.path)
        self.assertEqual(result['valid_cells'], 5)
        self.assertEqual(result['coverage_percent'], 62.5)
        self.assertEqual([(item['code'], item['percent']) for item in result['categories']],
                         [(1, 40), (2, 40), (999, 20)])

    def test_external_mask_honored_without_loading_other_sidecars(self):
        mask = np.full(self.values.shape, 255, dtype=np.uint8)
        mask[0, 0] = 0
        self.write(mask, external=True)
        result = soil.analyze_soil(self.basin(), self.path)
        self.assertEqual(result['valid_cells'], 5)
        self.assertEqual(result['coverage_percent'], 62.5)

    def test_missing_raster_is_unavailable(self):
        result = soil.analyze_soil(self.basin(), self.path.with_name('missing.tif'))
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['coverage_percent'])
        self.assertTrue(result['reason'])

    def test_cloud_only_raster_is_not_opened(self):
        with patch.object(soil, 'is_cloud_placeholder', return_value=True), \
                patch.object(soil.rasterio, 'open', side_effect=AssertionError('must not open cloud file')):
            result = soil.analyze_soil(self.basin(), self.path)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('облак', result['reason'])

    def test_corrupt_raster_is_unavailable(self):
        self.path.write_bytes(b'not a raster')
        result = soil.analyze_soil(self.basin(), self.path)
        self.assertEqual(result['status'], 'unavailable')

    def test_pathological_window_rejected_without_raster_reads(self):
        with patch.object(soil, 'MAX_WINDOW_CELLS', 10):
            result = soil.analyze_soil(self.basin(left=-10), self.path)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('размер', result['reason'].lower())
        self.assertIsNone(result['coverage_percent'])

    def test_chunk_boundaries_preserve_counts(self):
        expected = soil.analyze_soil(self.basin(left=-2), self.path)
        with patch.object(soil, 'CHUNK_SIZE', 2):
            self.assertEqual(soil.analyze_soil(self.basin(left=-2), self.path), expected)

    def test_every_raster_read_is_bounded_and_at_native_resolution(self):
        real_open = rasterio.open
        calls = []

        class ReadGuard:
            def __init__(guard, *args, **kwargs):
                guard.dataset = real_open(*args, **kwargs)

            def __enter__(guard):
                return guard

            def __exit__(guard, *args):
                guard.dataset.close()

            def __getattr__(guard, name):
                return getattr(guard.dataset, name)

            def read(guard, *args, **kwargs):
                self.assertIsNotNone(kwargs.get('window'))
                self.assertNotIn('out_shape', kwargs)
                self.assertLessEqual(kwargs['window'].width, 2)
                self.assertLessEqual(kwargs['window'].height, 2)
                calls.append(kwargs['window'])
                return guard.dataset.read(*args, **kwargs)

            def read_masks(guard, *args, **kwargs):
                self.assertIsNotNone(kwargs.get('window'))
                self.assertNotIn('out_shape', kwargs)
                self.assertLessEqual(kwargs['window'].width, 2)
                self.assertLessEqual(kwargs['window'].height, 2)
                return guard.dataset.read_masks(*args, **kwargs)

        with patch.object(soil.rasterio, 'open', side_effect=ReadGuard), patch.object(soil, 'CHUNK_SIZE', 2):
            result = soil.analyze_soil(self.basin(left=-2), self.path)
        self.assertEqual(result['valid_cells'], 6)
        self.assertEqual(len(calls), 2)

    def test_cloud_external_mask_prevents_reading_incomplete_validity(self):
        mask_path = self.path.with_name(self.path.name + '.msk')
        mask_path.write_bytes(b'cloud-placeholder')
        with patch.object(soil, 'is_cloud_placeholder', side_effect=lambda path: path == mask_path), \
                patch.object(soil.rasterio, 'open', side_effect=AssertionError('must not open cloud file')):
            result = soil.analyze_soil(self.basin(), self.path)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('облак', result['reason'])

    def test_missing_basin_crs_is_explicitly_unavailable(self):
        basin = gpd.GeoDataFrame(geometry=[self.basin().geometry[0]])
        self.assertEqual(soil.analyze_soil(basin, self.path)['status'], 'unavailable')

    def test_default_configured_raster_used(self):
        with patch.object(soil, 'SOIL_RASTER', self.path):
            self.assertEqual(soil.analyze_soil(self.basin()), soil.analyze_soil(self.basin(), self.path))


if __name__ == '__main__':
    unittest.main()
