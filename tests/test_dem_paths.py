import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config


class DemPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name) / 'local'
        self.external = Path(self.temp.name) / 'external'
        self.local.mkdir()
        self.external.mkdir()
        roots = patch('config.DEM_DATA_ROOTS', (self.local, self.external))
        roots.start()
        self.addCleanup(roots.stop)

    def source(self):
        return config._dem_source('model', 'Test model', '7,5″')

    def touch(self, root, kind):
        path = root / f'model_Flow{kind}.tif'
        path.touch()
        return path

    def test_downloaded_accumulation_pairs_with_external_direction(self):
        local_acc = self.touch(self.local, 'Acc')
        external_dir = self.touch(self.external, 'Dir')
        cloud_acc = self.touch(self.external, 'Acc')
        with patch('config.is_cloud_placeholder', side_effect=lambda path: path == cloud_acc, create=True):
            source = self.source()
        self.assertEqual(source['flow_acc'], local_acc)
        self.assertEqual(source['flow_dir'], external_dir)

    def test_complete_local_pair_takes_precedence(self):
        for root in (self.local, self.external):
            self.touch(root, 'Dir')
            self.touch(root, 'Acc')
        source = self.source()
        self.assertEqual(source['flow_dir'].parent, self.local)
        self.assertEqual(source['flow_acc'].parent, self.local)

    def test_complete_available_pair_preferred_over_mixing_roots(self):
        self.touch(self.local, 'Acc')
        self.touch(self.external, 'Dir')
        self.touch(self.external, 'Acc')
        source = self.source()
        self.assertEqual(source['flow_dir'].parent, self.external)
        self.assertEqual(source['flow_acc'].parent, self.external)

    def test_split_pair_without_cloud_duplicate(self):
        self.touch(self.local, 'Acc')
        self.touch(self.external, 'Dir')
        source = self.source()
        self.assertEqual(source['flow_dir'].parent, self.external)
        self.assertEqual(source['flow_acc'].parent, self.local)

    def preferred_source(self, root):
        return config._dem_source('model', 'Test model', '7,5″', preferred_root=root)

    def test_complete_preferred_pair_is_used_without_changing_original_paths(self):
        preferred = Path(self.temp.name) / 'cog'
        preferred.mkdir()
        for root in (self.local, preferred):
            self.touch(root, 'Dir')
            self.touch(root, 'Acc')
        source = self.preferred_source(preferred)
        self.assertEqual(source['flow_dir'].parent, preferred)
        self.assertEqual(source['flow_acc'].parent, preferred)
        self.assertTrue((self.local / 'model_FlowDir.tif').exists())
        self.assertTrue((self.local / 'model_FlowAcc.tif').exists())

    def test_incomplete_preferred_pair_falls_back_without_mixing_copies(self):
        preferred = Path(self.temp.name) / 'cog'
        preferred.mkdir()
        self.touch(preferred, 'Dir')
        self.touch(self.external, 'Dir')
        self.touch(self.local, 'Acc')
        source = self.preferred_source(preferred)
        self.assertEqual(source['flow_dir'].parent, self.external)
        self.assertEqual(source['flow_acc'].parent, self.local)

    def test_cloud_preferred_pair_falls_back_to_downloaded_originals(self):
        preferred = Path(self.temp.name) / 'cog'
        preferred.mkdir()
        for root in (self.local, preferred):
            self.touch(root, 'Dir')
            self.touch(root, 'Acc')
        with patch('config.is_cloud_placeholder', side_effect=lambda path: path.parent == preferred):
            source = self.preferred_source(preferred)
        self.assertEqual(source['flow_dir'].parent, self.local)
        self.assertEqual(source['flow_acc'].parent, self.local)


if __name__ == '__main__':
    unittest.main()
