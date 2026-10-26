import unittest
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import box

from start import app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_catalog_endpoint(self):
        catalog = {'sources': [], 'default_source': None}
        with patch('start.list_dem_sources', return_value=catalog):
            response = self.client.get('/dem_sources')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, catalog)

    def test_invalid_coordinates_do_not_start_calculation(self):
        with patch('start.delineate_watershed') as calculate:
            for coordinates in ['', 'latitude=nan&longitude=37', 'latitude=56&longitude=inf',
                                'latitude=91&longitude=37', 'latitude=56&longitude=181']:
                response = self.client.get('/get_watershed?source=ETOPO1_1min&' + coordinates)
                self.assertEqual(response.status_code, 400)
                self.assertIn('error', response.json)
            calculate.assert_not_called()

    def test_unknown_source_returns_json_error(self):
        response = self.client.get('/get_watershed?latitude=56&longitude=37&source=missing')
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json)

    def test_selected_dem_and_its_metadata_are_preserved(self):
        dem = {'id': 'test', 'label': 'Test', 'grid_resolution_m': 750, 'regional': True}
        basin = gpd.GeoDataFrame(geometry=[box(0, 0, 1000, 1000)], crs='EPSG:3395')
        with patch('start.get_dem_config', return_value=dem), \
                patch('start.delineate_watershed', return_value=basin) as calculate, \
                patch('start.run_analysis', return_value={'area_km2': 1}):
            response = self.client.get('/get_watershed?latitude=56&longitude=37&source=test')
        self.assertEqual(response.status_code, 200)
        calculate.assert_called_once_with(56.0, 37.0, 'test')
        self.assertEqual(response.json['source'], dem)
        self.assertEqual(response.json['watershed']['type'], 'FeatureCollection')

    def test_incomplete_coverage_returns_readable_error(self):
        with patch('start.get_dem_config', return_value={}), \
                patch('start.delineate_watershed', side_effect=ValueError('Водосбор выходит за покрытие.')):
            response = self.client.get('/get_watershed?latitude=56&longitude=37&source=test')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json['error'], 'Водосбор выходит за покрытие.')

    def test_terrain_point_returns_native_value_without_delineation(self):
        result = {'status': 'ok', 'value': -12.25, 'unit': 'm',
                  'source_label': 'GMTED2010 30″', 'reason': None}
        with patch('start.sample_terrain_value', return_value=result) as sample, \
                patch('start.delineate_watershed') as delineate:
            response = self.client.get('/terrain_value?latitude=56.25&longitude=37.5&layer=elevation')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, result)
        sample.assert_called_once_with(56.25, 37.5, 'elevation')
        delineate.assert_not_called()

    def test_terrain_point_invalid_requests_do_not_sample(self):
        with patch('start.sample_terrain_value') as sample:
            for query in ('', 'latitude=nan&longitude=37&layer=slope',
                          'latitude=56&longitude=inf&layer=slope',
                          'latitude=-86&longitude=37&layer=elevation',
                          'latitude=56&longitude=-181&layer=elevation',
                          'latitude=56&longitude=37&layer=../config.py',
                          'latitude=56&longitude=37', 'latitude=56&layer=slope'):
                with self.subTest(query=query):
                    response = self.client.get('/terrain_value?' + query)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn('error', response.json)
            sample.assert_not_called()

    def test_terrain_point_expected_missing_data_is_http_200(self):
        for status in ('no_data', 'unavailable'):
            result = {'status': status, 'value': None, 'unit': 'degrees',
                      'source_label': 'GMTED2010 30″', 'reason': 'Нет данных.'}
            with self.subTest(status=status), patch('start.sample_terrain_value', return_value=result):
                response = self.client.get('/terrain_value?latitude=56&longitude=37&layer=slope')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json, result)


if __name__ == '__main__':
    unittest.main()
