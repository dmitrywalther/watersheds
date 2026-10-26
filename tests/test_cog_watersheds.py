"""Regression comparison must detect changed basins, not harmless WKB ordering."""

from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_cog_watersheds import compare_result


def result(geometry):
    return {
        "snapped_row": 4, "snapped_col": 5, "native_accumulation": 3,
        "cell_count": 4, "area_m2": geometry.area, "crs": "EPSG:3395",
        "geometry_wkb_hex": geometry.wkb.hex(),
        "geometry_sha256": hashlib.sha256(geometry.wkb).hexdigest(),
        "analysis": {"area_km2": geometry.area / 1e6, "climate": {"temperature": [1, 2, 3]}},
    }


class WatershedComparisonTests(unittest.TestCase):
    def setUp(self):
        self.reference = result(Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]))

    def test_logical_geometry_can_match_with_different_vertex_order(self):
        candidate = result(Polygon([(2, 2), (0, 2), (0, 0), (2, 0)]))
        self.assertNotEqual(candidate["geometry_sha256"], self.reference["geometry_sha256"])
        self.assertEqual(compare_result(self.reference, candidate), [])

    def test_equal_area_different_basin_is_rejected(self):
        candidate = result(Polygon([(1, 0), (3, 0), (3, 2), (1, 2)]))
        self.assertEqual(compare_result(self.reference, candidate), ["logical_geometry"])

    def test_changed_analysis_is_rejected(self):
        candidate = deepcopy(self.reference)
        candidate["analysis"]["climate"]["temperature"][0] = 9
        self.assertEqual(compare_result(self.reference, candidate), ["analysis"])

    def test_changed_grid_outlet_and_accumulation_are_rejected(self):
        candidate = deepcopy(self.reference)
        candidate.update(snapped_row=5, native_accumulation=4, crs="EPSG:3857")
        self.assertEqual(compare_result(self.reference, candidate),
                         ["snapped_row", "native_accumulation", "crs"])

    def test_missing_requested_analysis_is_rejected(self):
        candidate = deepcopy(self.reference)
        del candidate["analysis"]
        self.assertEqual(compare_result(self.reference, candidate), ["analysis"])


if __name__ == "__main__":
    unittest.main()
