import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config


class ThematicConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def select(self, source):
        resolver = getattr(config, '_preferred_thematic_raster', None)
        self.assertTrue(callable(resolver), 'Thematic COG selection is not implemented')
        return resolver(source)

    def write_pair(self, name='soil.tif'):
        source = self.root / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'original')
        output = source.parent / 'cog' / 'zstd' / source.name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'copy')
        report = {
            'source': str(source.resolve()), 'output': str(output.resolve()),
            'source_size_bytes': source.stat().st_size,
            'source_mtime_ns': source.stat().st_mtime_ns,
            'output_size_bytes': output.stat().st_size,
            'all_native_pixels_validated': True, 'all_native_masks_validated': True,
            'source_native_sha256': 'a' * 64, 'output_native_sha256': 'a' * 64,
            'source_mask_sha256': 'b' * 64, 'output_mask_sha256': 'b' * 64,
            'creation_options': {'COMPRESS': 'ZSTD'},
            'source_files': {str(source.resolve()): {
                'size_bytes': source.stat().st_size, 'mtime_ns': source.stat().st_mtime_ns}},
        }
        manifest = Path(str(output) + '.cog.json')
        manifest.write_text(json.dumps(report), encoding='utf-8')
        return source, output, manifest, report

    def test_missing_copy_uses_original(self):
        source = self.root / 'soil.tif'
        self.assertEqual(self.select(source), source)

    def test_verified_local_copy_is_preferred(self):
        source, output, _, _ = self.write_pair()
        self.assertEqual(self.select(source), output)

    def test_unverified_or_incomplete_copy_is_not_selected(self):
        source, output, manifest, report = self.write_pair()
        manifest.unlink()
        self.assertEqual(self.select(source), source)
        for change in ({'all_native_masks_validated': False},
                       {'output_native_sha256': 'c' * 64},
                       {'output_size_bytes': output.stat().st_size + 1},
                       {'output': str(self.root / 'wrong.tif')}):
            with self.subTest(change=change):
                manifest.write_text(json.dumps({**report, **change}), encoding='utf-8')
                self.assertEqual(self.select(source), source)
        manifest.write_text('{incomplete', encoding='utf-8')
        self.assertEqual(self.select(source), source)

    def test_changed_original_falls_back_to_original(self):
        source, _, _, _ = self.write_pair()
        source.write_bytes(b'updated original')
        self.assertEqual(self.select(source), source)

    def test_cloud_copy_or_manifest_is_not_read(self):
        source, output, manifest, _ = self.write_pair()
        for placeholder in (output, manifest):
            with self.subTest(placeholder=placeholder), \
                    patch.object(config, 'is_cloud_placeholder', side_effect=lambda p: p == placeholder), \
                    patch.object(Path, 'read_text', side_effect=AssertionError('Must not hydrate')):
                self.assertEqual(self.select(source), source)

    def test_added_external_mask_invalidates_copy(self):
        source, _, _, _ = self.write_pair()
        Path(str(source) + '.msk').write_bytes(b'new validity mask')
        self.assertEqual(self.select(source), source)

    def test_removed_external_mask_invalidates_copy(self):
        source, _, manifest, report = self.write_pair()
        mask = Path(str(source) + '.msk')
        mask.write_bytes(b'old validity mask')
        report['source_files'][str(mask)] = {
            'size_bytes': mask.stat().st_size, 'mtime_ns': mask.stat().st_mtime_ns}
        manifest.write_text(json.dumps(report), encoding='utf-8')
        mask.unlink()
        self.assertEqual(self.select(source), source)

    def test_climate_requires_complete_verified_monthly_series(self):
        resolver = getattr(config, '_climate_template', None)
        self.assertTrue(callable(resolver), 'Climate COG selection is not implemented')
        name = 'c1_30s_tavg_{month}_Clip_Project.tif'
        with patch.object(config, 'CLIMATE_DIR', self.root):
            for month in range(1, 12):
                self.write_pair(name.format(month=f'{month:02d}'))
            self.assertEqual(resolver('tavg'), str(self.root / name))
            _, _, manifest, _ = self.write_pair(name.format(month='12'))
            self.assertEqual(resolver('tavg'), str(self.root / 'cog' / 'zstd' / name))
            manifest.unlink()
            self.assertEqual(resolver('tavg'), str(self.root / name))

    def terrain_startup_fixture(self):
        config_path = self.root / 'config.py'
        config_path.write_text(Path(config.__file__).read_text(encoding='utf-8'), encoding='utf-8')
        elevation = self.write_pair('data/heights/GMTED2010_30sec_Clip_Project_Fill.tif')
        slope = self.write_pair('data/slopes/GMTED2010_30sec_Clip_Project_Fill_Slope.tif')
        return config_path, elevation, slope

    def test_startup_selects_verified_elevation_and_slope_cogs(self):
        config_path, elevation, slope = self.terrain_startup_fixture()
        settings = runpy.run_path(str(config_path))
        self.assertEqual(settings['ELEVATION_RASTER'], elevation[1])
        self.assertEqual(settings['SLOPE_RASTER'], slope[1])
        self.assertTrue(elevation[0].is_file())
        self.assertTrue(slope[0].is_file())

    def test_startup_keeps_unverified_terrain_original_without_disabling_other_cog(self):
        config_path, elevation, slope = self.terrain_startup_fixture()
        slope[2].unlink()
        settings = runpy.run_path(str(config_path))
        self.assertEqual(settings['ELEVATION_RASTER'], elevation[1])
        self.assertEqual(settings['SLOPE_RASTER'], slope[0])


if __name__ == '__main__':
    unittest.main()
