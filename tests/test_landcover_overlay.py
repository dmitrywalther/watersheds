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

from services import analysis, categorical_overlay


class LandcoverOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'landcover.tif'
        self.transform = from_origin(1000000, 2001000, 500, 500)
        self.values = np.array([[14, 14, 70, 230], [0, 222, 210, 255]], dtype=np.uint8)
        self.write()

    def write(self, mask=None, external=False):
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=not external), rasterio.open(
                self.path, 'w', driver='GTiff', width=self.values.shape[1],
                height=self.values.shape[0], count=1, dtype=self.values.dtype,
                nodata=255, crs='EPSG:3395', transform=self.transform) as dst:
            dst.write(self.values, 1)
            if mask is not None:
                dst.write_mask(mask)

    def basin(self, left=0, top=0, right=4, bottom=2):
        x1, y1 = self.transform * (left, top)
        x2, y2 = self.transform * (right, bottom)
        return gpd.GeoDataFrame(geometry=[box(x1, y2, x2, y1)], crs='EPSG:3395')

    def build(self, basin=None, path=None):
        builder = getattr(analysis, 'build_landcover_map', None)
        self.assertTrue(callable(builder), 'Analysis must expose the new land-cover map builder')
        return builder(self.basin() if basin is None else basin, self.path if path is None else path)

    def native_geometry(self, result):
        return gpd.GeoDataFrame.from_features(result['geometry'], crs='EPSG:4326').to_crs('EPSG:3395')

    def test_excludes_semantic_and_raster_nodata_but_retains_unknown_codes(self):
        result = self.build()
        self.assertEqual(result['status'], 'ok')
        self.assertFalse(result['approximate'])
        self.assertEqual(result['sample_resolution_m'], 500)
        features = result['geometry']['features']
        self.assertEqual([f['properties']['code'] for f in features], [0, 14, 70, 210, 222])
        by_code = self.native_geometry(result).set_index('code')
        self.assertEqual(by_code.loc[14, 'name'], 'Rainfed croplands')
        self.assertEqual(by_code.loc[210, 'name'], 'Water bodies')
        self.assertEqual(by_code.loc[0, 'name'], 'Неизвестный класс (код 0)')
        self.assertEqual(by_code.loc[222, 'name'], 'Неизвестный класс (код 222)')
        self.assertAlmostEqual(by_code.loc[14].geometry.area, 2 * 500**2, places=3)
        self.assertAlmostEqual(by_code.geometry.area.sum(), 6 * 500**2, places=3)

    def test_only_semantic_nodata_produces_empty_layer(self):
        result = self.build(self.basin(left=3, bottom=1))
        self.assertEqual(result['status'], 'no_data')
        self.assertEqual(result['geometry']['features'], [])
        self.assertIn('земного покрова', result['reason'])

    def test_exact_boundary_holes_and_partial_cells_are_clipped(self):
        outer = self.basin(left=0.05, top=0.05, right=2.95, bottom=1.95).geometry[0]
        hole = self.basin(left=1.1, top=0.1, right=1.9, bottom=0.9).geometry[0]
        basin = gpd.GeoDataFrame(geometry=[Polygon(outer.exterior.coords, [hole.exterior.coords])], crs='EPSG:3395')
        result = self.build(basin)
        self.assertEqual(result['status'], 'ok')
        projected = basin.to_crs('EPSG:4326').geometry[0]
        for feature in result['geometry']['features']:
            self.assertTrue(projected.covers(shape(feature['geometry'])))
        actual = unary_union(self.native_geometry(result).geometry)
        self.assertLess(actual.symmetric_difference(basin.geometry[0]).area, 0.001)

    def test_internal_and_external_masks_are_combined_with_semantic_nodata(self):
        for external in (False, True):
            with self.subTest(external=external):
                mask = np.full(self.values.shape, 255, dtype=np.uint8)
                mask[0, 0] = 0
                self.write(mask, external)
                result = self.build()
                self.assertEqual(result['status'], 'ok')
                self.assertAlmostEqual(self.native_geometry(result).geometry.area.sum(), 5 * 500**2, places=3)

    def test_basin_outside_raster_is_no_data(self):
        result = self.build(self.basin(left=5, right=7))
        self.assertEqual(result['status'], 'no_data')
        self.assertEqual(result['geometry']['features'], [])

    def test_missing_and_corrupt_files_do_not_break_analysis(self):
        for path in (self.path.with_name('missing.tif'), self.path):
            if path == self.path:
                path.write_bytes(b'not a raster')
            result = self.build(path=path)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['geometry']['features'], [])
            self.assertIn('земного покрова', result['reason'])

    def test_coarse_samples_keep_codes_and_exclude_semantic_nodata(self):
        self.values = np.full((8, 8), 14, dtype=np.uint8)
        self.values[2, 2] = 230
        self.values[2, 6] = 70
        self.values[6, 2] = 210
        self.values[6, 6] = 222
        self.write()
        with patch.object(categorical_overlay, 'MAX_GRID_EDGE', 2):
            result = self.build(self.basin(right=8, bottom=8))
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['approximate'])
        self.assertEqual(result['sample_resolution_m'], 2000)
        self.assertEqual([f['properties']['code'] for f in result['geometry']['features']], [70, 210, 222])
        self.assertIn('Покров', result['reason'])

    def test_empty_coarse_preview_does_not_claim_no_native_classes(self):
        self.values = np.full((8, 8), 230, dtype=np.uint8)
        self.values[0, 0] = 14
        self.write()
        with patch.object(categorical_overlay, 'MAX_GRID_EDGE', 2):
            result = self.build(self.basin(right=8, bottom=8))
        self.assertEqual(result['status'], 'no_data')
        self.assertTrue(result['approximate'])
        self.assertIn('обзорной сетке', result['reason'])

    def test_missing_basin_crs_does_not_break_analysis(self):
        basin = gpd.GeoDataFrame(geometry=[self.basin().geometry[0]])
        result = self.build(basin)
        self.assertEqual(result['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
