import json
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin

from services import cog_preparation


class CogPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'source.tif'
        self.output = self.folder / 'cog.tif'
        self.transform = from_origin(34567.1234, 7654321.9876, 231.38321, 231.38321)

    def write_source(self, values, nodata=None, mask=None, overviews=False):
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
            with rasterio.open(self.source, 'w', driver='GTiff', width=values.shape[1],
                               height=values.shape[0], count=1, dtype=values.dtype,
                               nodata=nodata, crs='EPSG:3395', transform=self.transform) as dst:
                dst.write(values, 1)
                if mask is not None:
                    dst.write_mask(mask)
                if overviews:
                    dst.build_overviews([2], Resampling.average)

    def assert_no_partial_output(self):
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix('.tif.cog.json').exists())
        self.assertFalse(list(self.folder.glob('.cog-preparation-*')))

    def test_uint8_preserves_values_mask_grid_and_rebuilds_nearest_overviews(self):
        values = np.arange(1024 * 1024, dtype='uint32').reshape(1024, 1024).astype('uint8')
        mask = np.full(values.shape, 255, dtype='uint8')
        mask[7:16, 21:99] = 0
        self.write_source(values, mask=mask, overviews=True)
        original = self.source.read_bytes()
        report = cog_preparation.prepare_cog(self.source, self.output, compression='DEFLATE')
        with rasterio.open(self.output) as dst:
            self.assertEqual(dst.transform, self.transform)
            self.assertEqual(dst.crs.to_epsg(), 3395)
            self.assertEqual(dst.dtypes, ('uint8',))
            self.assertEqual(dst.shape, values.shape)
            np.testing.assert_array_equal(dst.read(1), values)
            np.testing.assert_array_equal(dst.read_masks(1), mask)
            np.testing.assert_array_equal(dst.read(1, out_shape=(512, 512)), values[::2, ::2])
            self.assertEqual(dst.tags(ns='IMAGE_STRUCTURE').get('LAYOUT'), 'COG')
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(report['source_native_sha256'], report['output_native_sha256'])
        self.assertTrue(report['all_native_pixels_validated'])
        self.assertEqual(report['creation_options']['OVERVIEWS'], 'IGNORE_EXISTING')
        saved = json.loads(self.output.with_suffix('.tif.cog.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['creation_options']['COMPRESS'], 'DEFLATE')

    def test_zstd_float_is_bit_exact_including_nan_payload_signed_zero_and_nodata(self):
        bits = np.array([0, 0x80000000, 0x7fc01234, 0x7f800000, 0xff800000,
                         0x3f800001, 0x4b800001, 0xc61c3c00], dtype='uint32')
        values = np.tile(bits.view('float32'), (17, 1))
        self.write_source(values, nodata=-9999)
        report = cog_preparation.prepare_cog(self.source, self.output, compression='ZSTD')
        with rasterio.open(self.output) as dst:
            np.testing.assert_array_equal(dst.read(1).view('uint32'), values.view('uint32'))
            self.assertEqual(dst.nodata, -9999)
        self.assertEqual(report['creation_options']['PREDICTOR'], 'FLOATING_POINT')
        self.assertEqual(report['source_mask_sha256'], report['output_mask_sha256'])

    def test_existing_output_is_never_overwritten(self):
        self.output.write_bytes(b'keep existing')
        with self.assertRaises(FileExistsError):
            cog_preparation.prepare_cog(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b'keep existing')

    def test_same_input_and_output_is_rejected(self):
        self.write_source(np.array([[1, 2]], dtype='uint8'))
        original = self.source.read_bytes()
        with self.assertRaises(FileExistsError):
            cog_preparation.prepare_cog(self.source, self.source)
        self.assertEqual(self.source.read_bytes(), original)

    def test_conversion_failure_cleans_scratch_and_does_not_publish(self):
        self.write_source(np.array([[1, 2]], dtype='uint8'))
        with patch.object(cog_preparation, '_copy_cog', side_effect=RuntimeError('copy failed')):
            with self.assertRaisesRegex(RuntimeError, 'copy failed'):
                cog_preparation.prepare_cog(self.source, self.output)
        self.assert_no_partial_output()

    def test_changed_source_is_rejected_before_publication(self):
        self.write_source(np.array([[1, 2]], dtype='uint8'))
        def progress(item):
            if item['phase'] == 'conversion_complete':
                with self.source.open('ab') as handle:
                    handle.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed|измен'):
            cog_preparation.prepare_cog(self.source, self.output, on_progress=progress)
        self.assert_no_partial_output()

    def test_a_valid_cog_with_changed_native_values_is_not_published(self):
        self.write_source(np.array([[1, 2]], dtype='uint8'))
        wrong_source = self.folder / 'wrong.tif'
        with rasterio.open(self.source) as src:
            profile = src.profile
        with rasterio.open(wrong_source, 'w', **profile) as dst:
            dst.write(np.array([[1, 3]], dtype='uint8'), 1)
        original_copy = cog_preparation._copy_cog
        def wrong_copy(source, staging, options):
            return original_copy(wrong_source, staging, options)
        with patch.object(cog_preparation, '_copy_cog', side_effect=wrong_copy):
            with self.assertRaisesRegex(ValueError, 'Значения COG отличаются'):
                cog_preparation.prepare_cog(self.source, self.output)
        self.assert_no_partial_output()

    def test_failed_publication_rolls_back_manifest_and_scratch(self):
        self.write_source(np.array([[1, 2]], dtype='uint8'))
        original_link = cog_preparation.os.link
        def link(src, dst):
            if Path(dst) == self.output:
                raise OSError('publication failed')
            return original_link(src, dst)
        with patch.object(cog_preparation.os, 'link', side_effect=link):
            with self.assertRaisesRegex(OSError, 'publication failed'):
                cog_preparation.prepare_cog(self.source, self.output)
        self.assert_no_partial_output()

    def test_external_overviews_are_not_visible_during_source_copy(self):
        values = np.arange(1024 * 1024, dtype='uint32').reshape(1024, 1024).astype('uint8')
        self.write_source(values)
        overview = Path(str(self.source) + '.ovr')
        with rasterio.open(overview, 'w', driver='GTiff', width=512, height=512,
                           count=1, dtype='uint8', crs='EPSG:3395',
                           transform=self.transform * self.transform.scale(2, 2)) as dst:
            dst.write(np.zeros((512, 512), dtype='uint8'), 1)
        original_copy = cog_preparation._copy_cog
        observed = []
        def checked_copy(source, staging, options):
            with rasterio.open(source) as src:
                observed.append(src.overviews(1))
            return original_copy(source, staging, options)
        with patch.object(cog_preparation, '_copy_cog', side_effect=checked_copy):
            report = cog_preparation.prepare_cog(self.source, self.output)
        self.assertEqual(observed, [[]])
        self.assertIn(str(overview.resolve()), report['source_files'])

    def test_external_mask_is_preserved_while_external_overviews_are_hidden(self):
        values = np.arange(64 * 64, dtype='uint16').reshape(64, 64).astype('uint8')
        mask = np.full(values.shape, 255, dtype='uint8')
        mask[3:27, 9:39] = 0
        self.write_source(values, nodata=255)
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=False), rasterio.open(self.source, 'r+') as dst:
            dst.write_mask(mask)
        external_mask = Path(str(self.source) + '.msk')
        self.assertTrue(external_mask.is_file())
        overview = Path(str(self.source) + '.ovr')
        with rasterio.open(overview, 'w', driver='GTiff', width=32, height=32,
                           count=1, dtype='uint8', crs='EPSG:3395',
                           transform=self.transform * self.transform.scale(2, 2)) as dst:
            dst.write(np.zeros((32, 32), dtype='uint8'), 1)
        original_copy = cog_preparation._copy_cog
        observed = []
        def checked_copy(source, staging, options):
            with rasterio.open(source) as src:
                observed.append(src.overviews(1))
            return original_copy(source, staging, options)
        with patch.object(cog_preparation, '_copy_cog', side_effect=checked_copy):
            report = cog_preparation.prepare_cog(self.source, self.output, compression='ZSTD')
        self.assertEqual(observed, [[]])
        with rasterio.open(self.output) as dst:
            np.testing.assert_array_equal(dst.read(1), values)
            np.testing.assert_array_equal(dst.read_masks(1), mask)
        self.assertIn(str(external_mask.resolve()), report['source_files'])
        self.assertEqual(report['valid_cells'], np.count_nonzero(mask))

    def test_cloud_external_mask_is_rejected_instead_of_silently_lost(self):
        self.write_source(np.array([[1, 2]], dtype='uint8'))
        external_mask = Path(str(self.source) + '.msk')
        external_mask.write_bytes(b'cloud placeholder: do not open')
        with patch.object(cog_preparation, 'is_cloud_placeholder',
                          side_effect=lambda path: Path(path) == external_mask):
            with self.assertRaisesRegex(ValueError, 'маск|mask'):
                cog_preparation.prepare_cog(self.source, self.output)
        self.assert_no_partial_output()

    def test_unrecognized_external_mask_is_rejected_before_publication(self):
        self.write_source(np.array([[1, 2]], dtype='uint8'))
        external_mask = Path(str(self.source) + '.msk')
        with rasterio.open(external_mask, 'w', driver='GTiff', width=2, height=1,
                           count=1, dtype='uint8', transform=self.transform) as dst:
            dst.write(np.array([[255, 0]], dtype='uint8'), 1)
        with self.assertRaisesRegex(ValueError, 'маск|mask'):
            cog_preparation.prepare_cog(self.source, self.output)
        self.assert_no_partial_output()

    def test_new_external_mask_during_conversion_is_rejected(self):
        self.write_source(np.array([[1, 2]], dtype='uint8'))
        def progress(item):
            if item['phase'] == 'conversion_complete':
                Path(str(self.source) + '.msk').write_bytes(b'mask appeared during conversion')
        with self.assertRaisesRegex(ValueError, 'changed|измен'):
            cog_preparation.prepare_cog(self.source, self.output, on_progress=progress)
        self.assert_no_partial_output()

    def test_internal_and_external_masks_are_rejected_as_ambiguous(self):
        values = np.arange(64, dtype='uint8').reshape(8, 8)
        internal_mask = np.full(values.shape, 255, dtype='uint8')
        internal_mask[2:5, 3:6] = 0
        self.write_source(values, mask=internal_mask)
        with rasterio.open(Path(str(self.source) + '.msk'), 'w', driver='GTiff',
                           width=8, height=8, count=1, dtype='uint8',
                           transform=self.transform) as dst:
            dst.write(255 - internal_mask, 1)
            dst.update_tags(INTERNAL_MASK_FLAGS_1='2')
        with self.assertRaisesRegex(ValueError, 'маск|mask'):
            cog_preparation.prepare_cog(self.source, self.output)
        self.assert_no_partial_output()

    def test_staged_copy_preserves_uint32_external_mask_and_band_metadata(self):
        values = np.arange(769 * 1025, dtype='uint32').reshape(769, 1025)
        values[4:13, 7:21] = 65536
        self.write_source(values, nodata=65536, overviews=True)
        mask = np.full(values.shape, 255, dtype='uint8')
        mask[18:32, 37:81] = 0
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=False), rasterio.open(self.source, 'r+') as dst:
            dst.write_mask(mask)
            dst.scales = (0.25,)
            dst.offsets = (-8.0,)
            dst.set_band_unit(1, 'class')
            dst.set_band_description(1, 'soil codes')
            dst.update_tags(1, RepresentationType='THEMATIC')
        original = self.source.read_bytes()
        original_copy = cog_preparation._copy_cog
        observed = []
        def observe_copy(source, staging, options):
            original_copy(source, staging, options)
            with rasterio.open(staging) as dst:
                observed.append((options['OVERVIEWS'], dst.overviews(1)))
        with patch.object(cog_preparation, '_copy_cog', side_effect=observe_copy):
            report = cog_preparation.prepare_cog(self.source, self.output, compression='ZSTD', staged=True)
        self.assertEqual(observed[0], ('NONE', []))
        self.assertEqual(observed[1][0], 'IGNORE_EXISTING')
        self.assertTrue(observed[1][1])
        with rasterio.open(self.output) as dst:
            np.testing.assert_array_equal(dst.read(1), values)
            np.testing.assert_array_equal(dst.read_masks(1), mask)
            self.assertEqual(dst.scales, (0.25,))
            self.assertEqual(dst.offsets, (-8.0,))
            self.assertEqual(dst.units, ('class',))
            self.assertEqual(dst.descriptions, ('soil codes',))
            self.assertEqual(dst.tags(1)['RepresentationType'], 'THEMATIC')
        self.assertEqual(report['preparation_mode'], 'compressed_native_staging')
        self.assertGreater(report['native_stage_size_bytes'], 0)
        self.assertGreaterEqual(report['native_stage_seconds'], 0)
        self.assertEqual(len(report['disk_budget_checks']), 2)
        self.assertEqual(report['source_native_sha256'], report['output_native_sha256'])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse(list(self.folder.glob('.cog-preparation-*')))

    def test_staged_copy_checks_space_before_writing_first_stage(self):
        self.write_source(np.array([[1, 2]], dtype='uint8'))
        with patch.object(cog_preparation.shutil, 'disk_usage', return_value=SimpleNamespace(free=1)), \
                patch.object(cog_preparation, '_copy_cog') as copy:
            with self.assertRaisesRegex(ValueError, 'Недостаточно места'):
                cog_preparation.prepare_cog(self.source, self.output, staged=True)
        copy.assert_not_called()
        self.assert_no_partial_output()

    def test_staged_copy_rechecks_space_after_measuring_compressed_stage(self):
        self.write_source(np.arange(1024 * 1024, dtype='uint32').reshape(1024, 1024))
        original_copy = cog_preparation._copy_cog
        with patch.object(cog_preparation.shutil, 'disk_usage',
                          side_effect=[SimpleNamespace(free=10**12), SimpleNamespace(free=1)]), \
                patch.object(cog_preparation, '_copy_cog', wraps=original_copy) as copy:
            with self.assertRaisesRegex(ValueError, 'Недостаточно места'):
                cog_preparation.prepare_cog(self.source, self.output, staged=True)
        self.assertEqual(copy.call_count, 1)
        self.assertEqual(copy.call_args.args[2]['OVERVIEWS'], 'NONE')
        self.assert_no_partial_output()

    def test_staged_result_is_verified_against_original_not_compressed_stage(self):
        self.write_source(np.array([[1, 2]], dtype='uint8'))
        wrong_source = self.folder / 'wrong.tif'
        with rasterio.open(self.source) as src:
            profile = src.profile
        with rasterio.open(wrong_source, 'w', **profile) as dst:
            dst.write(np.array([[1, 3]], dtype='uint8'), 1)
        original_copy = cog_preparation._copy_cog
        def corrupt_first_copy(source, staging, options):
            return original_copy(wrong_source if options['OVERVIEWS'] == 'NONE' else source,
                                 staging, options)
        with patch.object(cog_preparation, '_copy_cog', side_effect=corrupt_first_copy):
            with self.assertRaisesRegex(ValueError, 'Значения COG отличаются'):
                cog_preparation.prepare_cog(self.source, self.output, staged=True)
        self.assert_no_partial_output()

    def test_staged_final_conversion_failure_cleans_all_scratch(self):
        self.write_source(np.array([[1, 2]], dtype='uint8'))
        original_copy = cog_preparation._copy_cog
        def fail_final_copy(source, staging, options):
            if options['OVERVIEWS'] != 'NONE':
                raise RuntimeError('final copy failed')
            return original_copy(source, staging, options)
        with patch.object(cog_preparation, '_copy_cog', side_effect=fail_final_copy):
            with self.assertRaisesRegex(RuntimeError, 'final copy failed'):
                cog_preparation.prepare_cog(self.source, self.output, staged=True)
        self.assert_no_partial_output()

    def test_staged_budget_counts_padded_native_mask_and_both_overview_copies(self):
        budget = cog_preparation._staged_disk_budget(160300, 47189, 4)
        native_padded = 314 * 93 * 512 * 512 * 5
        self.assertEqual(budget['native_padded_bytes'], native_padded)
        self.assertGreater(budget['native_required_bytes'], native_padded)
        self.assertLess(budget['native_required_bytes'], 49_000_000_000)
        self.assertGreater(budget['overview_padded_bytes'], 10_000_000_000)
        measured = 123_456_789
        required = cog_preparation._staged_final_required_bytes(budget, measured)
        self.assertGreater(required, 2 * measured + 2 * budget['overview_padded_bytes'])

    def test_destination_may_have_the_same_name_as_the_native_scratch_file(self):
        self.output = self.folder / 'compressed-native.tif'
        self.write_source(np.array([[1, 2]], dtype='uint8'))
        cog_preparation.prepare_cog(self.source, self.output, staged=True)
        with rasterio.open(self.output) as dst:
            np.testing.assert_array_equal(dst.read(1), [[1, 2]])

    def test_changed_scale_is_rejected_even_when_native_values_match(self):
        self.write_source(np.array([[1, 2]], dtype='int16'))
        with rasterio.open(self.source, 'r+') as dst:
            dst.scales = (0.1,)
        wrong_source = self.folder / 'wrong.tif'
        with rasterio.open(self.source) as src:
            profile = src.profile
        with rasterio.open(wrong_source, 'w', **profile) as dst:
            dst.write(np.array([[1, 2]], dtype='int16'), 1)
        original_copy = cog_preparation._copy_cog
        with patch.object(cog_preparation, '_copy_cog',
                          side_effect=lambda source, staging, options: original_copy(wrong_source, staging, options)):
            with self.assertRaisesRegex(ValueError, 'Метаданные'):
                cog_preparation.prepare_cog(self.source, self.output)
        self.assert_no_partial_output()


if __name__ == '__main__':
    unittest.main()
