from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import math

from services.watershed import delineate_watershed
from services.analysis import run_analysis
from services.flood import compute_flood_zone, FloodAnalysisError
from services.buildings import estimate_flood_damage
from services.dem_catalog import DemSourceError, get_dem_config, list_dem_sources
from services.dem_coverage import get_dem_coverage
from services.terrain_sample import TERRAIN_LAYERS, sample_terrain_value
from config import DEFAULT_PEOPLE_PER_BUILDING


app = Flask(__name__)
CORS(app)


@app.route('/dem_sources', methods=['GET'])
def dem_sources():
    return jsonify(list_dem_sources())


@app.route('/dem_coverage', methods=['GET'])
def dem_coverage():
    source_id = request.args.get('source', type=str)
    try:
        return jsonify(get_dem_coverage(source_id))
    except DemSourceError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('DEM coverage calculation failed for %s', source_id)
        return jsonify({'error': 'Не удалось загрузить границы покрытия. Повторите попытку.'}), 500


@app.route("/get_watershed", methods=["GET"])
def calculate_watershed():
    lat = request.args.get("latitude", type=float)
    lon = request.args.get("longitude", type=float)
    dem_name = request.args.get("source", type=str)

    if (lat is None or lon is None or not math.isfinite(lat) or not math.isfinite(lon)
            or not -85 <= lat <= 85 or not -180 <= lon <= 180):
        return jsonify({'error': 'Укажите корректные широту (−85…85) и долготу (−180…180).'}), 400

    try:
        dem = get_dem_config(dem_name)
        watershed_gdf = delineate_watershed(lat, lon, dem_name)
        analysis = run_analysis(watershed_gdf)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Watershed calculation failed for %s', dem_name)
        return jsonify({'error': 'Не удалось выполнить расчёт. Проверьте доступность данных и повторите попытку.'}), 500

    geojson = json.loads(
        watershed_gdf.to_crs("EPSG:4326").to_json()
    )

    return jsonify({
        "watershed": geojson,
        "analysis": analysis,
        "source": {key: dem.get(key) for key in ('id', 'label', 'grid_resolution_m', 'regional')},
    })


@app.route('/terrain_value', methods=['GET'])
def terrain_value():
    lat = request.args.get('latitude', type=float)
    lon = request.args.get('longitude', type=float)
    layer = request.args.get('layer', type=str)
    if (lat is None or lon is None or not math.isfinite(lat) or not math.isfinite(lon)
            or not -85 <= lat <= 85 or not -180 <= lon <= 180):
        return jsonify({'error': 'Укажите корректные широту (−85…85) и долготу (−180…180).'}), 400
    if layer not in TERRAIN_LAYERS:
        return jsonify({'error': 'Выберите слой высот или уклонов.'}), 400
    try:
        return jsonify(sample_terrain_value(lat, lon, layer))
    except Exception:
        app.logger.exception('Terrain point sampling failed for %s', layer)
        return jsonify({'error': 'Не удалось загрузить значение. Повторите попытку.'}), 500


@app.route("/get_flood_risk", methods=["GET"])
def calculate_flood_risk():
    lat = request.args.get("latitude", type=float)
    lon = request.args.get("longitude", type=float)
    dem_name = request.args.get("source", type=str)
    water_level_m = request.args.get("water_level_m", type=float)
    people_per_building = request.args.get(
        "people_per_building", type=float, default=DEFAULT_PEOPLE_PER_BUILDING
    )

    try:
        result = compute_flood_zone(lat, lon, dem_name, water_level_m)
    except (FloodAnalysisError, ValueError) as e:
        return jsonify({"error": str(e)}), 400

    damage = estimate_flood_damage(
        result["flood_gdf"],
        result["flood_gdf_projected"],
        result["flood_area_km2"],
        people_per_building=people_per_building,
    )

    flood_geojson = json.loads(result["flood_gdf"].to_json())

    return jsonify({
        "flood_zone": flood_geojson,
        "flood_area_km2": result["flood_area_km2"],
        "watershed_area_km2": result["watershed_area_km2"],
        "water_level_m": result["water_level_m"],
        "reliable": result["reliable"],
        "damage": damage,
    })


if __name__ == "__main__":
    app.run()
