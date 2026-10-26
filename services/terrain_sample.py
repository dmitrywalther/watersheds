"""Read one native elevation/slope pixel for a map hover, without resampling."""

from contextlib import ExitStack
import math
from pathlib import Path
import warnings

import numpy as np
from pyproj import Transformer
from pyproj.exceptions import ProjError
import rasterio
from rasterio.errors import NotGeoreferencedWarning, RasterioError
from rasterio.windows import Window

from config import ELEVATION_RASTER, SLOPE_RASTER, is_cloud_placeholder
from services.soil import RASTER_ENV


SOURCE_LABEL = 'GMTED2010 30″'
TERRAIN_LAYERS = frozenset(('elevation', 'slope'))


def _result(status, unit, reason=None, value=None):
    return {'status': status, 'value': value, 'unit': unit,
            'source_label': SOURCE_LABEL, 'reason': reason}


def sample_terrain_value(latitude, longitude, layer, raster_path=None):
    """Return the original pixel at a WGS84 coordinate, honoring validity masks.

    The caller limits interaction to the displayed watershed. This endpoint
    intentionally does not delineate a basin or use generalized map intervals.
    Every data/mask read is a 1×1 native window with no out_shape/overview use.
    """
    if layer not in TERRAIN_LAYERS:
        raise ValueError('Выберите слой высот или уклонов.')
    if (not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float))
            or not math.isfinite(latitude) or not math.isfinite(longitude)
            or not -85 <= latitude <= 85 or not -180 <= longitude <= 180):
        raise ValueError('Укажите корректные широту (−85…85) и долготу (−180…180).')

    unit = 'm' if layer == 'elevation' else 'degrees'
    subject = 'высот' if layer == 'elevation' else 'уклонов'
    path = Path(raster_path) if raster_path is not None else Path(
        ELEVATION_RASTER if layer == 'elevation' else SLOPE_RASTER)
    try:
        if not path.is_file():
            return _result('unavailable', unit, f'Файл растра {subject} не найден.')
        if is_cloud_placeholder(path):
            return _result('unavailable', unit, f'Растр {subject} хранится только в облаке. Скачайте файл на диск.')
        mask_path = path.with_name(path.name + '.msk')
        if mask_path.is_file() and is_cloud_placeholder(mask_path):
            return _result('unavailable', unit, f'Маска растра {subject} хранится только в облаке. Скачайте её на диск.')

        with rasterio.Env(**RASTER_ENV), ExitStack() as stack:
            src = stack.enter_context(rasterio.open(path))
            if (src.crs is None or src.count != 1 or np.dtype(src.dtypes[0]).kind not in 'iuf'
                    or not all(math.isfinite(value) for value in src.transform)
                    or src.transform.determinant == 0):
                return _result('unavailable', unit,
                               f'Растр {subject} должен содержать один числовой канал и корректную пространственную привязку.')
            x, y = Transformer.from_crs('EPSG:4326', src.crs, always_xy=True).transform(
                longitude, latitude, errcheck=True)
            if not math.isfinite(x) or not math.isfinite(y):
                return _result('no_data', unit, f'Точка находится вне покрытия растра {subject}.')
            row, col = src.index(x, y)
            if not (0 <= row < src.height and 0 <= col < src.width):
                return _result('no_data', unit, f'Точка находится вне покрытия растра {subject}.')

            external_mask = None
            if mask_path.is_file():
                # EMPTY_DIR keeps cloud overviews undiscovered. Read the known
                # local external mask explicitly, in the same native pixel grid.
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', NotGeoreferencedWarning)
                    external_mask = stack.enter_context(rasterio.open(mask_path))
                if (external_mask.count != 1 or external_mask.shape != src.shape
                        or external_mask.dtypes != ('uint8',)
                        or external_mask.tags().get('INTERNAL_MASK_FLAGS_1') not in ('0', '2')):
                    return _result('unavailable', unit, f'Не поддерживается формат внешней маски растра {subject}.')

            window = Window(col, row, 1, 1)
            value = float(src.read(1, window=window)[0, 0])
            valid = src.read_masks(1, window=window)[0, 0] != 0
            if external_mask is not None:
                valid = valid and external_mask.read(1, window=window)[0, 0] != 0
            if (not valid or not math.isfinite(value) or value == src.nodata
                    or (layer == 'slope' and not 0 <= value <= 90)):
                return _result('no_data', unit, 'В этой точке нет данных.')
            return _result('ok', unit, value=value)
    except (OSError, RasterioError, ProjError, ValueError, TypeError, OverflowError):
        return _result('unavailable', unit, f'Не удалось прочитать значение из растра {subject}.')
