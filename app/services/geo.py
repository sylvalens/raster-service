from typing import Union
from shapely.geometry import shape, Polygon, MultiPolygon
from shapely.ops import transform
import pyproj

# Projections
wgs84 = pyproj.CRS('EPSG:4326')
lamb93 = pyproj.CRS('EPSG:2154')

project_to_2154 = pyproj.Transformer.from_crs(wgs84, lamb93, always_xy=True).transform
project_to_4326 = pyproj.Transformer.from_crs(lamb93, wgs84, always_xy=True).transform

def get_polygon_from_geojson(geojson_dict: dict) -> Union[Polygon, MultiPolygon]:
    """Converts a GeoJSON geometry dict to a Shapely Polygon or MultiPolygon."""
    geom = shape(geojson_dict)
    if not isinstance(geom, (Polygon, MultiPolygon)):
        if geom.geom_type == 'GeometryCollection':
            # Extract polygons and multipolygons from the collection
            polys = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
            if not polys:
                raise ValueError("GeometryCollection contains no polygons.")
            if len(polys) == 1:
                return polys[0]
            return MultiPolygon(polys)
        raise ValueError(f"Expected Polygon or MultiPolygon, got {geom.geom_type}")
    return geom

def reproject_polygon(polygon: Union[Polygon, MultiPolygon], target_crs: str = 'EPSG:2154') -> Union[Polygon, MultiPolygon]:
    """Reprojects a polygon or multipolygon to the target CRS."""
    if target_crs == 'EPSG:2154':
        return transform(project_to_2154, polygon)
    elif target_crs == 'EPSG:4326':
        return transform(project_to_4326, polygon)
    else:
        raise ValueError(f"Unsupported target CRS: {target_crs}")

def validate_area(polygon_2154: Union[Polygon, MultiPolygon], max_ha: float = 1000000.0) -> bool:
    """Validates that the polygon area is within acceptable limits (default 1M ha)."""
    area_m2 = polygon_2154.area
    area_ha = area_m2 / 10000.0
    return area_ha <= max_ha
