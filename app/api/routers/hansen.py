import os
import rasterio
from rasterio.mask import mask
from rasterstats import zonal_stats
from fastapi import APIRouter, HTTPException, Query
from app.schemas.schemas import PolygonInput, HansenStatsResponse, LossPixelsResponse, LossPixelFeature
from app.services.geo import get_polygon_from_geojson, reproject_polygon, validate_area
from app.core.config import settings
import numpy as np

router = APIRouter(prefix="/raster", tags=["hansen"])

@router.post("/forest-change", response_model=HansenStatsResponse)
def get_hansen_stats(input: PolygonInput):
    # 1. Parse and validate polygon
    polygon_4326 = get_polygon_from_geojson(input.geometry)
    
    # We need 2154 for area validation and 4326 for Hansen
    polygon_2154 = reproject_polygon(polygon_4326, target_crs='EPSG:2154')
    if not validate_area(polygon_2154):
        raise HTTPException(status_code=400, detail="Polygon area exceeds 1,000,000 hectares.")

    # 2. Paths
    # Using exact filenames from session_context
    treecover_tif = os.path.join(settings.GFC_PATH, "Hansen_GFC-2024-v1.12_treecover2000_50N_000E.tif")
    lossyear_tif = os.path.join(settings.GFC_PATH, "Hansen_GFC-2024-v1.12_lossyear_50N_000E.tif")
    gain_tif = os.path.join(settings.GFC_PATH, "Hansen_GFC-2024-v1.12_gain_50N_000E.tif")

    if not all(os.path.exists(p) for p in [treecover_tif, lossyear_tif, gain_tif]):
        raise HTTPException(status_code=404, detail="One or more Hansen raster files not found.")

    # 3. Treecover stats (mean)
    tc_stats = zonal_stats(polygon_4326, treecover_tif, stats=['mean'])
    tc_mean = tc_stats[0]['mean'] if tc_stats and tc_stats[0]['mean'] is not None else 0.0

    # 4. Gain stats (count pixels where value == 1)
    # Gain is 0 or 1. We want the area. 
    # Hansen resolution is approx 30m. Pixel area ~ 0.09 ha.
    gain_stats = zonal_stats(polygon_4326, gain_tif, stats=['sum'])
    gain_pixels = gain_stats[0]['sum'] if gain_stats and gain_stats[0]['sum'] is not None else 0
    gain_area_ha = gain_pixels * 0.09 # Rough approximation for 30m pixels

    # 5. Lossyear stats (categorical)
    # returns dict {value: count}
    loss_stats = zonal_stats(polygon_4326, lossyear_tif, categorical=True)
    loss_counts = loss_stats[0] if loss_stats else {}
    
    loss_area_ha_by_year = {}
    total_loss_ha = 0.0
    
    # Lossyear values are 1-23 (for 2001-2023)
    for val, count in loss_counts.items():
        if val > 0:
            year = 2000 + int(val)
            area_ha = count * 0.09
            loss_area_ha_by_year[year] = area_ha
            total_loss_ha += area_ha

    net_change_ha = gain_area_ha - total_loss_ha

    return HansenStatsResponse(
        treecover2000_mean=float(tc_mean),
        loss_area_ha_by_year=loss_area_ha_by_year,
        gain_area_ha=float(gain_area_ha),
        net_change_ha=float(net_change_ha)
    )


@router.post("/forest-loss-pixels", response_model=LossPixelsResponse)
def get_forest_loss_pixels(input: PolygonInput, year: int = Query(None, description="Optional year to filter (e.g. 2018). If None, all years are returned.")):
    polygon_4326 = get_polygon_from_geojson(input.geometry)
    
    # Validate area to prevent unbounded server compute
    polygon_2154 = reproject_polygon(polygon_4326, target_crs='EPSG:2154')
    if not validate_area(polygon_2154):
        raise HTTPException(status_code=400, detail="Polygon area exceeds 1,000,000 hectares.")

    lossyear_tif = os.path.join(settings.GFC_PATH, "Hansen_GFC-2024-v1.12_lossyear_50N_000E.tif")
    if not os.path.exists(lossyear_tif):
        raise HTTPException(status_code=404, detail="Hansen lossyear raster file not found.")

    with rasterio.open(lossyear_tif) as src:
        out_image, out_transform = mask(src, [polygon_4326], crop=True)
        out_image = out_image[0] # first band
        
        # Find indices where loss year is > 0
        if year is not None:
            # Loss year in raster is 1-24 (representing 2001-2024)
            target_val = year - 2000
            if target_val < 1 or target_val > 24:
                return LossPixelsResponse(features=[])
            indices = np.where(out_image == target_val)
        else:
            indices = np.where(out_image > 0)
            
        features = []
        
        # To avoid returning millions of points, limit the response
        # Cap it to 10,000 points deterministically for scientifically stable visual comparison
        num_points = len(indices[0])
        if num_points > 10000:
            sampled = np.linspace(0, num_points - 1, 10000, dtype=int)
            rows = indices[0][sampled]
            cols = indices[1][sampled]
        else:
            rows = indices[0]
            cols = indices[1]

        for row, col in zip(rows, cols):
            val = out_image[row, col]
            lon, lat = rasterio.transform.xy(out_transform, row, col)
            features.append(LossPixelFeature(
                type="Feature",
                geometry={"type": "Point", "coordinates": [lon, lat]},
                properties={"year": 2000 + int(val)}
            ))
            
    return LossPixelsResponse(features=features)


