import os
import json
import subprocess
import tempfile
import uuid
import re
import logging
import laspy
import numpy as np
from fastapi import APIRouter, HTTPException
from app.schemas.schemas import PolygonInput, LidarStatsResponse
from app.services.geo import get_polygon_from_geojson, reproject_polygon, validate_area, project_to_4326
from app.core.config import settings
from shapely.geometry import box, mapping
from shapely.ops import transform

# Enable logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/raster", tags=["lidar"])

@router.get("/lidar-coverage")
def get_lidar_coverage():
    """Returns a GeoJSON FeatureCollection of all available LiDAR tile footprints."""
    tiles = []
    if not os.path.exists(settings.LIDAR_PATH):
        return {"type": "FeatureCollection", "features": []}

    # Pattern: LHD_FXX_{x}_{y}_PTS_LAMB93_IGN69.copc.laz
    pattern = re.compile(r"LHD_FXX_(\d+)_(\d+)_PTS_LAMB93_IGN69\.copc\.laz")
    
    for filename in os.listdir(settings.LIDAR_PATH):
        match = pattern.match(filename)
        if match:
            x_km = int(match.group(1))
            y_km = int(match.group(2))
            
            # Each tile is 1km x 1km in EPSG:2154
            # Coordinates are in meters
            min_x = x_km * 1000
            # IGN naming uses top-left corner for Y, so tile spans [y-1, y] km.
            min_y = (y_km - 1) * 1000
            max_x = (x_km + 1) * 1000
            max_y = y_km * 1000
            
            rect_2154 = box(min_x, min_y, max_x, max_y)
            rect_4326 = transform(project_to_4326, rect_2154)
            
            tiles.append({
                "type": "Feature",
                "geometry": mapping(rect_4326),
                "properties": {
                    "filename": filename,
                    "x_km": x_km,
                    "y_km": y_km
                }
            })
    
    return {"type": "FeatureCollection", "features": tiles}

@router.post("/lidar-stats", response_model=LidarStatsResponse)
def get_lidar_stats(input: PolygonInput):
    # 1. Parse and validate polygon
    polygon_4326 = get_polygon_from_geojson(input.geometry)
    polygon_2154 = reproject_polygon(polygon_4326, target_crs='EPSG:2154')
    
    if not validate_area(polygon_2154):
        raise HTTPException(status_code=400, detail="Polygon area exceeds 1,000,000 hectares.")

    # 2. Identify intersecting tiles
    if not os.path.exists(settings.LIDAR_PATH):
        logger.info("LiDAR: LIDAR_PATH does not exist")
        return LidarStatsResponse(
            mean_height=0.0, max_height=0.0, p50=0.0, p75=0.0, p95=0.0, point_density=0.0
        )

    bounds = polygon_2154.bounds
    min_x_km = int(bounds[0] // 1000)
    max_x_km = int(bounds[2] // 1000)
    # With top-left Y naming, candidate Y indices are roughly [floor(min/1000)+1, ceil(max/1000)].
    min_y_km = int(bounds[1] // 1000) + 1
    max_y_km = int((bounds[3] + 999.9999) // 1000)
    
    logger.debug(f"LiDAR: Polygon 4326 bounds: {polygon_4326.bounds}")
    logger.debug(f"LiDAR: Polygon 2154 bounds: {bounds}")
    logger.debug(f"LiDAR: Polygon area m²: {polygon_2154.area}")
    logger.debug(f"LiDAR: km range x=[{min_x_km},{max_x_km}] y=[{min_y_km},{max_y_km}]")

    pattern = re.compile(r"LHD_FXX_(\d+)_(\d+)_PTS_LAMB93_IGN69\.copc\.laz")
    matching_tiles = []
    for filename in os.listdir(settings.LIDAR_PATH):
        match = pattern.match(filename)
        if not match:
            continue

        x = int(match.group(1))
        y = int(match.group(2))

        if x < min_x_km or x > max_x_km or y < min_y_km or y > max_y_km:
            continue

        tile_min_x = x * 1000
        tile_max_x = (x + 1) * 1000
        # IGN naming uses top-left corner for Y.
        tile_min_y = (y - 1) * 1000
        tile_max_y = y * 1000

        tile_geom = box(tile_min_x, tile_min_y, tile_max_x, tile_max_y)
        if tile_geom.intersects(polygon_2154):
            matching_tiles.append(os.path.join(settings.LIDAR_PATH, filename))
    
    logger.debug(f"LiDAR: Found {len(matching_tiles)} matching tiles: {[os.path.basename(t) for t in matching_tiles]}")

    if not matching_tiles:
        logger.info("LiDAR: No tiles intersect polygon")
        return LidarStatsResponse(
            mean_height=0.0, max_height=0.0, p50=0.0, p75=0.0, p95=0.0, point_density=0.0
        )

    # 3. Build PDAL Pipeline
    wkt_polygon = polygon_2154.wkt
    logger.debug(f"LiDAR: Polygon WKT: {wkt_polygon[:200]}...")  # first 200 chars only

    pipeline_nodes = []
    for tile in matching_tiles:
        pipeline_nodes.append({
            "type": "readers.las",
            "filename": tile
        })
    
    # Always merge, even for single tile - seems to be required for crop filter to work
    pipeline_nodes.append({"type": "filters.merge"})
    
    pipeline_nodes.append({
        "type": "filters.crop",
        "polygon": wkt_polygon
    })

    # Accept all ASPRS classes (0-31). Classes 3-4 were too restrictive.
    # Adjust min/max based on your LiDAR classification scheme.
    pipeline_nodes.append({
        "type": "filters.range",
        "limits": "Classification[0:31]"
    })

    pipeline_nodes.append({
        "type": "filters.stats",
        "dimensions": "Z"
    })

    # We must write the pipeline to a temp file because `pdal pipeline --stdin`
    # has parsing issues with certain formats in this specific PDAL version.
    run_id = str(uuid.uuid4())
    pipeline_path = os.path.join(tempfile.gettempdir(), f"pipeline_{run_id}.json")
    metadata_path = os.path.join(tempfile.gettempdir(), f"meta_{run_id}.json")
    filtered_las_path = os.path.join(tempfile.gettempdir(), f"filtered_{run_id}.las")

    # Persist filtered points so percentiles are computed from real Z samples.
    pipeline_nodes.append({
        "type": "writers.las",
        "filename": filtered_las_path,
    })
    
    logger.debug(f"LiDAR: Pipeline nodes (before merge/crop/filter): {len(pipeline_nodes)} stages")

    try:
        with open(pipeline_path, 'w') as f:
            json.dump(pipeline_nodes, f, indent=2)

        # Execute pdal pipeline
        process = subprocess.run(
            ["pdal", "pipeline", pipeline_path, "--metadata", metadata_path],
            capture_output=True,
            text=True
        )
        
        logger.debug(f"LiDAR: PDAL returncode={process.returncode}")
        if process.returncode != 0:
            logger.debug(f"LiDAR: PDAL stderr: {process.stderr}")
            raise Exception(f"PDAL pipeline error: {process.stderr}")

        with open(metadata_path, 'r') as f:
            meta = json.load(f)
        
        stages_data = meta.get('stages', {})
        stage_names = list(stages_data.keys()) if isinstance(stages_data, dict) else []
        logger.debug(f"LiDAR: PDAL stages: {stage_names}")

        # Extract stats from filters.stats stage
        stats_stage = stages_data.get('filters.stats', {}) if isinstance(stages_data, dict) else {}
        if isinstance(stats_stage, list):
            # If it's a list (which means multiple stats items), take the last one (after crop/filter)
            z_stats = next((s for item in reversed(stats_stage) if isinstance(item, dict) and 'statistic' in item for s in item.get("statistic", []) if s.get("name") == "Z"), None)
        elif isinstance(stats_stage, dict) and 'statistic' in stats_stage:
            z_stats = next((s for s in stats_stage.get("statistic", []) if s.get("name") == "Z"), None)
        else:
            z_stats = None
            logger.debug(f"LiDAR: No stats stage found")
        
        if not z_stats or z_stats.get("count", 0) == 0:
            logger.debug(f"LiDAR: No points after crop/filter/classification")
            logger.info("LiDAR: No Z stats or zero count")
            return LidarStatsResponse(
                mean_height=0.0, max_height=0.0, p50=0.0, p75=0.0, p95=0.0, point_density=0.0
            )

        mean_z = z_stats["average"]
        max_z = z_stats["maximum"]
        count = z_stats["count"]
        logger.debug(f"LiDAR: Success - count={count}, mean_z={mean_z}, max_z={max_z}")

        # Compute true Z percentiles from the filtered LAS output.
        las = laspy.read(filtered_las_path)
        z_values = np.asarray(las.z, dtype=np.float64)
        if z_values.size == 0:
            logger.info("LiDAR: Filtered LAS has zero points")
            return LidarStatsResponse(
                mean_height=0.0, max_height=0.0, p50=0.0, p75=0.0, p95=0.0, point_density=0.0
            )

        p50, p75, p95 = np.percentile(z_values, [50, 75, 95])
        count = int(z_values.size)
        
        # Area for density
        area_m2 = polygon_2154.area
        density = count / area_m2 if area_m2 > 0 else 0
        
        logger.info(f"LiDAR: Success - count={count}, mean_z={mean_z}, max_z={max_z}, density={density}")

        return LidarStatsResponse(
            mean_height=float(mean_z),
            max_height=float(max_z),
            p50=float(p50),
            p75=float(p75),
            p95=float(p95),
            point_density=float(density)
        )

    finally:
        if os.path.exists(pipeline_path):
            os.remove(pipeline_path)
        if os.path.exists(metadata_path):
            os.remove(metadata_path)
        if os.path.exists(filtered_las_path):
            os.remove(filtered_las_path)

