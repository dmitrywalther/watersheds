import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

from services import flow_preparation
from services.flow_preparation import prepare_flow_accumulation


class FlowPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'directions.tif'
        self.output = self.folder / 'accumulation.tif'
        self.transform = from_origin(10000, 20000, 100, 100)

    def write_directions(self, values):
        with rasterio.open(self.source, 'w', driver='GTiff', width=values.shape[1],
                           height=values.shape[0], count=1, dtype='uint8', nodata=255,
                           crs='EPSG:3395', transform=self.transform) as dst:
            dst.write(values, 1)

    def test_prepares_exact_counts_and_preserves_grid_and_nodata(self):
        directions = np.full((8, 8), 255, dtype='uint8')
        directions[2, 3] = 4
        directions[3, 2] = 1
        directions[3, 3] = 4
        directions[4, 3] = 0
        self.write_directions(directions)
        report = prepare_flow_accumulation(self.source, self.output)
        with rasterio.open(self.output) as result:
            self.assertEqual(result.transform, self.transform)
            self.assertEqual(result.crs.to_epsg(), 3395)
            self.assertEqual(result.dtypes, ('uint32',))
            values = result.read(1, masked=True)
        self.assertTrue(np.array_equal(np.ma.getmaskarray(values), directions == 255))
        self.assertEqual([values[2, 3], values[3, 2], values[3, 3], values[4, 3]], [0, 0, 2, 3])
        self.assertEqual(report['valid_cells'], 4)
        self.assertEqual(report['processed_cells'], 4)
        self.assertFalse(report['includes_self'])
        saved = json.loads(self.output.with_suffix('.tif.preparation.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['direction_sha256'], report['direction_sha256'])

    def test_existing_output_is_never_overwritten(self):
        self.output.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            prepare_flow_accumulation(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b'original')

    def test_cycle_does_not_publish_partial_output(self):
        directions = np.full((8, 8), 255, dtype='uint8')
        directions[3, 3:5] = [1, 16]
        self.write_directions(directions)
        with self.assertRaisesRegex(ValueError, 'cycl|цикл'):
            prepare_flow_accumulation(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_invalid_pointer_fails_before_output_publication(self):
        self.write_directions(np.array([[1, 3, 255]], dtype='uint8'))
        with self.assertRaisesRegex(ValueError, 'D8|direction'):
            prepare_flow_accumulation(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_provenance_refers_to_compiled_source_even_if_original_changes(self):
        self.write_directions(np.array([[1, 0]], dtype='uint8'))
        original = flow_preparation.KERNEL_SOURCE.read_bytes()
        kernel_copy = self.folder / 'source.cs'
        kernel_copy.write_bytes(original)
        def changed_after_computation(progress):
            if progress['phase'] == 'complete':
                kernel_copy.write_text('// changed after compilation', encoding='utf-8')
        with patch.object(flow_preparation, 'KERNEL_SOURCE', kernel_copy):
            report = prepare_flow_accumulation(self.source, self.output, on_progress=changed_after_computation)
        self.assertEqual(report['kernel_sha256'], hashlib.sha256(original).hexdigest())

    def test_failed_publication_does_not_leave_partial_result(self):
        self.write_directions(np.array([[1, 0]], dtype='uint8'))
        original_link = flow_preparation.os.link
        calls = 0
        def failing_link(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('Simulated publication failure')
            original_link(src, dst)
        with patch('services.flow_preparation.os.link', side_effect=failing_link):
            with self.assertRaisesRegex(OSError, 'publication'):
                prepare_flow_accumulation(self.source, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix('.tif.preparation.json').exists())


if __name__ == '__main__':
    unittest.main()
