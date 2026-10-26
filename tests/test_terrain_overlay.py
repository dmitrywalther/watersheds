import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union

from services import analysis, categorical_overlay, terrain_overlay


class TerrainOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'terrain.tif'
        self.transform = from_origin(1000000, 2001000, 500, 500)
        self.values = np.array([[-25.5, 0, 10.25, 900], [100, 300, -9999, np.nan]], dtype=np.float32)
        self.write()

    def write(self, mask=None, external=False, crs='EPSG:3395', nodata=-9999):
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=not external), rasterio.open(
                self.path, 'w', driver='GTiff', width=self.values.shape[1],
                height=self.values.shape[0], count=1, dtype=self.values.dtype,
                nodata=nodata, crs=crs, transform=self.transform) as dst:
            dst.write(self.values, 1)
            if mask is not None:
                dst.write_mask(mask)

    def basin(self, left=0, top=0, right=None, bottom=None):
        right = self.values.shape[1] if right is None else right
        bottom = self.values.shape[0] if bottom is None else bottom
        x1, y1 = self.transform * (left, top)
        x2, y2 = self.transform * (right, bottom)
        return gpd.GeoDataFrame(geometry=[box(x1, y2, x2, y1)], crs='EPSG:3395')

    def build(self, kind='elevation', basin=None, path=None):
        builder = getattr(analysis, f'build_{kind}_map', None)
        self.assertTrue(callable(builder), f'Analysis must expose the {kind} map builder')
        return builder(self.basin() if basin is None else basin, self.path if path is None else path)

    def native_geometry(self, result):
        return gpd.GeoDataFrame.from_features(result['geometry'], crs='EPSG:4326').to_crs('EPSG:3395')

    def test_float_elevation_keeps_negative_values_and_ignores_nodata_nan(self):
        result = self.build()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['unit'], 'm')
        self.assertEqual(result['source_label'], 'GMTED2010 30″')
        self.assertEqual(result['range'], {'min': -25.5, 'max': 900})
        self.assertFalse(result['approximate'])
        self.assertEqual(result['sample_resolution_m'], 500)
        self.assertLessEqual(len(result['bands']), 24)
        bands = {band['code']: band for band in result['bands']}
        self.assertEqual(result['bands'][0]['min'], -25.5)
        self.assertEqual(result['bands'][-1]['max'], 900)
        native = self.native_geometry(result)
        actual = rasterize([(geometry, code + 1) for geometry, code in zip(native.geometry, native.code)],
                           out_shape=self.values.shape, transform=self.transform, fill=0)
        valid = np.isfinite(self.values) & (self.values != -9999)
        np.testing.assert_array_equal(actual != 0, valid)
        for row, col in zip(*np.where(valid)):
            band = bands[int(actual[row, col]) - 1]
            self.assertGreaterEqual(self.values[row, col], band['min'])
            self.assertLessEqual(self.values[row, col], band['max'])
        for feature in result['geometry']['features']:
            self.assertEqual(feature['properties'], bands[feature['properties']['code']])
        json.dumps(result, allow_nan=False)

    def test_slope_keeps_zero_and_ninety_excludes_invalid_values(self):
        self.values = np.array([[-1, 0, 0.25, 90], [90.01, np.inf, -9999, np.nan]], dtype=np.float32)
        self.write()
        result = self.build('slope')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['unit'], 'degrees')
        self.assertEqual(result['range'], {'min': 0, 'max': 90})
        self.assertAlmostEqual(self.native_geometry(result).geometry.area.sum(), 3 * 500**2, places=3)

    def test_constant_field_has_single_band_without_division_by_zero(self):
        self.values = np.full((3, 3), -5, dtype=np.int16)
        self.write()
        result = self.build()
        self.assertEqual(result['range'], {'min': -5, 'max': -5})
        self.assertEqual(result['bands'], [{'code': 0, 'min': -5, 'max': -5}])
        self.assertEqual(len(result['geometry']['features']), 1)
        self.assertAlmostEqual(self.native_geometry(result).geometry.area.sum(), 9 * 500**2, places=3)

    def test_adjacent_float64_values_do_not_produce_zero_width_intervals(self):
        self.values = np.array([[1, np.nextafter(1.0, 2.0)]], dtype=np.float64)
        self.write()
        result = self.build()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['range'], {'min': 1, 'max': np.nextafter(1.0, 2.0)})
        self.assertTrue(all(band['min'] < band['max'] for band in result['bands']))
        self.assertLessEqual(len(result['bands']), 24)

    def test_exact_boundary_hole_and_partial_cells_are_clipped(self):
        self.values = np.arange(16, dtype=np.float32).reshape(4, 4)
        self.write()
        outer = self.basin(left=0.05, top=0.05, right=3.95, bottom=3.95).geometry[0]
        hole = self.basin(left=1.1, top=1.1, right=2.9, bottom=2.9).geometry[0]
        basin = gpd.GeoDataFrame(geometry=[Polygon(outer.exterior.coords, [hole.exterior.coords])], crs='EPSG:3395')
        result = self.build(basin=basin)
        self.assertEqual(result['status'], 'ok')
        geographic = basin.to_crs('EPSG:4326').geometry[0]
        for feature in result['geometry']['features']:
            self.assertTrue(geographic.covers(shape(feature['geometry'])))
        actual = unary_union(self.native_geometry(result).geometry)
        self.assertLess(actual.symmetric_difference(basin.geometry[0]).area, 0.001)

    def test_display_range_excludes_cells_outside_basin(self):
        result = self.build(basin=self.basin(left=0.1, top=0.1, right=1.9, bottom=0.9))
        self.assertEqual(result['range'], {'min': -25.5, 'max': 0})

    def test_masks_are_combined_with_nodata_and_nonfinite_values(self):
        for external in (False, True):
            with self.subTest(external=external):
                mask = np.full(self.values.shape, 255, dtype=np.uint8)
                mask[0, 0] = 0
                self.write(mask, external)
                result = self.build()
                self.assertEqual(result['range']['min'], 0)
                self.assertAlmostEqual(self.native_geometry(result).geometry.area.sum(), 5 * 500**2, places=3)

    def test_nodata_only_and_outside_coverage_return_empty(self):
        for basin in (self.basin(left=2, top=1), self.basin(left=5, right=6)):
            with self.subTest(bounds=basin.total_bounds):
                result = self.build(basin=basin)
                self.assertEqual(result['status'], 'no_data')
                self.assertEqual(result['geometry']['features'], [])
                self.assertIsNone(result['range'])
                self.assertEqual(result['bands'], [])

    def test_missing_and_corrupt_files_are_unavailable(self):
        missing = self.path.with_name('missing.tif')
        result = self.build(path=missing)
        self.assertEqual(result['status'], 'unavailable')
        self.path.write_bytes(b'not a raster')
        self.assertEqual(self.build()['status'], 'unavailable')

    def test_cloud_placeholder_is_not_opened(self):
        with patch.object(terrain_overlay, 'is_cloud_placeholder', return_value=True), \
                patch.object(rasterio, 'open', side_effect=AssertionError('Do not hydrate cloud TIFF')):
            result = self.build()
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('облаке', result['reason'])

    def test_missing_crs_is_unavailable(self):
        basin = gpd.GeoDataFrame(geometry=[self.basin().geometry[0]])
        self.assertEqual(self.build(basin=basin)['status'], 'unavailable')
        self.write(crs=None)
        self.assertEqual(self.build()['status'], 'unavailable')

    def test_large_grid_is_bounded_and_uses_explicit_nearest_generalization(self):
        self.values = np.arange(1025 * 2, dtype=np.float32).reshape(2, 1025)
        self.write()
        reads = []
        real_open = rasterio.open

        class ReadTracker:
            def __init__(inner, dataset):
                inner.dataset = dataset
            def __getattr__(inner, name):
                return getattr(inner.dataset, name)
            def __enter__(inner):
                return inner
            def __exit__(inner, *args):
                return inner.dataset.__exit__(*args)
            def read(inner, *args, **kwargs):
                reads.append(kwargs.get('window'))
                return inner.dataset.read(*args, **kwargs)
            def read_masks(inner, *args, **kwargs):
                reads.append(kwargs.get('window'))
                return inner.dataset.read_masks(*args, **kwargs)

        with patch.object(rasterio, 'open', side_effect=lambda *args, **kwargs: ReadTracker(real_open(*args, **kwargs))):
            result = self.build()
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['approximate'])
        self.assertEqual(result['sample_resolution_m'], 1500)
        self.assertIn('обобщ', result['reason'])
        self.assertTrue(reads)
        self.assertTrue(all(window is not None and max(window.width, window.height) <= 512 for window in reads))

    def test_complex_surface_adaptively_coarsens_and_reports_it(self):
        self.values = (np.indices((8, 8)).sum(axis=0) % 2).astype(np.float32)
        self.write()
        with patch.object(categorical_overlay, 'MAX_POLYGONS', 4):
            result = self.build()
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['approximate'])
        self.assertGreater(result['sample_resolution_m'], 500)

    def test_empty_coarse_preview_does_not_claim_no_native_data(self):
        self.values = np.full((8, 8), -9999, dtype=np.float32)
        self.values[0, 0] = 10
        self.write()
        with patch.object(categorical_overlay, 'MAX_GRID_EDGE', 2):
            result = self.build()
        self.assertEqual(result['status'], 'no_data')
        self.assertTrue(result['approximate'])
        self.assertEqual(result['sample_resolution_m'], 2000)
        self.assertIn('обзорной сетке', result['reason'])

    def test_source_limit_is_explicit(self):
        with patch.object(categorical_overlay, 'MAX_SOURCE_CELLS', 1):
            result = self.build()
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('лимит', result['reason'])

    def test_partial_raster_coverage_keeps_only_real_footprint(self):
        result = self.build(basin=self.basin(left=-1, top=-1, right=5, bottom=3))
        self.assertEqual(result['status'], 'ok')
        self.assertAlmostEqual(self.native_geometry(result).geometry.area.sum(), 6 * 500**2, places=3)

    def test_nan_declared_nodata_and_infinities_are_absent(self):
        self.values = np.array([[np.nan, np.inf], [-np.inf, -2.5]], dtype=np.float32)
        self.write(nodata=np.nan)
        result = self.build()
        self.assertEqual(result['range'], {'min': -2.5, 'max': -2.5})
        self.assertAlmostEqual(self.native_geometry(result).geometry.area.sum(), 500**2, places=3)


if __name__ == '__main__':
    unittest.main()
