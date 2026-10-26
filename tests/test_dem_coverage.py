import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import Point, box, shape
from shapely.ops import unary_union

from services import dem_coverage
from services.dem_catalog import DemSourceError


class DemCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = {
            'label': 'Test coverage', 'flow_dir_encoding': 'esri', 'flow_acc_units': 'cells',
            'flow_dir': self.root / 'dir.tif', 'flow_acc': self.root / 'acc.tif',
        }
        self.transform = from_origin(1000000, 2000000, 1000, 1000)
        self.direction = np.ones((12, 12), dtype=np.uint8)
        self.accumulation = np.ones((12, 12), dtype=np.float32)
        self.write_pair()
        self.registry = patch('services.dem_catalog.DEM_FILES', {'test': self.spec})
        self.registry.start()
        self.addCleanup(self.registry.stop)
        self.cache = patch.object(dem_coverage, 'CACHE_DIR', self.root / 'cache')
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def write(self, name, values, transform=None):
        with rasterio.open(self.spec[name], 'w', driver='GTiff', width=values.shape[1],
                           height=values.shape[0], count=1, dtype=values.dtype,
                           nodata=255 if name == 'flow_dir' else -9999,
                           crs='EPSG:3395', transform=transform or self.transform) as dst:
            dst.write(values, 1)

    def write_pair(self):
        self.write('flow_dir', self.direction)
        self.write('flow_acc', self.accumulation)

    def geometry(self, result):
        return unary_union([shape(feature['geometry']) for feature in result['geometry']['features']])

    def pixel_point(self, row, col):
        xy = self.transform * (col + 0.5, row + 0.5)
        return Point(*Transformer.from_crs(3395, 4326, always_xy=True).transform(*xy))

    def test_coverage_intersects_masks_and_excludes_nonfinite_accumulation(self):
        self.direction[3:6, 3:6] = 255
        self.accumulation[8:11, 8:11] = -9999
        self.accumulation[1, 1] = np.nan
        self.write_pair()
        result = dem_coverage.get_dem_coverage('test')
        geometry = self.geometry(result)
        self.assertTrue(geometry.contains(self.pixel_point(0, 0)))
        for row, col in [(4, 4), (9, 9), (1, 1)]:
            self.assertFalse(geometry.contains(self.pixel_point(row, col)))
        self.assertTrue(result['approximate'])
        self.assertEqual(result['sample_resolution_m'], 1000)
        self.assertEqual(result['source_id'], 'test')
        self.assertIn('водосбор', result['note'])
        self.assertEqual(result['geometry']['type'], 'FeatureCollection')

    def test_disconnected_parts_and_holes_are_preserved(self):
        self.direction[:, 6:8] = 255
        self.direction[3:5, 2:4] = 255
        self.write_pair()
        result = dem_coverage.get_dem_coverage('test')
        geometry = self.geometry(result)
        self.assertTrue(geometry.contains(self.pixel_point(1, 1)))
        self.assertTrue(geometry.contains(self.pixel_point(1, 9)))
        self.assertFalse(geometry.contains(self.pixel_point(3, 2)))
        self.assertFalse(geometry.contains(self.pixel_point(1, 6)))
        self.assertEqual(len(geometry.geoms), 2)

    def test_empty_coverage_has_empty_features_and_no_bounds(self):
        self.direction[:] = 255
        self.write_pair()
        result = dem_coverage.get_dem_coverage('test')
        self.assertEqual(result['geometry']['features'], [])
        self.assertIsNone(result['bounds'])

    def test_sampling_dimension_is_bounded(self):
        with patch.object(dem_coverage, 'MAX_SAMPLE_DIMENSION', 4):
            result = dem_coverage.get_dem_coverage('test')
        self.assertEqual(result['sample_resolution_m'], 3000)

    def test_cache_reused_and_invalidated_when_input_changes(self):
        first = dem_coverage.get_dem_coverage('test')
        with patch.object(dem_coverage, '_build_coverage', side_effect=AssertionError('cache missed')):
            self.assertEqual(dem_coverage.get_dem_coverage('test'), first)
        before = self.spec['flow_acc'].stat()
        self.accumulation[:] = -9999
        self.write_pair()
        os.utime(self.spec['flow_acc'], ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
        self.assertIsNone(dem_coverage.get_dem_coverage('test')['bounds'])

    def test_corrupted_cache_is_rebuilt(self):
        first = dem_coverage.get_dem_coverage('test')
        cache_file = next((self.root / 'cache').glob('*.json'))
        cache_file.write_text('{not json', encoding='utf-8')
        self.assertEqual(dem_coverage.get_dem_coverage('test'), first)

    def test_valid_json_with_invalid_cached_geometry_is_rebuilt(self):
        first = dem_coverage.get_dem_coverage('test')
        cache_file = next((self.root / 'cache').glob('*.json'))
        malformed = dict(first, geometry={'type': 'FeatureCollection'})
        cache_file.write_text(json.dumps(malformed), encoding='utf-8')
        self.assertEqual(dem_coverage.get_dem_coverage('test'), first)

    def test_concurrent_requests_build_once(self):
        with patch.object(dem_coverage, '_build_coverage', wraps=dem_coverage._build_coverage) as build:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(dem_coverage.get_dem_coverage, ['test', 'test']))
        self.assertEqual(results[0], results[1])
        self.assertEqual(build.call_count, 1)

    def test_input_change_during_build_does_not_publish_stale_cache(self):
        build = dem_coverage._build_coverage
        before = self.spec['flow_dir'].stat()
        def mutate_after_build(source_id, dem):
            result = build(source_id, dem)
            os.utime(self.spec['flow_dir'], ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
            return result
        with patch.object(dem_coverage, '_build_coverage', side_effect=mutate_after_build):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                dem_coverage.get_dem_coverage('test')
        self.assertFalse(list((self.root / 'cache').glob('*.json')))

    def test_misaligned_or_unavailable_pair_is_rejected(self):
        self.write('flow_acc', self.accumulation, from_origin(1000500, 2000000, 1000, 1000))
        with self.assertRaises(DemSourceError):
            dem_coverage.get_dem_coverage('test')
        self.spec['flow_acc'].unlink()
        with self.assertRaises(DemSourceError):
            dem_coverage.get_dem_coverage('test')

    def test_antimeridian_geometry_split_and_bounds_use_short_arc(self):
        project = Transformer.from_crs(4326, 3395, always_xy=True, force_over=True)
        left, top = project.transform(170, 65)
        right, _ = project.transform(190, 65)
        self.transform = from_origin(left, top, (right - left) / 12, 1000)
        self.write_pair()
        result = dem_coverage.get_dem_coverage('test')
        self.assertAlmostEqual(result['bounds'][0], 170, places=5)
        self.assertAlmostEqual(result['bounds'][2], 190, places=5)
        geometry = self.geometry(result)
        self.assertGreaterEqual(len(geometry.geoms), 2)
        for polygon in geometry.geoms:
            coordinates = list(polygon.exterior.coords)
            self.assertTrue(all(-180.000001 <= x <= 180.000001 for x, _ in coordinates))
            self.assertTrue(all(abs(a[0] - b[0]) <= 180 for a, b in zip(coordinates, coordinates[1:])))

    def test_camera_bounds_do_not_treat_covered_longitude_intervals_as_gaps(self):
        self.assertEqual(dem_coverage._camera_bounds([box(-180, 40, 180, 80)]), [-180, 40, 180, 80])
        self.assertEqual(dem_coverage._camera_bounds([box(-170, 40, 170, 80)]), [-170, 40, 170, 80])
        self.assertEqual(dem_coverage._camera_bounds([box(-180, 40, -169, 80), box(19, 40, 180, 80)]),
                         [19, 40, 191, 80])

    def test_external_masks_are_respected_and_invalidate_cache(self):
        before = dem_coverage.get_dem_coverage('test')
        self.assertTrue(self.geometry(before).contains(self.pixel_point(4, 4)))
        mask = np.full(self.direction.shape, 255, dtype=np.uint8)
        mask[3:6, 3:6] = 0
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=False), rasterio.open(self.spec['flow_acc'], 'r+') as dataset:
            dataset.write_mask(mask)
        self.assertTrue(self.spec['flow_acc'].with_name('acc.tif.msk').is_file())
        after = dem_coverage.get_dem_coverage('test')
        self.assertFalse(self.geometry(after).contains(self.pixel_point(4, 4)))

    def test_endpoint_reports_bad_source_and_friendly_server_error(self):
        from start import app
        client = app.test_client()
        response = client.get('/dem_coverage?source=unknown')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Неизвест', response.json['error'])
        with patch('start.get_dem_coverage', side_effect=RuntimeError('internal details')):
            with self.assertLogs(app.logger, level='ERROR'):
                response = client.get('/dem_coverage?source=test')
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('internal details', response.json['error'])
        with patch('start.get_dem_coverage', side_effect=ValueError('internal geometry details')):
            with self.assertLogs(app.logger, level='ERROR'):
                response = client.get('/dem_coverage?source=test')
        self.assertEqual(response.status_code, 500)


if __name__ == '__main__':
    unittest.main()
