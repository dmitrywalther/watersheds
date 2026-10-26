import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

from services.dem_catalog import DemSourceError, get_dem_config, list_dem_sources, is_cloud_placeholder


class DemCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = {
            'label': 'Test DEM', 'resolution_label': '30″',
            'coverage_note': 'Test region', 'flow_dir_encoding': 'esri',
            'flow_acc_units': 'cells',
            'flow_dir': self.root / 'dir.tif', 'flow_acc': self.root / 'acc.tif',
        }
        self.write_grid(self.spec['flow_dir'], 'uint8', 255)
        self.write_grid(self.spec['flow_acc'], 'float32', -9999)
        self.registry = patch('services.dem_catalog.DEM_FILES', {'test': self.spec})
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def write_grid(self, path, dtype, nodata, transform=None, crs='EPSG:3395'):
        with rasterio.open(path, 'w', driver='GTiff', width=3, height=3,
                           count=1, dtype=dtype, nodata=nodata, crs=crs,
                           transform=transform or from_origin(0, 900, 300, 300)) as dst:
            dst.write(np.ones((3, 3), dtype=dtype), 1)

    def test_catalog_uses_actual_grid_size_and_does_not_expose_paths(self):
        catalog = list_dem_sources()
        source = catalog['sources'][0]
        self.assertTrue(source['available'])
        self.assertEqual(source['grid_resolution_m'], 300)
        self.assertNotIn('flow_dir', source)
        self.assertEqual(catalog['default_source'], 'test')
        self.assertEqual(get_dem_config('test')['resolution'], 300)

    def test_missing_accumulation_disables_source(self):
        self.spec['flow_acc'].unlink()
        source = list_dem_sources()['sources'][0]
        self.assertFalse(source['available'])
        self.assertIn('накопления', source['unavailable_reason'])
        self.assertIsNone(list_dem_sources()['default_source'])
        with self.assertRaises(DemSourceError):
            get_dem_config('test')

    def test_misaligned_pair_is_rejected(self):
        self.write_grid(self.spec['flow_acc'], 'float32', -9999,
                        transform=from_origin(150, 900, 300, 300))
        with self.assertRaisesRegex(DemSourceError, 'сетк'):
            get_dem_config('test')

    def test_geographic_grid_is_not_treated_as_meters(self):
        for name, dtype, nodata in [('flow_dir', 'uint8', 255), ('flow_acc', 'float32', -9999)]:
            self.write_grid(self.spec[name], dtype, nodata, crs='EPSG:4326')
        with self.assertRaisesRegex(DemSourceError, 'метр'):
            get_dem_config('test')

    def test_cloud_placeholder_detected_before_open(self):
        fake = SimpleNamespace(stat=lambda: SimpleNamespace(st_file_attributes=0x400020))
        self.assertTrue(is_cloud_placeholder(fake))
        with patch('services.dem_catalog.is_cloud_placeholder', return_value=True), \
                patch('services.dem_catalog.rasterio.open') as open_raster:
            self.assertFalse(list_dem_sources()['sources'][0]['available'])
            open_raster.assert_not_called()

    def test_unknown_source_is_an_actionable_error(self):
        with self.assertRaisesRegex(DemSourceError, 'Неизвест'):
            get_dem_config('missing')


if __name__ == '__main__':
    unittest.main()
