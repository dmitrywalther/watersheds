"""Validated inventory of prepared hydrological grids (metadata reads only)."""

import numpy as np
import rasterio
from rasterio.errors import RasterioError

from config import DEFAULT_DEM_SOURCE, DEM_FILES, is_cloud_placeholder


RASTER_ENV = {'GDAL_DISABLE_READDIR_ON_OPEN': 'EMPTY_DIR', 'GDAL_PAM_ENABLED': 'NO'}


class DemSourceError(ValueError):
    """The selected DEM cannot currently be used for delineation."""


def get_dem_config(source_id):
    if source_id not in DEM_FILES:
        raise DemSourceError('Неизвестная ЦМР. Выберите источник из каталога.')
    spec = DEM_FILES[source_id]
    for key, title in [('flow_dir', 'направлений стока'), ('flow_acc', 'накопления стока')]:
        path = spec[key]
        if not path.is_file():
            raise DemSourceError(f'Нет подготовленного растра {title}.')
        if is_cloud_placeholder(path):
            raise DemSourceError(f'Растр {title} хранится только в облаке. Сначала скачайте его на диск.')
    try:
        with rasterio.Env(**RASTER_ENV), rasterio.open(spec['flow_dir']) as direction, \
                rasterio.open(spec['flow_acc']) as accumulation:
            if (direction.crs != accumulation.crs or direction.shape != accumulation.shape
                    or direction.transform != accumulation.transform):
                raise DemSourceError('Растры направлений и накопления стока имеют разные сетки.')
            if not direction.crs or not direction.crs.is_projected or direction.crs.linear_units not in ('metre', 'meter'):
                raise DemSourceError('Для расчёта нужна проекция с метрическими координатами.')
            transform = direction.transform
            if transform.b != 0 or transform.d != 0 or transform.a <= 0 or transform.e >= 0:
                raise DemSourceError('Не поддерживается повёрнутая или перевёрнутая сетка ЦМР.')
            if direction.count != 1 or accumulation.count != 1:
                raise DemSourceError('Растры стока должны содержать по одному каналу.')
            if not np.issubdtype(np.dtype(direction.dtypes[0]), np.integer):
                raise DemSourceError('Растр направлений стока должен содержать целочисленные коды D8.')
            if spec.get('flow_dir_encoding') not in ('esri', 'whitebox') or spec.get('flow_acc_units') != 'cells':
                raise DemSourceError('Не задана кодировка D8 или единица накопления стока.')
            resolution = max(abs(transform.a), abs(transform.e))
            return {**spec, 'id': source_id, 'resolution': resolution,
                    'grid_resolution_m': resolution, 'crs': direction.crs,
                    'bounds': tuple(direction.bounds)}
    except (RasterioError, OSError) as exc:
        raise DemSourceError('Не удалось прочитать растры ЦМР. Проверьте доступность файлов на диске.') from exc


def list_dem_sources():
    sources = []
    for source_id, spec in DEM_FILES.items():
        source = {key: spec.get(key) for key in (
            'label', 'resolution_label', 'coverage_note', 'regional', 'flow_dir_encoding')}
        source.update(id=source_id, available=False, unavailable_reason=None, grid_resolution_m=None)
        try:
            resolved = get_dem_config(source_id)
            source.update(available=True, grid_resolution_m=resolved['grid_resolution_m'])
        except (DemSourceError, OSError) as exc:
            source['unavailable_reason'] = str(exc) if isinstance(exc, DemSourceError) else 'Файлы ЦМР недоступны.'
        sources.append(source)
    available = [source['id'] for source in sources if source['available']]
    default = DEFAULT_DEM_SOURCE if DEFAULT_DEM_SOURCE in available else next(iter(available), None)
    return {'sources': sources, 'default_source': default}
