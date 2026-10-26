import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from services import analysis, landcover_overlay, soil, soil_overlay, terrain_overlay


class AnalysisSoilTests(unittest.TestCase):
    def setUp(self):
        self.basin = gpd.GeoDataFrame(geometry=[box(0, 0, 500, 500)], crs='EPSG:3395')
        # Keep all optional raster readers local to each test; no installed
        # dataset should make unit tests slow or change their results.
        for module, name in [(soil, 'SOIL_RASTER'), (soil_overlay, 'SOIL_RASTER'),
                             (landcover_overlay, 'LANDCOVER_RASTER'),
                             (terrain_overlay, 'ELEVATION_RASTER'), (terrain_overlay, 'SLOPE_RASTER')]:
            mocked = patch.object(module, name, Path(__file__).parent / 'missing-optional-raster.tif')
            mocked.start()
            self.addCleanup(mocked.stop)
        for target, value in [('zonal_stats', [{'min': 0, 'max': 10, 'mean': 5}]),
                              ('calculate_monthly_series', [0] * 12),
                              ('categorical_area_stats', {'Forest': 100})]:
            mocked = patch.object(analysis, target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_analysis_includes_real_soil_distribution(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / 'soil.tif'
            with rasterio.open(path, 'w', driver='GTiff', width=2, height=2, count=1,
                               dtype='uint32', nodata=65536, crs='EPSG:3395',
                               transform=from_origin(0, 500, 250, 250)) as dst:
                dst.write(np.full((2, 2), 307, dtype=np.uint32), 1)
            with patch.object(soil, 'SOIL_RASTER', path), patch.object(soil_overlay, 'SOIL_RASTER', path):
                result = analysis.run_analysis(self.basin)
        self.assertIn('soil', result)
        self.assertEqual(result['soil']['status'], 'ok')
        self.assertEqual(result['soil']['categories'], [{'code': 307, 'name': 'Вода', 'percent': 100}])
        self.assertIn('soil_map', result)
        self.assertEqual(result['soil_map']['status'], 'ok')
        self.assertEqual(result['soil_map']['geometry']['features'][0]['properties'], {'code': 307, 'name': 'Вода'})
        self.assertEqual(result['landcover'], {'Forest': 100})
        self.assertEqual(result['area_km2'], 0.25)
        self.assertEqual(result.get('landcover_map', {}).get('status'), 'unavailable')

    def test_analysis_includes_landcover_layer_without_changing_statistics(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / 'landcover.tif'
            with rasterio.open(path, 'w', driver='GTiff', width=2, height=2, count=1,
                               dtype='uint8', nodata=255, crs='EPSG:3395',
                               transform=from_origin(0, 500, 250, 250)) as dst:
                dst.write(np.array([[14, 70], [230, 255]], dtype=np.uint8), 1)
            with patch.object(landcover_overlay, 'LANDCOVER_RASTER', path):
                result = analysis.run_analysis(self.basin)
        self.assertIn('landcover_map', result)
        self.assertEqual(result['landcover_map']['status'], 'ok')
        self.assertEqual([f['properties'] for f in result['landcover_map']['geometry']['features']], [
            {'code': 14, 'name': 'Rainfed croplands'},
            {'code': 70, 'name': 'Closed needleleaved evergreen forest'},
        ])
        self.assertEqual(result['soil']['status'], 'unavailable')
        self.assertEqual(result['soil_map']['status'], 'unavailable')
        self.assertEqual(result['landcover'], {'Forest': 100})
        self.assertEqual(result['terrain']['elevation']['mean'], 5)
        self.assertEqual(result['climate']['temperature'], [0] * 12)

    def test_unavailable_soil_does_not_break_other_analysis(self):
        missing = Path(__file__).parent / 'missing-soil.tif'
        with patch.object(soil, 'SOIL_RASTER', missing), patch.object(soil_overlay, 'SOIL_RASTER', missing):
            result = analysis.run_analysis(self.basin)
        self.assertEqual(result['soil']['status'], 'unavailable')
        self.assertIn('soil_map', result)
        self.assertEqual(result['soil_map']['status'], 'unavailable')
        self.assertEqual(result['terrain']['elevation']['mean'], 5)
        self.assertEqual(result['climate']['temperature'], [0] * 12)
        self.assertEqual(result['landcover'], {'Forest': 100})

    def test_analysis_adds_real_terrain_layers_without_changing_statistics(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / 'terrain.tif'
            with rasterio.open(path, 'w', driver='GTiff', width=2, height=2, count=1,
                               dtype='float32', nodata=-9999, crs='EPSG:3395',
                               transform=from_origin(0, 500, 250, 250)) as dst:
                dst.write(np.array([[-25, 0], [20, 50]], dtype=np.float32), 1)
            with patch.object(terrain_overlay, 'ELEVATION_RASTER', path), patch.object(terrain_overlay, 'SLOPE_RASTER', path):
                result = analysis.run_analysis(self.basin)
        self.assertIn('elevation_map', result)
        self.assertIn('slope_map', result)
        self.assertEqual(result['elevation_map']['status'], 'ok')
        self.assertEqual(result['elevation_map']['range'], {'min': -25, 'max': 50})
        self.assertEqual(result['slope_map']['status'], 'ok')
        self.assertEqual(result['slope_map']['range'], {'min': 0, 'max': 50})
        self.assertEqual(result['terrain'], {
            'elevation': {'min': 0, 'max': 10, 'mean': 5},
            'slope': {'min': 0, 'max': 10, 'mean': 5},
        })
        self.assertEqual(result['climate']['temperature'], [0] * 12)
        self.assertEqual(result['landcover'], {'Forest': 100})
        self.assertEqual(result['soil']['status'], 'unavailable')

    def test_missing_terrain_layers_do_not_break_other_analysis(self):
        result = analysis.run_analysis(self.basin)
        self.assertEqual(result.get('elevation_map', {}).get('status'), 'unavailable')
        self.assertEqual(result.get('slope_map', {}).get('status'), 'unavailable')
        self.assertEqual(result['terrain']['elevation']['mean'], 5)


if __name__ == '__main__':
    unittest.main()
