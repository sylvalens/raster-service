from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List, Literal

class PolygonInput(BaseModel):
    geometry: Dict[str, Any] = Field(..., description="GeoJSON Geometry object")

class RasterStatsResponse(BaseModel):
    variable: str
    year: int
    mean: float
    min: float
    max: float
    std: float
    median: float
    unit: str

class FormsStatsResponse(BaseModel):
    agbd: RasterStatsResponse
    height: RasterStatsResponse
    wvd: RasterStatsResponse

class HansenStatsResponse(BaseModel):
    treecover2000_mean: float
    loss_area_ha_by_year: Dict[int, float]
    gain_area_ha: float
    net_change_ha: float

class LossPixelFeature(BaseModel):
    type: Literal['Feature'] = 'Feature'
    geometry: Dict[str, Any]
    properties: Dict[str, Any]

class LossPixelsResponse(BaseModel):
    type: Literal['FeatureCollection'] = 'FeatureCollection'
    features: List[LossPixelFeature]

class LidarStatsResponse(BaseModel):
    mean_height: float
    max_height: float
    p50: float
    p75: float
    p95: float
    point_density: float


class FormsHeightGridCellProperties(BaseModel):
    id: str
    meanHeightM: float
    row: int
    col: int


class FormsHeightGridCell(BaseModel):
    type: Literal['Feature'] = 'Feature'
    geometry: Dict[str, Any]
    properties: FormsHeightGridCellProperties


class FormsHeightGridMeta(BaseModel):
    year: int
    cellSizeM: float
    cellCount: int
    unit: str = 'm'


class FormsHeightGridResponse(BaseModel):
    type: Literal['FeatureCollection'] = 'FeatureCollection'
    features: List[FormsHeightGridCell]
    meta: FormsHeightGridMeta
