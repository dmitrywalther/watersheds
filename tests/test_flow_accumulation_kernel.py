"""Exercise the actual compiled, memory-mapped accumulation executable."""
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "FlowAccumulation.cs"
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
OFFSETS = {1: (0, 1), 2: (1, 1), 4: (1, 0), 8: (1, -1),
           16: (0, -1), 32: (-1, -1), 64: (-1, 0), 128: (-1, 1)}


@unittest.skipUnless(os.name == "nt" and CSC.exists(), "Requires the Windows .NET C# compiler")
class FlowAccumulationKernelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.exe = cls.directory / "FlowAccumulation.exe"
        cls.compiled = False

    def kernel(self):
        self.assertTrue(SOURCE.exists(), "The accumulation kernel has not been implemented")
        if not type(self).compiled:
            result = subprocess.run(
                [str(CSC), "/nologo", "/unsafe", "/platform:x64", "/optimize+",
                 "/out:" + str(self.exe), str(SOURCE)],
                text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            type(self).compiled = True
        return self.exe

    def run_grid(self, values, rows, cols, *, expected_code=0):
        case = Path(tempfile.mkdtemp(dir=self.directory))
        direction, counts, state = [case / name for name in ("directions.raw", "counts.raw", "state.raw")]
        direction.write_bytes(bytes(values))
        result = subprocess.run(
            [str(self.kernel()), str(direction), str(counts), str(state), str(rows), str(cols)],
            capture_output=True, text=True, timeout=30)
        if expected_code == 0:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0)
        records = [json.loads(line) for line in result.stdout.splitlines() if line]
        return result, records, counts, state

    def assert_grid(self, values, rows, cols, expected):
        result, records, counts, state = self.run_grid(values, rows, cols)
        self.assertEqual(list(struct.unpack("<" + "I" * len(expected), counts.read_bytes())), expected)
        self.assertEqual(list(state.read_bytes()), [254 if v == 255 else 255 for v in values])
        self.assertEqual(records[-1]["phase"], "complete")
        self.assertEqual(records[-1]["valid"], sum(v != 255 for v in values))
        self.assertEqual(records[-1]["processed"], records[-1]["valid"])
        self.assertEqual(records[-1]["rows"], rows)
        self.assertEqual(records[-1]["cols"], cols)
        self.assertEqual(records[-1]["cells"], rows * cols)

    def test_eight_esri_directions_converge_on_terminal_cell(self):
        self.assert_grid([2, 4, 8, 1, 0, 16, 128, 64, 32], 3, 3,
                         [1, 1, 1, 1, 9, 1, 1, 1, 1])

    def test_confluence_and_downstream_chain(self):
        self.assert_grid([2, 4, 8, 1, 4, 16, 1, 1, 0], 3, 3,
                         [1, 1, 1, 1, 6, 1, 1, 8, 9])

    def test_walk_revisits_a_downstream_cell_already_passed_by_scan(self):
        self.assert_grid([0, 16, 16, 16], 1, 4, [4, 3, 2, 1])

    def test_nodata_has_zero_count_and_receives_no_flow(self):
        self.assert_grid([1, 255, 16, 0, 64, 0], 2, 3, [1, 0, 1, 1, 1, 1])

    def test_all_nodata(self):
        self.assert_grid([255] * 4, 2, 2, [0] * 4)

    def test_outward_directions_do_not_wrap_rows_or_columns(self):
        self.assert_grid([32, 64, 128, 16, 0, 1, 8, 4, 2], 3, 3, [1] * 9)
        self.assert_grid([0, 1, 16, 0], 2, 2, [1] * 4)

    def test_cycle_is_rejected_even_with_tributaries(self):
        result, records, _, _ = self.run_grid([1, 1, 16], 1, 3, expected_code=1)
        self.assertIn("cycle", result.stderr.lower())
        self.assertFalse(any(record["phase"] == "complete" for record in records))

    def test_invalid_direction_is_rejected(self):
        for value in (3, 127, 129, 254):
            with self.subTest(value=value):
                result, records, _, _ = self.run_grid([0, value], 1, 2, expected_code=1)
                self.assertIn("direction", result.stderr.lower())
                self.assertFalse(any(record["phase"] == "complete" for record in records))

    def test_bad_input_lengths_and_dimensions_are_rejected(self):
        for values, rows, cols in (([0], 1, 2), ([0, 0], 1, 1), ([0], 0, 1),
                                   ([0], -1, 1), ([0], 65536, 65536)):
            with self.subTest(rows=rows, cols=cols, length=len(values)):
                self.run_grid(values, rows, cols, expected_code=1)

    def test_input_path_cannot_be_used_for_output(self):
        direction = self.directory / "aliased.raw"
        direction.write_bytes(b"\x00")
        result = subprocess.run([str(self.kernel()), str(direction), str(direction),
                                 str(self.directory / "alias-state.raw"), "1", "1"],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(direction.read_bytes(), b"\x00")

    def test_existing_output_is_preserved(self):
        case = Path(tempfile.mkdtemp(dir=self.directory))
        direction, counts, state = [case / name for name in ("dir", "counts", "state")]
        direction.write_bytes(b"\x00")
        counts.write_bytes(b"preserve")
        result = subprocess.run([str(self.kernel()), str(direction), str(counts), str(state), "1", "1"],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(counts.read_bytes(), b"preserve")

    def test_random_acyclic_grid_matches_independent_particle_tracing(self):
        rng = random.Random(20261024)
        rows, cols = 31, 37
        heights = list(range(rows * cols))
        rng.shuffle(heights)
        values = []
        for i, height in enumerate(heights):
            row, col = divmod(i, cols)
            options = [0]
            for code, (dr, dc) in OFFSETS.items():
                rr, cc = row + dr, col + dc
                if 0 <= rr < rows and 0 <= cc < cols and heights[rr * cols + cc] < height:
                    options.append(code)
            values.append(255 if rng.random() < 0.08 else rng.choice(options))
        expected = [0] * len(values)
        for source, value in enumerate(values):
            if value == 255:
                continue
            current = source
            while True:
                expected[current] += 1
                if values[current] == 0:
                    break
                row, col = divmod(current, cols)
                dr, dc = OFFSETS[values[current]]
                rr, cc = row + dr, col + dc
                if not (0 <= rr < rows and 0 <= cc < cols):
                    break
                current = rr * cols + cc
                if values[current] == 255:
                    break
        self.assert_grid(values, rows, cols, expected)


if __name__ == "__main__":
    unittest.main()
