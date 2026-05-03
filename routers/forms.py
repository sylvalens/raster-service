import os
import rasterio
from rasterio.mask import mask as rio_mask
from rasterstats import zonal_stats
from fastapi import APIRouter, HTTPException
from schemas import (
    PolygonInput,
    FormsStatsResponse,
    RasterStatsResponse,
    FormsHeightGridResponse,
)
from geo import get_polygon_from_geojson, reproject_polygon, validate_area
from config import settings
import numpy as np
import pyproj
from shapely.geometry import box, mapping
from shapely.ops import transform as shapely_transform

router = APIRouter(prefix="/raster", tags=["forms"])

def calculate_zonal_stats(
    tif_path: str,
    polygon_2154,
    variable: str,
    year: int,
    unit: str,
    scale_divisor: float = 1.0,
) -> RasterStatsResponse:
    if not os.path.exists(tif_path):
        raise HTTPException(status_code=404, detail=f"Raster file not found: {tif_path}")

    # Use rasterstats for efficiency
    stats = zonal_stats(polygon_2154, tif_path, stats=['min', 'max', 'mean', 'std', 'median'])
    
    if not stats or stats[0]['mean'] is None:
         # Return zero stats if no data found in polygon
        return RasterStatsResponse(
            variable=variable,
            year=year,
            mean=0.0,
            min=0.0,
            max=0.0,
            std=0.0,
            median=0.0,
            unit=unit
        )

    s = stats[0]
    return RasterStatsResponse(
        variable=variable,
        year=year,
        mean=float(s['mean']) / scale_divisor,
        min=float(s['min']) / scale_divisor,
        max=float(s['max']) / scale_divisor,
        std=float(s['std']) / scale_divisor,
        median=float(s['median']) / scale_divisor,
        unit=unit
    )

@router.post("/forms-stats", response_model=FormsStatsResponse)
def get_forms_stats(input: PolygonInput, year: int = 2024):
    # 1. Parse and validate polygon
    polygon_4326 = get_polygon_from_geojson(input.geometry)
    polygon_2154 = reproject_polygon(polygon_4326, target_crs='EPSG:2154')
    
    if not validate_area(polygon_2154):
        raise HTTPException(status_code=400, detail="Polygon area exceeds 1,000,000 hectares.")

    # 2. Calculate stats for each variable
    agbd_tif = os.path.join(settings.FORMS_T_PATH, f"AGBD_{year}_cog.tif")
    height_tif = os.path.join(settings.FORMS_T_PATH, f"Height_{year}_cog.tif")
    wvd_tif = os.path.join(settings.FORMS_T_PATH, f"WVD_{year}_cog.tif")

    agbd_stats = calculate_zonal_stats(agbd_tif, polygon_2154, "AGBD", year, "Mg/ha")
    # Height raster values are stored in centimeters; convert to meters for API consumers.
    height_stats = calculate_zonal_stats(
        height_tif,
        polygon_2154,
        "Height",
        year,
        "m",
        scale_divisor=100.0,
    )
    wvd_stats = calculate_zonal_stats(wvd_tif, polygon_2154, "WVD", year, "m3/ha")

    return FormsStatsResponse(
        agbd=agbd_stats,
        height=height_stats,
        wvd=wvd_stats
    )


@router.post("/forms-height-grid", response_model=FormsHeightGridResponse)
def get_forms_height_grid(
    input: PolygonInput,
    year: int = 2024,
    cellSizeM: float = 40.0,
    maxCells: int = 2500,
):
    if cellSizeM <= 0:
        raise HTTPException(status_code=400, detail='cellSizeM must be > 0')
    if maxCells <= 0:
        raise HTTPException(status_code=400, detail='maxCells must be > 0')

    polygon_4326 = get_polygon_from_geojson(input.geometry)
    polygon_2154 = reproject_polygon(polygon_4326, target_crs='EPSG:2154')

    if not validate_area(polygon_2154):
        raise HTTPException(status_code=400, detail='Polygon area exceeds 1,000,000 hectares.')

    height_tif = os.path.join(settings.FORMS_T_PATH, f'Height_{year}_cog.tif')
    if not os.path.exists(height_tif):
        raise HTTPException(status_code=404, detail=f'Raster file not found: {height_tif}')

    with rasterio.open(height_tif) as src:
        if src.crs is None:
            raise HTTPException(status_code=500, detail='Height raster has no CRS')

        raster_crs = src.crs

        if str(raster_crs).upper() == 'EPSG:2154':
            polygon_in_raster = polygon_2154
        else:
            to_raster = pyproj.Transformer.from_crs(
                'EPSG:2154', raster_crs, always_xy=True,
            ).transform
            polygon_in_raster = shapely_transform(to_raster, polygon_2154)

        clipped_band, clipped_transform = rio_mask(
            src,
            [mapping(polygon_in_raster)],
            crop=True,
            indexes=1,
            filled=False,
        )

        if clipped_band.size == 0:
            return {
                'type': 'FeatureCollection',
                'features': [],
                'meta': {
                    'year': year,
                    'cellSizeM': float(cellSizeM),
                    'cellCount': 0,
                    'unit': 'm',
                },
            }

        pixel_size = max(abs(src.transform.a), abs(src.transform.e))
        if pixel_size <= 0:
            raise HTTPException(status_code=500, detail='Invalid raster transform pixel size')

        cell_px = max(1, int(round(cellSizeM / pixel_size)))
        rows_total = int(np.ceil(clipped_band.shape[0] / cell_px))
        cols_total = int(np.ceil(clipped_band.shape[1] / cell_px))

        if rows_total * cols_total > maxCells:
            raise HTTPException(
                status_code=400,
                detail='Requested grid is too dense. Increase cellSizeM or reduce polygon size.',
            )

        to_wgs84 = pyproj.Transformer.from_crs(raster_crs, 'EPSG:4326', always_xy=True).transform

        features = []

        for row in range(rows_total):
            row_start = row * cell_px
            row_end = min((row + 1) * cell_px, clipped_band.shape[0])

            for col in range(cols_total):
                col_start = col * cell_px
                col_end = min((col + 1) * cell_px, clipped_band.shape[1])

                block = clipped_band[row_start:row_end, col_start:col_end]
                valid_values = np.ma.compressed(block)
                if valid_values.size == 0:
                    continue

                mean_height_m = float(valid_values.mean()) / 100.0
                if not np.isfinite(mean_height_m) or mean_height_m <= 0:
                    continue

                x0, y0 = clipped_transform * (col_start, row_start)
                x1, y1 = clipped_transform * (col_end, row_end)

                cell_geom = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                cell_clipped = cell_geom.intersection(polygon_in_raster)
                if cell_clipped.is_empty:
                    continue

                if cell_clipped.geom_type not in ('Polygon', 'MultiPolygon'):
                    continue

                cell_4326 = shapely_transform(to_wgs84, cell_clipped)
                if cell_4326.is_empty:
                    continue

                cell_id = f'{row}-{col}'

                features.append(
                    {
                        'type': 'Feature',
                        'geometry': mapping(cell_4326),
                        'properties': {
                            'id': cell_id,
                            'meanHeightM': mean_height_m,
                            'row': row,
                            'col': col,
                        },
                    },
                )

        return {
            'type': 'FeatureCollection',
            'features': features,
            'meta': {
                'year': year,
                'cellSizeM': float(cellSizeM),
                'cellCount': len(features),
                'unit': 'm',
            },
        }

