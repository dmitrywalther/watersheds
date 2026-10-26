import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / 'data'

DEM_DIR = DATA_DIR / 'dem'
CLIMATE_DIR = DATA_DIR / 'climate'
LANDCOVER_DIR = DATA_DIR / 'landcover'
SOIL_DIR = DATA_DIR / 'soil'
ELEVATION_DIR = DATA_DIR / 'heights'
SLOPE_DIR = DATA_DIR / 'slopes'

# External datasets are used in place, without copying or modifying their files.
# Complete downloaded pairs take precedence; a split pair is also supported.
EXTERNAL_DEM_DIR = Path(os.environ.get(
    'WATERSHED_DEM_DIR', str(Path.home() / 'Yandex.Disk' / 'watershed' / 'Data')
)).expanduser()
DEM_DATA_ROOTS = (DEM_DIR, EXTERNAL_DEM_DIR)


def is_cloud_placeholder(path):
    # Python exposes RECALL_ON_DATA_ACCESS even when it omits OFFLINE.
    attributes = getattr(path.stat(), 'st_file_attributes', 0)
    return bool(attributes & (0x1000 | 0x40000 | 0x400000))


def _local_raster(path):
    try:
        return path.is_file() and not is_cloud_placeholder(path)
    except OSError:
        return False


def _resolve_raster(filename):
    candidates = [folder / filename for folder in DEM_DATA_ROOTS]
    # Keep a cloud placeholder as a last resort so the catalog can explain why
    # it is unavailable. Never read it merely to choose a path.
    return next((path for path in candidates if _local_raster(path)),
                next((path for path in candidates if path.is_file()), candidates[0]))


def _dem_source(stem, label, nominal_resolution, *, direction=None, accumulation=None,
                regional=False, coverage_note=None, preferred_root=None):
    direction = direction or f'{stem}_FlowDir.tif'
    accumulation = accumulation or f'{stem}_FlowAcc.tif'
    # Prefer an optimized copy only as a complete downloaded pair. Partial
    # conversion outputs must never replace one half of the original pair.
    roots = (Path(preferred_root), *DEM_DATA_ROOTS) if preferred_root is not None else DEM_DATA_ROOTS
    root = next((folder for folder in roots
                 if _local_raster(folder / direction) and _local_raster(folder / accumulation)), None)
    # Grid compatibility is still checked by dem_catalog before use, even when
    # filenames match. This avoids copying multi-gigabyte files between roots.
    flow_dir = root / direction if root else _resolve_raster(direction)
    flow_acc = root / accumulation if root else _resolve_raster(accumulation)
    return {
        'label': label,
        'resolution_label': nominal_resolution,
        'coverage_note': coverage_note or (
            'Региональный фрагмент: 32–61,7° в. д., 41,2–62° с. ш. '
            'Водосбор должен целиком помещаться в покрытии.'
            if regional else 'Подготовленное покрытие России; внутри растра есть участки без данных.'
        ),
        'regional': regional,
        'flow_dir': flow_dir,
        'flow_acc': flow_acc,
        'flow_dir_encoding': 'esri',
        'flow_acc_units': 'cells',
    }


DEM_FILES = {
    'ETOPO1_1min': _dem_source(
        'ETOPO1_1min', 'ETOPO1 · 1′', '1′',
        preferred_root=DEM_DIR / 'cog' / 'etopo1_1min' / 'zstd'),
    'GMTED2010_30sec': _dem_source(
        'GMTED2010_30sec', 'GMTED2010 · 30″', '30″',
        preferred_root=DEM_DIR / 'cog' / 'gmted2010_30sec' / 'zstd'),
    'GMTED2010_15sec': _dem_source(
        'GMTED2010_15sec', 'GMTED2010 · 15″', '15″',
        preferred_root=DEM_DIR / 'cog' / 'gmted2010_15sec' / 'zstd'),
    'GMTED2010_7_5sec': _dem_source(
        'GMTED2010_7,5sec', 'GMTED2010 · 7,5″', '7,5″',
        preferred_root=DEM_DIR / 'cog' / 'gmted2010_7_5sec' / 'zstd'),
    'GEBCO2020_15sec': _dem_source(
        'GEBCO2020_15sec', 'GEBCO2020 · 15″', '15″',
        preferred_root=DEM_DIR / 'cog' / 'gebco2020_15sec' / 'zstd'),
    'GTOPO30_30sec': _dem_source(
        'GTOPO30_30sec', 'GTOPO30 · 30″', '30″',
        preferred_root=DEM_DIR / 'cog' / 'gtopo30_30sec' / 'zstd'),
    'GMTED2010_7_5sec_clip': _dem_source(
        'GMTED2010_7,5sec', 'GMTED2010 · 7,5″ · фрагмент', '7,5″',
        direction='GMTED2010_7,5sec_FlowDir_Clip.tif',
        accumulation='GMTED2010_7,5sec_FlowAcc_Clip.tif', regional=True,
        preferred_root=DEM_DIR / 'cog' / 'gmted2010_7_5sec_clip' / 'zstd'),
    'MERIT_3sec': _dem_source(
        'MERIT_3sec', 'MERIT · 3″ · Сибирь', '3″', direction='FlowDir_MERIT.tif', regional=True,
        preferred_root=DEM_DIR / 'cog' / 'merit_3sec' / 'zstd',
        coverage_note='Региональное покрытие Сибири: 76,8–113,8° в. д., 46,4–72,3° с. ш. '
                      'Внутри есть NoData; водосбор должен целиком помещаться в данных.'),
    'ALOS_local': _dem_source(
        'ALOS', 'ALOS · Сибирь', 'Фрагмент', direction='FlowDir_ALPS11.tif', regional=True,
        preferred_root=DEM_DIR / 'cog' / 'alos_local' / 'zstd',
        coverage_note='Региональное покрытие Сибири: 88–109,3° в. д., 57,7–68° с. ш. '
                      'Внутри есть NoData; водосбор должен целиком помещаться в данных.'),
}
DEFAULT_DEM_SOURCE = 'GMTED2010_30sec'

def _preferred_thematic_raster(original):
    """Select only a local copy with a successful full conversion manifest.

    This is a lightweight startup check, not a replacement for pixel validation
    in prepare_cog. Missing, unfinished or stale copies fall back to the source.
    """
    candidate = original.parent / 'cog' / 'zstd' / original.name
    manifest = candidate.with_suffix(candidate.suffix + '.cog.json')
    if not _local_raster(candidate) or not _local_raster(manifest):
        return original
    try:
        report = json.loads(manifest.read_text(encoding='utf-8'))
        if (Path(report['source']) != original.resolve()
                or Path(report['output']) != candidate.resolve()
                or report['output_size_bytes'] != candidate.stat().st_size
                or report['all_native_pixels_validated'] is not True
                or report['all_native_masks_validated'] is not True
                or report['creation_options']['COMPRESS'] != 'ZSTD'):
            return original
        for kind in ('native', 'mask'):
            digest = report[f'source_{kind}_sha256']
            if (not isinstance(digest, str) or len(digest) != 64
                    or digest != report[f'output_{kind}_sha256']):
                return original
        # Stat reads cloud metadata without downloading source raster contents.
        if original.is_file():
            info = original.stat()
            if (info.st_size != report['source_size_bytes']
                    or info.st_mtime_ns != report['source_mtime_ns']):
                return original
            # Match prepare_cog's source inventory: adding/removing an external
            # mask can change validity without changing the TIFF itself.
            related = [original] + [Path(str(original) + suffix)
                                    for suffix in ('.msk', '.ovr', '.aux.xml', '.vat.dbf')]
            current_files = {path.resolve() for path in related if path.is_file()}
            if current_files != {Path(name) for name in report['source_files']}:
                return original
        for name, signature in report.get('source_files', {}).items():
            source_file = Path(name)
            if source_file.is_file():
                info = source_file.stat()
                if (info.st_size != signature['size_bytes']
                        or info.st_mtime_ns != signature['mtime_ns']):
                    return original
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return original
    return candidate


def _climate_template(variable):
    name = f'c1_30s_{variable}_{{month}}_Clip_Project.tif'
    originals = [CLIMATE_DIR / name.format(month=f'{month:02d}') for month in range(1, 13)]
    # A partial conversion must not switch a monthly series halfway through.
    complete = all(_preferred_thematic_raster(path) != path for path in originals)
    folder = CLIMATE_DIR / 'cog' / 'zstd' if complete else CLIMATE_DIR
    return str(folder / name)


LANDCOVER_RASTER = _preferred_thematic_raster(LANDCOVER_DIR / 'GLOBCOVER_Clip_Project.tif')
SOIL_RASTER = _preferred_thematic_raster(SOIL_DIR / 'soil_map_M2_5.tif')
CLIMATE_TEMPERATURE_TEMPLATE = _climate_template('tavg')
CLIMATE_RAINFALL_TEMPLATE = _climate_template('prec')

ELEVATION_RASTER = _preferred_thematic_raster(ELEVATION_DIR / 'GMTED2010_30sec_Clip_Project_Fill.tif')
SLOPE_RASTER = _preferred_thematic_raster(SLOPE_DIR / 'GMTED2010_30sec_Clip_Project_Fill_Slope.tif')

# =========================
# FLOOD RISK (HAND-модель)
# =========================
#
# ВАЖНО: это screening-level оценка, а НЕ гидравлическая модель.
# HAND (Height Above Nearest Drainage) — стандартный в отрасли метод
# быстрой оценки потенциальных зон подтопления по ЦМР, без учёта
# геометрии русла, кривых расхода и конкретной вероятности паводка.
# Пользователь задаёт условный подъём уровня воды над руслом (сценарий),
# а не привязывается к историческому/расчётному паводку заданной
# повторяемости.
#
# Каталог DEM описывает доступность выделения водосборов. HAND пока
# использует общую поверхность GMTED2010 30″ и не становится детальнее
# автоматически при выборе более подробной сетки направлений стока.

# Порог площади водосбора для выделения русла (км²).
# Ячейки flow_acc с накопленной площадью >= порога считаются руслом.
# Значение 1.0 км² — рабочий дефолт, требует калибровки под регион:
# для равнинных крупных бассейнов может давать слишком густую сеть,
# для горных — наоборот, редкую.
FLOOD_STREAM_THRESHOLD_KM2 = 1.0

# Разрешённые сценарии подъёма уровня воды над руслом, метры.
FLOOD_WATER_LEVELS_M = [0.5, 1.0, 2.0, 5.0]

# =========================
# ОЦЕНКА УЩЕРБА (здания и население)
# =========================

# Overpass API (OpenStreetMap) — источник реальных зданий в зоне затопления.
OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
OVERPASS_TIMEOUT_S = 25

# Среднее число жителей на одно здание — грубая оценка, настраивается
# пользователем в UI. Дефолт скорее подходит для жилой застройки
# частного сектора, чем для многоквартирных домов.
DEFAULT_PEOPLE_PER_BUILDING = 3

# Если запрос к Overpass не удался (нет сети / таймаут / зона затопления
# слишком велика для одного запроса) — используем fallback: доля
# категории "застроенные территории" в landcover внутри зоны затопления,
# умноженная на условную плотность застройки. Это грубее, чем реальные
# здания из OSM, и помечается в ответе API как confidence="low".
URBAN_LANDCOVER_CLASS = 'Artificial areas'
BUILDINGS_PER_KM2_URBAN_FALLBACK = 150  # требует калибровки под регион

# Если зона затопления больше этого значения (км²) — не отправляем
# запрос в Overpass (риск таймаута/чрезмерной нагрузки на публичный API),
# сразу используем fallback-оценку по landcover.
FLOOD_OVERPASS_MAX_AREA_KM2 = 500
