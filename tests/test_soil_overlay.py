import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union

from services import categorical_overlay, soil_overlay


class SoilOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'soil.tif'
        self.crs = 'EPSG:3395'
        self.transform = from_origin(1000000, 2001000, 250, 250)
        self.values = np.array([[1, 1, 2, 65536], [1, 2, 999, 65536]], dtype=np.uint32)
        self.write()

    def write(self, mask=None, external=False):
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=not external), rasterio.open(
                self.path, 'w', driver='GTiff', width=self.values.shape[1],
                height=self.values.shape[0], count=1, dtype=self.values.dtype,
                nodata=65536, crs=self.crs, transform=self.transform) as dst:
            dst.write(self.values, 1)
            if mask is not None:
                dst.write_mask(mask)

    def basin(self, left=0, top=0, right=4, bottom=2):
        x1, y1 = self.transform * (left, top)
        x2, y2 = self.transform * (right, bottom)
        return gpd.GeoDataFrame(geometry=[box(x1, y2, x2, y1)], crs=self.crs)

    def native_geometry(self, result):
        return gpd.GeoDataFrame.from_features(result['geometry'], crs='EPSG:4326').to_crs('EPSG:3395')

    def test_native_class_geometry_excludes_nodata_and_preserves_unknown_code(self):
        result = soil_overlay.build_soil_map(self.basin(), self.path)
        self.assertIsInstance(result, dict)
        self.assertEqual(result['status'], 'ok')
        self.assertFalse(result['approximate'])
        self.assertEqual(result['sample_resolution_m'], 250)
        self.assertEqual(result['geometry']['type'], 'FeatureCollection')
        features = result['geometry']['features']
        self.assertEqual([item['properties']['code'] for item in features], [1, 2, 999])
        self.assertEqual(features[2]['properties']['name'], 'Неизвестный класс (код 999)')
        by_code = self.native_geometry(result).set_index('code')
        self.assertAlmostEqual(by_code.loc[1].geometry.area, 3 * 250**2, places=4)
        self.assertAlmostEqual(by_code.loc[2].geometry.area, 2 * 250**2, places=4)
        self.assertAlmostEqual(by_code.loc[999].geometry.area, 250**2, places=4)

    def test_class_polygons_are_clipped_to_exact_boundary_and_holes(self):
        outer = self.basin(right=3).geometry[0]
        hole = self.basin(left=1.1, top=0.1, right=1.9, bottom=0.9).geometry[0]
        basin = gpd.GeoDataFrame(geometry=[Polygon(outer.exterior.coords, [hole.exterior.coords])], crs='EPSG:3395')
        result = soil_overlay.build_soil_map(basin, self.path)
        self.assertIsInstance(result, dict)
        self.assertEqual(result['status'], 'ok')
        basin_4326 = basin.to_crs('EPSG:4326').geometry[0]
        for feature in result['geometry']['features']:
            polygon = shape(feature['geometry'])
            self.assertTrue(polygon.is_valid)
            self.assertTrue(basin_4326.covers(polygon))
        visible = unary_union(self.native_geometry(result).geometry)
        self.assertLess(visible.symmetric_difference(basin.geometry[0]).area, 0.001)

    def test_fractional_boundary_uses_intersecting_cells_even_without_centres(self):
        basin = self.basin(left=0.01, top=0.01, right=0.1, bottom=0.1)
        result = soil_overlay.build_soil_map(basin, self.path)
        self.assertIsInstance(result, dict)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([f['properties']['code'] for f in result['geometry']['features']], [1])
        self.assertLess(self.native_geometry(result).geometry[0].symmetric_difference(basin.geometry[0]).area, 0.001)

    def test_native_mask_removes_cell_geometry(self):
        for external in (False, True):
            with self.subTest(external=external):
                mask = np.full(self.values.shape, 255, dtype=np.uint8)
                mask[0, 0] = 0
                self.write(mask, external=external)
                result = soil_overlay.build_soil_map(self.basin(), self.path)
                self.assertIsInstance(result, dict)
                self.assertEqual(result['status'], 'ok')
                visible = unary_union(self.native_geometry(result).geometry)
                self.assertAlmostEqual(visible.area, 5 * 250**2, places=4)

    def test_no_data_and_outside_raster_return_empty_collection(self):
        for basin in (self.basin(left=3), self.basin(left=5, right=7)):
            result = soil_overlay.build_soil_map(basin, self.path)
            self.assertIsInstance(result, dict)
            self.assertEqual(result['status'], 'no_data')
            self.assertEqual(result['geometry'], {'type': 'FeatureCollection', 'features': []})
            self.assertTrue(result['reason'])

    def test_missing_or_corrupt_raster_does_not_break_analysis(self):
        for path in (self.path.with_name('missing.tif'), self.path):
            if path == self.path:
                path.write_bytes(b'not a raster')
            result = soil_overlay.build_soil_map(self.basin(), path)
            self.assertIsInstance(result, dict)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['geometry']['features'], [])
            self.assertTrue(result['reason'])

    def test_cloud_raster_or_mask_is_not_opened(self):
        mask_path = self.path.with_name(self.path.name + '.msk')
        mask_path.write_bytes(b'cloud')
        for cloud_path in (self.path, mask_path):
            with patch.object(categorical_overlay, 'is_cloud_placeholder', create=True,
                              side_effect=lambda path: path == cloud_path), \
                    patch.object(categorical_overlay, 'rasterio', create=True) as mocked:
                result = soil_overlay.build_soil_map(self.basin(), self.path)
                mocked.open.assert_not_called()
            self.assertIsInstance(result, dict)
            self.assertEqual(result['status'], 'unavailable')
            self.assertIn('облак', result['reason'])

    def test_overlapping_basins_are_not_duplicated(self):
        basin = self.basin()
        duplicated = gpd.GeoDataFrame(geometry=[basin.geometry[0], basin.geometry[0]], crs=basin.crs)
        result = soil_overlay.build_soil_map(duplicated, self.path)
        self.assertIsInstance(result, dict)
        self.assertAlmostEqual(self.native_geometry(result).geometry.area.sum(), 6 * 250**2, places=4)

    def test_large_unsigned_code_keeps_its_exact_integer_value(self):
        self.values[0, 0] = np.iinfo(np.uint32).max
        self.write()
        result = soil_overlay.build_soil_map(self.basin(), self.path)
        self.assertIsInstance(result, dict)
        self.assertIn(4294967295, [f['properties']['code'] for f in result['geometry']['features']])

    def test_nearest_sampling_reduces_large_preview_without_inventing_codes(self):
        self.values = np.arange(1, 65, dtype=np.uint32).reshape(8, 8)
        self.write()
        with patch.object(categorical_overlay, 'MAX_GRID_EDGE', 2, create=True):
            result = soil_overlay.build_soil_map(self.basin(right=8, bottom=8), self.path)
        self.assertIsInstance(result, dict)
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['approximate'])
        self.assertEqual(result['sample_resolution_m'], 1000)
        self.assertEqual([f['properties']['code'] for f in result['geometry']['features']], [19, 23, 51, 55])

    def test_high_polygon_complexity_is_coarsened_instead_of_returning_huge_geojson(self):
        self.values = (np.indices((8, 8)).sum(axis=0) % 2 + 1).astype(np.uint32)
        self.write()
        with patch.object(categorical_overlay, 'MAX_POLYGONS', 5, create=True):
            result = soil_overlay.build_soil_map(self.basin(right=8, bottom=8), self.path)
        self.assertIsInstance(result, dict)
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['approximate'])
        self.assertLessEqual(len(result['geometry']['features']), 5)
        self.assertEqual(result['sample_resolution_m'], 500)

    def test_empty_coarse_samples_do_not_claim_original_raster_has_no_data(self):
        self.values = np.full((8, 8), 65536, dtype=np.uint32)
        self.values[0, 0] = 1
        self.write()
        with patch.object(categorical_overlay, 'MAX_GRID_EDGE', 2):
            result = soil_overlay.build_soil_map(self.basin(right=8, bottom=8), self.path)
        self.assertEqual(result['status'], 'no_data')
        self.assertTrue(result['approximate'])
        self.assertEqual(result['sample_resolution_m'], 1000)
        self.assertIn('обзорной сетке', result['reason'])

    def test_geographic_grid_does_not_label_degrees_as_metres(self):
        self.crs = 'EPSG:4326'
        self.transform = from_origin(10, 20, 0.01, 0.01)
        self.write()
        result = soil_overlay.build_soil_map(self.basin(), self.path)
        self.assertEqual(result['status'], 'ok')
        self.assertIsNone(result['sample_resolution_m'])

    def test_projected_feet_are_converted_to_metres_in_metadata(self):
        self.crs = 'EPSG:2263'
        self.transform = from_origin(1000000, 200000, 250, 250)
        self.write()
        result = soil_overlay.build_soil_map(self.basin(), self.path)
        self.assertEqual(result['status'], 'ok')
        self.assertAlmostEqual(result['sample_resolution_m'], 250 * 1200 / 3937, places=6)

    def test_hard_coordinate_budget_returns_explicit_unavailable(self):
        with patch.object(categorical_overlay, 'MAX_COORDINATES', 3, create=True):
            result = soil_overlay.build_soil_map(self.basin(), self.path)
        self.assertIsInstance(result, dict)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['geometry']['features'], [])
        self.assertIn('слож', result['reason'].lower())

    def test_source_area_limit_rejects_before_reading(self):
        with patch.object(categorical_overlay, 'MAX_SOURCE_CELLS', 3, create=True):
            result = soil_overlay.build_soil_map(self.basin(), self.path)
        self.assertIsInstance(result, dict)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('размер', result['reason'].lower())

    def test_missing_basin_crs_is_unavailable(self):
        basin = gpd.GeoDataFrame(geometry=[self.basin().geometry[0]])
        result = soil_overlay.build_soil_map(basin, self.path)
        self.assertIsInstance(result, dict)
        self.assertEqual(result['status'], 'unavailable')

    def test_reads_use_bounded_windows_even_when_preview_is_coarse(self):
        self.values = (np.indices((8, 8)).sum(axis=0) % 2 + 1).astype(np.uint32)
        self.write()
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
                self.assertLessEqual(kwargs['window'].width, 3)
                self.assertLessEqual(kwargs['window'].height, 3)
                calls.append(kwargs['window'])
                return guard.dataset.read(*args, **kwargs)

            def read_masks(guard, *args, **kwargs):
                self.assertIsNotNone(kwargs.get('window'))
                self.assertLessEqual(kwargs['window'].width, 3)
                self.assertLessEqual(kwargs['window'].height, 3)
                return guard.dataset.read_masks(*args, **kwargs)

        with patch.object(categorical_overlay, 'rasterio', create=True) as mocked, \
                patch.object(categorical_overlay, 'READ_CHUNK_EDGE', 3, create=True), \
                patch.object(categorical_overlay, 'MAX_GRID_EDGE', 2, create=True):
            mocked.Env = rasterio.Env
            mocked.open.side_effect = ReadGuard
            result = soil_overlay.build_soil_map(self.basin(right=8, bottom=8), self.path)
        self.assertIsInstance(result, dict)
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(calls)


if __name__ == '__main__':
    unittest.main()
