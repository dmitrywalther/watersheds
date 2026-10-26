import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from services import watershed


class WatershedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.transform = from_origin(1000, 2000, 10, 10)
        self.crs = rasterio.crs.CRS.from_epsg(3395)

    def write_grid(self, directions, accumulation=None, nodata=-9999):
        if accumulation is None:
            accumulation = np.ones(directions.shape, dtype=np.float32)
        for name, data in (("flow_dir", directions), ("flow_acc", accumulation)):
            path = self.directory / (name + ".tif")
            with rasterio.open(
                path, "w", driver="GTiff", count=1,
                height=data.shape[0], width=data.shape[1],
                dtype=str(data.dtype), crs=self.crs, transform=self.transform,
                nodata=nodata,
            ) as dst:
                dst.write(data, 1)
        return {
            "flow_dir": self.directory / "flow_dir.tif",
            "flow_acc": self.directory / "flow_acc.tif",
            "flow_dir_encoding": "esri",
            "resolution": 10.0,
            "grid_resolution_m": 10.0,
            "crs": self.crs,
        }

    def delineate(self, config, row, col):
        x, y = rasterio.transform.xy(self.transform, row, col)
        lon, lat = Transformer.from_crs(self.crs, 4326, always_xy=True).transform(x, y)
        with patch.object(watershed, "get_dem_config", return_value=config):
            return watershed.delineate_watershed(lat, lon, "synthetic")

    def test_delineates_a_complete_synthetic_catchment_without_prefilter_basins(self):
        directions = np.zeros((12, 12), dtype=np.float32)
        directions[4:6, 6] = 4  # ESRI south
        directions[5, 5] = 2   # ESRI southeast
        accumulation = np.ones(directions.shape, dtype=np.float32)
        accumulation[6, 6] = 4
        config = self.write_grid(directions, accumulation)

        result = self.delineate(config, 6, 6)

        self.assertEqual(result.crs, self.crs)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result.geometry.area.sum(), 400.0)

    def test_handles_unsigned_direction_grid_with_unsigned_nodata(self):
        directions = np.zeros((12, 12), dtype=np.uint8)
        directions[4:6, 6] = 4
        directions[5, 5] = 2
        accumulation = np.ones(directions.shape, dtype=np.float32)
        accumulation[6, 6] = 4
        config = self.write_grid(directions, accumulation, nodata=255)

        result = self.delineate(config, 6, 6)

        self.assertAlmostEqual(result.geometry.area.sum(), 400.0)

    def test_snap_uses_largest_accumulation_only_within_one_cell(self):
        directions = np.zeros((12, 12), dtype=np.float32)
        directions[4:6, 7] = 4
        directions[6, 6] = 1
        accumulation = np.ones(directions.shape, dtype=np.float32)
        accumulation[6, 7] = 4
        accumulation[6, 8] = 100  # Two cells away: must not attract snap.
        config = self.write_grid(directions, accumulation)

        result = self.delineate(config, 6, 6)

        self.assertAlmostEqual(result.geometry.area.sum(), 400.0)

    def test_all_eight_esri_directions_connect_to_the_outlet(self):
        directions = np.zeros((12, 12), dtype=np.float32)
        directions[5:8, 5:8] = [[2, 4, 8], [1, 0, 16], [128, 64, 32]]
        accumulation = np.ones(directions.shape, dtype=np.float32)
        accumulation[6, 6] = 9
        config = self.write_grid(directions, accumulation)

        result = self.delineate(config, 6, 6)

        self.assertAlmostEqual(result.geometry.area.sum(), 900.0)

    def test_native_whitebox_d8_encoding(self):
        directions = np.zeros((12, 12), dtype=np.float32)
        directions[5:8, 5:8] = [[4, 8, 16], [2, 0, 32], [1, 128, 64]]
        accumulation = np.ones(directions.shape, dtype=np.float32)
        accumulation[6, 6] = 9
        config = self.write_grid(directions, accumulation)
        config["flow_dir_encoding"] = "whitebox"

        result = self.delineate(config, 6, 6)

        self.assertAlmostEqual(result.geometry.area.sum(), 900.0)

    def test_uses_the_selected_rasters_crs(self):
        self.crs = rasterio.crs.CRS.from_epsg(32637)
        self.transform = from_origin(500000, 5100000, 10, 10)
        directions = np.zeros((12, 12), dtype=np.float32)
        config = self.write_grid(directions)

        result = self.delineate(config, 6, 6)

        self.assertEqual(result.crs, self.crs)
        self.assertAlmostEqual(result.geometry.area.sum(), 100.0)

    def test_expands_window_to_include_upstream_cells(self):
        directions = np.zeros((128, 128), dtype=np.float32)
        directions[41:70, 64] = 4
        accumulation = np.ones(directions.shape, dtype=np.float32)
        accumulation[70, 64] = 30
        config = self.write_grid(directions, accumulation)

        with patch.object(watershed, "INITIAL_WINDOW_CELLS", 8, create=True):
            result = self.delineate(config, 70, 64)

        self.assertAlmostEqual(result.geometry.area.sum(), 3000.0)

    def test_rejects_catchment_reaching_source_edge(self):
        directions = np.zeros((12, 12), dtype=np.float32)
        directions[:6, 6] = 4
        accumulation = np.ones(directions.shape, dtype=np.float32)
        accumulation[6, 6] = 7
        config = self.write_grid(directions, accumulation)

        with self.assertRaisesRegex(ValueError, "покрыти|границ"):
            self.delineate(config, 6, 6)

    def test_rejects_catchment_adjacent_to_nodata(self):
        directions = np.zeros((12, 12), dtype=np.float32)
        directions[4:6, 6] = 4
        directions[3, 6] = -9999
        accumulation = np.ones(directions.shape, dtype=np.float32)
        accumulation[6, 6] = 3
        config = self.write_grid(directions, accumulation)

        with self.assertRaisesRegex(ValueError, "покрыти|NoData"):
            self.delineate(config, 6, 6)

    def test_rejects_nodata_at_selected_point(self):
        directions = np.zeros((12, 12), dtype=np.float32)
        directions[6, 6] = -9999
        config = self.write_grid(directions)

        with self.assertRaisesRegex(ValueError, "NoData|данных"):
            self.delineate(config, 6, 6)

    def test_rejects_point_outside_raster(self):
        config = self.write_grid(np.zeros((12, 12), dtype=np.float32))

        with self.assertRaisesRegex(ValueError, "покрыти|границ"):
            self.delineate(config, 30, 30)

    def test_rejects_invalid_latitude(self):
        with self.assertRaisesRegex(ValueError, "координат|широт"):
            watershed.delineate_watershed(91, 0, "synthetic")

    def test_refuses_to_allocate_beyond_window_limit(self):
        directions = np.zeros((128, 128), dtype=np.float32)
        directions[20:70, 64] = 4
        accumulation = np.ones(directions.shape, dtype=np.float32)
        accumulation[70, 64] = 51
        config = self.write_grid(directions, accumulation)

        with patch.object(watershed, "INITIAL_WINDOW_CELLS", 8, create=True):
            with patch.object(watershed, "MAX_WINDOW_CELLS", 256, create=True):
                with self.assertRaisesRegex(ValueError, "лимит|слишком"):
                    self.delineate(config, 70, 64)

    def test_whitebox_failure_is_reported(self):
        config = self.write_grid(np.zeros((12, 12), dtype=np.float32))
        with patch.object(watershed.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(ValueError, "WhiteboxTools"):
                self.delineate(config, 6, 6)

    def test_whitebox_missing_output_is_reported_even_on_zero_exit(self):
        config = self.write_grid(np.zeros((12, 12), dtype=np.float32))
        with patch.object(watershed.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(ValueError, "WhiteboxTools"):
                self.delineate(config, 6, 6)

    def test_all_source_reads_have_bounded_windows(self):
        config = self.write_grid(np.zeros((128, 128), dtype=np.float32))
        original_open = rasterio.open
        reads = []

        class RecordingDataset:
            def __init__(self, dataset):
                self.dataset = dataset

            def __getattr__(self, name):
                return getattr(self.dataset, name)

            def __enter__(self):
                self.dataset.__enter__()
                return self

            def __exit__(self, *args):
                return self.dataset.__exit__(*args)

            def read(self, *args, **kwargs):
                reads.append(kwargs.get("window"))
                return self.dataset.read(*args, **kwargs)

        def recording_open(path, *args, **kwargs):
            dataset = original_open(path, *args, **kwargs)
            if Path(path) in (config["flow_dir"], config["flow_acc"]) and not args:
                return RecordingDataset(dataset)
            return dataset

        with patch.object(watershed, "INITIAL_WINDOW_CELLS", 8):
            with patch.object(watershed.rasterio, "open", side_effect=recording_open):
                result = self.delineate(config, 64, 64)

        self.assertAlmostEqual(result.geometry.area.sum(), 100.0)
        self.assertTrue(reads)
        self.assertTrue(all(window is not None for window in reads))
        self.assertTrue(all(window.width * window.height <= 64 for window in reads))


if __name__ == "__main__":
    unittest.main()
