"""Validate the I/O benchmark's equality checks and timing aggregation."""

from collections import Counter
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_cog_io import (
    array_fingerprint, check_pairs, make_cases, summarize, timed_read,
    trial_schedule, validate_windows,
)


class ArrayFingerprintTests(unittest.TestCase):
    def test_native_bytes_detect_masked_pixel_changes(self):
        expected = np.ma.array(np.array([[1, 255]], dtype="uint8"), mask=[[False, True]])
        changed = np.ma.array(np.array([[1, 254]], dtype="uint8"), mask=[[False, True]])
        first, second = array_fingerprint(expected), array_fingerprint(changed)
        self.assertNotEqual(first["raw_sha256"], second["raw_sha256"])
        self.assertEqual(first["mask_sha256"], second["mask_sha256"])

    def test_mask_changes_are_detected_when_pixels_match(self):
        expected = np.ma.array(np.array([[1, 2]], dtype="uint8"), mask=[[False, True]])
        changed = np.ma.array(np.array([[1, 2]], dtype="uint8"), mask=[[False, False]])
        first, second = array_fingerprint(expected), array_fingerprint(changed)
        self.assertEqual(first["raw_sha256"], second["raw_sha256"])
        self.assertNotEqual(first["mask_sha256"], second["mask_sha256"])

    def test_dtype_participates_in_fingerprint(self):
        first = array_fingerprint(np.ma.array([[1]], dtype="uint8"))
        second = array_fingerprint(np.ma.array([[1]], dtype="int8"))
        self.assertEqual(first["raw_sha256"], second["raw_sha256"])
        self.assertNotEqual(first["dtype"], second["dtype"])


class ScheduleTests(unittest.TestCase):
    def test_cases_have_native_windows_clamped_inside_grid(self):
        points = [{"name": "edge", "snapped_row": 0, "snapped_col": 0}]
        cases = make_cases(points, 3000, 4000)
        self.assertEqual([case["window"] for case in cases],
                         [[0, 0, 256, 256], [0, 0, 1024, 1024], [0, 0, 2048, 2048]])

    def test_seeded_schedule_covers_every_case_variant_repeat(self):
        cases = [{"id": str(i)} for i in range(18)]
        names = ["ORIGINAL", "DEFLATE", "ZSTD"]
        first = list(trial_schedule(cases, names, 5, 42))
        self.assertEqual(first, list(trial_schedule(cases, names, 5, 42)))
        self.assertNotEqual(first, list(trial_schedule(cases, names, 5, 43)))
        counts = Counter((trial["repeat"], trial["variant"], trial["case"]["id"]) for trial in first)
        self.assertEqual(len(counts), 18 * 3 * 5)
        self.assertEqual(set(counts.values()), {1})
        for repeat in range(1, 6):
            first_variants = Counter(trial["variant"] for trial in first
                                     if trial["repeat"] == repeat and trial["variant_order"] == 1)
            self.assertEqual(first_variants, Counter({name: 6 for name in names}))


class SummaryTests(unittest.TestCase):
    def test_whole_workload_aggregates_cases_before_median(self):
        runs = []
        for repeat, readings in [(1, [1, 3]), (2, [5, 7])]:
            for size, reading in zip([256, 1024], readings):
                runs.append({"variant": "A", "repeat": repeat, "window_size": size,
                             "read_seconds": reading, "open_seconds": 0.1,
                             "open_plus_read_seconds": reading + 0.1,
                             "same_handle_reread_seconds": []})
        summary = summarize(runs, [{"name": "A"}], 2)["A"]
        self.assertEqual(summary["whole_workload"]["read_seconds"]["median_seconds"], 8)
        self.assertEqual(summary["pooled_individual_read"]["median_seconds"], 4)
        self.assertEqual(summary["by_window_size"]["256"]["read_seconds"]["median_seconds"], 3)
        self.assertAlmostEqual(summary["by_window_size"]["256"]["read_seconds"]["p90_seconds"], 4.6)


class ActualRasterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.variants = []
        for name, compression in [("ORIGINAL", None), ("DEFLATE", "DEFLATE")]:
            variant = {"name": name}
            for role, dtype, nodata in [("flow_dir", "uint8", 255), ("flow_acc", "float32", -9999)]:
                path = self.folder / f"{name}_{role}.tif"
                values = np.arange(64 * 64, dtype="float64").reshape(64, 64) % 128
                values = values.astype(dtype)
                values[0, 0] = nodata
                with rasterio.open(path, "w", driver="GTiff", width=64, height=64, count=1,
                                   dtype=dtype, nodata=nodata, crs="EPSG:3395",
                                   transform=from_origin(0, 640, 10, 10), tiled=True,
                                   blockxsize=16, blockysize=16, compress=compression) as dst:
                    dst.write(values, 1)
                variant[role] = str(path)
            self.variants.append(variant)
        self.cases = [{"id": "tiny", "window": [0, 0, 64, 64]}]

    def tearDown(self):
        self.temporary.cleanup()

    def test_compressed_native_pixels_and_masks_match_and_can_be_timed(self):
        grid = check_pairs(self.variants)
        self.assertEqual(grid["width"], 64)
        validated = validate_windows(self.cases, self.variants)
        self.assertEqual(len(validated), 2)
        self.assertEqual(validated[0]["flow_dir"]["masked_pixels"], 1)
        result = timed_read(self.variants[1], rasterio.windows.Window(0, 0, 64, 64), 3)
        self.assertGreater(result["open_seconds"], 0)
        self.assertGreater(result["read_seconds"], 0)
        self.assertEqual(len(result["same_handle_reread_seconds"]), 3)
        self.assertAlmostEqual(result["open_plus_read_seconds"],
                               result["open_seconds"] + result["read_seconds"])

    def test_changed_native_pixel_is_rejected_before_timing(self):
        with rasterio.open(self.variants[1]["flow_acc"], "r+") as dst:
            dst.write(np.array([[99]], dtype="float32"), 1,
                      window=rasterio.windows.Window(1, 1, 1, 1))
        with self.assertRaisesRegex(ValueError, "Native pixels or masks differ"):
            validate_windows(self.cases, self.variants)

    def test_changed_transform_is_rejected(self):
        with rasterio.open(self.variants[1]["flow_dir"], "r+") as dst:
            dst.transform = from_origin(10, 640, 10, 10)
        with self.assertRaisesRegex(ValueError, "metadata differs at transform"):
            check_pairs(self.variants)


if __name__ == "__main__":
    unittest.main()
