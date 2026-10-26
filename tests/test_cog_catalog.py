"""Safety contracts for catalog-wide watershed comparison."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.shutil import copy as copy_raster
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import benchmark_cog_watersheds as regression
import verify_cog_catalog as catalog


class SourceContextTests(unittest.TestCase):
    def test_context_changes_source_and_both_paths_then_restores_on_error(self):
        source_id = "MERIT_3sec"
        original_source = regression.SOURCE
        original_spec = regression.DEM_FILES[source_id]
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            with catalog.selected_source(source_id, {"flow_dir": "a.tif", "flow_acc": "b.tif"}):
                self.assertEqual(regression.SOURCE, source_id)
                self.assertEqual(regression.DEM_FILES[source_id]["flow_dir"], Path("a.tif"))
                self.assertEqual(regression.DEM_FILES[source_id]["flow_acc"], Path("b.tif"))
                raise RuntimeError("simulated")
        self.assertEqual(regression.SOURCE, original_source)
        self.assertIs(regression.DEM_FILES[source_id], original_spec)


class ImmutableReportTests(unittest.TestCase):
    def test_existing_baseline_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            catalog.save_new(path, {"original": 1})
            previous = path.read_bytes()
            with self.assertRaises(FileExistsError):
                catalog.save_new(path, {"replacement": 2})
            self.assertEqual(path.read_bytes(), previous)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.source = self.folder / "original.tif"
        self.output = self.folder / "output.tif"
        values = np.ones((16, 16), dtype="uint8")
        values[0, 0] = 255
        with rasterio.open(self.source, "w", driver="GTiff", width=16, height=16, count=1,
                           dtype="uint8", nodata=255, crs="EPSG:3395",
                           transform=from_origin(0, 160, 10, 10)) as dst:
            dst.write(values, 1)
        with rasterio.Env(GDAL_PAM_ENABLED="NO"):
            copy_raster(self.source, self.output, driver="COG", COMPRESS="ZSTD", BLOCKSIZE=512)
        self.record = catalog.raster_record(self.source)
        self.manifest = {
            "source": str(self.source), "output": str(self.output),
            "source_size_bytes": self.record["size_bytes"],
            "source_mtime_ns": self.record["mtime_ns"],
            "output_size_bytes": self.output.stat().st_size,
            "all_native_pixels_validated": True, "all_native_masks_validated": True,
            "source_native_sha256": "a" * 64, "output_native_sha256": "a" * 64,
            "source_mask_sha256": "b" * 64, "output_mask_sha256": "b" * 64,
            "creation_options": {"COMPRESS": "ZSTD"}, "image_structure": {"LAYOUT": "COG"},
            **{key: self.record[key] for key in ("shape", "dtype", "nodata", "count", "transform", "crs")},
        }
        self.write_manifest()

    def tearDown(self):
        self.temporary.cleanup()

    def write_manifest(self):
        self.output.with_suffix(".tif.cog.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_verified_complete_cog_is_accepted(self):
        result = catalog.verified_manifest(self.record, self.output)
        self.assertEqual(result["source_native_sha256"], "a" * 64)
        self.assertEqual(result["output_signature"], catalog.file_signature(self.output))

    def test_missing_full_pixel_or_mask_validation_is_rejected(self):
        for key in ("all_native_pixels_validated", "all_native_masks_validated"):
            with self.subTest(key=key):
                self.manifest[key] = False
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, "lacks complete native validation"):
                    catalog.verified_manifest(self.record, self.output)
                self.manifest[key] = True

    def test_different_original_or_output_path_is_rejected(self):
        for key in ("source", "output"):
            with self.subTest(key=key):
                previous = self.manifest[key]
                self.manifest[key] = str(self.folder / "different.tif")
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, "path mismatch|reference baseline original"):
                    catalog.verified_manifest(self.record, self.output)
                self.manifest[key] = previous

    def test_different_native_or_mask_hash_is_rejected(self):
        for key in ("output_native_sha256", "output_mask_sha256"):
            with self.subTest(key=key):
                previous = self.manifest[key]
                self.manifest[key] = "c" * 64
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, "unequal or missing verification hashes"):
                    catalog.verified_manifest(self.record, self.output)
                self.manifest[key] = previous

    def test_original_changed_after_baseline_is_rejected(self):
        with self.source.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "Original raster changed"):
            catalog.verified_manifest(self.record, self.output)

    def test_manifest_cannot_claim_different_source_signature(self):
        self.manifest["source_mtime_ns"] += 1
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "source signature mismatch"):
            catalog.verified_manifest(self.record, self.output)

    def test_output_changed_size_after_validation_is_rejected(self):
        self.manifest["output_size_bytes"] += 1
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "output size changed"):
            catalog.verified_manifest(self.record, self.output)

    def test_actual_output_nodata_is_checked_against_baseline(self):
        with patch.object(catalog.rasterio, "open") as opened:
            dataset = opened.return_value.__enter__.return_value
            dataset.shape = tuple(self.record["shape"])
            dataset.dtypes = [self.record["dtype"]]
            dataset.transform = from_origin(0, 160, 10, 10)
            dataset.crs = rasterio.crs.CRS.from_epsg(3395)
            dataset.count = 1
            dataset.nodata = 0
            dataset.tags.return_value = {"LAYOUT": "COG", "COMPRESSION": "ZSTD"}
            with self.assertRaisesRegex(ValueError, "Actual COG metadata"):
                catalog.verified_manifest(self.record, self.output)

    def test_manifest_compression_and_metadata_are_checked(self):
        mutations = [("dtype", "float32"), ("transform", [0] * 9),
                     ("creation_options", {"COMPRESS": "DEFLATE"}),
                     ("image_structure", {"LAYOUT": "GTiff"})]
        for key, value in mutations:
            with self.subTest(key=key):
                previous = deepcopy(self.manifest[key])
                self.manifest[key] = value
                self.write_manifest()
                with self.assertRaises(ValueError):
                    catalog.verified_manifest(self.record, self.output)
                self.manifest[key] = previous


if __name__ == "__main__":
    unittest.main()
