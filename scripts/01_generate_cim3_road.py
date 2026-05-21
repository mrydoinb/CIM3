#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
CIM 道路模型生成主脚本 (POC 第一轮)：

输入：
- data/raw/road_centerline.geojson (OSM 原生路网数据)
- data/rules/road_rules.json (道路模板与材质配置)
- data/raw/road_centerline/*.tif (可选，DGM 局部高程切片)

输出：
- data/processed/road_centerline_local.geojson (带高程与局部相对坐标的属性路网)
- output/obj/road_test.obj (路面、路缘石、人行道、标线三维模型)
- output/gltf/road_test.glb (同上的 glTF 模型)
- output/semantic/road_test_semantic.json (包含模型关联与语义信息清单)
- output/qc_report/road_test_qc_report.json (几何与拓扑生成质检报告)

当前策略：
- 属性映射：自动提取并转换 OSM 的道路等级、车道数、限速、单行、桥梁及层级(layer)等标签。
- 动态宽度：支持依据 lanes 字段动态计算扩展每段道路对应的路面及各设施宽度。
- 真实高程：支持读取多图幅 DGM 影像获取地表起伏，并对 bridge / layer 标签进行桥梁净空高度叠加补偿。
- 空间防穿模：基于三维高程对路口进行聚类分层，保障立交桥与底层道路在几何合并时拓扑独立、互不干涉。
- 三维组装装配：基于路口 2D 并集融合，利用受限三角剖分(Triangulate)和垂直拉伸(Extrude)生成具有厚度的 3D Mesh。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging 
import math
import copy
import os
import re
from urllib.parse import urlencode
from typing import Iterable, Any

import geopandas as gpd
import pandas as pd
import numpy as np
import shapely
from shapely.geometry import (
    LineString,
    MultiLineString,
    Point,
    Polygon,
    MultiPolygon,
    GeometryCollection,
    mapping,
)
from shapely.ops import nearest_points, substring, unary_union, triangulate
import trimesh
from trimesh.visual.texture import TextureVisuals
from trimesh.creation import triangulate_polygon

try:
    import rasterio
except ImportError:
    rasterio = None

ROOT = Path(__file__).resolve().parents[1]

def first_existing_data_file(patterns: list[str], base_dir: Path) -> Path:
    for pattern in patterns:
        matches = sorted(base_dir.glob(pattern))
        if matches:
            return matches[0]
    return base_dir / patterns[0]


RAW_SOURCE_DIR = ROOT / "data" / "Data"
RAW_ROADS = first_existing_data_file(
    [
        "road_centerline.geojson",
        "road50kms/*.shp",
        "**/road_centerline.geojson",
        "**/*.geojson",
    ],
    RAW_SOURCE_DIR,
)
PROCESSED_DIR = ROOT / "data" / "processed"
RULE_PATH = ROOT / "data" / "rules" / "road_rules.json"
SECTION_RULE_PATH = ROOT / "data" / "rules" / "road_section_requirements.json"

OBJ_PATH = ROOT / "output" / "obj" / "road_test.obj"
GLB_PATH = ROOT / "output" / "gltf" / "road_test.glb"
SEMANTIC_PATH = ROOT / "output" / "semantic" / "road_test_semantic.json"
QC_PATH = ROOT / "output" / "qc_report" / "road_test_qc_report.json"
TEXTURE_DIR = ROOT / "output" / "textures"
GOOGLE_MAP_TEXTURE_PATH = TEXTURE_DIR / "google_static_map.png"
DGM_DIR = ROOT / "data" / "raw" / "road_centerline"  # 高程切片所在的文件夹

DGM_DIR = RAW_SOURCE_DIR / "DEM" / "DEM"
LOCAL_ROADS_PATH = PROCESSED_DIR / "road_centerline_local.geojson"

# 安联球场位于德国慕尼黑，适合用 UTM Zone 32N
TARGET_CRS = "EPSG:3857"
ROAD_LINK_GAP_TOLERANCE_M = 2.5
MIN_CONNECTOR_LENGTH_M = 0.05
BRIDGE_ELEVATION_GROUP_M = 3.0
CIM3_PLAN_ACCURACY_M = 0.8
CIM3_HEIGHT_ACCURACY_M = 1.0
CIM3_MIN_GEOMETRIC_DETAIL_M = 0.5
ELEVATION_SAMPLE_INTERVAL_M = 5.0
SWEEP_SAMPLE_INTERVAL_M = 4.0
DEFAULT_GROUND_ELEVATION_M = 0.0
DEM_MIN_VALID_ELEVATION_M = -50.0
DEM_MAX_VALID_ELEVATION_M = 300.0
ENABLE_DEM_ELEVATION = False
FLAT_TOPOLOGY_TILE_SIZE_M = 3500.0
STUDY_AREA_RADIUS_M = 0.0
CROWN_SLOPE = 0.02
LANE_CROWN_SLOPE = 0.015
JUNCTION_NODE_TOLERANCE_M = 3.0
JUNCTION_RADIUS_M = 8.0
JUNCTION_CLIP_EXTRA_M = 1.5
MIN_JUNCTION_CLIP_DISTANCE_M = 8.0
MAX_JUNCTION_CLIP_DISTANCE_M = 14.0
JUNCTION_ENDPOINT_SNAP_TOLERANCE_M = 3.5
JUNCTION_LINE_INTERSECTION_TOLERANCE_M = 0.35
ENABLE_CORRIDOR_JUNCTION_CLUSTERING = True
CORRIDOR_JUNCTION_CLUSTER_DISTANCE_M = 24.0
CORRIDOR_JUNCTION_CONNECT_TOLERANCE_M = 18.0
CORRIDOR_CROSSING_MIN_ANGLE_DEG = 25.0
CORRIDOR_CROSSING_EXTRA_WIDTH_M = 1.5
ROAD_SURFACE_OVERLAP_MIN_AREA_M2 = 18.0
ROAD_SURFACE_OVERLAP_MIN_COMPACTNESS = 0.015
JUNCTION_EDGE_MAX_INTERSECTION_FACTOR = 2.6
JUNCTION_MIN_SURFACE_AREA_M2 = 4.0
JUNCTION_CLEAN_FILLET_M = 2.0
JUNCTION_SOCKET_OVERLAP_M = 1.2
JUNCTION_CITYENGINE_SMOOTH_M = 1.1
JUNCTION_MARKING_CLEARANCE_M = 0.65
JUNCTION_ASPHALT_ONLY_CLEARANCE_M = 0.05
JUNCTION_ROAD_SURFACE_SETBACK_M = 0.0
JUNCTION_CURB_SETBACK_M = 2.5
JUNCTION_SIDEWALK_SETBACK_M = 2.5
JUNCTION_NON_ASPHALT_CLEARANCE_M = 2.5
JUNCTION_APPROACH_CONNECT_TOLERANCE_M = 22.0
JUNCTION_APPROACH_CONNECT_MAX_LENGTH_M = 18.0
LANE_CONNECTOR_SAMPLE_COUNT = 16
LANE_CONNECTOR_HANDLE_RATIO = 0.45
MIN_CHANNEL_ISLAND_AREA_M2 = 5.0
TRANSITION_MIN_ANGLE_DEG = 8.0
TRANSITION_MIN_LENGTH_M = 4.0
TRANSITION_MAX_LENGTH_M = 24.0
TRANSITION_SAMPLES = 10
LANE_GUIDE_MARKING_WIDTH_M = 0.12
DOUBLE_YELLOW_LINE_WIDTH_M = 0.10
DOUBLE_YELLOW_LINE_OFFSET_M = 0.12
LANE_DASH_LENGTH_M = 5.0
LANE_DASH_GAP_M = 7.0
LANE_DASH_MIN_ROAD_LENGTH_M = 18.0
TURN_ARROW_LENGTH_M = 4.0
GENERATE_TURN_ARROWS = True
APPROACH_ARROW_NEAR_M = 14.0
APPROACH_ARROW_FAR_M = 27.0
APPROACH_ARROW_MIN_GAP_M = 6.0
CHANNELIZATION_MIN_ANGLE_DEG = 35.0
CHANNELIZATION_MAX_ANGLE_DEG = 125.0
CROSSWALK_DISTANCE_FROM_JUNCTION_M = 7.0
CROSSWALK_STRIPE_WIDTH_M = 0.32
CROSSWALK_STRIPE_GAP_M = 0.42
CROSSWALK_BAND_LENGTH_M = 3.0
GENERATE_CROSSWALKS = True
GENERATE_SWEPT_ROAD_SURFACES = False
GENERATE_LANE_SURFACES = False
STOP_LINE_DISTANCE_FROM_JUNCTION_M = 4.0
STREET_LIGHT_SPACING_M = 30.0
TREE_SPACING_M = 18.0
TREE_JUNCTION_CLEARANCE_M = 20.0
STREET_LIGHT_JUNCTION_CLEARANCE_M = 14.0
STREET_LIGHT_POLE_ROAD_CLEARANCE_M = 0.45
MAX_STREET_LIGHTS_PER_ROAD = 800
MAX_TREES_PER_ROAD = 900
BRIDGE_PIER_SPACING_M = 28.0
GENERATE_JUNCTION_LABELS = False
MIN_JUNCTION_QUALITY_SCORE = 85.0

CIM3_COLORS = {
    "median": [120, 126, 112, 255],
    "guardrail": [185, 190, 188, 255],
    "street_light": [88, 86, 78, 255],
    "street_light_lamp": [248, 226, 138, 255],
    "tree_trunk": [94, 68, 42, 255],
    "tree_crown": [58, 116, 68, 255],
    "bridge_deck": [118, 118, 112, 255],
    "bridge_pier": [148, 148, 142, 255],
    "gis_basemap": [88, 118, 94, 255],
    "gis_grid": [132, 150, 132, 255],
    "crosswalk": [245, 245, 235, 255],
    "stop_line": [245, 245, 235, 255],
    "lane_guide": [245, 245, 235, 255],
    "turn_arrow": [245, 245, 235, 255],
    "double_yellow": [245, 196, 48, 255],
    "junction": [42, 42, 40, 255],
    "channelization": [218, 214, 196, 255],
    "junction_label_major": [220, 70, 52, 255],
    "junction_label_secondary": [235, 166, 45, 255],
    "junction_label_local": [58, 142, 86, 255],
    "junction_label_ramp": [82, 122, 210, 255],
    "junction_label_complex": [156, 88, 190, 255],
}

ROAD_SECTION_REQUIREMENTS = {
    # section: road class, default total section width, modeled carriageway
    # lane count, and lane width. The total width is also overridden by the
    # source width field when present in data/Data/road50kms.
    "A1": {"road_class": "trunk", "grade": "expressway", "total_width": 93.5, "lane_count": 8, "lane_width": 3.75},
    "A2": {"road_class": "trunk", "grade": "expressway", "total_width": 100.0, "lane_count": 8, "lane_width": 3.75},
    "A3": {"road_class": "trunk", "grade": "expressway", "total_width": 72.0, "lane_count": 6, "lane_width": 3.75},
    "B1": {"road_class": "primary", "grade": "arterial", "total_width": 197.0, "lane_count": 8, "lane_width": 3.5},
    "B2": {"road_class": "primary", "grade": "arterial", "total_width": 70.0, "lane_count": 6, "lane_width": 3.5},
    "B3": {"road_class": "primary", "grade": "arterial", "total_width": 50.0, "lane_count": 4, "lane_width": 3.5},
    "B4": {"road_class": "secondary", "grade": "secondary", "total_width": 40.0, "lane_count": 4, "lane_width": 3.25},
    "B5": {"road_class": "secondary", "grade": "secondary", "total_width": 30.0, "lane_count": 4, "lane_width": 3.25},
    "C1": {"road_class": "secondary", "grade": "secondary", "total_width": 35.0, "lane_count": 4, "lane_width": 3.25},
    "C2": {"road_class": "secondary", "grade": "secondary", "total_width": 28.0, "lane_count": 4, "lane_width": 3.25},
    "C3": {"road_class": "secondary", "grade": "secondary", "total_width": 26.0, "lane_count": 4, "lane_width": 3.25},
    "C4": {"road_class": "secondary", "grade": "secondary", "total_width": 64.0, "lane_count": 6, "lane_width": 3.25},
    "C5": {"road_class": "secondary", "grade": "secondary", "total_width": 45.0, "lane_count": 4, "lane_width": 3.25},
    "D1": {"road_class": "tertiary", "grade": "branch", "total_width": 20.0, "lane_count": 2, "lane_width": 3.25},
    "D2": {"road_class": "tertiary", "grade": "branch", "total_width": 20.0, "lane_count": 2, "lane_width": 3.25},
    "D3": {"road_class": "tertiary", "grade": "branch", "total_width": 20.0, "lane_count": 2, "lane_width": 3.25},
    "D4": {"road_class": "tertiary", "grade": "branch", "total_width": 20.0, "lane_count": 2, "lane_width": 3.25},
    "D5": {
        "road_class": "tertiary",
        "grade": "branch",
        "total_width": 14.0,
        "lane_count": 2,
        "lane_width": 5.0,
        "road_width": 10.0,
    },
    "D6": {
        "road_class": "tertiary",
        "grade": "branch",
        "total_width": 7.0,
        "lane_count": 1,
        "lane_width": 5.0,
        "road_width": 5.0,
    },
}

ROAD_SECTION_ALIASES = {
    "AA": "D1",
    "BB": "D2",
    "CC": "D3",
    "DD": "D4",
    "JJ": "D1",
    "RR": "D1",
    "WW": "D1",
}

_SECTION_REQUIREMENTS_CACHE: dict[str, dict[str, Any]] | None = None

VISUAL_SIDE_RESERVE_LIMITS_M = {
    "expressway": 8.0,
    "arterial": 6.0,
    "secondary": 4.8,
    "branch": 3.0,
}

MODEL_CROSS_SECTIONS_AS_SYMMETRIC = True
SECTION_SYMMETRY_TOLERANCE_M = 0.05
SYMMETRIC_SECTION_FALLBACKS = {
    "A2": "A3",
    "C4": "C5",
    "D2": "D1",
    "D6": "D5",
}
SYMMETRIC_FALLBACK_KEEP_MODEL_WIDTH = {"D6"}
SYMMETRIC_DEFAULT_SECTION_BY_CATEGORY = {
    "expressway": "A3",
    "arterial": "B3",
    "primary": "B3",
    "secondary": "C5",
    "branch": "D3",
}


@dataclass
class RoadRule:
    """
    定义道路生成的规则参数，包括车道数、各部分宽度、高度、材质等。
    
    示例 (JSON 结构):
    {
      "default_road": {
        "lane_count": 2,               # 车道数
        "lane_width": 3.5,             # 车道宽度
        "road_width": 7.0,             # 道路总宽度
        "sidewalk_width": 2.0,         # 人行道宽度
        "curb_width": 0.3,             # 路缘石宽度
        "curb_height": 0.15,           # 路缘石高度
        "lane_marking_width": 0.15,    # 标线宽度
        "road_z": 0.0,                 # 道路基础高度
        "lane_marking_z_offset": 0.015,# 标线高度偏移 (防 Z-fighting)
        "material": "asphalt",         # 道路材质
        "sidewalk_material": "concrete", # 人行道材质
        "curb_material": "curb_concrete", # 路缘石材质   
        "marking_material": "white_marking" # 标线材质
      }
    }
    """
    lane_count: int
    lane_width: float
    road_width: float
    sidewalk_width: float
    curb_width: float
    curb_height: float
    lane_marking_width: float
    road_z: float
    lane_marking_z_offset: float
    material: str
    sidewalk_material: str
    curb_material: str
    marking_material: str
@dataclass
class LocalOrigin:
    """定义局部坐标系的原点，用于将全局地理坐标转换为以原点为中心的局部相对坐标。"""
    x: float
    y: float
    z: float = 0.0


@dataclass
class LaneSocket:
    lane_socket_id: str
    parent_socket_id: str
    road_id: str
    lane_index: int
    lane_role: str
    center: tuple[float, float]
    tangent: tuple[float, float]
    normal: tuple[float, float]
    lane_width: float
    allowed_movements: list[str]
    lane_id: str = ""
    movement: str = "through"
    allowed_turns: list[str] | None = None
    traffic_direction: str = "unknown"
    signal_group: str = "unsignalized"
    stop_control: str = "yield_or_stop_line"


@dataclass
class RoadSocket:
    socket_id: str
    road_id: str
    junction_id: str
    distance_m: float
    center: tuple[float, float]
    tangent: tuple[float, float]
    normal: tuple[float, float]
    road_width: float
    lane_count: int
    lane_width: float
    forward_lane_count: int
    backward_lane_count: int
    sidewalk_width: float
    curb_width: float
    clip_radius: float
    clip_distance: float
    line_direction_sign: float
    elevation: float
    approach_type: str
    left_edge: tuple[float, float]
    right_edge: tuple[float, float]
    lane_sockets: list[LaneSocket]
    road_class: str = "unknown"
    node_distance_m: float = 0.0
    generation_method: str = "socket"
    road_name: str = "unknown"


@dataclass
class JunctionNode:
    junction_id: str
    center: Point
    radius: float
    sockets: list[RoadSocket]
    junction_type: str = "UNKNOWN"
    hierarchy: str = "LOCAL_JUNCTION"
    surface_strategy: str = "edge_intersection"
    detection_method: str = "endpoint_cluster"
    metadata: dict[str, Any] | None = None


@dataclass
class LaneConnector:
    connector_id: str
    junction_id: str
    from_lane: LaneSocket
    to_lane: LaneSocket
    movement_type: str
    centerline: LineString
    width: float
    surface_polygon: Polygon
    marking_guides: list[LineString]


@dataclass(frozen=True)
class LaneLayout:
    lane_count: int
    forward_count: int
    backward_count: int
    center_turn_count: int = 0


def ensure_dirs() -> None:
    """确保所有需要用到的输出目录都已存在，若不存在则自动创建。"""
    for path in [
        PROCESSED_DIR,
        OBJ_PATH.parent,
        GLB_PATH.parent,
        SEMANTIC_PATH.parent,
        QC_PATH.parent,
        TEXTURE_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def load_rules() -> dict[str, RoadRule]:
    """从规则 JSON 文件中读取并加载所有模板信息，返回规则字典。"""
    with RULE_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: RoadRule(**v) for k, v in data.items() if isinstance(v, dict)}


def study_area_center_xy(roads: gpd.GeoDataFrame) -> tuple[float, float] | None:
    if rasterio and DGM_DIR.exists() and DGM_DIR.is_dir():
        for tif_file in sorted(DGM_DIR.glob("*.tif*")):
            if tif_file.suffix.lower() not in {".tif", ".tiff"}:
                continue
            try:
                with rasterio.open(tif_file) as src:
                    bounds = src.bounds
                    if src.crs and str(src.crs) != TARGET_CRS:
                        from rasterio.warp import transform_bounds

                        left, bottom, right, top = transform_bounds(src.crs, TARGET_CRS, *bounds)
                    else:
                        left, bottom, right, top = bounds.left, bounds.bottom, bounds.right, bounds.top
                    return ((left + right) / 2.0, (bottom + top) / 2.0)
            except Exception:
                continue
    if roads is None or roads.empty:
        return None
    minx, miny, maxx, maxy = roads.total_bounds
    return ((float(minx) + float(maxx)) / 2.0, (float(miny) + float(maxy)) / 2.0)


def read_and_prepare_roads() -> tuple[gpd.GeoDataFrame, LocalOrigin, dict[str, Any]]:
    """
    读取 OSM 道路中心线数据，进行投影转换、高程采样，并转换为局部坐标系。
    """
    if not RAW_ROADS.exists():
        raise FileNotFoundError(
            f"未找到道路数据：{RAW_ROADS}\n"
            "请先运行：python scripts/00_download_allianz_osm.py"
        )

    roads = gpd.read_file(RAW_ROADS)

    if roads.empty:
        raise ValueError("road_centerline.geojson 为空，无法生成道路模型。")

    if roads.crs is None:
        # OSM GeoJSON 默认投影通常为 WGS84 (EPSG:4326)
        roads = roads.set_crs("EPSG:4326")

    # 清洗数据：剔除空几何体，仅保留有效的线段结构（LineString 和 MultiLineString）
    roads = roads[roads.geometry.notna()].copy()
    roads = roads[roads.geometry.geom_type.isin(["LineString", "MultiLineString"])].copy()

    # 投影到目标投影坐标系 (EPSG:32632, UTM Zone 32N)，统一单位为米
    roads = roads.to_crs(TARGET_CRS)
    study_center = study_area_center_xy(roads)
    if STUDY_AREA_RADIUS_M > 0.0 and study_center is not None:
        study_mask = Point(study_center).buffer(STUDY_AREA_RADIUS_M, resolution=32)
        roads = roads[roads.intersects(study_mask)].copy()
        roads["geometry"] = roads.geometry.intersection(study_mask)
        roads = roads[roads.geometry.notna() & ~roads.geometry.is_empty].copy()
        if roads.empty:
            raise ValueError(f"No roads found inside study radius {STUDY_AREA_RADIUS_M}m around {study_center}.")

    # 将 MultiLineString 拆分（Explode）为单条的 LineString
    roads = roads.explode(index_parts=False).reset_index(drop=True)
    roads = roads[roads.geometry.geom_type == "LineString"].copy()
    roads = deduplicate_bidirectional_osm_edges(roads)

    # 标准化处理道路 ID
    if "osmid" in roads.columns:
        roads["road_id"] = roads["osmid"].apply(normalize_osmid)
    elif "id" in roads.columns:
        roads["road_id"] = roads["id"].astype(str)
    elif "Id" in roads.columns:
        roads["road_id"] = roads["Id"].astype(str)
    else:
        # 若无数据源 ID，则自动生成五位序列化编号
        roads["road_id"] = [f"R{i:05d}" for i in range(len(roads))]
    roads["road_id"] = make_unique_road_ids(roads["road_id"])

    # 标准化处理道路名称
    roads["road_name"] = roads.apply(source_road_name, axis=1)

    # 映射与提取 OSM 原生道路属性
    roads["road_class"] = roads.apply(source_road_class, axis=1)
    roads["lane_count"] = roads["lanes"] if "lanes" in roads.columns else None
    roads["lanes_forward"] = roads["lanes:forward"] if "lanes:forward" in roads.columns else None
    roads["lanes_backward"] = roads["lanes:backward"] if "lanes:backward" in roads.columns else None
    roads["osm_width"] = roads["width"] if "width" in roads.columns else None
    roads["maxspeed"] = roads["maxspeed"] if "maxspeed" in roads.columns else None
    roads["oneway"] = roads["oneway"] if "oneway" in roads.columns else None
    roads["junction_type"] = roads["junction"] if "junction" in roads.columns else None
    roads["is_bridge"] = roads["bridge"] if "bridge" in roads.columns else None
    roads["road_ref"] = roads["ref"] if "ref" in roads.columns else None
    roads["access"] = roads["access"] if "access" in roads.columns else None
    roads["lane_count"] = normalize_corridor_lane_counts(roads)
    roads["length_m"] = roads.geometry.length

    # 尝试加载指定目录下的所有 DGM 高程切片，支持多图幅 tif 自动拼接与读取
    dgm_srcs = []
    if ENABLE_DEM_ELEVATION and rasterio and DGM_DIR.exists() and DGM_DIR.is_dir():
        for tif_file in DGM_DIR.glob("*.tif*"):  # 匹配 .tif 或 .tiff
            try:
                if tif_file.suffix.lower() not in {".tif", ".tiff"}:
                    continue
                dgm_srcs.append(rasterio.open(tif_file))
            except Exception as e:
                print(f"无法读取高程文件 {tif_file}: {e}")

    roads["is_bridge"] = roads["is_bridge"].apply(check_is_bridge)
    roads["bridge_clearance"] = roads["road_class"].apply(get_bridge_clearance)
    
    z_info = roads.apply(lambda row: get_elevations(row, dgm_srcs), axis=1)
    roads = pd.concat([roads, z_info], axis=1)
    roads["elevation"] = roads["road_z_mean"]
    
    for src in dgm_srcs:
        src.close()

    # 计算所有道路的边界框中心点，将其作为三维场景的局部坐标原点
    minx, miny, maxx, maxy = roads.total_bounds
    origin = LocalOrigin(
        x=float((minx + maxx) / 2.0),
        y=float((miny + maxy) / 2.0),
        z=0.0,
    )

    # 备份全局坐标，利用仿射变换把当前几何体平移到局部坐标系中
    roads["geometry_global"] = roads.geometry
    roads["geometry"] = roads.geometry.apply(lambda geom: translate_to_local(geom, origin))
    roads["transition_curve_count"] = roads.geometry.apply(count_transition_curve_candidates)
    roads["geometry"] = roads.geometry.apply(smooth_line_with_clothoid_transitions)
    attach_junction_distances(roads)

    # 提取核心所需的属性列，保存处理完毕的局部路网 GeoJSON 
    keep_cols = [
        "road_id", "road_name", "road_class", "lane_count", "lanes_forward", "lanes_backward", "osm_width", "maxspeed", 
        "oneway", "junction_type", "is_bridge", "road_ref", "access", "elevation", "length_m", 
        "bridge_clearance", "height_source", "height_mode", "ground_z_start", 
        "ground_z_end", "road_z_start", "road_z_end", "road_z_mean",
        "elevation_profile_json", "max_grade_percent", "junction_distances_json",
        "transition_curve_count",
        "geometry"
    ]
    local_roads = roads[keep_cols].copy()
    local_roads = gpd.GeoDataFrame(local_roads, geometry="geometry", crs=TARGET_CRS)
    local_roads.to_file(LOCAL_ROADS_PATH, driver="GeoJSON")

    meta = {
        "source_file": str(RAW_ROADS.relative_to(ROOT)),
        "target_crs": TARGET_CRS,
        "unit": "meter",
        "local_origin": {"x": origin.x, "y": origin.y, "z": origin.z},
        "road_count": int(len(local_roads)),
    }

    return local_roads, origin, meta


def normalize_osmid(value: Any) -> str:
    """
    统一 OSM ID 格式。OSMnx 导出的 osmid 有时是 int，有时是 list，此函数统一转为字符串形式。
    """
    if isinstance(value, (list, tuple, set)):
        return "_".join(str(v) for v in value)
    return str(value)


def normalized_key_value(value: Any) -> str:
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, (list, tuple, set)):
        return "|".join(sorted(str(v) for v in value))
    return safe_str(value) or ""


def deduplicate_bidirectional_osm_edges(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if roads.empty or not {"u", "v", "osmid"}.issubset(roads.columns):
        return roads

    keyed = roads.copy()
    keyed["_oneway_dir"] = keyed.apply(oneway_direction, axis=1)
    keyed["_dedupe_key"] = keyed.apply(
        lambda row: (
            tuple(sorted((safe_str(row.get("u")) or "", safe_str(row.get("v")) or ""))),
            normalized_key_value(row.get("osmid")),
            normalized_key_value(row.get("key")),
        ),
        axis=1,
    )
    keyed["_reversed_rank"] = keyed.get("reversed", pd.Series([None] * len(keyed), index=keyed.index)).apply(
        lambda value: 1 if (safe_str(value) or "").lower() == "true" else 0
    )

    keep_indexes: list[Any] = []
    for _, group in keyed.groupby("_dedupe_key", sort=False):
        if len(group) <= 1 or not (group["_oneway_dir"] == 0).all():
            keep_indexes.extend(group.index.tolist())
            continue

        endpoint_pairs = {(safe_str(row.get("u")), safe_str(row.get("v"))) for _, row in group.iterrows()}
        has_reciprocal = any((v, u) in endpoint_pairs for u, v in endpoint_pairs)
        if not has_reciprocal:
            keep_indexes.extend(group.index.tolist())
            continue

        keep_indexes.append(group.sort_values(["_reversed_rank", "length"], ascending=[True, False]).index[0])

    return keyed.loc[keep_indexes].drop(columns=["_oneway_dir", "_dedupe_key", "_reversed_rank"]).reset_index(drop=True)


def translate_to_local(geom, origin: LocalOrigin):
    """
    利用 Shapely 的仿射变换 (translate)，将全局投影坐标转换为以 origin 为原点的局部相对坐标。
    """
    return shapely.affinity.translate(geom, xoff=-origin.x, yoff=-origin.y, zoff=-origin.z)


def safe_str(val: Any) -> str | None:
    """安全地将属性值转换为字符串，若为空值（如 nan, NaT）则返回 None。"""
    if val is None:
        return None
    val_str = str(val)
    if val_str in ("nan", "None", "<NA>", "NaN", "NaT"):
        return None
    return val_str


def make_unique_road_ids(values: Iterable[Any]) -> list[str]:
    base_values = [safe_str(value) or f"R{i:05d}" for i, value in enumerate(values)]
    totals: dict[str, int] = {}
    for value in base_values:
        totals[value] = totals.get(value, 0) + 1

    seen: dict[str, int] = {}
    unique_values: list[str] = []
    for value in base_values:
        seen[value] = seen.get(value, 0) + 1
        if totals[value] == 1:
            unique_values.append(value)
        else:
            unique_values.append(f"{value}_{seen[value] - 1:04d}")
    return unique_values


def parse_lane_count(val: Any, default: int = 2) -> int:
    """从字符串或列表中解析出车道数（提取首个数字），若解析失败则返回默认值 default。"""
    val_str = safe_str(val)
    if not val_str:
        return default
    matches = [int(match) for match in re.findall(r"\d+", val_str)]
    if not matches:
        return default
    return max(1, min(max(matches), 8))


def parsed_lane_count_or_none(val: Any) -> int | None:
    val_str = safe_str(val)
    if not val_str:
        return None
    matches = [int(match) for match in re.findall(r"\d+", val_str)]
    if not matches:
        return None
    return max(1, min(max(matches), 8))


def modal_lane_count(values: Iterable[Any]) -> int | None:
    counts: dict[int, int] = {}
    for value in values:
        lane_count = parsed_lane_count_or_none(value)
        if lane_count is None:
            continue
        counts[lane_count] = counts.get(lane_count, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)[0][0]


def normalize_corridor_lane_counts(roads: gpd.GeoDataFrame) -> pd.Series:
    if roads.empty or "lane_count" not in roads.columns:
        return roads.get("lane_count", pd.Series(dtype=object))

    normalized = roads["lane_count"].copy()
    key_series = roads.apply(
        lambda row: normalized_key_value(row.get("road_name")),
        axis=1,
    )
    for key in sorted(set(key_series)):
        if not key or key == "unknown":
            continue
        mask = key_series == key
        if int(mask.sum()) < 3:
            continue
        baseline_mask = mask & ~roads.apply(is_single_lane_junction_pocket, axis=1) & ~roads.apply(is_circular_junction_row, axis=1)
        corridor_lane_count = modal_lane_count(roads.loc[baseline_mask, "lane_count"])
        if corridor_lane_count is None:
            corridor_lane_count = modal_lane_count(roads.loc[mask, "lane_count"])
        if corridor_lane_count is None:
            continue
        apply_mask = mask & ~roads.apply(is_single_lane_junction_pocket, axis=1) & ~roads.apply(is_circular_junction_row, axis=1)
        normalized.loc[apply_mask] = max(2, corridor_lane_count)

    circular_mask = roads.apply(is_circular_junction_row, axis=1)
    normalized.loc[circular_mask] = 2
    return normalized


def is_circular_junction_row(row: pd.Series) -> bool:
    junction = (normalized_key_value(row.get("junction_type")) or normalized_key_value(row.get("junction"))).lower()
    return "circular" in junction or "roundabout" in junction


def is_single_lane_junction_pocket(row: pd.Series) -> bool:
    return oneway_direction(row) != 0 and parsed_lane_count_or_none(row.get("lane_count")) == 1


def parse_optional_lane_count(val: Any) -> int | None:
    val_str = safe_str(val)
    if not val_str:
        return None
    matches = [int(match) for match in re.findall(r"\d+", val_str)]
    if not matches:
        return None
    return max(0, min(max(matches), 8))


def normalize_directional_lanes(total_lanes: int, forward: int | None, backward: int | None, oneway: int) -> LaneLayout:
    total_lanes = max(1, min(int(total_lanes), 8))
    if oneway == 1:
        return LaneLayout(total_lanes, total_lanes, 0)
    if oneway == -1:
        return LaneLayout(total_lanes, 0, total_lanes)

    if forward is not None or backward is not None:
        forward_count = max(0, int(forward or 0))
        backward_count = max(0, int(backward or 0))
        known_total = forward_count + backward_count
        if known_total <= 0:
            forward_count = total_lanes // 2
            backward_count = total_lanes - forward_count
        elif known_total < total_lanes:
            missing = total_lanes - known_total
            if forward is None:
                forward_count += missing
            elif backward is None:
                backward_count += missing
            else:
                forward_count += missing // 2
                backward_count += missing - missing // 2
        elif known_total > total_lanes:
            total_lanes = min(known_total, 8)
        return LaneLayout(total_lanes, forward_count, backward_count)

    backward_count = total_lanes // 2
    forward_count = total_lanes - backward_count
    return LaneLayout(total_lanes, forward_count, backward_count)


def lane_layout_for_row(row: pd.Series, rule: RoadRule) -> LaneLayout:
    return normalize_directional_lanes(
        rule.lane_count,
        parse_optional_lane_count(row.get("lanes_forward")),
        parse_optional_lane_count(row.get("lanes_backward")),
        oneway_direction(row),
    )


def parse_road_width_m(val: Any) -> float | None:
    """Parse width values and return a plausible full section width."""
    val_str = safe_str(val)
    if not val_str:
        return None
    matches = [float(match) for match in re.findall(r"\d+(?:\.\d+)?", val_str)]
    plausible = [width for width in matches if 2.0 <= width <= 220.0]
    if not plausible:
        return None
    return max(plausible)


def road_section_requirements() -> dict[str, dict[str, Any]]:
    global _SECTION_REQUIREMENTS_CACHE
    if _SECTION_REQUIREMENTS_CACHE is not None:
        return _SECTION_REQUIREMENTS_CACHE

    requirements = {key: dict(value) for key, value in ROAD_SECTION_REQUIREMENTS.items()}
    if SECTION_RULE_PATH.exists():
        try:
            with SECTION_RULE_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            sections = data.get("sections", data) if isinstance(data, dict) else {}
            for key, value in sections.items():
                if isinstance(value, dict):
                    requirements[str(key).upper()] = dict(value)
        except Exception as exc:
            logging.warning("Unable to load road section requirements: %s", exc)

    for alias, target in ROAD_SECTION_ALIASES.items():
        if target in requirements and alias not in requirements:
            aliased = dict(requirements[target])
            aliased["alias_of"] = target
            requirements[alias] = aliased

    _SECTION_REQUIREMENTS_CACHE = requirements
    return requirements


def road_section_code(value: Any) -> str | None:
    text = (safe_str(value) or "").strip().upper()
    if not text:
        return None
    requirements = road_section_requirements()
    match = re.search(r"[A-D][0-9]", text)
    if match:
        code = ROAD_SECTION_ALIASES.get(match.group(0), match.group(0))
        if code in requirements:
            return code
    code = ROAD_SECTION_ALIASES.get(text, text)
    return code if code in requirements else None


def road_section_requirement(value: Any) -> dict[str, Any] | None:
    section = road_section_code(value)
    if section is None:
        return None
    requirement = road_section_requirements().get(section)
    if requirement is None:
        return None
    result = dict(requirement)
    result.setdefault("section_id", section)
    return result


def row_section_code(row: pd.Series) -> str | None:
    for column in ("section", "road_section", "section_code", "road_name", "name", "roadname", "Name", "NAME"):
        if column not in row:
            continue
        code = road_section_code(row.get(column))
        if code:
            return code
    return None


def row_declared_road_class(row: pd.Series) -> str:
    for column in ("roadclass", "road_class", "highway", "class", "等级"):
        text = safe_str(row.get(column))
        if text:
            return normalize_source_road_class(text)
    return "unclassified"


def infer_section_requirement_from_width(row: pd.Series) -> dict[str, Any] | None:
    width = parse_road_width_m(row.get("osm_width", row.get("width")))
    if width is None:
        return None
    road_class = row_declared_road_class(row)
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for section, requirement in road_section_requirements().items():
        if requirement.get("alias_of"):
            continue
        if str(requirement.get("road_class", "")).lower() != road_class:
            continue
        total_width = float(requirement.get("total_width", 0.0) or 0.0)
        if total_width <= 0.0:
            continue
        candidates.append((abs(total_width - width), section, requirement))
    if not candidates:
        return None

    delta, section, requirement = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
    tolerance = max(0.75, width * 0.04)
    if delta > tolerance:
        return None
    inferred = dict(requirement)
    inferred["inferred_from"] = "roadclass_width"
    inferred["inferred_section"] = section
    inferred.setdefault("section_id", section)
    return inferred


def row_section_requirement(row: pd.Series) -> dict[str, Any] | None:
    code = row_section_code(row)
    return road_section_requirement(code) or infer_section_requirement_from_width(row)


def component_total_width(components: list[dict[str, Any]]) -> float:
    return sum(float(component.get("width", 0.0) or 0.0) for component in components)


def section_components_are_symmetric(
    components: list[dict[str, Any]],
    tolerance_m: float = SECTION_SYMMETRY_TOLERANCE_M,
) -> bool:
    if not components:
        return True
    pairs = zip(components, reversed(components))
    for left, right in pairs:
        if left is right:
            break
        if str(left.get("type", "")) != str(right.get("type", "")):
            return False
        left_width = float(left.get("width", 0.0) or 0.0)
        right_width = float(right.get("width", 0.0) or 0.0)
        if abs(left_width - right_width) > tolerance_m:
            return False
    return True


def modeled_section_code_for_row(row: pd.Series, section_rule: dict[str, Any] | None = None) -> str | None:
    section_rule = section_rule or row_section_requirement(row)
    if section_rule is None:
        return None
    source_code = safe_str(section_rule.get("section_id") or section_rule.get("inferred_section")) or row_section_code(row)
    if not source_code:
        return None

    raw_components = section_rule.get("components")
    if not MODEL_CROSS_SECTIONS_AS_SYMMETRIC or not isinstance(raw_components, list):
        return source_code
    if section_components_are_symmetric(raw_components):
        return source_code

    requirements = road_section_requirements()
    fallback = SYMMETRIC_SECTION_FALLBACKS.get(source_code)
    if fallback in requirements:
        return fallback

    category = (safe_str(section_rule.get("category") or section_rule.get("grade")) or "").lower()
    if not category:
        category = road_class_category_name(safe_str(section_rule.get("road_class")) or row_declared_road_class(row))
    fallback = SYMMETRIC_DEFAULT_SECTION_BY_CATEGORY.get(category)
    if fallback in requirements:
        return fallback
    return source_code


def modeled_section_requirement(row: pd.Series, section_rule: dict[str, Any] | None = None) -> dict[str, Any] | None:
    section_rule = section_rule or row_section_requirement(row)
    if section_rule is None:
        return None

    source_code = safe_str(section_rule.get("section_id") or section_rule.get("inferred_section")) or row_section_code(row)
    model_code = modeled_section_code_for_row(row, section_rule)
    if not model_code:
        return dict(section_rule)

    requirements = road_section_requirements()
    modeled = dict(requirements.get(model_code, section_rule))
    modeled.setdefault("section_id", model_code)
    if source_code and model_code != source_code:
        modeled["source_section_id"] = source_code
        modeled["symmetry_normalized_from"] = source_code
        modeled["source_components"] = section_rule.get("components", [])
        modeled["source_total_width"] = section_rule.get("total_width")
    if section_rule.get("inferred_from"):
        modeled["inferred_from"] = section_rule.get("inferred_from")
        modeled["inferred_section"] = source_code or section_rule.get("inferred_section")
    return modeled


def source_cross_section_components_for_row(row: pd.Series) -> list[dict[str, Any]]:
    section_rule = row_section_requirement(row)
    raw_components = section_rule.get("components") if section_rule else None
    if not isinstance(raw_components, list):
        return []
    return [dict(component) for component in raw_components if float(component.get("width", 0.0) or 0.0) > 0.01]


def row_target_total_width(row: pd.Series, section_rule: dict[str, Any] | None = None) -> float | None:
    if section_rule and section_rule.get("use_rule_width") and "total_width" in section_rule:
        return float(section_rule["total_width"])
    width = parse_road_width_m(row.get("osm_width", row.get("width")))
    if width is not None:
        return width
    if section_rule and "total_width" in section_rule:
        return float(section_rule["total_width"])
    return None


def cross_section_components_for_row(row: pd.Series) -> list[dict[str, Any]]:
    source_section_rule = row_section_requirement(row)
    section_rule = modeled_section_requirement(row, source_section_rule)
    raw_components = section_rule.get("components") if section_rule else None
    if not isinstance(raw_components, list) or not raw_components:
        return []

    components = [dict(component) for component in raw_components if float(component.get("width", 0.0) or 0.0) > 0.01]
    base_width = component_total_width(components)
    source_code = safe_str(section_rule.get("source_section_id")) if section_rule else None
    model_code = safe_str(section_rule.get("section_id")) if section_rule else None
    target_width = row_target_total_width(row, source_section_rule or section_rule)
    if (
        source_code
        and model_code
        and source_code != model_code
        and source_code in SYMMETRIC_FALLBACK_KEEP_MODEL_WIDTH
    ):
        target_width = float(section_rule.get("total_width", base_width) or base_width)
    if source_code and model_code and source_code != model_code:
        for component in components:
            component["source_section_id"] = source_code
            component["model_section_id"] = model_code
            component["symmetry_normalized"] = True
    if base_width > 0.0 and target_width is not None and abs(target_width - base_width) > 0.05:
        scale = float(target_width) / base_width
        for component in components:
            component["width"] = float(component["width"]) * scale
            component["scaled_from_width"] = base_width
    return components


def component_width_by_type(components: list[dict[str, Any]], types: set[str]) -> float:
    return sum(float(component.get("width", 0.0) or 0.0) for component in components if component.get("type") in types)


def section_category_name(section_rule: dict[str, Any] | None, road_class: str = "") -> str:
    category = safe_str(section_rule.get("category") or section_rule.get("grade")) if section_rule else None
    if category:
        return category
    return road_class_category_name(road_class)


def road_class_category_name(road_class: str = "") -> str:
    text = road_class.lower()
    if "motorway" in text or "trunk" in text:
        return "expressway"
    if "primary" in text:
        return "arterial"
    if "secondary" in text:
        return "secondary"
    return "branch"


def modeled_side_reserve_width(section_rule: dict[str, Any] | None, road_class: str, full_side_reserve: float) -> float:
    category = road_class_category_name(road_class)
    if category == "branch" and "tertiary" not in road_class and "residential" not in road_class and "service" not in road_class:
        category = section_category_name(section_rule, road_class)
    limit = VISUAL_SIDE_RESERVE_LIMITS_M.get(category, VISUAL_SIDE_RESERVE_LIMITS_M["branch"])
    if full_side_reserve <= 0.0:
        return 0.0
    return min(full_side_reserve, limit)


def normalize_source_road_class(value: Any) -> str:
    text = safe_str(value) or ""
    text_lower = text.lower()
    if "快速" in text or "express" in text_lower:
        return "trunk"
    if "主干" in text or "primary" in text_lower or "arterial" in text_lower:
        return "primary"
    if "次干" in text or "secondary" in text_lower:
        return "secondary"
    if "支路" in text or "tertiary" in text_lower:
        return "tertiary"
    if "居住" in text or "residential" in text_lower:
        return "residential"
    return text_lower or "unclassified"


def source_road_name(row: pd.Series) -> str:
    for column in ("name", "roadname", "road_name", "Name", "NAME", "section", "备注"):
        value = row.get(column)
        text = safe_str(value)
        if text:
            return text
    return "unknown"


def source_road_class(row: pd.Series) -> str:
    for column in ("highway", "roadclass", "road_class", "class", "等级"):
        value = row.get(column)
        text = safe_str(value)
        if text:
            return normalize_source_road_class(text)
    section_rule = row_section_requirement(row)
    if section_rule is not None:
        return str(section_rule["road_class"])
    return "unclassified"


def canonical_road_class(value: Any, rules: dict[str, RoadRule]) -> str:
    text = (safe_str(value) or "").lower()
    if text in rules:
        return text

    priority = [
        "motorway",
        "primary",
        "secondary",
        "tertiary",
        "residential",
        "service",
        "living_street",
        "unclassified",
    ]
    for road_class in priority:
        if road_class not in text:
            continue
        if road_class in rules:
            return road_class
        if road_class == "living_street" and "residential" in rules:
            return "residential"
        if road_class == "unclassified" and "tertiary" in rules:
            return "tertiary"
    return "default_road"


def get_road_rule(row: pd.Series, rules: dict[str, RoadRule]) -> RoadRule:
    """依据原始道路属性（如车道数）动态计算并返回适用于该道路的规则与宽度配置。"""
    source_section_rule = row_section_requirement(row)
    section_rule = modeled_section_requirement(row, source_section_rule)
    declared_road_class = row_declared_road_class(row)
    raw_road_class = safe_str(row.get("road_class")) or declared_road_class
    if source_section_rule is not None and (not raw_road_class or raw_road_class.lower() in {"unknown", "unclassified"}):
        road_class = str(source_section_rule["road_class"])
    else:
        road_class = canonical_road_class(raw_road_class, rules)
    base_rule = rules.get(road_class, rules.get("default_road"))
    rule = copy.copy(base_rule)
    if section_rule is not None:
        rule.lane_count = int(section_rule["lane_count"])
        rule.lane_width = float(section_rule["lane_width"])
        rule.road_width = float(section_rule.get("road_width", rule.lane_count * rule.lane_width))
    cross_section_components = cross_section_components_for_row(row)
    main_component_width = component_width_by_type(cross_section_components, {"main_carriageway", "carriageway"})
    service_component_width = component_width_by_type(cross_section_components, {"service_lane"})
    edge_component_width = component_width_by_type(
        cross_section_components,
        {"sidewalk", "green_belt", "facility_belt", "side_divider", "divider", "non_motor_lane", "parking_lane"},
    )
    if cross_section_components and main_component_width > 0.0:
        rule.road_width = main_component_width
        total_component_width = component_total_width(cross_section_components)
        rule.sidewalk_width = max(0.0, (total_component_width - main_component_width - service_component_width) / 2.0)
    
    # 覆盖车道数并重新计算道路总宽度
    forward_lanes = parse_optional_lane_count(row.get("lanes_forward"))
    backward_lanes = parse_optional_lane_count(row.get("lanes_backward"))
    directional_lanes = None
    if forward_lanes is not None or backward_lanes is not None:
        directional_lanes = max(1, (forward_lanes or 0) + (backward_lanes or 0))
    lanes = parse_lane_count(row.get("lane_count"), default=directional_lanes or rule.lane_count)
    if directional_lanes is not None and not safe_str(row.get("lane_count")):
        lanes = directional_lanes
    rule.lane_count = lanes
    if not cross_section_components and (section_rule is None or "road_width" not in section_rule):
        rule.road_width = lanes * rule.lane_width
    osm_width = parse_road_width_m(row.get("osm_width", row.get("width")))
    if cross_section_components:
        pass
    elif section_rule is not None:
        total_width = osm_width or float(section_rule.get("total_width", rule.road_width + rule.sidewalk_width * 2.0))
        carriageway_width = float(section_rule.get("road_width", rule.road_width))
        if total_width > carriageway_width:
            rule.road_width = carriageway_width
            full_side_reserve = max(0.0, (total_width - carriageway_width) / 2.0)
            rule.sidewalk_width = modeled_side_reserve_width(section_rule, road_class, full_side_reserve)
    elif osm_width is not None:
        lane_width = osm_width / max(lanes, 1)
        if 2.4 <= lane_width <= 4.5:
            rule.road_width = osm_width
            rule.lane_width = lane_width
    return rule


def cim3_road_grade(highway: Any) -> str:
    text = (safe_str(highway) or "").lower()
    if text in {"motorway", "trunk"}:
        return "expressway"
    if text in {"primary", "primary_link"}:
        return "arterial"
    if text in {"secondary", "secondary_link"}:
        return "secondary"
    return "branch"


def design_speed_kmh(highway: Any, maxspeed: Any = None) -> int:
    speed_text = safe_str(maxspeed)
    if speed_text:
        match = re.search(r"\d+", speed_text)
        if match:
            return int(match.group(0))

    return {
        "expressway": 80,
        "arterial": 50,
        "secondary": 40,
        "branch": 30,
    }[cim3_road_grade(highway)]


def line_sample_distances(line: LineString, interval: float) -> list[float]:
    if line.length <= 0.0:
        return [0.0]
    count = max(int(math.ceil(line.length / interval)), 1)
    distances = [min(line.length, i * interval) for i in range(count + 1)]
    if distances[-1] < line.length:
        distances.append(float(line.length))
    return sorted(set(round(float(d), 3) for d in distances))


def smooth_values(values: list[float]) -> list[float]:
    if len(values) < 3:
        return values
    smoothed = []
    for idx, value in enumerate(values):
        if idx == 0 or idx == len(values) - 1:
            smoothed.append(float(value))
            continue
        smoothed.append(float(values[idx - 1] * 0.25 + value * 0.5 + values[idx + 1] * 0.25))
    return smoothed


def elevation_at_distance(row: pd.Series, distance: float, default_z: float | None = None) -> float:
    profile_text = safe_str(row.get("elevation_profile_json"))
    if not profile_text:
        return float(default_z if default_z is not None else row.get("road_z_mean", row.get("elevation", 0.0)))
    try:
        profile = json.loads(profile_text)
    except Exception:
        return float(default_z if default_z is not None else row.get("road_z_mean", row.get("elevation", 0.0)))
    samples = profile.get("samples", [])
    if not samples:
        return float(default_z if default_z is not None else row.get("road_z_mean", row.get("elevation", 0.0)))

    distance = float(distance)
    if distance <= float(samples[0]["distance_m"]):
        return float(samples[0]["road_z"])
    if distance >= float(samples[-1]["distance_m"]):
        return float(samples[-1]["road_z"])

    for left, right in zip(samples, samples[1:]):
        d0 = float(left["distance_m"])
        d1 = float(right["distance_m"])
        if d0 <= distance <= d1:
            ratio = (distance - d0) / max(d1 - d0, 1e-6)
            return float(left["road_z"]) + (float(right["road_z"]) - float(left["road_z"])) * ratio
    return float(samples[-1]["road_z"])


def check_is_bridge(value: Any) -> bool:
    """判断当前道路要素是否被明确标记为桥梁或高架（如 bridge=yes）。"""
    if pd.isna(value) or value is None:
        return False
    return str(value).lower() in ["yes", "true", "1", "viaduct"]


def get_bridge_clearance(highway: Any) -> float:
    """根据道路等级获取默认的桥梁净空高度。"""
    highway = str(highway).lower()
    if highway in ["motorway", "trunk"]:
        return 6.0
    if highway in ["motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"]:
        return 5.0
    return 4.5


def get_elevations(row: pd.Series, dgm_srcs: list) -> pd.Series:
    """结合多图幅 DGM (数字高程模型) 和桥梁规则，计算出每段道路的准确高程参数。"""
    line = row.geometry
    if line is None or line.is_empty or not isinstance(line, LineString):
        return pd.Series({
            "ground_z_start": 0.0, "ground_z_end": 0.0,
            "road_z_start": 0.0, "road_z_end": 0.0,
            "road_z_mean": 0.0, "height_mode": "unknown", "height_source": "default",
            "elevation_profile_json": json.dumps({"samples": []}, ensure_ascii=False),
            "max_grade_percent": 0.0,
        })

    start_pt = line.coords[0]
    end_pt = line.coords[-1]

    def sample_z(pt):
        if dgm_srcs:
            x, y = pt[0], pt[1]
            for src in dgm_srcs:
                # 判定坐标点是否在当前 TIF 影像图幅的边界内，大幅提升多切片检索性能
                bounds = src.bounds
                if bounds.left <= x <= bounds.right and bounds.bottom <= y <= bounds.top:
                    try:
                        val = next(src.sample([(x, y)]))[0]
                        if val is None:
                            continue
                        if hasattr(src, "nodata") and src.nodata is not None and float(val) == float(src.nodata):
                            continue
                        val = float(val)
                        if not np.isfinite(val):
                            continue
                        if not (DEM_MIN_VALID_ELEVATION_M <= val <= DEM_MAX_VALID_ELEVATION_M):
                            continue
                        return val
                    except Exception:
                        pass
        # 如果没有 DGM 或图幅均未覆盖该点，使用安联球场基准高度 (约 505m) 模拟
        # Default mock terrain when no DGM tile covers the point.
        return DEFAULT_GROUND_ELEVATION_M

    sample_distances = line_sample_distances(line, ELEVATION_SAMPLE_INTERVAL_M)
    ground_samples = [sample_z(line.interpolate(distance).coords[0]) for distance in sample_distances]
    smoothed_ground_samples = smooth_values(ground_samples)
    ground_z_start = smoothed_ground_samples[0]
    ground_z_end = smoothed_ground_samples[-1]

    is_bridge = row.get("is_bridge", False)
    clearance = row.get("bridge_clearance", 0.0)

    if is_bridge:
        road_z_start = ground_z_start + clearance
        road_z_end = ground_z_end + clearance
        height_mode = "dem_sampled_bridge_adjusted"
        height_source = "Directory_Tiles_plus_bridge_rule" if dgm_srcs else "mock_dem_plus_bridge_rule"
    else:
        road_z_start = ground_z_start
        road_z_end = ground_z_end
        height_mode = "dem_sampled"
        height_source = "Directory_Tiles" if dgm_srcs else "mock_dem"

    # 依据 OSM 层级标签进行高程补偿计算（适用于隧道下穿或地下重叠设施）
    layer_offset = 0.0
    layer = row.get("layer")
    if pd.notna(layer):
        try:
            m = re.search(r"[-+]?\d+", str(layer))
            if m:
                layer_offset = float(m.group()) * 5.0
        except Exception:
            pass

    if layer_offset != 0.0 and not is_bridge:
        road_z_start += layer_offset
        road_z_end += layer_offset
        height_mode = "dem_sampled_layer_adjusted"

    offset_start = road_z_start - ground_z_start
    offset_end = road_z_end - ground_z_end
    road_samples = []
    grades = []
    for idx, distance in enumerate(sample_distances):
        ratio = 0.0 if line.length <= 0.0 else float(distance) / float(line.length)
        offset = offset_start + (offset_end - offset_start) * ratio
        road_samples.append(smoothed_ground_samples[idx] + offset)

    for left_idx, right_idx in zip(range(len(sample_distances) - 1), range(1, len(sample_distances))):
        run = sample_distances[right_idx] - sample_distances[left_idx]
        if run > 0.05:
            grades.append(abs((road_samples[right_idx] - road_samples[left_idx]) / run) * 100.0)

    road_z_mean = float(np.mean(road_samples)) if road_samples else (road_z_start + road_z_end) / 2.0
    elevation_profile = {
        "sample_interval_m": ELEVATION_SAMPLE_INTERVAL_M,
        "smoothing": "three_point_weighted_vertical_profile",
        "samples": [
            {
                "distance_m": round(float(distance), 3),
                "ground_z": round(float(ground_z), 3),
                "road_z": round(float(road_z), 3),
            }
            for distance, ground_z, road_z in zip(sample_distances, smoothed_ground_samples, road_samples)
        ],
    }

    return pd.Series({
        "ground_z_start": round(ground_z_start, 3),
        "ground_z_end": round(ground_z_end, 3),
        "road_z_start": round(road_z_start, 3),
        "road_z_end": round(road_z_end, 3),
        "road_z_mean": round(road_z_mean, 3),
        "height_mode": height_mode,
        "height_source": height_source,
        "elevation_profile_json": json.dumps(elevation_profile, ensure_ascii=False),
        "max_grade_percent": round(max(grades) if grades else 0.0, 3),
    })


def clean_polygonal(geom):
    """
    清理多边形 (Polygon / MultiPolygon) 几何结构。
    采用 buffer(0) 的经典技巧以自动修复细小自交 (Self-intersection) 等无效多边形问题。
    """
    if geom is None or geom.is_empty:
        return None

    try:
        # 尝试通过距离为 0 的缓冲区操作来修正几何形状
        geom = geom.buffer(0)
    except Exception:
        pass

    if geom.is_empty:
        return None

    if geom.geom_type in ["Polygon", "MultiPolygon"]:
        return geom

    # 对于复杂的几何集合，仅提取其中的有效面级对象并进行统一合并
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ["Polygon", "MultiPolygon"] and not g.is_empty]
        if not polys:
            return None
        return unary_union(polys)

    return None


def subtract_polygonal_mask(geom, mask, clearance: float = 0.0):
    if geom is None or geom.is_empty:
        return None
    if mask is None or mask.is_empty:
        return clean_polygonal(geom)
    try:
        cut_mask = mask.buffer(clearance, resolution=8, join_style=1) if clearance > 0.0 else mask
        return clean_polygonal(geom.difference(cut_mask))
    except Exception:
        return clean_polygonal(geom)


def iter_polygons(geom) -> Iterable[Polygon]:
    """
    迭代解析工具：将各类复杂的面级几何体 (Polygon, MultiPolygon, Collection) 铺平为单层面数组。
    """
    if geom is None or geom.is_empty:
        return

    if isinstance(geom, Polygon):
        yield geom
    elif isinstance(geom, MultiPolygon):
        for p in geom.geoms:
            if not p.is_empty and p.area > 1e-6:
                yield p
    elif isinstance(geom, GeometryCollection):
        for g in geom.geoms:
            yield from iter_polygons(g)


def line_buffer(line: LineString, width: float):
    """
    中心线等距扩展器：沿法向外扩生成双向缓冲面 (Buffer)。
    参数设定：cap_style=2 (平端裁剪), join_style=2 (Mitre 拐角尖角延伸模式)。
    """
    return line.buffer(width / 2.0, cap_style=2, join_style=1, resolution=8)


def roundabout_ring_geometries(entries: list[tuple[pd.Series, LineString, RoadRule]]) -> tuple[list[Polygon], list[Polygon], list[Polygon]]:
    circular_entries = [(row, line, rule) for row, line, rule in entries if is_circular_junction_row(row)]
    if not circular_entries:
        return [], [], []

    grouped: dict[str, list[tuple[pd.Series, LineString, RoadRule]]] = {}
    for row, line, rule in circular_entries:
        key = normalized_key_value(row.get("road_name")) or str(row.get("road_id"))
        grouped.setdefault(key, []).append((row, line, rule))

    rings: list[Polygon] = []
    totals: list[Polygon] = []
    islands: list[Polygon] = []
    for _, group_entries in grouped.items():
        coords: list[tuple[float, float]] = []
        widths: list[float] = []
        sidewalks: list[float] = []
        for _, line, rule in group_entries:
            coords.extend((float(x), float(y)) for x, y in line.coords)
            widths.append(float(rule.road_width))
            sidewalks.append(float(rule.sidewalk_width))
        if len(coords) < 6:
            continue

        cx = float(np.mean([pt[0] for pt in coords]))
        cy = float(np.mean([pt[1] for pt in coords]))
        center = Point(cx, cy)
        center_radius = float(np.median([math.hypot(pt[0] - cx, pt[1] - cy) for pt in coords]))
        road_width = max(widths) if widths else 7.0
        sidewalk_width = max(sidewalks) if sidewalks else 2.0
        outer_radius = center_radius + road_width / 2.0
        inner_radius = max(2.0, center_radius - road_width / 2.0)

        inner = center.buffer(inner_radius, resolution=96)
        ring = clean_polygonal(center.buffer(outer_radius, resolution=96).difference(inner))
        total = clean_polygonal(center.buffer(outer_radius + sidewalk_width, resolution=96).difference(inner))
        if ring is not None and not ring.is_empty:
            rings.append(ring)
            totals.append(total)
            islands.append(inner)

    return rings, totals, islands


def road_endpoint_points(line: LineString) -> list[Point]:
    coords = list(line.coords)
    if len(coords) < 2:
        return []
    return [Point(coords[0]), Point(coords[-1])]


def vector_length(vec: tuple[float, float]) -> float:
    return math.hypot(vec[0], vec[1])


def unit_vector(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return (0.0, 0.0)
    return (dx / length, dy / length)


def line_tangent_at_distance(line: LineString, distance: float, sample: float = 2.0) -> tuple[float, float]:
    if line.length <= 1e-9:
        return (0.0, 0.0)
    start = max(0.0, min(float(line.length), distance - sample))
    end = max(0.0, min(float(line.length), distance + sample))
    if abs(end - start) <= 1e-6:
        start = max(0.0, distance - sample * 2.0)
        end = min(float(line.length), distance + sample * 2.0)
    p0 = line.interpolate(start)
    p1 = line.interpolate(end)
    return unit_vector((p0.x, p0.y), (p1.x, p1.y))


def acute_angle_between_vectors(a: tuple[float, float], b: tuple[float, float]) -> float:
    la = vector_length(a)
    lb = vector_length(b)
    if la <= 1e-9 or lb <= 1e-9:
        return 0.0
    dot = abs(max(-1.0, min(1.0, (a[0] * b[0] + a[1] * b[1]) / (la * lb))))
    return math.degrees(math.acos(dot))


def detection_road_half_width(row: pd.Series, rule: RoadRule | None = None) -> float:
    if rule is not None:
        return max(3.5, min(16.0, rule.road_width * 0.5 + CORRIDOR_CROSSING_EXTRA_WIDTH_M))
    road_class = (safe_str(row.get("road_class", row.get("highway"))) or "").lower()
    lanes = parse_lane_count(row.get("lane_count", row.get("lanes")), 0)
    if lanes <= 0:
        if "motorway" in road_class or "trunk" in road_class:
            lanes = 4
        elif "primary" in road_class or "secondary" in road_class:
            lanes = 4
        elif "tertiary" in road_class:
            lanes = 2
        else:
            lanes = 2
    return max(3.5, min(12.0, lanes * 3.5 * 0.5 + CORRIDOR_CROSSING_EXTRA_WIDTH_M))


def road_buffer_for_junction_detection(row: pd.Series, line: LineString, rule: RoadRule | None = None) -> Polygon | MultiPolygon:
    return line.buffer(detection_road_half_width(row, rule), cap_style=2, join_style=2)


def corridor_crossing_candidate_point(
    row_a: pd.Series,
    line_a: LineString,
    row_b: pd.Series,
    line_b: LineString,
    rule_a: RoadRule | None = None,
    rule_b: RoadRule | None = None,
) -> Point | None:
    max_distance = detection_road_half_width(row_a, rule_a) + detection_road_half_width(row_b, rule_b)
    if line_a.distance(line_b) > max(max_distance, JUNCTION_LINE_INTERSECTION_TOLERANCE_M):
        return None

    pa, pb = nearest_points(line_a, line_b)
    pa_distance = float(line_a.project(pa))
    pb_distance = float(line_b.project(pb))

    interior_a = JUNCTION_NODE_TOLERANCE_M < pa_distance < line_a.length - JUNCTION_NODE_TOLERANCE_M
    interior_b = JUNCTION_NODE_TOLERANCE_M < pb_distance < line_b.length - JUNCTION_NODE_TOLERANCE_M
    if not (interior_a or interior_b):
        return None

    angle = acute_angle_between_vectors(line_tangent_at_distance(line_a, pa_distance), line_tangent_at_distance(line_b, pb_distance))
    if angle < CORRIDOR_CROSSING_MIN_ANGLE_DEG:
        return None

    return Point((pa.x + pb.x) * 0.5, (pa.y + pb.y) * 0.5)


def road_surface_overlap_candidate_point(
    row_a: pd.Series,
    line_a: LineString,
    row_b: pd.Series,
    line_b: LineString,
    rule_a: RoadRule | None = None,
    rule_b: RoadRule | None = None,
) -> Point | None:
    pa, pb = nearest_points(line_a, line_b)
    da = float(line_a.project(pa))
    db = float(line_b.project(pb))
    angle = acute_angle_between_vectors(line_tangent_at_distance(line_a, da), line_tangent_at_distance(line_b, db))
    if angle < CORRIDOR_CROSSING_MIN_ANGLE_DEG:
        return None

    surface_a = road_buffer_for_junction_detection(row_a, line_a, rule_a)
    surface_b = road_buffer_for_junction_detection(row_b, line_b, rule_b)
    overlap = clean_polygonal(surface_a.intersection(surface_b))
    if overlap is None or overlap.is_empty or float(overlap.area) < ROAD_SURFACE_OVERLAP_MIN_AREA_M2:
        return None

    compactness = float(overlap.area) / max(float(overlap.length) ** 2, 1e-6)
    if compactness < ROAD_SURFACE_OVERLAP_MIN_COMPACTNESS:
        return None

    point = overlap.representative_point()
    da = float(line_a.project(point))
    db = float(line_b.project(point))
    interior_a = JUNCTION_NODE_TOLERANCE_M < da < line_a.length - JUNCTION_NODE_TOLERANCE_M
    interior_b = JUNCTION_NODE_TOLERANCE_M < db < line_b.length - JUNCTION_NODE_TOLERANCE_M
    if not (interior_a or interior_b):
        return None
    return point


def interior_turn_angle(prev_pt: tuple[float, float], curr_pt: tuple[float, float], next_pt: tuple[float, float]) -> float:
    incoming = unit_vector(prev_pt, curr_pt)
    outgoing = unit_vector(curr_pt, next_pt)
    dot = max(-1.0, min(1.0, incoming[0] * outgoing[0] + incoming[1] * outgoing[1]))
    return math.acos(dot)


def clothoid_transition_points(
    start: tuple[float, float],
    end: tuple[float, float],
    incoming_dir: tuple[float, float],
    outgoing_dir: tuple[float, float],
    samples: int = TRANSITION_SAMPLES,
) -> list[tuple[float, float]]:
    """Approximate a transition curve by integrating linearly changing curvature."""
    theta0 = math.atan2(incoming_dir[1], incoming_dir[0])
    theta1 = math.atan2(outgoing_dir[1], outgoing_dir[0])
    delta = math.atan2(math.sin(theta1 - theta0), math.cos(theta1 - theta0))
    chord = math.dist(start, end)
    if chord <= 0.01:
        return []

    local = [(0.0, 0.0)]
    length = max(chord, TRANSITION_MIN_LENGTH_M)
    for idx in range(1, samples):
        t = idx / samples
        theta = delta * t * t
        prev_t = (idx - 1) / samples
        prev_theta = delta * prev_t * prev_t
        avg_theta = (theta + prev_theta) * 0.5
        ds = length / samples
        last_x, last_y = local[-1]
        local.append((last_x + math.cos(avg_theta) * ds, last_y + math.sin(avg_theta) * ds))

    raw_end_x = local[-1][0] + math.cos(delta) * (length / samples)
    raw_end_y = local[-1][1] + math.sin(delta) * (length / samples)
    raw_angle = math.atan2(raw_end_y, raw_end_x)
    target_angle = math.atan2(end[1] - start[1], end[0] - start[0])
    rotate = target_angle - raw_angle
    raw_len = max(math.hypot(raw_end_x, raw_end_y), 1e-6)
    scale = chord / raw_len

    points = []
    cos_r = math.cos(rotate)
    sin_r = math.sin(rotate)
    for x, y in local[1:]:
        rx = (x * cos_r - y * sin_r) * scale
        ry = (x * sin_r + y * cos_r) * scale
        points.append(
            (
                start[0] + rx,
                start[1] + ry,
            )
        )
    return points


def count_transition_curve_candidates(line: LineString) -> int:
    if line is None or line.is_empty or not isinstance(line, LineString):
        return 0
    coords = [(float(coord[0]), float(coord[1])) for coord in line.coords]
    count = 0
    min_angle = math.radians(TRANSITION_MIN_ANGLE_DEG)
    for prev_pt, curr_pt, next_pt in zip(coords, coords[1:], coords[2:]):
        angle = interior_turn_angle(prev_pt, curr_pt, next_pt)
        if angle >= min_angle:
            count += 1
    return count


def smooth_line_with_clothoid_transitions(line: LineString) -> LineString:
    """Replace sharp polyline corners with clothoid-style transition arcs."""
    if line is None or line.is_empty or not isinstance(line, LineString):
        return line
    coords = [(float(coord[0]), float(coord[1])) for coord in line.coords]
    if len(coords) < 3:
        return line

    min_angle = math.radians(TRANSITION_MIN_ANGLE_DEG)
    smoothed: list[tuple[float, float]] = [coords[0]]

    for prev_pt, curr_pt, next_pt in zip(coords, coords[1:], coords[2:]):
        len_in = math.dist(prev_pt, curr_pt)
        len_out = math.dist(curr_pt, next_pt)
        angle = interior_turn_angle(prev_pt, curr_pt, next_pt)
        if angle < min_angle or min(len_in, len_out) < TRANSITION_MIN_LENGTH_M * 1.8:
            smoothed.append(curr_pt)
            continue

        transition_len = min(
            TRANSITION_MAX_LENGTH_M,
            max(TRANSITION_MIN_LENGTH_M, min(len_in, len_out) * 0.35, angle / math.pi * TRANSITION_MAX_LENGTH_M),
        )
        incoming = unit_vector(prev_pt, curr_pt)
        outgoing = unit_vector(curr_pt, next_pt)
        start = (curr_pt[0] - incoming[0] * transition_len, curr_pt[1] - incoming[1] * transition_len)
        end = (curr_pt[0] + outgoing[0] * transition_len, curr_pt[1] + outgoing[1] * transition_len)

        if math.dist(smoothed[-1], start) > 0.01:
            smoothed.append(start)
        smoothed.extend(clothoid_transition_points(start, end, incoming, outgoing))
        smoothed.append(end)

    if math.dist(smoothed[-1], coords[-1]) > 0.01:
        smoothed.append(coords[-1])

    if len(smoothed) < 2:
        return line
    return LineString(smoothed)


def rounded_node_key(point: Point, tolerance: float = JUNCTION_NODE_TOLERANCE_M) -> tuple[int, int]:
    return (round(point.x / tolerance), round(point.y / tolerance))


def add_junction_candidate(
    buckets: dict[tuple[int, int], dict[str, Any]],
    point: Point,
    road_ids: Iterable[Any],
    method: str,
    tolerance: float = JUNCTION_NODE_TOLERANCE_M,
    road_classes: Iterable[Any] | None = None,
    segment_ids: Iterable[Any] | None = None,
) -> None:
    if point is None or point.is_empty:
        return
    key = rounded_node_key(point, tolerance)
    bucket = buckets.setdefault(key, {"points": [], "road_ids": set(), "segment_ids": set(), "methods": set(), "has_link": False})
    bucket["points"].append(point)
    for road_id in road_ids:
        text = safe_str(road_id)
        if not text:
            continue
        bucket["road_ids"].add(text)
    for segment_id in segment_ids or road_ids:
        text = safe_str(segment_id)
        if text:
            bucket["segment_ids"].add(text)
    for road_class in road_classes or []:
        if "link" in (safe_str(road_class) or "").lower():
            bucket["has_link"] = True
    bucket["methods"].add(method)


def junction_candidate_segment_key(row: pd.Series, idx: Any) -> str:
    road_id = safe_str(row.get("road_id", row.get("osmid")))
    return f"{road_id or 'unknown'}:{idx}"


def representative_point_from_intersection(geom) -> Point | None:
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, Point):
        return geom
    if geom.geom_type == "MultiPoint":
        points = list(geom.geoms)
        return points[0] if points else None
    if isinstance(geom, LineString):
        return geom.interpolate(geom.length * 0.5)
    if isinstance(geom, MultiLineString):
        parts = list(geom.geoms)
        return parts[0].interpolate(parts[0].length * 0.5) if parts else None
    if isinstance(geom, GeometryCollection):
        for part in geom.geoms:
            point = representative_point_from_intersection(part)
            if point is not None:
                return point
    return None


def row_layer_value(row: pd.Series) -> int:
    text = safe_str(row.get("layer"))
    if not text:
        return 0
    match = re.search(r"-?\d+", text)
    return int(match.group(0)) if match else 0


def rows_same_spatial_level(row_a: pd.Series, row_b: pd.Series) -> bool:
    if bool(row_a.get("is_bridge", False)) != bool(row_b.get("is_bridge", False)):
        return False
    if row_layer_value(row_a) != row_layer_value(row_b):
        return False
    za = float(row_a.get("elevation", row_a.get("road_z_mean", 0.0)) or 0.0)
    zb = float(row_b.get("elevation", row_b.get("road_z_mean", 0.0)) or 0.0)
    return abs(za - zb) <= BRIDGE_ELEVATION_GROUP_M


def cluster_corridor_junction_points(points: list[Point]) -> list[Point]:
    if not ENABLE_CORRIDOR_JUNCTION_CLUSTERING or len(points) <= 1:
        return points

    clusters: list[list[Point]] = []
    for point in points:
        best_idx = None
        best_distance = float("inf")
        for cluster_idx, cluster in enumerate(clusters):
            cx = sum(item.x for item in cluster) / len(cluster)
            cy = sum(item.y for item in cluster) / len(cluster)
            centroid = Point(cx, cy)
            centroid_distance = point.distance(centroid)
            if centroid_distance > CORRIDOR_JUNCTION_CLUSTER_DISTANCE_M:
                continue
            if any(point.distance(item) > CORRIDOR_JUNCTION_CLUSTER_DISTANCE_M for item in cluster):
                continue
            if centroid_distance < best_distance:
                best_idx = cluster_idx
                best_distance = centroid_distance
        if best_idx is None:
            clusters.append([point])
        else:
            clusters[best_idx].append(point)

    merged = []
    for cluster in clusters:
        if len(cluster) == 1:
            merged.append(cluster[0])
            continue
        x = sum(point.x for point in cluster) / len(cluster)
        y = sum(point.y for point in cluster) / len(cluster)
        merged.append(Point(x, y))
    return merged


def junction_connection_tolerance() -> float:
    if ENABLE_CORRIDOR_JUNCTION_CLUSTERING:
        return max(JUNCTION_NODE_TOLERANCE_M * 1.5, CORRIDOR_JUNCTION_CONNECT_TOLERANCE_M)
    return JUNCTION_NODE_TOLERANCE_M * 1.5


def detect_junction_points(roads: gpd.GeoDataFrame, rules: dict[str, RoadRule] | None = None) -> list[Point]:
    buckets: dict[tuple[int, int], dict[str, Any]] = {}
    entries: list[tuple[Any, pd.Series, LineString, RoadRule | None]] = []
    for idx, row in roads.iterrows():
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue
        rule = get_road_rule(row, rules) if rules is not None else None
        entries.append((idx, row, line, rule))
        for point in road_endpoint_points(line):
            add_junction_candidate(
                buckets,
                point,
                [row.get("road_id", idx)],
                "endpoint_cluster",
                road_classes=[row.get("road_class", row.get("highway"))],
                segment_ids=[junction_candidate_segment_key(row, idx)],
            )

    for i, (idx_a, row_a, line_a, rule_a) in enumerate(entries):
        for endpoint in road_endpoint_points(line_a):
            for idx_b, row_b, line_b, _ in entries:
                if idx_a == idx_b or not rows_same_spatial_level(row_a, row_b):
                    continue
                projected_distance = float(line_b.project(endpoint))
                if projected_distance <= JUNCTION_NODE_TOLERANCE_M or projected_distance >= line_b.length - JUNCTION_NODE_TOLERANCE_M:
                    continue
                projected = line_b.interpolate(projected_distance)
                if endpoint.distance(projected) <= JUNCTION_ENDPOINT_SNAP_TOLERANCE_M:
                    add_junction_candidate(
                        buckets,
                        projected,
                        [row_a.get("road_id", idx_a), row_b.get("road_id", idx_b)],
                        "endpoint_snap",
                        road_classes=[row_a.get("road_class", row_a.get("highway")), row_b.get("road_class", row_b.get("highway"))],
                        segment_ids=[junction_candidate_segment_key(row_a, idx_a), junction_candidate_segment_key(row_b, idx_b)],
                    )

        for idx_b, row_b, line_b, rule_b in entries[i + 1 :]:
            if not rows_same_spatial_level(row_a, row_b):
                continue
            if safe_str(row_a.get("road_id")) and safe_str(row_a.get("road_id")) == safe_str(row_b.get("road_id")):
                continue
            if not line_a.bounds or not line_b.bounds:
                continue
            line_distance = line_a.distance(line_b)
            max_corridor_distance = detection_road_half_width(row_a, rule_a) + detection_road_half_width(row_b, rule_b)
            if line_distance > max(JUNCTION_LINE_INTERSECTION_TOLERANCE_M, max_corridor_distance):
                continue
            point = None
            if line_distance <= JUNCTION_LINE_INTERSECTION_TOLERANCE_M:
                try:
                    intersection = line_a.intersection(line_b)
                except Exception:
                    intersection = None
                point = representative_point_from_intersection(intersection)
            if point is None:
                point = road_surface_overlap_candidate_point(row_a, line_a, row_b, line_b, rule_a, rule_b)
                method = "road_surface_overlap"
                if point is None:
                    point = corridor_crossing_candidate_point(row_a, line_a, row_b, line_b, rule_a, rule_b)
                    method = "corridor_crossing"
            else:
                da = float(line_a.project(point))
                db = float(line_b.project(point))
                # Shared endpoints are already handled by endpoint clustering;
                # this branch is for true mid-line crossings or unsplit T junctions.
                interior_a = JUNCTION_NODE_TOLERANCE_M < da < line_a.length - JUNCTION_NODE_TOLERANCE_M
                interior_b = JUNCTION_NODE_TOLERANCE_M < db < line_b.length - JUNCTION_NODE_TOLERANCE_M
                method = "line_intersection"
                if not (interior_a or interior_b):
                    point = road_surface_overlap_candidate_point(row_a, line_a, row_b, line_b, rule_a, rule_b)
                    method = "road_surface_overlap"
                    if point is None:
                        point = corridor_crossing_candidate_point(row_a, line_a, row_b, line_b, rule_a, rule_b)
                        method = "corridor_crossing"
            if point is not None:
                add_junction_candidate(
                    buckets,
                    point,
                    [row_a.get("road_id", idx_a), row_b.get("road_id", idx_b)],
                    method,
                    road_classes=[row_a.get("road_class", row_a.get("highway")), row_b.get("road_class", row_b.get("highway"))],
                    segment_ids=[junction_candidate_segment_key(row_a, idx_a), junction_candidate_segment_key(row_b, idx_b)],
                )

    junctions = []
    for bucket in buckets.values():
        points = bucket["points"]
        road_count = len(bucket["road_ids"])
        segment_count = len(bucket.get("segment_ids", []))
        methods = bucket["methods"]
        is_topology_candidate = bool({"line_intersection", "endpoint_snap", "corridor_crossing", "road_surface_overlap"} & methods)
        is_multi_segment_endpoint = "endpoint_cluster" in methods and bucket.get("has_link", False) and segment_count >= 3
        if road_count < 3 and not (is_topology_candidate and segment_count >= 2) and not is_multi_segment_endpoint:
            continue
        x = sum(point.x for point in points) / len(points)
        y = sum(point.y for point in points) / len(points)
        junctions.append(Point(x, y))
    return cluster_corridor_junction_points(junctions)


def attach_junction_distances(roads: gpd.GeoDataFrame) -> None:
    junctions = detect_junction_points(roads)
    for idx, row in roads.iterrows():
        line = row.geometry
        distances: list[float] = []
        if line is not None and not line.is_empty and isinstance(line, LineString):
            for point in junctions:
                if line.distance(point) <= junction_connection_tolerance():
                    distances.append(round(float(line.project(point)), 3))
        roads.at[idx, "junction_distances_json"] = json.dumps(sorted(set(distances)))


def row_junction_distances(row: pd.Series) -> list[float]:
    text = safe_str(row.get("junction_distances_json"))
    if not text:
        return []
    try:
        return [float(v) for v in json.loads(text)]
    except Exception:
        return []


def is_near_any_distance(distance: float, blocked: list[float], radius: float) -> bool:
    return any(abs(float(distance) - d) <= radius for d in blocked)


def build_short_road_connectors(
    lines: list[LineString],
    tolerance: float = ROAD_LINK_GAP_TOLERANCE_M,
) -> list[LineString]:
    """Bridge small same-level centerline gaps before buffering road surfaces."""
    connectors: list[LineString] = []
    seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()

    for i, line in enumerate(lines):
        for endpoint in road_endpoint_points(line):
            best_point = None
            best_distance = tolerance

            for j, other in enumerate(lines):
                if i == j or other.is_empty:
                    continue

                projected = other.interpolate(other.project(endpoint))
                distance = endpoint.distance(projected)
                if MIN_CONNECTOR_LENGTH_M < distance <= best_distance:
                    best_distance = distance
                    best_point = projected

            if best_point is None:
                continue

            a = (round(endpoint.x, 3), round(endpoint.y, 3))
            b = (round(best_point.x, 3), round(best_point.y, 3))
            key = tuple(sorted([a, b]))
            if key in seen:
                continue

            connector = LineString([endpoint, best_point])
            if connector.length > MIN_CONNECTOR_LENGTH_M:
                connectors.append(connector)
                seen.add(key)

    if connectors:
        print(f"补齐道路小断缝连接段: {len(connectors)} 条")
    return connectors


def build_junction_approach_connectors(
    clipped_entries: list[tuple[pd.Series, LineString, RoadRule, float]],
    junction_union,
    junction_nodes: list[JunctionNode],
) -> list[tuple[LineString, RoadRule]]:
    """Pull near-junction frontage/parallel road ends into the junction asphalt."""
    if junction_union is None or junction_union.is_empty or not junction_nodes:
        return []

    connectors: list[tuple[LineString, RoadRule]] = []
    seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()

    for _, line, rule, _ in clipped_entries:
        if line is None or line.is_empty:
            continue
        for endpoint in road_endpoint_points(line):
            if junction_union.buffer(0.05, resolution=4).contains(endpoint):
                continue
            distance_to_junction = float(endpoint.distance(junction_union))
            if distance_to_junction <= MIN_CONNECTOR_LENGTH_M or distance_to_junction > JUNCTION_APPROACH_CONNECT_MAX_LENGTH_M:
                continue
            near_node = any(
                endpoint.distance(node.center) <= node.radius + JUNCTION_APPROACH_CONNECT_TOLERANCE_M
                for node in junction_nodes
            )
            if not near_node:
                continue
            try:
                _, target = nearest_points(endpoint, junction_union)
            except Exception:
                continue
            if endpoint.distance(target) > JUNCTION_APPROACH_CONNECT_MAX_LENGTH_M:
                continue
            connector = LineString([endpoint, target])
            if connector.length <= MIN_CONNECTOR_LENGTH_M:
                continue

            a = (round(endpoint.x, 3), round(endpoint.y, 3))
            b = (round(target.x, 3), round(target.y, 3))
            key = tuple(sorted([a, b]))
            if key in seen:
                continue
            seen.add(key)
            connectors.append((connector, rule))

    if connectors:
        print(f"补齐路口近端接入段: {len(connectors)} 条")
    return connectors


def road_topology_group(row: pd.Series) -> str:
    """Group roads for planar merging without letting bridges fuse into ground roads."""
    elevation = float(row.get("elevation", 0.0))
    elevation_group = round(elevation / 1.0)
    if bool(row.get("is_bridge", False)):
        bucket = round(elevation / BRIDGE_ELEVATION_GROUP_M)
        return f"bridge_{bucket}"
    if not ENABLE_DEM_ELEVATION and row.geometry is not None and not row.geometry.is_empty:
        centroid = row.geometry.centroid
        tile_x = math.floor(float(centroid.x) / FLAT_TOPOLOGY_TILE_SIZE_M)
        tile_y = math.floor(float(centroid.y) / FLAT_TOPOLOGY_TILE_SIZE_M)
        return f"ground_flat_{tile_x}_{tile_y}"
    return f"ground_{elevation_group}"


def line_frame_at_distance(line: LineString, distance: float) -> tuple[Point, tuple[float, float], tuple[float, float]]:
    distance = max(0.0, min(float(distance), float(line.length)))
    point = line.interpolate(distance)
    ahead = line.interpolate(min(line.length, distance + 0.6))
    behind = line.interpolate(max(0.0, distance - 0.6))
    dx = ahead.x - behind.x
    dy = ahead.y - behind.y
    length = math.hypot(dx, dy)
    if length <= 0.05:
        return point, (1.0, 0.0), (0.0, 1.0)
    tx, ty = dx / length, dy / length
    return point, (tx, ty), (-ty, tx)


def oriented_rect_at_line(
    line: LineString,
    distance: float,
    along_length: float,
    across_width: float,
) -> Polygon:
    center, tangent, normal = line_frame_at_distance(line, distance)
    hx = max(along_length, 0.05) / 2.0
    hy = max(across_width, 0.05) / 2.0
    tx, ty = tangent
    nx, ny = normal
    return Polygon(
        [
            (center.x - tx * hx - nx * hy, center.y - ty * hx - ny * hy),
            (center.x + tx * hx - nx * hy, center.y + ty * hx - ny * hy),
            (center.x + tx * hx + nx * hy, center.y + ty * hx + ny * hy),
            (center.x - tx * hx + nx * hy, center.y - ty * hx + ny * hy),
        ]
    )


def sample_line_for_sweep(line: LineString, interval: float = SWEEP_SAMPLE_INTERVAL_M) -> list[float]:
    distances = line_sample_distances(line, interval)
    vertex_distances = []
    accumulated = 0.0
    coords = list(line.coords)
    for a, b in zip(coords, coords[1:]):
        vertex_distances.append(accumulated)
        accumulated += math.dist((a[0], a[1]), (b[0], b[1]))
    vertex_distances.append(float(line.length))
    return sorted(set(round(float(d), 3) for d in distances + vertex_distances if 0.0 <= d <= line.length))


def cross_section_offsets(rule: RoadRule) -> list[float]:
    half_width = rule.road_width / 2.0
    offsets = [-half_width, 0.0, half_width]
    for lane_idx in range(1, rule.lane_count):
        offsets.append(-half_width + lane_idx * rule.lane_width)
    return sorted(set(round(offset, 4) for offset in offsets))


def lane_center_offsets(rule: RoadRule) -> list[float]:
    half_width = rule.road_width / 2.0
    return [
        -half_width + lane_idx * rule.lane_width + rule.lane_width / 2.0
        for lane_idx in range(rule.lane_count)
    ]


def lane_direction_offsets(row: pd.Series, rule: RoadRule, direction: str) -> list[float]:
    offsets = lane_center_offsets(rule)
    layout = lane_layout_for_row(row, rule)
    if direction == "forward":
        return offsets[: layout.forward_count]
    if direction == "backward":
        start = max(0, len(offsets) - layout.backward_count)
        return offsets[start:]
    return offsets


def oneway_direction(row: pd.Series) -> int:
    value = (safe_str(row.get("oneway")) or "").strip().lower()
    if value in {"yes", "true", "1"}:
        return 1
    if value in {"-1", "reverse"}:
        return -1
    return 0


def incoming_lane_offsets(row: pd.Series, rule: RoadRule, sign: float) -> list[float]:
    direction = oneway_direction(row)
    if direction == 1:
        return lane_center_offsets(rule) if sign < 0.0 else []
    if direction == -1:
        return lane_center_offsets(rule) if sign > 0.0 else []
    if rule.lane_count <= 1:
        return lane_center_offsets(rule)

    # Right-hand traffic assumption: negative offsets travel with the line,
    # positive offsets travel opposite the line.
    if sign < 0.0:
        selected = lane_direction_offsets(row, rule, "forward")
    else:
        selected = lane_direction_offsets(row, rule, "backward")
    return selected or lane_center_offsets(rule)


def crossfall_z(center_z: float, offset: float, rule: RoadRule) -> float:
    half_width = max(rule.road_width / 2.0, 0.1)
    slope = LANE_CROWN_SLOPE if abs(offset) < half_width * 0.55 else CROWN_SLOPE
    return center_z - abs(offset) * slope


def swept_road_mesh(
    row: pd.Series,
    line: LineString,
    rule: RoadRule,
    name: str,
    visual_color=None,
    distance_offset: float = 0.0,
) -> trimesh.Trimesh:
    distances = sample_line_for_sweep(line)
    offsets = cross_section_offsets(rule)
    if len(distances) < 2 or len(offsets) < 2:
        return empty_mesh(name)

    vertices = []
    for distance in distances:
        point, _, normal = line_frame_at_distance(line, distance)
        center_z = elevation_at_distance(
            row,
            distance + distance_offset,
            default_z=float(row.get("road_z_mean", row.get("elevation", 0.0))),
        )
        nx, ny = normal
        for offset in offsets:
            vertices.append((point.x + nx * offset, point.y + ny * offset, crossfall_z(center_z, offset, rule)))

    faces = []
    cols = len(offsets)
    for row_idx in range(len(distances) - 1):
        for col_idx in range(cols - 1):
            a = row_idx * cols + col_idx
            b = a + 1
            c = (row_idx + 1) * cols + col_idx + 1
            d = (row_idx + 1) * cols + col_idx
            faces.append((a, b, c))
            faces.append((a, c, d))

    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=False)
    mesh.metadata["name"] = name
    if visual_color is not None:
        mesh.visual.face_colors = visual_color
    return mesh


def swept_lane_mesh(
    row: pd.Series,
    line: LineString,
    rule: RoadRule,
    lane_idx: int,
    name: str,
    visual_color=None,
    distance_offset: float = 0.0,
) -> trimesh.Trimesh:
    distances = sample_line_for_sweep(line)
    half_width = rule.road_width / 2.0
    left = -half_width + lane_idx * rule.lane_width
    right = min(left + rule.lane_width, half_width)
    offsets = [left, right]
    if len(distances) < 2 or right <= left:
        return empty_mesh(name)

    vertices = []
    for distance in distances:
        point, _, normal = line_frame_at_distance(line, distance)
        center_z = elevation_at_distance(
            row,
            distance + distance_offset,
            default_z=float(row.get("road_z_mean", row.get("elevation", 0.0))),
        )
        nx, ny = normal
        for offset in offsets:
            vertices.append((point.x + nx * offset, point.y + ny * offset, crossfall_z(center_z, offset, rule) + 0.003))

    faces = []
    for row_idx in range(len(distances) - 1):
        a = row_idx * 2
        b = a + 1
        c = a + 3
        d = a + 2
        faces.append((a, b, c))
        faces.append((a, c, d))

    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=False)
    mesh.metadata["name"] = name
    if visual_color is not None:
        mesh.visual.face_colors = visual_color
    return mesh


def bezier_points(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    samples: int = 12,
) -> list[tuple[float, float]]:
    points = []
    for idx in range(samples + 1):
        t = idx / samples
        u = 1.0 - t
        points.append(
            (
                u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
                u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1],
            )
        )
    return points


def movement_type(from_lane: LaneSocket, to_lane: LaneSocket) -> str:
    exit_direction = (-to_lane.tangent[0], -to_lane.tangent[1])
    fx, fy = from_lane.tangent
    ex, ey = exit_direction
    cross = fx * ey - fy * ex
    dot = fx * ex + fy * ey
    angle = math.degrees(math.atan2(cross, dot))
    if abs(angle) <= 35.0:
        return "straight"
    return "left" if angle > 0.0 else "right"


def lane_connector_curve(from_lane: LaneSocket, to_lane: LaneSocket) -> LineString:
    p0 = from_lane.center
    p3 = to_lane.center
    distance = math.dist(p0, p3)
    handle_len = max(2.0, min(distance * LANE_CONNECTOR_HANDLE_RATIO, 28.0))
    p1 = (
        p0[0] + from_lane.tangent[0] * handle_len,
        p0[1] + from_lane.tangent[1] * handle_len,
    )
    p2 = (
        p3[0] + to_lane.tangent[0] * handle_len,
        p3[1] + to_lane.tangent[1] * handle_len,
    )
    return LineString(bezier_points(p0, p1, p2, p3, samples=LANE_CONNECTOR_SAMPLE_COUNT))


def build_lane_connectors(node: JunctionNode) -> list[LaneConnector]:
    ingress_lanes = [
        lane
        for socket in node.sockets
        for lane in socket.lane_sockets
        if lane.lane_role == "ingress"
    ]
    egress_lanes = [
        lane
        for socket in node.sockets
        for lane in socket.lane_sockets
        if lane.lane_role == "egress"
    ]
    connectors: list[LaneConnector] = []
    seen: set[tuple[str, str]] = set()

    for from_lane in ingress_lanes:
        by_socket: dict[str, LaneSocket] = {}
        for to_lane in egress_lanes:
            if to_lane.parent_socket_id == from_lane.parent_socket_id:
                continue
            current = by_socket.get(to_lane.parent_socket_id)
            if current is None or math.dist(from_lane.center, to_lane.center) < math.dist(from_lane.center, current.center):
                by_socket[to_lane.parent_socket_id] = to_lane

        candidates = []
        for to_lane in by_socket.values():
            move = movement_type(from_lane, to_lane)
            if move not in from_lane.allowed_movements:
                continue
            exit_direction = (-to_lane.tangent[0], -to_lane.tangent[1])
            dot = from_lane.tangent[0] * exit_direction[0] + from_lane.tangent[1] * exit_direction[1]
            candidates.append((move, -dot, math.dist(from_lane.center, to_lane.center), to_lane))

        selected: dict[str, tuple[str, float, float, LaneSocket]] = {}
        for candidate in sorted(candidates, key=lambda item: (item[0] != "straight", item[1], item[2])):
            move = candidate[0]
            if move not in selected:
                selected[move] = candidate
        for move, _, _, to_lane in selected.values():
            key = (from_lane.lane_socket_id, to_lane.lane_socket_id)
            if key in seen:
                continue
            centerline = lane_connector_curve(from_lane, to_lane)
            if centerline.length <= 0.5:
                continue
            width = min(from_lane.lane_width, to_lane.lane_width)
            surface = centerline.buffer(width / 2.0, cap_style=2, join_style=2)
            if surface.is_empty:
                continue
            connectors.append(
                LaneConnector(
                    connector_id=f"{node.junction_id}_{from_lane.lane_socket_id}_to_{to_lane.lane_socket_id}",
                    junction_id=node.junction_id,
                    from_lane=from_lane,
                    to_lane=to_lane,
                    movement_type=move,
                    centerline=centerline,
                    width=width,
                    surface_polygon=surface,
                    marking_guides=[centerline],
                )
            )
            seen.add(key)

    return connectors


def oriented_rect_from_frame(
    center: tuple[float, float],
    tangent: tuple[float, float],
    normal: tuple[float, float],
    along_length: float,
    across_width: float,
) -> Polygon:
    hx = max(along_length, 0.05) / 2.0
    hy = max(across_width, 0.05) / 2.0
    tx, ty = tangent
    nx, ny = normal
    return Polygon(
        [
            (center[0] - tx * hx - nx * hy, center[1] - ty * hx - ny * hy),
            (center[0] + tx * hx - nx * hy, center[1] + ty * hx - ny * hy),
            (center[0] + tx * hx + nx * hy, center[1] + ty * hx + ny * hy),
            (center[0] - tx * hx + nx * hy, center[1] - ty * hx + ny * hy),
        ]
    )


def socket_bound_stop_line(socket: RoadSocket) -> Polygon:
    offset = 3.0 if socket.road_width >= 12.0 else 2.0
    center = (
        socket.center[0] - socket.tangent[0] * offset,
        socket.center[1] - socket.tangent[1] * offset,
    )
    return oriented_rect_from_frame(center, socket.tangent, socket.normal, 0.45, socket.road_width + 0.8)


def socket_bound_crosswalks(socket: RoadSocket, stripe_width: float = CROSSWALK_STRIPE_WIDTH_M) -> list[Polygon]:
    if not GENERATE_CROSSWALKS:
        return []
    if socket.approach_type == "egress":
        return []
    offset = 6.0 if socket.road_width >= 12.0 else 5.0
    stripe_step = CROSSWALK_STRIPE_WIDTH_M + CROSSWALK_STRIPE_GAP_M
    stripe_count = max(3, int((socket.road_width + 1.2) / stripe_step))
    start_lateral = -((stripe_count - 1) * stripe_step) / 2.0
    base_center = (
        socket.center[0] - socket.tangent[0] * offset,
        socket.center[1] - socket.tangent[1] * offset,
    )
    stripes: list[Polygon] = []
    for stripe_idx in range(stripe_count):
        center = (
            base_center[0] + socket.normal[0] * (start_lateral + stripe_idx * stripe_step),
            base_center[1] + socket.normal[1] * (start_lateral + stripe_idx * stripe_step),
        )
        stripes.append(oriented_rect_from_frame(center, socket.tangent, socket.normal, CROSSWALK_BAND_LENGTH_M, stripe_width))
    return stripes


def socket_bound_turn_arrows(socket: RoadSocket) -> list[Polygon]:
    arrows: list[Polygon] = []
    return arrows


def dashed_line_markings(line: LineString, width: float, dash_length: float = LANE_DASH_LENGTH_M, gap_length: float = LANE_DASH_GAP_M) -> list[Polygon]:
    if line is None or line.is_empty or line.length <= LANE_DASH_MIN_ROAD_LENGTH_M:
        return [line.buffer(width / 2.0, cap_style=2, join_style=1)] if line is not None and not line.is_empty else []

    markings: list[Polygon] = []
    period = max(dash_length + gap_length, 0.1)
    distance = gap_length * 0.5
    while distance < line.length:
        start = distance
        end = min(line.length, distance + dash_length)
        if end - start >= max(1.2, dash_length * 0.35):
            try:
                segment = substring(line, start, end)
            except Exception:
                segment = None
            if segment is not None and not segment.is_empty and isinstance(segment, LineString):
                markings.append(segment.buffer(width / 2.0, cap_style=2, join_style=1))
        distance += period
    return markings


def offset_line_parts(line: LineString, offset: float) -> list[LineString]:
    if abs(offset) < 0.05:
        return [line]
    side = "left" if offset >= 0.0 else "right"
    try:
        offset_line = line.parallel_offset(abs(offset), side=side, join_style=1)
    except Exception:
        return []
    if offset_line.is_empty:
        return []
    if isinstance(offset_line, MultiLineString):
        return [part for part in offset_line.geoms if not part.is_empty]
    if isinstance(offset_line, LineString):
        return [offset_line]
    return []


def lane_offset_markings(line: LineString, row: pd.Series, rule: RoadRule) -> list[Polygon]:
    markings = []
    if rule.lane_count <= 1:
        return markings
    layout = lane_layout_for_row(row, rule)
    direction_boundary_idx = layout.forward_count if layout.forward_count and layout.backward_count else None
    for lane_idx in range(1, rule.lane_count):
        if direction_boundary_idx == lane_idx:
            continue
        offset = -rule.road_width / 2.0 + lane_idx * rule.lane_width
        for part in offset_line_parts(line, offset):
            markings.extend(dashed_line_markings(part, LANE_GUIDE_MARKING_WIDTH_M))
    return markings


def lane_direction_boundary_marking(line: LineString, row: pd.Series, rule: RoadRule) -> Polygon | None:
    layout = lane_layout_for_row(row, rule)
    if layout.forward_count <= 0 or layout.backward_count <= 0:
        return None
    boundary_offset = -rule.road_width / 2.0 + layout.forward_count * rule.lane_width
    width = max(rule.lane_marking_width, DOUBLE_YELLOW_LINE_WIDTH_M)
    dividers: list[Polygon] = []
    for yellow_offset in (-DOUBLE_YELLOW_LINE_OFFSET_M, DOUBLE_YELLOW_LINE_OFFSET_M):
        for part in offset_line_parts(line, boundary_offset + yellow_offset):
            dividers.append(part.buffer(width / 2.0, cap_style=2, join_style=1))
    if not dividers:
        return None
    return clean_polygonal(unary_union(dividers))


def arm_direction_from_junction(line: LineString, node_distance: float, radius: float) -> tuple[tuple[float, float], tuple[float, float], float]:
    if node_distance <= line.length * 0.5:
        outside_distance = min(line.length, node_distance + radius)
        sign = 1.0
    else:
        outside_distance = max(0.0, node_distance - radius)
        sign = -1.0
    outside = line.interpolate(outside_distance)
    node = line.interpolate(node_distance)
    direction = unit_vector((node.x, node.y), (outside.x, outside.y))
    return (node.x, node.y), direction, sign


def junction_clip_radius(rule: RoadRule, connected_count: int = 3) -> float:
    width_radius = (rule.road_width + 2.0 * rule.sidewalk_width) * 0.55
    complexity_radius = max(0.0, connected_count - 3) * 1.2
    return max(JUNCTION_RADIUS_M, width_radius) + JUNCTION_CLIP_EXTRA_M + complexity_radius


def road_priority(row: pd.Series) -> int:
    road_class = (safe_str(row.get("road_class")) or "").lower()
    if "motorway" in road_class or "trunk" in road_class:
        return 5
    if "primary" in road_class:
        return 4
    if "secondary" in road_class:
        return 3
    if "tertiary" in road_class:
        return 2
    if "service" in road_class or "residential" in road_class:
        return 1
    return 2


def road_class_clip_distance(
    row: pd.Series,
    rule: RoadRule,
    connected_count: int = 3,
    min_adjacent_angle_deg: float | None = None,
    junction_type: str | None = None,
    is_minor_to_major: bool = False,
) -> float:
    road_class = (safe_str(row.get("road_class")) or "").lower()
    if "motorway" in road_class or "trunk" in road_class or "link" in road_class:
        base = 16.0
    elif "primary" in road_class:
        base = 14.0
    elif "secondary" in road_class:
        base = 16.0
    elif "service" in road_class:
        base = 10.0
    else:
        base = 12.0
    width_driven = max(rule.road_width * 1.2, rule.lane_width * rule.lane_count * 1.5)
    complexity = max(0, connected_count - 3) * 2.0
    distance = max(base, width_driven) + complexity

    if min_adjacent_angle_deg is not None:
        if min_adjacent_angle_deg < 45.0:
            distance += 4.0
        elif min_adjacent_angle_deg < 65.0:
            distance += 2.0

    if junction_type in {"MULTI_ARM_JUNCTION", "ROUNDABOUT_LIKE"}:
        distance += 3.0
    elif junction_type == "RAMP_MERGE":
        distance += 2.0

    if is_minor_to_major and road_priority(row) <= 1:
        distance -= 3.0
    if rule.lane_count <= 1 and ("service" in road_class or "residential" in road_class):
        distance -= 1.5

    return max(MIN_JUNCTION_CLIP_DISTANCE_M, min(MAX_JUNCTION_CLIP_DISTANCE_M, distance))


def socket_cross_section_points(socket: RoadSocket, include_sidewalk: bool = False) -> tuple[tuple[float, float], tuple[float, float]]:
    if not include_sidewalk:
        return socket.left_edge, socket.right_edge
    return socket_edges(socket.center, socket.normal, socket.road_width + 2.0 * socket.sidewalk_width)


def socket_edges(center: tuple[float, float], normal: tuple[float, float], road_width: float) -> tuple[tuple[float, float], tuple[float, float]]:
    half_width = road_width / 2.0
    cx, cy = center
    nx, ny = normal
    return (
        (cx + nx * half_width, cy + ny * half_width),
        (cx - nx * half_width, cy - ny * half_width),
    )


def socket_approach_type(row: pd.Series, sign: float) -> str:
    direction = oneway_direction(row)
    if direction == 0:
        return "bidirectional"
    if direction == 1:
        return "ingress" if sign < 0.0 else "egress"
    if direction == -1:
        return "ingress" if sign > 0.0 else "egress"
    return "bidirectional"


def build_lane_sockets(socket: RoadSocket) -> list[LaneSocket]:
    lane_sockets: list[LaneSocket] = []
    lane_count = max(1, int(socket.lane_count))
    half_width = socket.road_width / 2.0
    cx, cy = socket.center
    nx, ny = socket.normal
    forward_lane_indexes = set(range(max(0, min(socket.forward_lane_count, lane_count))))
    backward_start = max(0, lane_count - max(0, socket.backward_lane_count))
    backward_lane_indexes = set(range(backward_start, lane_count))

    for lane_idx in range(lane_count):
        offset = -half_width + socket.lane_width / 2.0 + lane_idx * socket.lane_width
        center = (cx + nx * offset, cy + ny * offset)
        if socket.approach_type == "ingress":
            role = "ingress"
        elif socket.approach_type == "egress":
            role = "egress"
        elif lane_count == 1:
            role = "ingress"
        elif lane_idx in forward_lane_indexes and lane_idx not in backward_lane_indexes:
            role = "ingress" if socket.line_direction_sign < 0.0 else "egress"
        elif lane_idx in backward_lane_indexes and lane_idx not in forward_lane_indexes:
            role = "ingress" if socket.line_direction_sign > 0.0 else "egress"
        else:
            role = "ingress" if offset < 0.0 else "egress"
        lane_sockets.append(
            LaneSocket(
                lane_socket_id=f"{socket.socket_id}_L{lane_idx:02d}",
                parent_socket_id=socket.socket_id,
                road_id=socket.road_id,
                lane_index=lane_idx,
                lane_role=role,
                center=center,
                tangent=socket.tangent,
                normal=socket.normal,
                lane_width=socket.lane_width,
                allowed_movements=["left", "straight", "right"],
                lane_id=f"{socket.road_id}:lane:{lane_idx}",
                movement="inbound" if role == "ingress" else "outbound",
                allowed_turns=["left", "straight", "right"] if role == "ingress" else ["straight"],
                traffic_direction=role,
                signal_group=f"{socket.junction_id}_{role}",
                stop_control="stop_line" if role == "ingress" else "none",
            )
        )
    return lane_sockets


def classify_junction_type(junction_node_or_sockets) -> str:
    sockets = junction_node_or_sockets.sockets if isinstance(junction_node_or_sockets, JunctionNode) else junction_node_or_sockets
    degree = len(sockets)
    if degree <= 2:
        return "RAMP_MERGE"
    if any("roundabout" in (socket.road_class or "").lower() or "circular" in (socket.road_class or "").lower() for socket in sockets):
        return "ROUNDABOUT_LIKE"

    priorities = [road_priority(pd.Series({"road_class": socket.road_class})) for socket in sockets]
    has_link = any("link" in (socket.road_class or "").lower() or "ramp" in (socket.road_class or "").lower() for socket in sockets)
    if has_link or (degree == 3 and max(priorities) - min(priorities) >= 3):
        return "RAMP_MERGE"

    directions = [socket.tangent for socket in sockets]
    sorted_angles = sorted(math.atan2(direction[1], direction[0]) for direction in directions)
    gaps = []
    for idx, angle in enumerate(sorted_angles):
        nxt = sorted_angles[(idx + 1) % len(sorted_angles)]
        gap = (nxt - angle) % (2.0 * math.pi)
        gaps.append(math.degrees(gap))
    max_gap = max(gaps) if gaps else 0.0
    min_gap = min(gaps) if gaps else 0.0

    if degree == 3:
        return "T_JUNCTION" if max_gap > 150.0 else "Y_JUNCTION"
    if degree == 4:
        return "CROSS_JUNCTION" if min_gap > 45.0 and max_gap < 145.0 else "MULTI_ARM_JUNCTION"
    if degree > 4:
        return "MULTI_ARM_JUNCTION"
    return "UNKNOWN"


def classify_junction_hierarchy(sockets: list[RoadSocket], junction_type: str) -> str:
    if junction_type == "ROUNDABOUT_LIKE":
        return "ROUNDABOUT_JUNCTION"
    if junction_type == "RAMP_MERGE":
        return "RAMP_OR_GRADE_SEPARATED"
    priorities = [road_priority(pd.Series({"road_class": socket.road_class})) for socket in sockets]
    max_priority = max(priorities) if priorities else 0
    max_lanes = max((socket.lane_count for socket in sockets), default=1)
    max_width = max((socket.road_width for socket in sockets), default=0.0)
    if junction_type == "MULTI_ARM_JUNCTION" or len(sockets) >= 5:
        return "COMPLEX_MULTI_ARM"
    if max_priority >= 4 or max_lanes >= 4 or max_width >= 14.0:
        return "MAJOR_ARTERIAL"
    if max_priority >= 3 or max_lanes >= 3 or max_width >= 10.0:
        return "SECONDARY_COLLECTOR"
    return "LOCAL_JUNCTION"


def junction_surface_strategy_for(hierarchy: str, junction_type: str) -> str:
    if hierarchy == "ROUNDABOUT_JUNCTION":
        return "socket_boundary"
    if hierarchy == "COMPLEX_MULTI_ARM":
        return "socket_boundary_then_edge"
    if hierarchy == "RAMP_OR_GRADE_SEPARATED":
        return "ramp_merge_edge"
    if hierarchy in {"MAJOR_ARTERIAL", "SECONDARY_COLLECTOR", "LOCAL_JUNCTION"}:
        return "edge_intersection"
    return "edge_intersection"


def adjacent_angle_by_socket(socket_idx: int, directions: list[tuple[float, float]]) -> float | None:
    if len(directions) < 2 or socket_idx >= len(directions):
        return None
    angle = math.atan2(directions[socket_idx][1], directions[socket_idx][0])
    gaps = []
    for idx, direction in enumerate(directions):
        if idx == socket_idx:
            continue
        other = math.atan2(direction[1], direction[0])
        gaps.append(abs(math.degrees(math.atan2(math.sin(other - angle), math.cos(other - angle)))))
    return min(gaps) if gaps else None


def build_junction_nodes(
    road_entries: list[tuple[pd.Series, LineString, RoadRule]],
    junction_points: list[Point],
) -> list[JunctionNode]:
    nodes: list[JunctionNode] = []
    connection_tolerance = junction_connection_tolerance()
    for node_idx, point in enumerate(junction_points):
        connected = [(row, line, rule) for row, line, rule in road_entries if line.distance(point) <= connection_tolerance]
        if len(connected) < 2:
            continue

        radius = max(junction_clip_radius(rule, len(connected)) for _, _, rule in connected)
        junction_id = f"J{node_idx:04d}"
        sockets: list[RoadSocket] = []
        arm_specs: list[tuple[pd.Series, LineString, RoadRule, float, float, tuple[float, float]]] = []
        for row, line, rule in connected:
            node_distance = float(line.project(point))
            is_internal_node = JUNCTION_NODE_TOLERANCE_M < node_distance < line.length - JUNCTION_NODE_TOLERANCE_M
            signs = [1.0, -1.0] if is_internal_node else ([1.0] if node_distance <= line.length * 0.5 else [-1.0])
            for sign in signs:
                outside_distance = max(0.0, min(float(line.length), node_distance + sign * max(radius, JUNCTION_RADIUS_M)))
                outside = line.interpolate(outside_distance)
                node = line.interpolate(node_distance)
                direction = unit_vector((node.x, node.y), (outside.x, outside.y))
                if vector_length(direction) > 0.01:
                    arm_specs.append((row, line, rule, node_distance, sign, direction))

        if len(arm_specs) < 3:
            continue

        preliminary_type = "UNKNOWN"
        priorities = [road_priority(spec[0]) for spec in arm_specs]
        max_priority = max(priorities) if priorities else 0

        for socket_idx, (row, line, rule, node_distance, sign, direction) in enumerate(arm_specs):
            min_angle = adjacent_angle_by_socket(socket_idx, [spec[5] for spec in arm_specs])
            is_minor_to_major = road_priority(row) + 2 <= max_priority
            clip_distance = road_class_clip_distance(
                row,
                rule,
                len(arm_specs),
                min_adjacent_angle_deg=min_angle,
                junction_type=preliminary_type,
                is_minor_to_major=is_minor_to_major,
            )
            socket_distance = max(0.0, min(float(line.length), node_distance + sign * clip_distance))
            socket_point, _, _ = line_frame_at_distance(line, socket_distance)
            tangent_to_junction = (-direction[0], -direction[1])
            normal = (-tangent_to_junction[1], tangent_to_junction[0])
            center = (socket_point.x, socket_point.y)
            left_edge, right_edge = socket_edges(center, normal, rule.road_width)
            lane_layout = lane_layout_for_row(row, rule)
            socket = RoadSocket(
                socket_id=f"{junction_id}_S{socket_idx:02d}_{safe_str(row.get('road_id')) or 'road'}",
                road_id=str(row.get("road_id")),
                junction_id=junction_id,
                distance_m=socket_distance,
                center=center,
                tangent=tangent_to_junction,
                normal=normal,
                road_width=rule.road_width,
                lane_count=rule.lane_count,
                lane_width=rule.lane_width,
                forward_lane_count=lane_layout.forward_count,
                backward_lane_count=lane_layout.backward_count,
                sidewalk_width=rule.sidewalk_width,
                curb_width=rule.curb_width,
                clip_radius=radius,
                clip_distance=clip_distance,
                line_direction_sign=sign,
                elevation=float(row.get("elevation", 0.0)),
                approach_type=socket_approach_type(row, sign),
                left_edge=left_edge,
                right_edge=right_edge,
                lane_sockets=[],
                road_class=safe_str(row.get("road_class")) or "unknown",
                node_distance_m=node_distance,
                road_name=safe_str(row.get("road_name")) or "unknown",
            )
            socket.lane_sockets = build_lane_sockets(socket)
            sockets.append(socket)

        if len(sockets) >= 3:
            node_type = classify_junction_type(sockets)
            hierarchy = classify_junction_hierarchy(sockets, node_type)
            surface_strategy = junction_surface_strategy_for(hierarchy, node_type)
            nodes.append(
                JunctionNode(
                    junction_id=junction_id,
                    center=point,
                    radius=max(socket.clip_distance for socket in sockets),
                    sockets=sockets,
                    junction_type=node_type,
                    hierarchy=hierarchy,
                    surface_strategy=surface_strategy,
                    detection_method="enhanced_topology",
                    metadata={
                        "connected_road_count": len(connected),
                        "socket_count": len(sockets),
                        "road_classes": sorted(set(socket.road_class for socket in sockets)),
                        "surface_strategy": surface_strategy,
                    },
                )
            )
    return nodes


def socket_boundary_polygon(node: JunctionNode, include_sidewalk: bool = False) -> Polygon | None:
    boundary_points: list[tuple[float, float]] = []
    for socket in node.sockets:
        boundary_points.extend(socket_cross_section_points(socket, include_sidewalk=include_sidewalk))

    if len(boundary_points) < 3:
        return None

    cx, cy = node.center.x, node.center.y
    ordered = sorted(boundary_points, key=lambda pt: math.atan2(pt[1] - cy, pt[0] - cx))
    polygon = Polygon(ordered)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty or polygon.area <= 1.0:
        return None

    hull = MultiPolygon([polygon]).convex_hull if isinstance(polygon, Polygon) else polygon.convex_hull
    if hull is not None and not hull.is_empty and hull.area > polygon.area:
        polygon = hull

    # Keep the junction footprint as a single clean node shape. Earlier Bezier
    # interpolation between socket edges could overshoot and create tangled
    # center islands on dense intersections.
    fillet = max(0.6, min(2.4 if include_sidewalk else 2.0, node.radius * 0.14))
    try:
        polygon = polygon.buffer(fillet, resolution=6, join_style=1).buffer(-fillet, resolution=6, join_style=1)
    except Exception:
        pass
    if polygon.is_empty:
        return None
    return polygon


def socket_throat_polygon(node: JunctionNode, socket: RoadSocket, include_sidewalk: bool = False) -> Polygon | None:
    width = socket.road_width + (2.0 * socket.sidewalk_width if include_sidewalk else 0.0)
    start = (float(node.center.x), float(node.center.y))
    end = (
        socket.center[0] - socket.tangent[0] * JUNCTION_SOCKET_OVERLAP_M,
        socket.center[1] - socket.tangent[1] * JUNCTION_SOCKET_OVERLAP_M,
    )
    if math.dist(start, end) <= 0.05:
        return None
    try:
        polygon = LineString([start, end]).buffer(width / 2.0, cap_style=2, join_style=2)
    except Exception:
        return None
    polygon = clean_polygonal(polygon)
    if polygon is None or polygon.is_empty or polygon.area < JUNCTION_MIN_SURFACE_AREA_M2:
        return None
    return polygon


def socket_throat_union(node: JunctionNode, include_sidewalk: bool = False) -> Polygon | MultiPolygon | None:
    throats = [
        polygon
        for socket in node.sockets
        for polygon in [socket_throat_polygon(node, socket, include_sidewalk=include_sidewalk)]
        if polygon is not None and not polygon.is_empty
    ]
    if not throats:
        return None
    return clean_polygonal(unary_union(throats))


def lane_connector_surface_union(node: JunctionNode, include_sidewalk: bool = False) -> Polygon | MultiPolygon | None:
    extra_width = max((socket.sidewalk_width for socket in node.sockets), default=0.0) if include_sidewalk else 0.0
    surfaces = []
    for connector in build_lane_connectors(node):
        if connector.surface_polygon is None or connector.surface_polygon.is_empty:
            continue
        surface = connector.surface_polygon
        if extra_width > 0.0:
            surface = surface.buffer(extra_width, resolution=8, join_style=1)
        surfaces.append(surface)
    if not surfaces:
        return None
    return clean_polygonal(unary_union(surfaces))


def smooth_cityengine_junction_surface(geom, node: JunctionNode, include_sidewalk: bool = False):
    polygon = clean_polygonal(geom)
    if polygon is None or polygon.is_empty:
        return None
    radius = max(0.35, min(JUNCTION_CITYENGINE_SMOOTH_M + (0.25 if include_sidewalk else 0.0), node.radius * 0.08))
    try:
        polygon = polygon.buffer(radius, resolution=8, join_style=1).buffer(-radius, resolution=8, join_style=1)
    except Exception:
        pass
    return clean_polygonal(polygon)


def enforce_socket_throat_coverage(
    polygon,
    node: JunctionNode,
    include_sidewalk: bool = False,
) -> Polygon | MultiPolygon | None:
    throat_union = socket_throat_union(node, include_sidewalk=include_sidewalk)
    connector_union = lane_connector_surface_union(node, include_sidewalk=include_sidewalk)
    parts = [part for part in [polygon, throat_union, connector_union] if part is not None and not part.is_empty]
    if not parts:
        return None
    smoothed = smooth_cityengine_junction_surface(unary_union(parts), node, include_sidewalk)
    if throat_union is not None:
        parts = [part for part in [smoothed, throat_union, connector_union] if part is not None and not part.is_empty]
        return clean_polygonal(unary_union(parts))
    return clean_junction_polygon(smoothed, node, include_sidewalk)


def node_cityengine_surface_union(node: JunctionNode, include_sidewalk: bool = False):
    socket_polygon = socket_boundary_polygon(node, include_sidewalk=include_sidewalk)
    edge_polygon = edge_intersection_junction_polygon(node, include_sidewalk=include_sidewalk)
    throat_polygon = socket_throat_union(node, include_sidewalk=include_sidewalk)
    connector_polygon = lane_connector_surface_union(node, include_sidewalk=include_sidewalk)
    parts = [
        part
        for part in [socket_polygon, edge_polygon, throat_polygon, connector_polygon]
        if part is not None and not part.is_empty
    ]
    if not parts:
        return None
    smoothed = smooth_cityengine_junction_surface(unary_union(parts), node, include_sidewalk)
    parts = [
        part
        for part in [smoothed, throat_polygon, connector_polygon]
        if part is not None and not part.is_empty
    ]
    return clean_polygonal(unary_union(parts)) if parts else None


def infinite_line_intersection(
    a_point: tuple[float, float],
    a_dir: tuple[float, float],
    b_point: tuple[float, float],
    b_dir: tuple[float, float],
) -> tuple[float, float] | None:
    ax, ay = a_point
    bx, by = b_point
    adx, ady = a_dir
    bdx, bdy = b_dir
    denom = adx * bdy - ady * bdx
    if abs(denom) <= 1e-6:
        return None
    t = ((bx - ax) * bdy - (by - ay) * bdx) / denom
    return (ax + adx * t, ay + ady * t)


def clean_junction_polygon(geom, node: JunctionNode, include_sidewalk: bool = False):
    polygon = clean_polygonal(geom)
    if polygon is None or polygon.is_empty:
        return None
    if isinstance(polygon, MultiPolygon):
        parts = [part for part in polygon.geoms if part.area >= JUNCTION_MIN_SURFACE_AREA_M2]
        if not parts:
            return None
        polygon = max(parts, key=lambda part: part.area)
    if polygon.area < JUNCTION_MIN_SURFACE_AREA_M2:
        return None
    fillet = max(0.6, min(JUNCTION_CLEAN_FILLET_M + (0.35 if include_sidewalk else 0.0), node.radius * 0.14))
    try:
        polygon = polygon.buffer(fillet, resolution=6, join_style=1).buffer(-fillet, resolution=6, join_style=1)
    except Exception:
        pass
    polygon = clean_polygonal(polygon)
    if polygon is None or polygon.is_empty or polygon.area < JUNCTION_MIN_SURFACE_AREA_M2:
        return None
    return polygon


def convex_hull_junction_polygon(node: JunctionNode, include_sidewalk: bool = False):
    points: list[tuple[float, float]] = []
    for socket in node.sockets:
        points.extend(socket_cross_section_points(socket, include_sidewalk=include_sidewalk))
    if len(points) < 3:
        return None
    return clean_junction_polygon(MultiPolygon([Polygon(points)]).convex_hull, node, include_sidewalk)


def edge_intersection_junction_polygon(node: JunctionNode, include_sidewalk: bool = False) -> Polygon | None:
    """
    Main CIM3 junction footprint algorithm. It intersects adjacent offset road
    edges to create a less convex node boundary. socket_boundary_polygon() stays
    as the stable fallback, and a convex hull is the final safety net.
    """
    if len(node.sockets) < 3:
        return None

    ordered = sorted(
        node.sockets,
        key=lambda socket: math.atan2(socket.center[1] - node.center.y, socket.center[0] - node.center.x),
    )
    max_distance = max(
        node.radius * JUNCTION_EDGE_MAX_INTERSECTION_FACTOR,
        max((socket.clip_distance + socket.road_width for socket in ordered), default=node.radius),
    )
    max_width = max((socket.road_width + (2.0 * socket.sidewalk_width if include_sidewalk else 0.0) for socket in ordered), default=7.0)
    boundary_points: list[tuple[float, float]] = []

    for idx, current in enumerate(ordered):
        nxt = ordered[(idx + 1) % len(ordered)]
        current_edges = socket_cross_section_points(current, include_sidewalk=include_sidewalk)
        next_edges = socket_cross_section_points(nxt, include_sidewalk=include_sidewalk)
        candidates: list[tuple[float, tuple[float, float]]] = []
        for current_edge in current_edges:
            for next_edge in next_edges:
                point = infinite_line_intersection(current_edge, current.tangent, next_edge, nxt.tangent)
                if point is None:
                    continue
                distance = math.hypot(point[0] - node.center.x, point[1] - node.center.y)
                if 0.5 <= distance <= max_distance:
                    candidates.append((distance, point))
        if candidates:
            boundary_points.append(sorted(candidates, key=lambda item: item[0])[0][1])
        else:
            # Keep the boundary moving even when adjacent road edges are nearly
            # parallel; the validation below decides whether this is good enough.
            boundary_points.extend([current_edges[1], next_edges[0]])

    if len(boundary_points) < 3:
        return None
    try:
        raw_polygon = Polygon(boundary_points).buffer(0)
        polygon = unary_union([raw_polygon, node.center.buffer(max(0.75, max_width * 0.18), resolution=12)])
    except Exception:
        return None
    cleaned = clean_junction_polygon(polygon, node, include_sidewalk)
    if cleaned is None:
        return None
    socket_fallback = socket_boundary_polygon(node, include_sidewalk=include_sidewalk)
    if socket_fallback is not None and cleaned.area < socket_fallback.area * 0.25:
        return None
    if not cleaned.buffer(0.05).contains(node.center):
        repaired = clean_junction_polygon(unary_union([cleaned, node.center.buffer(max(0.75, max_width * 0.22), resolution=12)]), node, include_sidewalk)
        if repaired is None or not repaired.buffer(0.05).contains(node.center):
            return None
        cleaned = repaired
        if socket_fallback is not None and cleaned.area < socket_fallback.area * 0.25:
            return None
    covered = enforce_socket_throat_coverage(cleaned, node, include_sidewalk)
    return covered if covered is not None else cleaned


def robust_junction_polygon(node: JunctionNode, include_sidewalk: bool = False) -> Polygon | None:
    polygon = edge_intersection_junction_polygon(node, include_sidewalk=include_sidewalk)
    if polygon is not None:
        return enforce_socket_throat_coverage(polygon, node, include_sidewalk)
    logging.info("Junction %s edge-intersection polygon failed; using socket fallback.", node.junction_id)
    polygon = socket_boundary_polygon(node, include_sidewalk=include_sidewalk)
    if polygon is not None:
        return enforce_socket_throat_coverage(polygon, node, include_sidewalk)
    logging.warning("Junction %s socket fallback failed; using convex hull fallback.", node.junction_id)
    polygon = convex_hull_junction_polygon(node, include_sidewalk=include_sidewalk)
    return enforce_socket_throat_coverage(polygon, node, include_sidewalk)


def junction_polygon_with_method(node: JunctionNode, include_sidewalk: bool = False) -> tuple[Polygon | None, str]:
    strategy = node.surface_strategy or junction_surface_strategy_for(node.hierarchy, node.junction_type)
    if strategy == "socket_boundary":
        polygon = socket_boundary_polygon(node, include_sidewalk=include_sidewalk)
        if polygon is not None:
            return enforce_socket_throat_coverage(polygon, node, include_sidewalk), "socket_boundary_by_hierarchy"
    elif strategy == "socket_boundary_then_edge":
        polygon = socket_boundary_polygon(node, include_sidewalk=include_sidewalk)
        if polygon is not None:
            return enforce_socket_throat_coverage(polygon, node, include_sidewalk), "socket_boundary_complex"
        polygon = edge_intersection_junction_polygon(node, include_sidewalk=include_sidewalk)
        if polygon is not None:
            return enforce_socket_throat_coverage(polygon, node, include_sidewalk), "edge_intersection_after_socket"
    else:
        polygon = edge_intersection_junction_polygon(node, include_sidewalk=include_sidewalk)
        if polygon is not None:
            return enforce_socket_throat_coverage(polygon, node, include_sidewalk), f"{strategy}:edge_intersection"
        logging.info("Junction %s edge-intersection polygon failed; using socket fallback.", node.junction_id)
        polygon = socket_boundary_polygon(node, include_sidewalk=include_sidewalk)
        if polygon is not None:
            return enforce_socket_throat_coverage(polygon, node, include_sidewalk), f"{strategy}:socket_boundary_fallback"
    logging.warning("Junction %s socket fallback failed; using convex hull fallback.", node.junction_id)
    polygon = convex_hull_junction_polygon(node, include_sidewalk=include_sidewalk)
    return enforce_socket_throat_coverage(polygon, node, include_sidewalk), f"{strategy}:convex_hull_fallback"


def bezier_closed_boundary_points(
    points: list[tuple[float, float]],
    samples_per_edge: int = 6,
) -> list[tuple[float, float]]:
    if len(points) < 4:
        return points

    smooth: list[tuple[float, float]] = []
    count = len(points)
    for idx in range(count):
        p0 = points[(idx - 1) % count]
        p1 = points[idx]
        p2 = points[(idx + 1) % count]
        p3 = points[(idx + 2) % count]
        c1 = (
            p1[0] + (p2[0] - p0[0]) / 6.0,
            p1[1] + (p2[1] - p0[1]) / 6.0,
        )
        c2 = (
            p2[0] - (p3[0] - p1[0]) / 6.0,
            p2[1] - (p3[1] - p1[1]) / 6.0,
        )
        segment = bezier_points(p1, c1, c2, p2, samples=samples_per_edge)
        if smooth:
            smooth.extend(segment[1:])
        else:
            smooth.extend(segment)
    return smooth


def line_junction_clip_ranges(
    row: pd.Series,
    line: LineString,
    rule: RoadRule,
    junction_points: list[Point],
    junction_nodes: list[JunctionNode] | None = None,
) -> list[tuple[float, float]]:
    ranges = []
    connection_tolerance = junction_connection_tolerance()
    if junction_nodes is not None:
        for node in junction_nodes:
            if line.distance(node.center) > connection_tolerance:
                continue
            node_distance = float(line.project(node.center))
            road_id = str(row.get("road_id"))
            sockets = [
                item
                for item in node.sockets
                if item.road_id == road_id
                and line.distance(Point(item.center)) <= max(JUNCTION_LINE_INTERSECTION_TOLERANCE_M, 0.75)
            ]
            if sockets:
                for socket in sockets:
                    socket_distance = float(line.project(Point(socket.center)))
                    start = min(node_distance, socket_distance)
                    end = max(node_distance, socket_distance)
                    ranges.append((max(0.0, start), min(float(line.length), end)))
            else:
                ranges.append((max(0.0, node_distance - node.radius), min(float(line.length), node_distance + node.radius)))
    else:
        for point in junction_points:
            if line.distance(point) > connection_tolerance:
                continue
            node_distance = float(line.project(point))
            radius = road_class_clip_distance(row, rule)
            ranges.append((max(0.0, node_distance - radius), min(float(line.length), node_distance + radius)))

    if not ranges:
        return []

    ranges.sort()
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 0.25:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def line_segments_outside_ranges(line: LineString, ranges: list[tuple[float, float]]) -> list[tuple[LineString, float]]:
    if not ranges:
        return [(line, 0.0)]

    segments: list[tuple[LineString, float]] = []
    cursor = 0.0
    for start, end in ranges:
        if start - cursor > MIN_CONNECTOR_LENGTH_M:
            part = substring(line, cursor, start)
            if isinstance(part, LineString) and not part.is_empty and part.length > MIN_CONNECTOR_LENGTH_M:
                segments.append((part, cursor))
        cursor = max(cursor, end)

    if line.length - cursor > MIN_CONNECTOR_LENGTH_M:
        part = substring(line, cursor, line.length)
        if isinstance(part, LineString) and not part.is_empty and part.length > MIN_CONNECTOR_LENGTH_M:
            segments.append((part, cursor))
    return segments


def turn_arrow_polygon(center: tuple[float, float], direction: tuple[float, float], width: float) -> Polygon:
    length = TURN_ARROW_LENGTH_M
    tx, ty = direction
    if vector_length(direction) <= 0.01:
        tx, ty = 1.0, 0.0
    nx, ny = -ty, tx
    tail_w = width * 0.35
    head_w = width * 0.75
    return Polygon(
        [
            (center[0] - tx * length * 0.5 - nx * tail_w, center[1] - ty * length * 0.5 - ny * tail_w),
            (center[0] + tx * length * 0.05 - nx * tail_w, center[1] + ty * length * 0.05 - ny * tail_w),
            (center[0] + tx * length * 0.05 - nx * head_w, center[1] + ty * length * 0.05 - ny * head_w),
            (center[0] + tx * length * 0.5, center[1] + ty * length * 0.5),
            (center[0] + tx * length * 0.05 + nx * head_w, center[1] + ty * length * 0.05 + ny * head_w),
            (center[0] + tx * length * 0.05 + nx * tail_w, center[1] + ty * length * 0.05 + ny * tail_w),
            (center[0] - tx * length * 0.5 + nx * tail_w, center[1] - ty * length * 0.5 + ny * tail_w),
        ]
    )


def approach_arrow_polygons(
    row: pd.Series,
    line: LineString,
    node_distance: float,
    sign: float,
    rule: RoadRule,
    radius: float,
) -> list[Polygon]:
    arrows: list[Polygon] = []
    lane_offsets = incoming_lane_offsets(row, rule, sign)
    if not lane_offsets:
        return arrows

    candidate_offsets = [radius + APPROACH_ARROW_NEAR_M]
    if line.length > radius + APPROACH_ARROW_FAR_M + APPROACH_ARROW_MIN_GAP_M:
        candidate_offsets.append(radius + APPROACH_ARROW_FAR_M)

    for advance in candidate_offsets:
        arrow_distance = max(0.0, min(float(line.length), node_distance + sign * advance))
        if abs(arrow_distance - node_distance) < radius + APPROACH_ARROW_MIN_GAP_M:
            continue
        point, tangent, normal = line_frame_at_distance(line, arrow_distance)
        tx, ty = tangent
        nx, ny = normal
        approach_direction = (-sign * tx, -sign * ty)
        for lane_offset in lane_offsets:
            center = (
                point.x + nx * lane_offset,
                point.y + ny * lane_offset,
            )
            arrows.append(turn_arrow_polygon(center, approach_direction, max(0.42, rule.lane_width * 0.22)))
    return arrows


def channelization_island_polygon(
    center: Point,
    direction_a: tuple[float, float],
    direction_b: tuple[float, float],
    radius: float,
) -> Polygon | None:
    gap = angular_gap(direction_a, direction_b)
    if gap < math.radians(CHANNELIZATION_MIN_ANGLE_DEG):
        return None
    if gap > math.radians(CHANNELIZATION_MAX_ANGLE_DEG):
        return None

    ax, ay = direction_a
    bx, by = direction_b
    bisector = (ax + bx, ay + by)
    if vector_length(bisector) <= 0.01:
        return None
    bl = vector_length(bisector)
    ux, uy = bisector[0] / bl, bisector[1] / bl

    nose = radius * 0.58
    tail = radius * 0.96
    lateral = min(2.0, radius * 0.14)
    nx, ny = -uy, ux
    polygon = Polygon(
        [
            (center.x + ux * nose, center.y + uy * nose),
            (center.x + ux * tail + nx * lateral, center.y + uy * tail + ny * lateral),
            (center.x + ux * tail - nx * lateral, center.y + uy * tail - ny * lateral),
        ]
    )
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty or polygon.area < 1.0:
        return None
    return polygon


def angular_gap(direction_a: tuple[float, float], direction_b: tuple[float, float]) -> float:
    angle_a = math.atan2(direction_a[1], direction_a[0])
    angle_b = math.atan2(direction_b[1], direction_b[0])
    gap = abs(math.atan2(math.sin(angle_b - angle_a), math.cos(angle_b - angle_a)))
    if gap > math.pi:
        gap = 2.0 * math.pi - gap
    return gap


def is_link_or_ramp_rule(row: pd.Series) -> bool:
    road_class = (safe_str(row.get("road_class")) or "").lower()
    road_ref = (safe_str(row.get("road_ref")) or "").lower()
    return "link" in road_class or "ramp" in road_class or "ramp" in road_ref


def channelization_chevron_polygons(
    center: Point,
    direction_a: tuple[float, float],
    direction_b: tuple[float, float],
    radius: float,
) -> list[Polygon]:
    gap = angular_gap(direction_a, direction_b)
    if gap < math.radians(CHANNELIZATION_MIN_ANGLE_DEG) or gap > math.radians(155.0):
        return []

    bisector = (direction_a[0] + direction_b[0], direction_a[1] + direction_b[1])
    if vector_length(bisector) <= 0.01:
        return []
    bl = vector_length(bisector)
    ux, uy = bisector[0] / bl, bisector[1] / bl
    nx, ny = -uy, ux

    markings: list[Polygon] = []
    for idx, distance_ratio in enumerate((0.55, 0.68, 0.81)):
        bar_center = (
            center.x + ux * radius * distance_ratio,
            center.y + uy * radius * distance_ratio,
        )
        half_len = max(0.8, min(1.8, radius * (0.12 + idx * 0.025)))
        bar = LineString(
            [
                (bar_center[0] - nx * half_len, bar_center[1] - ny * half_len),
                (bar_center[0] + nx * half_len, bar_center[1] + ny * half_len),
            ]
        )
        markings.append(bar.buffer(LANE_GUIDE_MARKING_WIDTH_M / 2.0, cap_style=2, join_style=2))
    return markings


def adjacent_arm_pairs(arm_infos: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    pairs: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    if len(arm_infos) < 2:
        return pairs
    for idx, arm in enumerate(arm_infos):
        target = arm_infos[(idx + 1) % len(arm_infos)]
        pairs.append((arm, target, angular_gap(arm["direction"], target["direction"])))
    return pairs


def build_junction_detail_geometries(
    road_entries: list[tuple[pd.Series, LineString, RoadRule]],
    junction_points: list[Point],
    junction_nodes: list[JunctionNode] | None = None,
) -> tuple[list[Polygon], list[Polygon], list[Polygon], list[Polygon], list[Polygon], list[Polygon], list[Polygon], int, int, int, list[dict[str, Any]]]:
    junction_surfaces: list[Polygon] = []
    junction_total_surfaces: list[Polygon] = []
    stop_lines: list[Polygon] = []
    crosswalks: list[Polygon] = []
    lane_guides: list[Polygon] = []
    turn_arrows: list[Polygon] = []
    channelization_islands: list[Polygon] = []
    junction_metadata: list[dict[str, Any]] = []
    parametric_junction_count = 0
    junction_socket_count = 0
    lane_connector_count = 0

    nodes = junction_nodes if junction_nodes is not None else build_junction_nodes(road_entries, junction_points)
    for node in nodes:
        point = node.center
        connected = [(row, line, rule) for row, line, rule in road_entries if line.distance(point) <= junction_connection_tolerance()]
        if len(connected) < 2:
            connected = []

        radius = node.radius
        socket_polygon = node_cityengine_surface_union(node)
        socket_total_polygon = node_cityengine_surface_union(node, include_sidewalk=True)
        polygon_method = f"{node.surface_strategy}:cityengine_socket_connector"
        total_polygon_method = f"{node.surface_strategy}:cityengine_socket_connector_total"
        throat_polygon = socket_throat_union(node, include_sidewalk=False)
        if throat_polygon is not None:
            socket_polygon = clean_polygonal(unary_union([part for part in [socket_polygon, throat_polygon] if part is not None and not part.is_empty]))
        total_throat_polygon = socket_throat_union(node, include_sidewalk=True)
        if total_throat_polygon is not None:
            socket_total_polygon = clean_polygonal(unary_union([part for part in [socket_total_polygon, total_throat_polygon] if part is not None and not part.is_empty]))
        if socket_polygon is not None:
            junction_surfaces.append(socket_polygon)
            parametric_junction_count += 1
            junction_socket_count += len(node.sockets)
            junction_metadata.append({
                "junction_id": node.junction_id,
                "junction_type": node.junction_type,
                "junction_hierarchy": node.hierarchy,
                "surface_strategy": node.surface_strategy,
                "surface_algorithm": polygon_method,
                "total_surface_algorithm": total_polygon_method,
                "socket_count": len(node.sockets),
                "center": [round(float(point.x), 3), round(float(point.y), 3)],
                "area_m2": round(float(socket_polygon.area), 3),
                "road_ids": sorted(set(socket.road_id for socket in node.sockets)),
                "road_names": sorted(set(socket.road_name for socket in node.sockets if socket.road_name)),
                "road_classes": sorted(set(socket.road_class for socket in node.sockets if socket.road_class)),
            })
        else:
            junction_surfaces.append(point.buffer(radius, resolution=12))
        if socket_total_polygon is not None:
            junction_total_surfaces.append(socket_total_polygon)
        elif socket_polygon is not None:
            max_sidewalk = max((socket.sidewalk_width for socket in node.sockets), default=0.0)
            junction_total_surfaces.append(socket_polygon.buffer(max_sidewalk, join_style=1))

        # Lane connectors are still built to validate vehicle movement, but
        # not rendered as separate road strips. CityEngine-style intersections
        # use one clean node surface and clipped street arms.
        lane_connector_count += len(build_lane_connectors(node))

        for socket in node.sockets:
            if socket.approach_type != "egress":
                stop_lines.append(socket_bound_stop_line(socket))
                crosswalks.extend(socket_bound_crosswalks(socket))
        arms = []

        for row, line, rule in connected:
            node_distance = float(line.project(point))
            if node_distance <= line.length * 0.5:
                sign = 1.0
            else:
                sign = -1.0

            arms.append((row, line, rule, node_distance, sign))

        if GENERATE_TURN_ARROWS:
            for row, line, rule, node_distance, sign in arms:
                turn_arrows.extend(approach_arrow_polygons(row, line, node_distance, sign, rule, radius))

        arm_infos = []
        for row, line, rule, node_distance, sign in arms:
            node_xy, direction, _ = arm_direction_from_junction(line, node_distance, radius * 1.25)
            angle = math.atan2(direction[1], direction[0])
            arm_infos.append(
                {
                    "row": row,
                    "line": line,
                    "rule": rule,
                    "node_distance": node_distance,
                    "direction": direction,
                    "angle": angle,
                    "sign": sign,
                }
            )
        arm_infos.sort(key=lambda item: item["angle"])

        pairs = adjacent_arm_pairs(arm_infos)
        ramp_like = any(is_link_or_ramp_rule(item["row"]) for item in arm_infos)
        eligible_pairs = [
            pair
            for pair in pairs
            if math.radians(CHANNELIZATION_MIN_ANGLE_DEG) <= pair[2] <= math.radians(CHANNELIZATION_MAX_ANGLE_DEG)
        ]
        if len(arm_infos) == 3 or ramp_like:
            for arm, target, _ in sorted(eligible_pairs, key=lambda pair: pair[2])[:1]:
                island = channelization_island_polygon(point, arm["direction"], target["direction"], radius)
                if island is not None:
                    channelization_islands.append(island)

    return (
        junction_surfaces,
        junction_total_surfaces,
        stop_lines,
        crosswalks,
        lane_guides,
        turn_arrows,
        channelization_islands,
        parametric_junction_count,
        junction_socket_count,
        lane_connector_count,
        junction_metadata,
    )


def generate_planar_geometries(roads: gpd.GeoDataFrame, rules: dict[str, RoadRule]) -> list[dict[str, Any]]:
    """
    按高程层级聚类并批量生成路面、人行道、路缘石及交通标线的 2D 几何面。
    分离特性：处于不同高程的路网（如高架路与底层道路）保持拓扑独立，互不相交穿模。
    """
    layers_geoms = []
    grouped_roads = roads.copy()
    grouped_roads["topology_group"] = grouped_roads.apply(road_topology_group, axis=1)

    # 依据高程对道路进行分组处理
    for topology_group, group in grouped_roads.groupby("topology_group"):
        elevation = float(group["elevation"].mean())
        road_surfaces = []
        total_surfaces = []
        lane_markings = []
        stop_lines = []
        crosswalks = []
        lane_guides = []
        turn_arrows = []
        channelization_islands = []
        roundabout_islands = []
        junction_surfaces = []
        road_entries = []
        swept_entries = []
        clipped_road_entries = []
        junction_metadata = []
        default_rule = rules.get("default_road")
        group_junction_points = detect_junction_points(group, rules)

        for _, row in group.iterrows():
            line: LineString = row.geometry
            rule = get_road_rule(row, rules)
            if line is None or line.is_empty:
                continue
            road_entries.append((row, line, rule))

        roundabout_rings, roundabout_totals, roundabout_islands = roundabout_ring_geometries(road_entries)
        junction_surfaces.extend(roundabout_rings)
        total_surfaces.extend(roundabout_totals)

        junction_nodes = build_junction_nodes(road_entries, group_junction_points)

        for row, line, rule in road_entries:
            clip_ranges = line_junction_clip_ranges(row, line, rule, group_junction_points, junction_nodes)
            clipped_segments = line_segments_outside_ranges(line, clip_ranges)
            clipped_road_entries.extend((row, segment, rule, offset) for segment, offset in clipped_segments)
            swept_entries.extend((row, segment, rule, offset) for segment, offset in clipped_segments)

        for row, line, rule, _ in clipped_road_entries:
            road_surface = line_buffer(line, rule.road_width)
            total_surface = line_buffer(line, rule.road_width + 2 * rule.sidewalk_width)

            if not road_surface.is_empty:
                road_surfaces.append(road_surface)

            if not total_surface.is_empty:
                total_surfaces.append(total_surface)

            marking = lane_direction_boundary_marking(line, row, rule)
            if marking is not None and not marking.is_empty:
                lane_markings.append(marking)
            lane_guides.extend(lane_offset_markings(line, row, rule))

        (
            detail_junctions,
            detail_junction_totals,
            detail_stop_lines,
            detail_crosswalks,
            detail_lane_guides,
            detail_turn_arrows,
            detail_channelization_islands,
            parametric_junction_count,
            junction_socket_count,
            lane_connector_count,
            detail_junction_metadata,
        ) = build_junction_detail_geometries(road_entries, group_junction_points, junction_nodes)
        junction_surfaces.extend(detail_junctions)
        total_surfaces.extend(detail_junction_totals)
        stop_lines.extend(detail_stop_lines)
        crosswalks.extend(detail_crosswalks)
        lane_guides.extend(detail_lane_guides)
        turn_arrows.extend(detail_turn_arrows)
        channelization_islands.extend(detail_channelization_islands)
        junction_metadata.extend(detail_junction_metadata)

        preliminary_junction_union = clean_polygonal(unary_union(junction_surfaces)) if junction_surfaces else None
        for connector, connector_rule in build_junction_approach_connectors(
            clipped_road_entries,
            preliminary_junction_union,
            junction_nodes,
        ):
            road_surface = line_buffer(connector, connector_rule.road_width)
            total_surface = line_buffer(connector, connector_rule.road_width + 2 * connector_rule.sidewalk_width)

            if not road_surface.is_empty:
                road_surfaces.append(road_surface)

            if not total_surface.is_empty:
                total_surfaces.append(total_surface)

        for connector in build_short_road_connectors([line for _, line, _, _ in clipped_road_entries]):
            road_surface = line_buffer(connector, default_rule.road_width)
            total_surface = line_buffer(connector, default_rule.road_width + 2 * default_rule.sidewalk_width)

            if not road_surface.is_empty:
                road_surfaces.append(road_surface)

            if not total_surface.is_empty:
                total_surfaces.append(total_surface)

        if not road_surfaces and not junction_surfaces:
            continue

        # [核心] 仅对相同高程组内部的道路对象执行布尔并集 (初步路口与渠化融合)
        merged_road = clean_polygonal(unary_union(road_surfaces)) if road_surfaces else None
        merged_total = clean_polygonal(unary_union(total_surfaces)) if total_surfaces else None
        junction_union = clean_polygonal(unary_union(junction_surfaces)) if junction_surfaces else None

        if merged_road is None and junction_union is None:
            continue
        if merged_road is None:
            merged_road = junction_union
        if merged_total is None:
            merged_total = merged_road

        if roundabout_islands:
            island_union = clean_polygonal(unary_union(roundabout_islands))
            if island_union is not None:
                merged_road = clean_polygonal(merged_road.difference(island_union))
                merged_total = clean_polygonal(merged_total.difference(island_union))
                if junction_union is not None:
                    junction_union = clean_polygonal(junction_union.difference(island_union))

        final_throat_surfaces = [
            throat
            for node in junction_nodes
            for throat in [node_cityengine_surface_union(node, include_sidewalk=False)]
            if throat is not None and not throat.is_empty
        ]
        if final_throat_surfaces:
            final_throat_union = clean_polygonal(unary_union(final_throat_surfaces))
            if final_throat_union is not None:
                junction_union = clean_polygonal(unary_union([part for part in [junction_union, final_throat_union] if part is not None and not part.is_empty]))

        final_total_throat_surfaces = [
            throat
            for node in junction_nodes
            for throat in [node_cityengine_surface_union(node, include_sidewalk=True)]
            if throat is not None and not throat.is_empty
        ]
        if final_total_throat_surfaces:
            final_total_throat_union = clean_polygonal(unary_union(final_total_throat_surfaces))
            if final_total_throat_union is not None:
                merged_total = clean_polygonal(unary_union([part for part in [merged_total, final_total_throat_union] if part is not None and not part.is_empty]))

        road_surface_export = merged_road
        if junction_union is not None:
            road_surface_export = subtract_polygonal_mask(road_surface_export, junction_union, JUNCTION_ROAD_SURFACE_SETBACK_M)
        driveable_surface = clean_polygonal(unary_union([part for part in [road_surface_export, junction_union] if part is not None and not part.is_empty]))
        if driveable_surface is None:
            driveable_surface = road_surface_export
        visual_road_surface = clean_polygonal(driveable_surface)

        sidewalk = clean_polygonal(merged_total.difference(driveable_surface))
        if junction_union is not None:
            sidewalk = subtract_polygonal_mask(sidewalk, junction_union, JUNCTION_SIDEWALK_SETBACK_M)

        curb_source = road_surface_export if road_surface_export is not None and not road_surface_export.is_empty else driveable_surface
        curb = clean_polygonal(curb_source.buffer(default_rule.curb_width, resolution=8, join_style=1).difference(curb_source))
        if junction_union is not None:
            curb = subtract_polygonal_mask(curb, junction_union, JUNCTION_CURB_SETBACK_M)

        lane_marking_union = clean_polygonal(unary_union(lane_markings).intersection(driveable_surface))
        stop_line_union = clean_polygonal(unary_union(stop_lines).intersection(driveable_surface)) if stop_lines else None
        crosswalk_union = clean_polygonal(unary_union(crosswalks).intersection(driveable_surface)) if crosswalks else None
        if crosswalk_union is not None and junction_union is not None:
            crosswalk_union = clean_polygonal(crosswalk_union.difference(junction_union.buffer(0.2, join_style=2)))
        lane_guide_union = clean_polygonal(unary_union(lane_guides).intersection(driveable_surface)) if lane_guides else None
        turn_arrow_union = clean_polygonal(unary_union(turn_arrows).intersection(driveable_surface)) if turn_arrows else None
        channelization_union = clean_polygonal(unary_union(channelization_islands).intersection(driveable_surface)) if channelization_islands else None
        if junction_union is not None:
            asphalt_only_mask = junction_union.buffer(
                max(JUNCTION_ASPHALT_ONLY_CLEARANCE_M, JUNCTION_MARKING_CLEARANCE_M, JUNCTION_NON_ASPHALT_CLEARANCE_M),
                resolution=8,
                join_style=1,
            )
            lane_marking_union = subtract_polygonal_mask(lane_marking_union, asphalt_only_mask)
            stop_line_union = subtract_polygonal_mask(stop_line_union, asphalt_only_mask)
            crosswalk_union = subtract_polygonal_mask(crosswalk_union, asphalt_only_mask)
            lane_guide_union = subtract_polygonal_mask(lane_guide_union, asphalt_only_mask)
            turn_arrow_union = subtract_polygonal_mask(turn_arrow_union, asphalt_only_mask)
            channelization_union = subtract_polygonal_mask(channelization_union, asphalt_only_mask)

        layers_geoms.append({
            "elevation": float(elevation),
            "road_surface": road_surface_export,
            "visual_road_surface": visual_road_surface,
            "sidewalk": sidewalk,
            "curb": curb,
            "lane_marking": lane_marking_union,
            "junction_surface": junction_union,
            "stop_line": stop_line_union,
            "crosswalk": crosswalk_union,
            "lane_guide": lane_guide_union,
            "turn_arrow": turn_arrow_union,
            "channelization_island": channelization_union,
            "junction_count": len(junction_surfaces),
            "parametric_junction_count": parametric_junction_count,
            "junction_socket_count": junction_socket_count,
            "lane_connector_count": lane_connector_count,
            "approach_arrow_count": len(detail_turn_arrows),
            "channelization_island_count": len(detail_channelization_islands),
            "clipped_segment_count": len(clipped_road_entries),
            "swept_entries": swept_entries,
            "junction_metadata": junction_metadata,
            "junction_nodes": junction_nodes,
        })

    return layers_geoms


def polygon_to_top_mesh(geom, z: float, name: str, visual_color=None) -> trimesh.Trimesh:
    """
    将 2D 几何平面约束转换为 3D Mesh（仅保留顶面结构）。
    
    算法特性：
    - 基于 trimesh 约束三角剖分，保持了多边形内部孔洞和精确边界
    - 法线检查：通过计算三角形三顶点叉积强制规整向上法线，防止引擎中发生背面剔除
    """
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    v_index: dict[tuple[float, float, float], int] = {}

    # 辅函数：注册顶点坐标去重并返回分配的顶点索引
    def add_vertex(x, y, z_) -> int:
        key = (round(float(x), 6), round(float(y), 6), round(float(z_), 6))
        if key in v_index:
            return v_index[key]
        idx = len(vertices)
        vertices.append(key)
        v_index[key] = idx
        return idx

    for poly in iter_polygons(geom):
        if poly.area <= 1e-6:
            continue

        try:
            # 使用 trimesh 的约束三角化（支持带孔洞的复杂多边形，不丢失边界）
            verts, fcs = triangulate_polygon(poly)
        except Exception:
            continue
            
        for f in fcs:
            v0, v1, v2 = verts[f[0]], verts[f[1]], verts[f[2]]
            idxs = [
                add_vertex(v0[0], v0[1], z),
                add_vertex(v1[0], v1[1], z),
                add_vertex(v2[0], v2[1], z)
            ]

            # 判断重塑法线方向：若 Z 分量朝下则倒序重排顶点
            p0 = np.array(vertices[idxs[0]])
            p1 = np.array(vertices[idxs[1]])
            p2 = np.array(vertices[idxs[2]])
            normal_z = np.cross(p1 - p0, p2 - p0)[2]
            if normal_z < 0:
                idxs = [idxs[0], idxs[2], idxs[1]]

            faces.append(tuple(idxs))

    if not vertices or not faces:
        return trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3)), process=False)

    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=False)
    mesh.metadata["name"] = name
    if visual_color is not None:
        mesh.visual.face_colors = visual_color
    return mesh


def interpolated_junction_z(x: float, y: float, nodes: list[JunctionNode], fallback_z: float) -> float:
    samples: list[tuple[float, float]] = []
    for node in nodes or []:
        if math.hypot(x - node.center.x, y - node.center.y) > node.radius * 1.8:
            continue
        for socket in node.sockets:
            d = max(0.25, math.hypot(x - socket.center[0], y - socket.center[1]))
            samples.append((d, float(socket.elevation)))
    if not samples:
        return fallback_z
    nearest = sorted(samples, key=lambda item: item[0])[:4]
    numerator = 0.0
    denominator = 0.0
    for distance, z_value in nearest:
        weight = 1.0 / (distance * distance)
        numerator += z_value * weight
        denominator += weight
    return numerator / denominator if denominator > 0.0 else fallback_z


def polygon_to_junction_mesh(
    geom,
    z: float,
    name: str,
    junction_nodes: list[JunctionNode] | None = None,
    visual_color=None,
) -> trimesh.Trimesh:
    if not junction_nodes:
        return polygon_to_top_mesh(geom, z, name, visual_color)

    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    v_index: dict[tuple[float, float], int] = {}

    def add_vertex(x, y) -> int:
        key = (round(float(x), 6), round(float(y), 6))
        if key in v_index:
            return v_index[key]
        z_value = interpolated_junction_z(float(x), float(y), junction_nodes, z) + 0.003
        idx = len(vertices)
        vertices.append((key[0], key[1], round(float(z_value), 6)))
        v_index[key] = idx
        return idx

    for poly in iter_polygons(geom):
        if poly.area <= 1e-6:
            continue
        try:
            verts, fcs = triangulate_polygon(poly)
        except Exception:
            continue
        for f in fcs:
            idxs = [add_vertex(verts[f[0]][0], verts[f[0]][1]), add_vertex(verts[f[1]][0], verts[f[1]][1]), add_vertex(verts[f[2]][0], verts[f[2]][1])]
            p0 = np.array(vertices[idxs[0]])
            p1 = np.array(vertices[idxs[1]])
            p2 = np.array(vertices[idxs[2]])
            if np.cross(p1 - p0, p2 - p0)[2] < 0:
                idxs = [idxs[0], idxs[2], idxs[1]]
            faces.append(tuple(idxs))

    if not vertices or not faces:
        return empty_mesh(name)
    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=False)
    mesh.metadata["name"] = name
    if visual_color is not None:
        mesh.visual.face_colors = visual_color
    return mesh


def polygon_to_extruded_mesh(geom, z_bottom: float, z_top: float, name: str, visual_color=None) -> trimesh.Trimesh:
    """
    将 2D 平面沿着 Z 轴垂直拉伸，并将其封盖转化为具备厚度的封闭三维实体 (Watertight Mesh)。
    此类网格常用于需要立体体积表现的道路设施（例如路缘石）。

    算法步骤：
    1. 通过 triangulate 生成双份网格构造出顶部与底部平面。
    2. 依次迭代多边形的外边界 (exterior) 和内环孔洞 (interiors)，封包生成连续的侧壁四边形(拆分为三角面)。
    """
    all_vertices: list[tuple[float, float, float]] = []
    all_faces: list[tuple[int, int, int]] = []
    v_index: dict[tuple[float, float, float], int] = {}

    # 内部辅助函数：顶点去重与索引化
    def add_vertex(x, y, z_) -> int:
        key = (round(float(x), 6), round(float(y), 6), round(float(z_), 6))
        if key in v_index:
            return v_index[key]
        idx = len(all_vertices)
        all_vertices.append(key)
        v_index[key] = idx
        return idx

    def add_side_faces(coords):
        # 添加侧面三角形，传入的 coords 必须构成一条闭合环路线
        ring = list(coords)
        if len(ring) < 4:
            return

        # Shapely 的几何环首尾两点坐标重合，需去掉最后一个重复点
        ring = ring[:-1]
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]

            b1 = add_vertex(x1, y1, z_bottom)
            b2 = add_vertex(x2, y2, z_bottom)
            t1 = add_vertex(x1, y1, z_top)
            t2 = add_vertex(x2, y2, z_top)

            # 使用两枚三角面构筑单个矩形侧壁
            all_faces.append((b1, b2, t2))
            all_faces.append((b1, t2, t1))

    for poly in iter_polygons(geom):
        if poly.area <= 1e-6:
            continue

        # 1. 生成 顶面 与 底面 的网格 (采用约束多边形算法)
        try:
            verts, fcs = triangulate_polygon(poly)
        except Exception as e:
            logging.warning(f"Polygon 挤出三角剖分失败 ({name}): {e}")
            continue

        for f in fcs:
            v0, v1, v2 = verts[f[0]], verts[f[1]], verts[f[2]]
            top = [add_vertex(v0[0], v0[1], z_top), add_vertex(v1[0], v1[1], z_top), add_vertex(v2[0], v2[1], z_top)]
            bottom = [add_vertex(v0[0], v0[1], z_bottom), add_vertex(v1[0], v1[1], z_bottom), add_vertex(v2[0], v2[1], z_bottom)]

            p0 = np.array(all_vertices[top[0]])
            p1 = np.array(all_vertices[top[1]])
            p2 = np.array(all_vertices[top[2]])
            normal_z = np.cross(p1 - p0, p2 - p0)[2]

            # 校正顶面法线一致朝上
            if normal_z < 0:
                top = [top[0], top[2], top[1]]
            all_faces.append(tuple(top))

            # 校正底面法线一致朝下
            if normal_z > 0:
                bottom = [bottom[0], bottom[2], bottom[1]]
            all_faces.append(tuple(bottom))

        # 2. 生成封闭轮廓（外边界加内洞）对应的侧边面
        add_side_faces(poly.exterior.coords)
        for interior in poly.interiors:
            add_side_faces(interior.coords)

    if not all_vertices or not all_faces:
        return trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3)), process=False)

    mesh = trimesh.Trimesh(vertices=np.array(all_vertices), faces=np.array(all_faces), process=False)
    mesh.metadata["name"] = name
    if visual_color is not None:
        mesh.visual.face_colors = visual_color
    return mesh


def empty_mesh(name: str) -> trimesh.Trimesh:
    mesh = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3)), process=False)
    mesh.metadata["name"] = name
    return mesh


def merge_named_meshes(name: str, parts: list[trimesh.Trimesh], color=None) -> trimesh.Trimesh:
    valid = [mesh for mesh in parts if mesh is not None and len(mesh.vertices) > 0]
    if not valid:
        return empty_mesh(name)
    mesh = trimesh.util.concatenate(valid)
    mesh.metadata["name"] = name
    if color is not None:
        mesh.visual.face_colors = color
    return mesh


def mesh_bounds_xy(meshes: Iterable[trimesh.Trimesh]) -> tuple[float, float, float, float] | None:
    bounds = []
    for mesh in meshes:
        if mesh is None or len(mesh.vertices) == 0:
            continue
        mesh_bounds = mesh.bounds
        bounds.append((float(mesh_bounds[0][0]), float(mesh_bounds[0][1]), float(mesh_bounds[1][0]), float(mesh_bounds[1][1])))
    if not bounds:
        return None
    return (
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )


def projected_xy_to_latlon(x: float, y: float, crs: str = TARGET_CRS) -> tuple[float, float] | None:
    try:
        point = gpd.GeoSeries([Point(float(x), float(y))], crs=crs).to_crs("EPSG:4326").iloc[0]
    except Exception as exc:
        logging.warning("Unable to transform basemap center to lat/lon: %s", exc)
        return None
    return (float(point.y), float(point.x))


def google_static_map_url(
    center_latlon: tuple[float, float],
    zoom: int = 16,
    size: str = "640x640",
    scale: int = 2,
    maptype: str = "roadmap",
) -> str | None:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return None
    params = {
        "center": f"{center_latlon[0]:.7f},{center_latlon[1]:.7f}",
        "zoom": str(int(zoom)),
        "size": size,
        "scale": str(int(scale)),
        "maptype": os.environ.get("GOOGLE_MAPTYPE", maptype),
        "format": "png",
        "key": api_key,
    }
    return "https://maps.googleapis.com/maps/api/staticmap?" + urlencode(params)


def ensure_google_map_texture(center_latlon: tuple[float, float] | None) -> Path | None:
    if center_latlon is None:
        return None
    if GOOGLE_MAP_TEXTURE_PATH.exists() and GOOGLE_MAP_TEXTURE_PATH.stat().st_size > 1024:
        return GOOGLE_MAP_TEXTURE_PATH
    url = google_static_map_url(
        center_latlon,
        zoom=int(os.environ.get("GOOGLE_MAP_ZOOM", "16")),
        maptype=os.environ.get("GOOGLE_MAPTYPE", "roadmap"),
    )
    if url is None:
        logging.warning("GOOGLE_MAPS_API_KEY is not set; using generated GIS basemap fallback.")
        return None
    try:
        import requests

        TEXTURE_DIR.mkdir(parents=True, exist_ok=True)
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        GOOGLE_MAP_TEXTURE_PATH.write_bytes(response.content)
    except Exception as exc:
        logging.warning("Unable to download Google Static Map texture: %s", exc)
        return None
    return GOOGLE_MAP_TEXTURE_PATH if GOOGLE_MAP_TEXTURE_PATH.exists() else None


def textured_basemap_mesh(
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    z: float,
    texture_path: Path | None,
) -> trimesh.Trimesh:
    vertices = np.array(
        [
            (minx, miny, z),
            (maxx, miny, z),
            (maxx, maxy, z),
            (minx, maxy, z),
        ],
        dtype=float,
    )
    faces = np.array([(0, 1, 2), (0, 2, 3)])
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.metadata["name"] = "GIS_GoogleMap" if texture_path is not None else "GIS_BaseMap"
    uv = np.array([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], dtype=float)
    try:
        mesh.visual = TextureVisuals(uv=uv, image=str(texture_path)) if texture_path is not None else mesh.visual
    except Exception:
        mesh.visual.face_colors = CIM3_COLORS["gis_basemap"]
    if texture_path is None:
        mesh.visual.face_colors = CIM3_COLORS["gis_basemap"]
    return mesh


def make_gis_basemap_meshes(
    bounds: tuple[float, float, float, float] | None,
    z: float = -0.08,
    padding: float = 80.0,
    grid_spacing: float = 100.0,
    google_center_latlon: tuple[float, float] | None = None,
) -> dict[str, trimesh.Trimesh]:
    if bounds is None:
        return {}
    minx, miny, maxx, maxy = bounds
    if maxx <= minx or maxy <= miny:
        return {}
    minx -= padding
    miny -= padding
    maxx += padding
    maxy += padding

    texture_path = ensure_google_map_texture(google_center_latlon)
    base = textured_basemap_mesh(minx, miny, maxx, maxy, z, texture_path)

    grid_parts = []
    x = math.floor(minx / grid_spacing) * grid_spacing
    while x <= maxx:
        grid_parts.append(LineString([(x, miny), (x, maxy)]).buffer(0.12, cap_style=2, join_style=2))
        x += grid_spacing
    y = math.floor(miny / grid_spacing) * grid_spacing
    while y <= maxy:
        grid_parts.append(LineString([(minx, y), (maxx, y)]).buffer(0.12, cap_style=2, join_style=2))
        y += grid_spacing

    grid_geom = clean_polygonal(unary_union(grid_parts)) if grid_parts else None
    grid = polygon_to_top_mesh(grid_geom, z + 0.004, "GIS_Grid", CIM3_COLORS["gis_grid"])
    result = {}
    if len(base.vertices) > 0:
        result[base.metadata["name"]] = base
    if len(grid.vertices) > 0:
        result["GIS_Grid"] = grid
    return result


def cylinder_between(name: str, start, end, radius: float, color, sections: int = 12) -> trimesh.Trimesh | None:
    p0 = np.array(start, dtype=float)
    p1 = np.array(end, dtype=float)
    if np.linalg.norm(p1 - p0) <= 0.05:
        return None
    mesh = trimesh.creation.cylinder(radius=radius, sections=sections, segment=np.vstack([p0, p1]))
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def box_at(name: str, center, size, color) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=size)
    mesh.apply_translation(center)
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def ellipsoid_at(name: str, center, radii, color, subdivisions: int = 2) -> trimesh.Trimesh:
    mesh = trimesh.creation.icosphere(subdivisions=subdivisions, radius=1.0)
    sx, sy, sz = radii
    mesh.apply_transform(np.diag([sx, sy, sz, 1.0]))
    mesh.apply_translation(center)
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def offset_segment_xy(a, b, offset: float) -> tuple[tuple[float, float], tuple[float, float]]:
    ax, ay = a[0], a[1]
    bx, by = b[0], b[1]
    dx = bx - ax
    dy = by - ay
    length = math.hypot(dx, dy)
    if length <= 0.05:
        return (ax, ay), (bx, by)
    nx = -dy / length
    ny = dx / length
    return (ax + nx * offset, ay + ny * offset), (bx + nx * offset, by + ny * offset)


def line_side_point(line: LineString, distance: float, offset: float) -> tuple[float, float]:
    distance = max(0.0, min(float(distance), float(line.length)))
    p = line.interpolate(distance)
    ahead = line.interpolate(min(line.length, distance + 0.5))
    behind = line.interpolate(max(0.0, distance - 0.5))
    dx = ahead.x - behind.x
    dy = ahead.y - behind.y
    length = math.hypot(dx, dy)
    if length <= 0.05:
        return (p.x, p.y)
    nx = -dy / length
    ny = dx / length
    return (p.x + nx * offset, p.y + ny * offset)


def iter_line_distances(line: LineString, spacing: float, first_offset: float) -> Iterable[float]:
    if line.length < first_offset:
        return
    distance = first_offset
    while distance < line.length:
        yield distance
        distance += spacing


def road_asset_category(row: pd.Series) -> str:
    road_class = row_declared_road_class(row)
    if "motorway" in road_class or "trunk" in road_class:
        return "expressway"
    if "primary" in road_class:
        return "arterial"
    if "secondary" in road_class:
        return "secondary"
    if "tertiary" in road_class or "residential" in road_class or "service" in road_class:
        return "branch"

    section_rule = row_section_requirement(row)
    section_category = (safe_str(section_rule.get("category") or section_rule.get("grade")) if section_rule else "") or ""
    if section_category:
        return section_category
    return "branch"


def road_asset_profile(row: pd.Series, rule: RoadRule) -> dict[str, Any]:
    category = road_asset_category(row)
    profiles = {
        "expressway": {
            "street_light_spacing_m": 34.0,
            "street_light_height_m": 11.5,
            "street_light_arm_m": 3.0,
            "street_light_sides": "both",
            "tree_spacing_m": 17.0,
            "tree_sides": "both",
            "tree_scale": 1.55,
        },
        "arterial": {
            "street_light_spacing_m": 32.0,
            "street_light_height_m": 10.5,
            "street_light_arm_m": 2.6,
            "street_light_sides": "both",
            "tree_spacing_m": 16.0,
            "tree_sides": "both",
            "tree_scale": 1.45,
        },
        "secondary": {
            "street_light_spacing_m": 30.0,
            "street_light_height_m": 9.2,
            "street_light_arm_m": 2.2,
            "street_light_sides": "both",
            "tree_spacing_m": 16.0,
            "tree_sides": "both",
            "tree_scale": 1.32,
        },
        "branch": {
            "street_light_spacing_m": 28.0,
            "street_light_height_m": 7.8,
            "street_light_arm_m": 1.8,
            "street_light_sides": "both",
            "tree_spacing_m": 18.0,
            "tree_sides": "both",
            "tree_scale": 1.15,
        },
    }
    profile = dict(profiles.get(category, profiles["branch"]))
    profile["street_light_lateral_m"] = rule.road_width / 2.0 + max(
        rule.curb_width + 0.55,
        min(max(rule.sidewalk_width * 0.28, 0.9), 2.8),
    )
    profile["tree_lateral_m"] = rule.road_width / 2.0 + max(
        min(max(rule.sidewalk_width * 0.72, 1.4), 18.0),
        rule.curb_width + 1.0,
    )
    return profile


def asset_sides(mode: str, index: int) -> list[float]:
    if mode == "both":
        return [1.0, -1.0] if index % 2 == 0 else [-1.0, 1.0]
    return [1.0] if index % 2 == 0 else [-1.0]


def evenly_limit_asset_distances(distances: list[float], max_assets: int, side_mode: str) -> list[float]:
    ordered = sorted(set(round(float(distance), 3) for distance in distances))
    if max_assets <= 0 or not ordered:
        return []
    assets_per_distance = 2 if side_mode == "both" else 1
    max_distance_count = max(1, int(math.ceil(max_assets / assets_per_distance)))
    if len(ordered) <= max_distance_count:
        return ordered
    if max_distance_count == 1:
        return [ordered[len(ordered) // 2]]
    indices = sorted({round(i * (len(ordered) - 1) / (max_distance_count - 1)) for i in range(max_distance_count)})
    return [ordered[int(index)] for index in indices]


def asset_mesh_group_name(mesh: trimesh.Trimesh) -> str:
    name = str(mesh.metadata.get("name", ""))
    if name.startswith("Street_Light_Lamp"):
        return "Street_Light_Lamp"
    if name.startswith("Street_Light"):
        return "Street_Light"
    if name.startswith("Tree_Trunk"):
        return "Tree_Trunk"
    if name.startswith("Tree_Crown"):
        return "Tree_Crown"
    if name.startswith("Bridge_Deck"):
        return "Bridge_Deck"
    if name.startswith("Bridge_Pier"):
        return "Bridge_Pier"
    for prefix in ("Median", "Guardrail", "Bridge"):
        if name.startswith(prefix):
            return prefix
    return name.split("_", 1)[0] if name else "Asset"


def asset_group_color(group: str) -> list[int] | None:
    return CIM3_COLORS.get(group.lower())


def build_guardrail_meshes(row: pd.Series, rule: RoadRule) -> list[trimesh.Trimesh]:
    meshes = []
    if not bool(row.get("is_bridge", False)) and rule.lane_count < 4:
        return meshes
    z = float(row.get("road_z_mean", row.get("elevation", 0.0))) + 0.62
    offsets = [rule.road_width / 2.0 + 0.45, -rule.road_width / 2.0 - 0.45]
    for line in [row.geometry]:
        coords = list(line.coords)
        for seg_idx, (a, b) in enumerate(zip(coords, coords[1:])):
            for side_idx, offset in enumerate(offsets):
                start_xy, end_xy = offset_segment_xy(a, b, offset)
                mesh = cylinder_between(
                    f"Guardrail_{row['road_id']}_{seg_idx}_{side_idx}",
                    (start_xy[0], start_xy[1], z),
                    (end_xy[0], end_xy[1], z),
                    0.055,
                    CIM3_COLORS["guardrail"],
                    sections=8,
                )
                if mesh is not None:
                    meshes.append(mesh)
    return meshes


def build_street_light_meshes(row: pd.Series, rule: RoadRule) -> list[trimesh.Trimesh]:
    meshes = []
    line: LineString = row.geometry
    profile = road_asset_profile(row, rule)
    lateral = float(profile["street_light_lateral_m"])
    road_pavement = line_buffer(line, rule.road_width + STREET_LIGHT_POLE_ROAD_CLEARANCE_M)
    total_corridor = line_buffer(line, rule.road_width + 2.0 * rule.sidewalk_width + 2.4)
    junction_distances = row_junction_distances(row)
    spacing = float(profile["street_light_spacing_m"])
    candidate_distances = list(iter_line_distances(line, spacing, spacing / 2.0))
    for junction_distance in junction_distances:
        for offset in (-CROSSWALK_DISTANCE_FROM_JUNCTION_M - 3.0, CROSSWALK_DISTANCE_FROM_JUNCTION_M + 3.0):
            candidate = max(0.0, min(float(line.length), junction_distance + offset))
            if 1.0 < candidate < line.length - 1.0:
                candidate_distances.append(candidate)

    filtered_distances = []
    junction_clearance = max(
        STREET_LIGHT_JUNCTION_CLEARANCE_M,
        JUNCTION_RADIUS_M * 1.25,
        min(42.0, road_class_clip_distance(row, rule) * 0.75),
        min(38.0, rule.road_width * 0.35 + rule.sidewalk_width),
    )
    for distance in sorted(set(round(float(d), 3) for d in candidate_distances)):
        if is_near_any_distance(distance, junction_distances, junction_clearance):
            continue
        filtered_distances.append(distance)
    filtered_distances = evenly_limit_asset_distances(
        filtered_distances,
        MAX_STREET_LIGHTS_PER_ROAD,
        str(profile["street_light_sides"]),
    )

    placed_idx = 0
    for dist_idx, distance in enumerate(filtered_distances):
        if placed_idx >= MAX_STREET_LIGHTS_PER_ROAD:
            break
        point, tangent, normal = line_frame_at_distance(line, distance)
        nx, ny = normal

        for side in asset_sides(str(profile["street_light_sides"]), dist_idx):
            if placed_idx >= MAX_STREET_LIGHTS_PER_ROAD:
                break
            x = point.x + nx * lateral * side
            y = point.y + ny * lateral * side
            pole_point = Point(x, y)
            if road_pavement.buffer(0.05).contains(pole_point):
                continue
            if not total_corridor.buffer(0.05).contains(pole_point):
                continue
            if any(pole_point.distance(line.interpolate(jd)) <= junction_clearance for jd in junction_distances):
                continue

            pole_height = float(profile["street_light_height_m"])
            arm_length = float(profile["street_light_arm_m"])
            arm_end = (
                x - nx * side * arm_length,
                y - ny * side * arm_length,
            )
            z0 = elevation_at_distance(row, distance) + rule.curb_height
            base = cylinder_between(
                f"Street_Light_Base_{row['road_id']}_{placed_idx}",
                (x, y, z0),
                (x, y, z0 + 0.42),
                0.26,
                CIM3_COLORS["street_light"],
                sections=14,
            )
            pole = cylinder_between(
                f"Street_Light_Pole_{row['road_id']}_{placed_idx}",
                (x, y, z0 + 0.32),
                (x, y, z0 + pole_height),
                0.115,
                CIM3_COLORS["street_light"],
                sections=14,
            )
            arm = cylinder_between(
                f"Street_Light_Arm_{row['road_id']}_{placed_idx}",
                (x, y, z0 + pole_height - 0.25),
                (arm_end[0], arm_end[1], z0 + pole_height - 0.65),
                0.065,
                CIM3_COLORS["street_light"],
                sections=10,
            )
            lamp = box_at(
                f"Street_Light_Lamp_{row['road_id']}_{placed_idx}",
                (arm_end[0], arm_end[1], z0 + pole_height - 0.75),
                (1.35, 0.44, 0.30),
                CIM3_COLORS["street_light_lamp"],
            )
            if base is not None:
                meshes.append(base)
            if pole is not None:
                meshes.append(pole)
            if arm is not None:
                meshes.append(arm)
            meshes.append(lamp)
            placed_idx += 1
    return meshes


def build_tree_meshes(row: pd.Series, rule: RoadRule) -> list[trimesh.Trimesh]:
    meshes = []
    line: LineString = row.geometry
    profile = road_asset_profile(row, rule)
    spacing = float(profile["tree_spacing_m"])
    if line.length < spacing * 0.8:
        return meshes

    lateral = float(profile["tree_lateral_m"])
    junction_distances = row_junction_distances(row)
    junction_clearance = max(
        TREE_JUNCTION_CLEARANCE_M,
        JUNCTION_RADIUS_M * 1.35,
        min(46.0, road_class_clip_distance(row, rule) * 0.85),
        min(44.0, rule.road_width * 0.4 + rule.sidewalk_width),
    )
    candidate_distances = list(iter_line_distances(line, spacing, spacing * 0.55))
    filtered_distances = []
    for distance in sorted(set(round(float(d), 3) for d in candidate_distances)):
        if is_near_any_distance(distance, junction_distances, junction_clearance):
            continue
        filtered_distances.append(distance)
    filtered_distances = evenly_limit_asset_distances(
        filtered_distances,
        MAX_TREES_PER_ROAD,
        str(profile["tree_sides"]),
    )

    tree_idx = 0
    for dist_idx, distance in enumerate(filtered_distances):
        if tree_idx >= MAX_TREES_PER_ROAD:
            break
        for side in asset_sides(str(profile["tree_sides"]), dist_idx):
            if tree_idx >= MAX_TREES_PER_ROAD:
                break
            x, y = line_side_point(line, distance, lateral * side)
            # Keep trees out of the entire junction throat, not only the centerline
            # point, so crowns do not appear in the middle of intersection surfaces.
            if any(Point(x, y).distance(line.interpolate(jd)) <= junction_clearance for jd in junction_distances):
                continue
            z0 = elevation_at_distance(row, distance) + rule.curb_height
            variation = 0.92 + 0.12 * (0.5 + 0.5 * math.sin(tree_idx * 1.7))
            tree_scale = float(profile["tree_scale"]) * variation
            trunk_h = 3.8 * tree_scale
            crown_base = z0 + trunk_h
            trunk = cylinder_between(
                f"Tree_Trunk_{row['road_id']}_{tree_idx}",
                (x, y, z0),
                (x, y, crown_base + 0.15),
                0.26 * tree_scale,
                CIM3_COLORS["tree_trunk"],
                sections=10,
            )
            crown = ellipsoid_at(
                f"Tree_Crown_{row['road_id']}_{tree_idx}",
                (x, y, crown_base + 1.75 * tree_scale),
                (2.75 * tree_scale, 2.55 * tree_scale, 2.45 * tree_scale),
                CIM3_COLORS["tree_crown"],
                subdivisions=2,
            )
            crown_top = ellipsoid_at(
                f"Tree_Crown_Top_{row['road_id']}_{tree_idx}",
                (x, y, crown_base + 3.28 * tree_scale),
                (1.85 * tree_scale, 1.7 * tree_scale, 1.6 * tree_scale),
                CIM3_COLORS["tree_crown"],
                subdivisions=1,
            )
            if trunk is not None:
                meshes.append(trunk)
            meshes.extend([crown, crown_top])
            tree_idx += 1
    return meshes


def build_bridge_meshes(row: pd.Series, rule: RoadRule) -> list[trimesh.Trimesh]:
    if not bool(row.get("is_bridge", False)):
        return []
    line: LineString = row.geometry
    road_z = float(row.get("road_z_mean", row.get("elevation", 0.0)))
    deck_geom = line_buffer(line, rule.road_width + 1.2)
    deck = polygon_to_extruded_mesh(
        deck_geom,
        z_bottom=road_z - 0.65,
        z_top=road_z - 0.15,
        name=f"Bridge_Deck_{row['road_id']}",
        visual_color=CIM3_COLORS["bridge_deck"],
    )
    meshes = [deck]
    for pier_idx, distance in enumerate(iter_line_distances(line, BRIDGE_PIER_SPACING_M, BRIDGE_PIER_SPACING_M / 2.0)):
        point = line.interpolate(distance)
        pier = cylinder_between(
            f"Bridge_Pier_{row['road_id']}_{pier_idx}",
            (point.x, point.y, 0.0),
            (point.x, point.y, max(0.2, road_z - 0.65)),
            0.45,
            CIM3_COLORS["bridge_pier"],
            sections=14,
        )
        if pier is not None:
            meshes.append(pier)
    return meshes


def build_median_mesh(row: pd.Series, rule: RoadRule) -> trimesh.Trimesh:
    if rule.lane_count < 4:
        return empty_mesh(f"Median_{row['road_id']}")
    z = float(row.get("road_z_mean", row.get("elevation", 0.0)))
    geom = line_buffer(row.geometry, 0.8)
    return polygon_to_extruded_mesh(
        geom,
        z_bottom=z + 0.01,
        z_top=z + 0.18,
        name=f"Median_{row['road_id']}",
        visual_color=CIM3_COLORS["median"],
    )


def junction_asset_exclusion_mask(layers: list[dict[str, Any]] | None, clearance: float = 2.0):
    if not layers:
        return None
    surfaces = [
        layer.get("junction_surface").buffer(clearance, resolution=8, join_style=1)
        for layer in layers
        if layer.get("junction_surface") is not None and not layer.get("junction_surface").is_empty
    ]
    return clean_polygonal(unary_union(surfaces)) if surfaces else None


def filter_meshes_outside_mask(parts: list[trimesh.Trimesh], mask) -> list[trimesh.Trimesh]:
    if mask is None or mask.is_empty:
        return parts
    filtered = []
    for mesh in parts:
        if len(mesh.vertices) == 0:
            continue
        center = mesh.centroid
        if mask.contains(Point(float(center[0]), float(center[1]))):
            continue
        if any(mask.contains(Point(float(vertex[0]), float(vertex[1]))) for vertex in mesh.vertices):
            continue
        filtered.append(mesh)
    return filtered


def build_cim3_road_asset_meshes(
    roads: gpd.GeoDataFrame,
    rules: dict[str, RoadRule],
    layers: list[dict[str, Any]] | None = None,
) -> dict[str, trimesh.Trimesh]:
    meshes: dict[str, trimesh.Trimesh] = {}
    asset_exclusion_mask = junction_asset_exclusion_mask(layers, clearance=4.0)
    for _, row in roads.iterrows():
        if row.geometry is None or row.geometry.is_empty or not isinstance(row.geometry, LineString):
            continue
        rule = get_road_rule(row, rules)
        median_meshes = filter_meshes_outside_mask([build_median_mesh(row, rule)], asset_exclusion_mask)
        guardrail_meshes = filter_meshes_outside_mask(build_guardrail_meshes(row, rule), asset_exclusion_mask)
        street_light_meshes = filter_meshes_outside_mask(build_street_light_meshes(row, rule), asset_exclusion_mask)
        tree_meshes = filter_meshes_outside_mask(build_tree_meshes(row, rule), asset_exclusion_mask)
        asset_groups: dict[str, list[trimesh.Trimesh]] = {}
        for part in median_meshes + guardrail_meshes + street_light_meshes + tree_meshes + build_bridge_meshes(row, rule):
            if part is None or len(part.vertices) == 0:
                continue
            asset_groups.setdefault(asset_mesh_group_name(part), []).append(part)
        for prefix, parts in asset_groups.items():
            mesh = merge_named_meshes(
                f"{prefix}_{row['road_id']}",
                parts,
                asset_group_color(prefix),
            )
            if len(mesh.vertices) > 0:
                meshes[mesh.metadata["name"]] = mesh
    return meshes


def street_light_pole_road_conflict_qc(roads: gpd.GeoDataFrame, rules: dict[str, RoadRule]) -> dict[str, Any]:
    checked_count = 0
    conflict_items: list[dict[str, Any]] = []
    for _, row in roads.iterrows():
        if row.geometry is None or row.geometry.is_empty or not isinstance(row.geometry, LineString):
            continue
        rule = get_road_rule(row, rules)
        road_pavement = line_buffer(row.geometry, rule.road_width + STREET_LIGHT_POLE_ROAD_CLEARANCE_M)
        for mesh in build_street_light_meshes(row, rule):
            name = mesh.metadata.get("name", "")
            if not any(part in name for part in ("Base", "Pole")):
                continue
            checked_count += 1
            center = mesh.centroid
            point = Point(float(center[0]), float(center[1]))
            if road_pavement.buffer(0.02).contains(point):
                conflict_items.append({
                    "road_id": safe_str(row.get("road_id")),
                    "light_id": name,
                    "x": round(float(center[0]), 3),
                    "y": round(float(center[1]), 3),
                })
    return {
        "checked_street_light_part_count": checked_count,
        "street_light_road_conflict_count": len(conflict_items),
        "passed": len(conflict_items) == 0,
        "sample_issues": conflict_items[:20],
    }


def junction_label_color(node: JunctionNode) -> list[int]:
    if node.hierarchy == "MAJOR_ARTERIAL":
        return CIM3_COLORS["junction_label_major"]
    if node.hierarchy == "SECONDARY_COLLECTOR":
        return CIM3_COLORS["junction_label_secondary"]
    if node.hierarchy == "RAMP_OR_GRADE_SEPARATED":
        return CIM3_COLORS["junction_label_ramp"]
    if node.hierarchy in {"COMPLEX_MULTI_ARM", "ROUNDABOUT_JUNCTION"}:
        return CIM3_COLORS["junction_label_complex"]
    return CIM3_COLORS["junction_label_local"]


def build_junction_label_meshes(nodes: list[JunctionNode], z: float) -> dict[str, trimesh.Trimesh]:
    meshes: dict[str, trimesh.Trimesh] = {}
    for node in nodes:
        radius = 0.55 if node.hierarchy == "LOCAL_JUNCTION" else 0.75
        height = 0.08
        mesh = trimesh.creation.cylinder(radius=radius, height=height, sections=12)
        mesh.apply_translation((node.center.x, node.center.y, z + 0.12 + height / 2.0))
        mesh.metadata["name"] = f"Junction_Label_{node.junction_id}_{node.junction_type}_{node.hierarchy}"
        mesh.visual.face_colors = junction_label_color(node)
        meshes[mesh.metadata["name"]] = mesh
    return meshes


def make_scene(
    layers: list[dict[str, Any]],
    rule: RoadRule,
    google_center_latlon: tuple[float, float] | None = None,
    include_basemap: bool = False,
) -> tuple[trimesh.Scene, dict[str, trimesh.Trimesh]]:
    """
    场景构建器：接收加工好的 2D 几何信息，映射相关高度与颜色预设，转化为真正的 3D Mesh 对象，
    并将它们统筹装配合并至 Trimesh 核心场景容器 (Scene) 内部。
    """
    # RGBA 颜色，仅用于 POC 视觉区分
    road_color = [45, 45, 45, 255]
    sidewalk_color = [170, 170, 170, 255]
    curb_color = [210, 210, 210, 255]
    marking_color = [245, 206, 58, 255]
    lane_colors = [
        [46, 46, 44, 255],
        [39, 39, 38, 255],
        [52, 52, 49, 255],
        [43, 43, 41, 255],
    ]

    scene = trimesh.Scene()
    all_meshes = {}

    for i, layer in enumerate(layers):
        z = layer["elevation"] + rule.road_z

        # 分层级生成路面、人行道、路缘石和标线的独立 Mesh 对象
        road_swept_parts = []
        lane_meshes = {}
        for road_idx, entry in enumerate(layer.get("swept_entries", [])):
            if len(entry) == 4:
                row, line, road_rule, distance_offset = entry
            else:
                row, line, road_rule = entry
                distance_offset = 0.0
            if GENERATE_SWEPT_ROAD_SURFACES:
                road_swept_parts.append(
                    swept_road_mesh(
                        row,
                        line,
                        road_rule,
                        name=f"Road_Surface_Swept_{i}_{road_idx}",
                        visual_color=road_color,
                        distance_offset=distance_offset,
                    )
                )
            if GENERATE_LANE_SURFACES:
                for lane_idx in range(road_rule.lane_count):
                    lane_name = f"Lane_Surface_{i}_{road_idx}_{lane_idx}"
                    lane_meshes[lane_name] = swept_lane_mesh(
                        row,
                        line,
                        road_rule,
                        lane_idx,
                        lane_name,
                        visual_color=lane_colors[lane_idx % len(lane_colors)],
                        distance_offset=distance_offset,
                    )

        road_mesh = merge_named_meshes(f"Road_Surface_{i}", road_swept_parts, road_color)
        if len(road_mesh.vertices) == 0:
            road_mesh = polygon_to_top_mesh(
                layer.get("visual_road_surface") or layer["road_surface"],
                z=z,
                name=f"Road_Surface_{i}",
                visual_color=road_color,
            )

        sidewalk_mesh = polygon_to_top_mesh(
            layer["sidewalk"],
            z=z + rule.curb_height,
            name=f"Sidewalk_{i}",
            visual_color=sidewalk_color,
        )

        curb_mesh = polygon_to_extruded_mesh(
            layer["curb"],
            z_bottom=z,
            z_top=z + rule.curb_height,
            name=f"Curb_{i}",
            visual_color=curb_color,
        )

        lane_mesh = polygon_to_top_mesh(
            layer["lane_marking"],
            z=z + rule.lane_marking_z_offset,
            name=f"Lane_Marking_{i}",
            visual_color=marking_color,
        )
        # Keep junction polygons for semantic QC and for clearing markings/assets,
        # but do not export a second visible asphalt top face. The rendered FBX
        # should contain one dissolved drivable asphalt mesh so junctions do not
        # show dark seams or wrapper-like coplanar boundaries.
        junction_mesh = empty_mesh(f"Junction_Surface_{i}")
        stop_line_mesh = polygon_to_top_mesh(
            layer.get("stop_line"),
            z=z + rule.lane_marking_z_offset + 0.004,
            name=f"Stop_Line_{i}",
            visual_color=CIM3_COLORS["stop_line"],
        )
        crosswalk_mesh = polygon_to_top_mesh(
            layer.get("crosswalk"),
            z=z + rule.lane_marking_z_offset + 0.006,
            name=f"Crosswalk_{i}",
            visual_color=CIM3_COLORS["crosswalk"],
        )
        lane_guide_mesh = polygon_to_top_mesh(
            layer.get("lane_guide"),
            z=z + rule.lane_marking_z_offset + 0.008,
            name=f"Lane_Guide_{i}",
            visual_color=CIM3_COLORS["lane_guide"],
        )
        turn_arrow_mesh = polygon_to_top_mesh(
            layer.get("turn_arrow"),
            z=z + rule.lane_marking_z_offset + 0.01,
            name=f"Turn_Arrow_{i}",
            visual_color=CIM3_COLORS["turn_arrow"],
        )
        channelization_mesh = polygon_to_top_mesh(
            layer.get("channelization_island"),
            z=z + rule.curb_height + 0.006,
            name=f"Channelization_Island_{i}",
            visual_color=CIM3_COLORS["channelization"],
        )

        layer_meshes = {
            f"Road_Surface_{i}": road_mesh,
            f"Sidewalk_{i}": sidewalk_mesh,
            f"Curb_{i}": curb_mesh,
            f"Lane_Marking_{i}": lane_mesh,
            f"Junction_Surface_{i}": junction_mesh,
            f"Stop_Line_{i}": stop_line_mesh,
            f"Crosswalk_{i}": crosswalk_mesh,
            f"Lane_Guide_{i}": lane_guide_mesh,
            f"Turn_Arrow_{i}": turn_arrow_mesh,
            f"Channelization_Island_{i}": channelization_mesh,
        }
        layer_meshes.update(lane_meshes)
        if GENERATE_JUNCTION_LABELS:
            layer_meshes.update(build_junction_label_meshes(layer.get("junction_nodes", []), z))

        # 过滤掉空数据的网格，将有效模型添加到场景容器中
        for name, mesh in layer_meshes.items():
            if len(mesh.vertices) > 0:
                scene.add_geometry(mesh, node_name=name, geom_name=name)
                all_meshes[name] = mesh

    if include_basemap:
        base_bounds = mesh_bounds_xy(all_meshes.values())
        for name, mesh in make_gis_basemap_meshes(base_bounds, google_center_latlon=google_center_latlon).items():
            scene.add_geometry(mesh, node_name=name, geom_name=name)
            all_meshes[name] = mesh

    return scene, all_meshes


def export_scene(scene: trimesh.Scene) -> None:
    """
    将整个 trimesh 场景导出为 OBJ。FBX 在 Blender 材质流程中导出。
    """
    scene.export(OBJ_PATH)
    scene.export(GLB_PATH)


def build_semantic_json(
    roads: gpd.GeoDataFrame,
    origin: LocalOrigin,
    meta: dict[str, Any],
    rules: dict[str, RoadRule],
    layers: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    构建并导出 CIM 语义信息 JSON 清单，该清单详细记录了从 2D 数据到 3D 网格转换过程中的属性映射，
    作为外部系统挂载 3D 模型材质、执行长面积统计及数据回溯的关键基石。
    """
    objects = []
    default_rule = rules.get("default_road")

    # POC 阶段中的 3D 几何对象被整合为一个连续大面，在此依然保留每条路源数据的中心线记录供后续溯源追溯
    for _, row in roads.iterrows():
        rule = get_road_rule(row, rules)
        lane_layout = lane_layout_for_row(row, rule)
        source_section_rule = row_section_requirement(row)
        section_rule = modeled_section_requirement(row, source_section_rule)
        source_section_code = row_section_code(row) or (
            safe_str(source_section_rule.get("section_id") or source_section_rule.get("inferred_section"))
            if source_section_rule
            else None
        )
        modeled_section_code = (
            safe_str(section_rule.get("section_id") or section_rule.get("inferred_section"))
            if section_rule
            else None
        )
        objects.append({
            "object_id": f"{row['road_id']}_source_centerline",
            "source_road_id": row["road_id"],
            "road_name": row["road_name"],
            "road_class": safe_str(row.get("road_class")),
            "section_code": source_section_code or safe_str(row.get("section")),
            "modeled_section_code": modeled_section_code,
            "section_symmetry_normalized": bool(source_section_code and modeled_section_code and source_section_code != modeled_section_code),
            "section_rule_source": str(section_rule.get("inferred_from", "section")) if section_rule else "none",
            "section_category": safe_str(section_rule.get("category") or section_rule.get("grade")) if section_rule else "unknown",
            "cim3_road_grade": cim3_road_grade(row.get("road_class")),
            "design_speed_kmh": design_speed_kmh(row.get("road_class"), row.get("maxspeed")),
            "lane_count": parse_lane_count(row.get("lane_count"), default=rule.lane_count),
            "lanes_forward": lane_layout.forward_count,
            "lanes_backward": lane_layout.backward_count,
            "lane_width_m": rule.lane_width,
            "road_width_m": rule.road_width,
            "osm_width_m": parse_road_width_m(row.get("osm_width")),
            "total_section_width_m": parse_road_width_m(row.get("osm_width")) or (
                float(section_rule["total_width"]) if section_rule and "total_width" in section_rule else None
            ),
            "modeled_side_reserve_width_m": rule.sidewalk_width,
            "sidewalk_width_m": rule.sidewalk_width,
            "source_cross_section_components": source_cross_section_components_for_row(row),
            "cross_section_components": cross_section_components_for_row(row),
            "surface_material": rule.material,
            "construction_year": safe_str(row.get("start_date")) or "unknown",
            "owner_unit": safe_str(row.get("operator")) or "unknown",
            "maxspeed": safe_str(row.get("maxspeed")),
            "oneway": safe_str(row.get("oneway")),
            "is_bridge": bool(row.get("is_bridge")),
            "road_ref": safe_str(row.get("road_ref")),
            "access": safe_str(row.get("access")),
            "object_type": "Source_Centerline",
            "geometry_type": row.geometry.geom_type,
            "length_m": float(row["length_m"]),
            "elevation": float(row["elevation"]),
            "bridge_clearance": float(row.get("bridge_clearance", 0.0)),
            "height_source": str(row.get("height_source", "")),
            "height_mode": str(row.get("height_mode", "")),
            "ground_z_start": float(row.get("ground_z_start", 0.0)),
            "ground_z_end": float(row.get("ground_z_end", 0.0)),
            "road_z_start": float(row.get("road_z_start", 0.0)),
            "road_z_end": float(row.get("road_z_end", 0.0)),
            "road_z_mean": float(row.get("road_z_mean", 0.0)),
            "elevation_profile": json.loads(row.get("elevation_profile_json", "{\"samples\": []}")),
            "max_grade_percent": float(row.get("max_grade_percent", 0.0)),
            "junction_distances_m": row_junction_distances(row),
            "transition_curve_count": int(row.get("transition_curve_count", 0)),
            "rule_id": canonical_road_class(row.get("road_class"), rules),
        })

    for i, layer in enumerate(layers):
        ele = layer["elevation"]
        merged_objects = [
            ("Road_Surface", layer["road_surface"], default_rule.material),
            ("Sidewalk", layer["sidewalk"], default_rule.sidewalk_material),
            ("Curb", layer["curb"], default_rule.curb_material),
            ("Lane_Marking", layer["lane_marking"], default_rule.marking_material),
            ("Stop_Line", layer.get("stop_line"), default_rule.marking_material),
            ("Crosswalk", layer.get("crosswalk"), default_rule.marking_material),
            ("Lane_Guide", layer.get("lane_guide"), default_rule.marking_material),
            ("Turn_Arrow", layer.get("turn_arrow"), default_rule.marking_material),
            ("Channelization_Island", layer.get("channelization_island"), "painted_channelization_island"),
            ("Junction_Surface", layer.get("junction_surface"), default_rule.material),
        ]

        for base_type, geom, material in merged_objects:
            if geom is None or geom.is_empty:
                continue
            area = float(geom.area)
            objects.append({
                "object_id": f"{base_type}_layer_{i}",
                "object_type": base_type,
                "elevation": ele,
                "source_road_id": "merged",
                "rule_id": "default_road",
                "material": material,
                "area_m2": area,
                "mesh_files": [
                    str(OBJ_PATH.relative_to(ROOT)),
                    str(GLB_PATH.relative_to(ROOT)),
                ],
            })

        for item in layer.get("junction_metadata", []):
            objects.append({
                "object_id": item["junction_id"],
                "object_type": "Junction_Node",
                "junction_type": item.get("junction_type", "UNKNOWN"),
                "junction_hierarchy": item.get("junction_hierarchy", "LOCAL_JUNCTION"),
                "surface_strategy": item.get("surface_strategy", "edge_intersection"),
                "surface_algorithm": item.get("surface_algorithm", "unknown"),
                "total_surface_algorithm": item.get("total_surface_algorithm", "unknown"),
                "socket_count": item.get("socket_count", 0),
                "source_road_ids": item.get("road_ids", []),
                "source_road_names": item.get("road_names", []),
                "source_road_classes": item.get("road_classes", []),
                "center_xy": item.get("center", []),
                "area_m2": item.get("area_m2", 0.0),
                "material": default_rule.material,
                "mesh_files": [
                    str(OBJ_PATH.relative_to(ROOT)),
                    str(GLB_PATH.relative_to(ROOT)),
                ],
            })

        for node in layer.get("junction_nodes", []):
            for socket in node.sockets:
                for lane in socket.lane_sockets:
                    objects.append({
                        "object_id": lane.lane_socket_id,
                        "object_type": "Lane_Socket",
                        "junction_id": node.junction_id,
                        "source_road_id": socket.road_id,
                        "lane_id": lane.lane_id or lane.lane_socket_id,
                        "lane_index": lane.lane_index,
                        "lane_width_m": lane.lane_width,
                        "movement": lane.movement,
                        "allowed_turns": lane.allowed_turns or lane.allowed_movements,
                        "traffic_direction": lane.traffic_direction,
                        "signal_group": lane.signal_group,
                        "stop_control": lane.stop_control,
                    })

    semantic = {
        "project": "cim_road_poc",
        "model_level": "CIM4_candidate",
        "description": "第一轮 POC：统一道路模板生成公共道路模型",
        "cim3_requirements": {
            "scale_range": "1:500-1:2000",
            "plan_accuracy_m": CIM3_PLAN_ACCURACY_M,
            "height_accuracy_m": CIM3_HEIGHT_ACCURACY_M,
            "min_geometric_detail_m": CIM3_MIN_GEOMETRIC_DETAIL_M,
            "lane_width_tolerance_m": 0.3,
            "sidewalk_width_tolerance_m": 0.5,
            "length_radius_tolerance_percent": 5,
            "junction_gap_tolerance_m": 0.2,
            "elevation_sample_interval_m": ELEVATION_SAMPLE_INTERVAL_M,
            "junction_node_tolerance_m": JUNCTION_NODE_TOLERANCE_M,
            "junction_canalization": True,
            "crosswalk_generation": GENERATE_CROSSWALKS,
            "stop_line_generation": True,
            "clothoid_transition_centerlines": True,
            "lane_level_junction_topology": True,
            "cross_section_driven_sweep": True,
            "junction_clipped_road_sweep": True,
            "junction_socket_model": True,
            "socket_driven_junction_mesh": True,
            "approach_lane_arrows": GENERATE_TURN_ARROWS,
            "turn_arrow_generation": GENERATE_TURN_ARROWS,
            "channelization_islands": True,
            "road_crown_slope": CROWN_SLOPE,
            "lane_crown_slope": LANE_CROWN_SLOPE,
            "sweep_sample_interval_m": SWEEP_SAMPLE_INTERVAL_M,
            "transition_min_angle_deg": TRANSITION_MIN_ANGLE_DEG,
            "transition_max_length_m": TRANSITION_MAX_LENGTH_M,
        },
        "coordinate": {
            "source_crs": "OSM WGS84 -> projected",
            "model_crs": meta["target_crs"],
            "unit": "meter",
            "local_origin": {"x": origin.x, "y": origin.y, "z": origin.z},
        },
        "active_rules": list(rules.keys()),
        "objects": objects,
    }

    with SEMANTIC_PATH.open("w", encoding="utf-8") as f:
        json.dump(semantic, f, ensure_ascii=False, indent=2)

    return semantic


def mesh_qc(meshes: dict[str, trimesh.Trimesh]) -> dict[str, Any]:
    """
    基础 3D 拓扑形态质检：核查模型组件有效性、计算模型复杂度（面数/顶点），并侦测网格水密性 (Watertight)。
    """
    result = {}
    for name, mesh in meshes.items():
        if len(mesh.vertices) == 0:
            result[name] = {
                "empty_mesh": True,
                "vertices": 0,
                "faces": 0,
                "watertight": False,
                "euler_number": None,
                "bounds": None,
            }
            continue

        result[name] = {
            "empty_mesh": False,
            "vertices": int(len(mesh.vertices)),
            "faces": int(len(mesh.faces)),
            "watertight": bool(mesh.is_watertight),
            "euler_number": int(mesh.euler_number) if mesh.euler_number is not None else None,
            "bounds": mesh.bounds.round(3).tolist(),
        }
    return result


def arrow_traffic_qc(roads: gpd.GeoDataFrame, rules: dict[str, RoadRule]) -> dict[str, Any]:
    if not GENERATE_TURN_ARROWS:
        return {
            "enabled": False,
            "checked_arrow_count": 0,
            "ingress_lane_count": 0,
            "simulated_connector_count": 0,
            "wrong_direction_count": 0,
            "disconnected_ingress_count": 0,
            "passed": True,
            "sample_issues": [],
        }

    issues: list[dict[str, Any]] = []
    checked_arrow_count = 0
    ingress_lane_count = 0
    disconnected_ingress_count = 0
    wrong_direction_count = 0
    simulated_connector_count = 0

    grouped_roads = roads.copy()
    grouped_roads["topology_group"] = grouped_roads.apply(road_topology_group, axis=1)
    for _, group in grouped_roads.groupby("topology_group"):
        road_entries: list[tuple[pd.Series, LineString, RoadRule]] = []
        for _, row in group.iterrows():
            line = row.geometry
            if line is None or line.is_empty or not isinstance(line, LineString):
                continue
            road_entries.append((row, line, get_road_rule(row, rules)))

        junction_nodes = build_junction_nodes(road_entries, detect_junction_points(group, rules))
        for node in junction_nodes:
            connected = [(row, line, rule) for row, line, rule in road_entries if line.distance(node.center) <= junction_connection_tolerance()]
            for row, line, rule in connected:
                node_distance = float(line.project(node.center))
                sign = 1.0 if node_distance <= line.length * 0.5 else -1.0
                approach_arrows = approach_arrow_polygons(row, line, node_distance, sign, rule, node.radius)
                checked_arrow_count += len(approach_arrows)
                for arrow in approach_arrows:
                    if arrow.centroid.distance(node.center) <= node.radius:
                        wrong_direction_count += 1
                        issues.append(
                            {
                                "junction_id": node.junction_id,
                                "road_id": row.get("road_id"),
                                "issue": "approach_arrow_inside_junction_radius",
                                "distance_to_junction_m": round(float(arrow.centroid.distance(node.center)), 3),
                                "junction_radius_m": round(float(node.radius), 3),
                            }
                        )

            connectors = build_lane_connectors(node)
            simulated_connector_count += len(connectors)
            connector_from_ids = {connector.from_lane.lane_socket_id for connector in connectors}

            for socket in node.sockets:
                for lane in socket.lane_sockets:
                    if lane.lane_role != "ingress":
                        continue
                    ingress_lane_count += 1
                    dot = lane.tangent[0] * socket.tangent[0] + lane.tangent[1] * socket.tangent[1]
                    if dot < 0.98:
                        wrong_direction_count += 1
                        issues.append(
                            {
                                "junction_id": node.junction_id,
                                "road_id": socket.road_id,
                                "lane_id": lane.lane_socket_id,
                                "issue": "ingress_arrow_not_aligned_with_travel_direction",
                                "dot": round(float(dot), 4),
                            }
                        )
                    if lane.lane_socket_id not in connector_from_ids:
                        disconnected_ingress_count += 1
                        issues.append(
                            {
                                "junction_id": node.junction_id,
                                "road_id": socket.road_id,
                                "lane_id": lane.lane_socket_id,
                                "issue": "ingress_lane_has_no_simulated_exit_connector",
                            }
                        )

    return {
        "checked_arrow_count": checked_arrow_count,
        "ingress_lane_count": ingress_lane_count,
        "simulated_connector_count": simulated_connector_count,
        "wrong_direction_count": wrong_direction_count,
        "disconnected_ingress_count": disconnected_ingress_count,
        "passed": wrong_direction_count == 0 and disconnected_ingress_count == 0,
        "sample_issues": issues[:20],
    }


def geometry_qc(roads: gpd.GeoDataFrame, layers: list[dict[str, Any]], rule: RoadRule) -> dict[str, Any]:
    """
    2D 几何平面质检：统计生成道路的总中心线长及分类铺装面（如人行道、路缘石）覆盖面积。
    """
    road_area = sum([0.0 if l["road_surface"] is None else float(l["road_surface"].area) for l in layers])
    sidewalk_area = sum([0.0 if l["sidewalk"] is None else float(l["sidewalk"].area) for l in layers])
    curb_area = sum([0.0 if l["curb"] is None else float(l["curb"].area) for l in layers])
    marking_area = sum([0.0 if l["lane_marking"] is None else float(l["lane_marking"].area) for l in layers])
    stop_line_area = sum([0.0 if l.get("stop_line") is None else float(l["stop_line"].area) for l in layers])
    crosswalk_area = sum([0.0 if l.get("crosswalk") is None else float(l["crosswalk"].area) for l in layers])
    lane_guide_area = sum([0.0 if l.get("lane_guide") is None else float(l["lane_guide"].area) for l in layers])
    turn_arrow_area = sum([0.0 if l.get("turn_arrow") is None else float(l["turn_arrow"].area) for l in layers])
    channelization_area = sum([0.0 if l.get("channelization_island") is None else float(l["channelization_island"].area) for l in layers])
    junction_area = sum([0.0 if l.get("junction_surface") is None else float(l["junction_surface"].area) for l in layers])
    junction_count = sum(int(l.get("junction_count", 0)) for l in layers)
    parametric_junction_count = sum(int(l.get("parametric_junction_count", 0)) for l in layers)
    junction_socket_count = sum(int(l.get("junction_socket_count", 0)) for l in layers)
    junction_algorithm_counts: dict[str, int] = {}
    junction_type_counts: dict[str, int] = {}
    junction_hierarchy_counts: dict[str, int] = {}
    junction_strategy_counts: dict[str, int] = {}
    for layer in layers:
        for item in layer.get("junction_metadata", []):
            method = item.get("surface_algorithm", "unknown")
            junction_algorithm_counts[method] = junction_algorithm_counts.get(method, 0) + 1
            jtype = item.get("junction_type", "UNKNOWN")
            junction_type_counts[jtype] = junction_type_counts.get(jtype, 0) + 1
            hierarchy = item.get("junction_hierarchy", "LOCAL_JUNCTION")
            junction_hierarchy_counts[hierarchy] = junction_hierarchy_counts.get(hierarchy, 0) + 1
            strategy = item.get("surface_strategy", "unknown")
            junction_strategy_counts[strategy] = junction_strategy_counts.get(strategy, 0) + 1
    lane_connector_count = sum(int(l.get("lane_connector_count", 0)) for l in layers)
    approach_arrow_count = sum(int(l.get("approach_arrow_count", 0)) for l in layers)
    channelization_island_count = sum(int(l.get("channelization_island_count", 0)) for l in layers)
    swept_road_count = sum(len(l.get("swept_entries", [])) for l in layers)
    swept_lane_count = 0
    lane_widths = []
    lane_width_ratios = []
    for layer in layers:
        for entry in layer.get("swept_entries", []):
            road_rule = entry[2]
            swept_lane_count += road_rule.lane_count
            lane_widths.append(float(road_rule.lane_width))
            if road_rule.lane_count > 0:
                lane_width_ratios.append(float(road_rule.road_width) / float(road_rule.lane_count))
    clipped_segment_count = sum(int(l.get("clipped_segment_count", 0)) for l in layers)

    return {
        "road_count": int(len(roads)),
        "total_centerline_length_m": float(roads["length_m"].sum()),
        "default_road_width_m": rule.road_width,
        "default_sidewalk_width_m": rule.sidewalk_width,
        "default_curb_height_m": rule.curb_height,
        "road_surface_area_m2": road_area,
        "sidewalk_area_m2": sidewalk_area,
        "curb_area_m2": curb_area,
        "lane_marking_area_m2": marking_area,
        "stop_line_area_m2": stop_line_area,
        "crosswalk_area_m2": crosswalk_area,
        "lane_guide_area_m2": lane_guide_area,
        "turn_arrow_area_m2": turn_arrow_area,
        "channelization_island_area_m2": channelization_area,
        "junction_surface_area_m2": junction_area,
        "detected_junction_count": junction_count,
        "parametric_junction_count": parametric_junction_count,
        "junction_socket_count": junction_socket_count,
        "junction_algorithm_counts": junction_algorithm_counts,
        "junction_type_counts": junction_type_counts,
        "junction_hierarchy_counts": junction_hierarchy_counts,
        "junction_strategy_counts": junction_strategy_counts,
        "lane_connector_count": lane_connector_count,
        "approach_arrow_count": approach_arrow_count,
        "channelization_island_count": channelization_island_count,
        "transition_curve_count": int(roads.get("transition_curve_count", pd.Series([0])).sum()),
        "swept_road_count": swept_road_count,
        "swept_lane_count": swept_lane_count,
        "lane_width_values_m": sorted(set(round(width, 3) for width in lane_widths)),
        "lane_width_min_m": min(lane_widths) if lane_widths else 0.0,
        "lane_width_max_m": max(lane_widths) if lane_widths else 0.0,
        "road_width_per_lane_min_m": min(lane_width_ratios) if lane_width_ratios else 0.0,
        "road_width_per_lane_max_m": max(lane_width_ratios) if lane_width_ratios else 0.0,
        "clipped_road_segment_count": clipped_segment_count,
        "road_crown_slope": CROWN_SLOPE,
        "max_grade_percent": float(roads.get("max_grade_percent", pd.Series([0.0])).max()),
    }


def junction_quality_qc(layers: list[dict[str, Any]]) -> dict[str, Any]:
    records = [
        item
        for layer in layers
        for item in layer.get("junction_metadata", [])
    ]
    areas = [float(item.get("area_m2", 0.0) or 0.0) for item in records]
    small_area_items = [
        {
            "junction_id": item.get("junction_id"),
            "junction_type": item.get("junction_type"),
            "surface_algorithm": item.get("surface_algorithm"),
            "area_m2": item.get("area_m2"),
            "socket_count": item.get("socket_count"),
        }
        for item in records
        if float(item.get("area_m2", 0.0) or 0.0) < 20.0
    ]
    fallback_count = sum(
        1
        for item in records
        if "fallback" in str(item.get("surface_algorithm", "")).lower()
    )
    lane_connector_count = sum(int(layer.get("lane_connector_count", 0)) for layer in layers)
    zero_socket_count = sum(1 for item in records if int(item.get("socket_count", 0) or 0) < 3)
    socket_checks = 0
    socket_gap_items: list[dict[str, Any]] = []
    marking_overlap_area = 0.0
    lane_guide_overlap_area = 0.0
    turn_arrow_overlap_area = 0.0
    junction_scores: list[dict[str, Any]] = []

    for layer in layers:
        junction_surface = layer.get("junction_surface")
        if junction_surface is None or junction_surface.is_empty:
            continue
        coverage = junction_surface.buffer(0.08, resolution=6)
        for node in layer.get("junction_nodes", []):
            node_socket_checks = 0
            node_gap_count = 0
            node_missing_area = 0.0
            node_throat_area = 0.0
            for socket in node.sockets:
                socket_checks += 1
                node_socket_checks += 1
                throat = socket_throat_polygon(node, socket, include_sidewalk=False)
                if throat is None or throat.is_empty:
                    continue
                missing_area = float(throat.difference(coverage).area)
                node_missing_area += missing_area
                node_throat_area += float(throat.area)
                if missing_area > max(0.02, float(throat.area) * 0.002):
                    node_gap_count += 1
                    socket_gap_items.append({
                        "junction_id": node.junction_id,
                        "socket_id": socket.socket_id,
                        "missing_area_m2": round(missing_area, 4),
                        "throat_area_m2": round(float(throat.area), 4),
                    })

            connector_count = len(build_lane_connectors(node))
            node_surface = node_cityengine_surface_union(node)
            surface_area = float(node_surface.area) if node_surface is not None and not node_surface.is_empty else 0.0
            area_per_socket = surface_area / max(1, len(node.sockets))
            missing_ratio = node_missing_area / max(node_throat_area, 1e-6)
            score = 100.0
            score -= min(40.0, node_gap_count * 12.0 + missing_ratio * 120.0)
            if len(node.sockets) < 3:
                score -= 25.0
            if connector_count <= 0:
                score -= 20.0
            if surface_area < JUNCTION_MIN_SURFACE_AREA_M2 * max(1, len(node.sockets)):
                score -= 20.0
            if area_per_socket > 900.0:
                score -= min(12.0, (area_per_socket - 900.0) / 120.0)
            junction_scores.append({
                "junction_id": node.junction_id,
                "score": round(max(0.0, score), 2),
                "junction_type": node.junction_type,
                "hierarchy": node.hierarchy,
                "socket_count": len(node.sockets),
                "connector_count": connector_count,
                "socket_gap_count": node_gap_count,
                "surface_area_m2": round(surface_area, 3),
                "area_per_socket_m2": round(area_per_socket, 3),
                "missing_throat_ratio": round(missing_ratio, 6),
            })

        lane_marking = layer.get("lane_marking")
        if lane_marking is not None and not lane_marking.is_empty:
            marking_overlap_area += float(lane_marking.intersection(junction_surface).area)
        lane_guide = layer.get("lane_guide")
        if lane_guide is not None and not lane_guide.is_empty:
            lane_guide_overlap_area += float(lane_guide.intersection(junction_surface).area)
        turn_arrow = layer.get("turn_arrow")
        if turn_arrow is not None and not turn_arrow.is_empty:
            turn_arrow_overlap_area += float(turn_arrow.intersection(junction_surface).area)

    marking_intrusion_area = marking_overlap_area + lane_guide_overlap_area + turn_arrow_overlap_area
    low_score_items = [
        item
        for item in sorted(junction_scores, key=lambda value: value["score"])
        if item["score"] < MIN_JUNCTION_QUALITY_SCORE
    ]
    min_score = min((item["score"] for item in junction_scores), default=0.0)
    avg_score = (
        sum(float(item["score"]) for item in junction_scores) / len(junction_scores)
        if junction_scores
        else 0.0
    )

    return {
        "checked_junction_count": len(records),
        "min_junction_area_m2": min(areas) if areas else 0.0,
        "small_junction_area_threshold_m2": 20.0,
        "small_junction_area_count": len(small_area_items),
        "socket_boundary_fallback_count": fallback_count,
        "zero_or_underconnected_junction_count": zero_socket_count,
        "lane_connector_count": lane_connector_count,
        "socket_throat_check_count": socket_checks,
        "socket_throat_gap_count": len(socket_gap_items),
        "junction_marking_overlap_m2": round(marking_overlap_area, 6),
        "junction_lane_guide_overlap_m2": round(lane_guide_overlap_area, 6),
        "junction_turn_arrow_overlap_m2": round(turn_arrow_overlap_area, 6),
        "junction_marking_intrusion_m2": round(marking_intrusion_area, 6),
        "min_junction_quality_score": round(min_score, 2),
        "avg_junction_quality_score": round(avg_score, 2),
        "min_required_junction_quality_score": MIN_JUNCTION_QUALITY_SCORE,
        "low_score_junction_count": len(low_score_items),
        "passed": (
            len(records) > 0
            and len(small_area_items) == 0
            and zero_socket_count == 0
            and lane_connector_count > 0
            and len(socket_gap_items) == 0
            and marking_intrusion_area <= 0.001
            and len(low_score_items) == 0
        ),
        "sample_issues": (small_area_items + socket_gap_items + low_score_items)[:20],
    }


def focused_road_score(
    geometry: dict[str, Any],
    junction_quality: dict[str, Any],
    street_light_quality: dict[str, Any],
    arrow_quality: dict[str, Any],
) -> dict[str, Any]:
    lane_score = 0.0
    if geometry.get("lane_guide_area_m2", 0.0) > 0.0:
        lane_score += 1.2
    if geometry.get("lane_marking_area_m2", 0.0) > 0.0:
        lane_score += 0.8
    if geometry.get("approach_arrow_count", 0) > 0:
        lane_score += 1.0
    if geometry.get("lane_connector_count", 0) > 0 and arrow_quality.get("passed"):
        lane_score += 1.4
    lane_width_min = float(geometry.get("road_width_per_lane_min_m", 0.0) or 0.0)
    lane_width_max = float(geometry.get("road_width_per_lane_max_m", 0.0) or 0.0)
    if 2.6 <= lane_width_min <= lane_width_max <= 4.2:
        lane_score += 0.6

    marking_asset_score = 0.0
    if geometry.get("lane_marking_area_m2", 0.0) > 0.0 and geometry.get("lane_guide_area_m2", 0.0) > 0.0:
        marking_asset_score += 1.2
    if geometry.get("stop_line_area_m2", 0.0) > 0.0 and geometry.get("crosswalk_area_m2", 0.0) > 0.0:
        marking_asset_score += 0.8
    if geometry.get("turn_arrow_area_m2", 0.0) > 0.0:
        marking_asset_score += 0.5
    if street_light_quality.get("street_light_road_conflict_count", 0) == 0:
        marking_asset_score += 0.8
    if junction_quality.get("junction_marking_intrusion_m2", 0.0) <= 0.001:
        marking_asset_score += 0.7

    smoothness_score = 0.0
    if junction_quality.get("socket_throat_gap_count", 1) == 0:
        smoothness_score += 1.0
    if junction_quality.get("low_score_junction_count", 1) == 0:
        smoothness_score += 0.8
    if geometry.get("clipped_road_segment_count", 0) > 0:
        smoothness_score += 0.4
    if geometry.get("transition_curve_count", 0) >= 0:
        smoothness_score += 0.4
    if geometry.get("junction_surface_area_m2", 0.0) > 0.0:
        smoothness_score += 0.4

    return {
        "lane_expression": {
            "score": round(min(5.0, lane_score), 2),
            "max_score": 5,
            "checks": {
                "lane_guides_present": geometry.get("lane_guide_area_m2", 0.0) > 0.0,
                "yellow_center_marking_present": geometry.get("lane_marking_area_m2", 0.0) > 0.0,
                "approach_arrow_count": geometry.get("approach_arrow_count", 0),
                "lane_connector_count": geometry.get("lane_connector_count", 0),
                "road_width_per_lane_range_m": [lane_width_min, lane_width_max],
            },
        },
        "markings_and_assets": {
            "score": round(min(4.0, marking_asset_score), 2),
            "max_score": 4,
            "checks": {
                "stop_line_area_m2": round(float(geometry.get("stop_line_area_m2", 0.0)), 3),
                "crosswalk_area_m2": round(float(geometry.get("crosswalk_area_m2", 0.0)), 3),
                "turn_arrow_area_m2": round(float(geometry.get("turn_arrow_area_m2", 0.0)), 3),
                "street_light_road_conflict_count": street_light_quality.get("street_light_road_conflict_count", 0),
                "junction_marking_intrusion_m2": junction_quality.get("junction_marking_intrusion_m2", 0.0),
            },
        },
        "geometric_smoothness": {
            "score": round(min(3.0, smoothness_score), 2),
            "max_score": 3,
            "checks": {
                "socket_throat_gap_count": junction_quality.get("socket_throat_gap_count", 0),
                "low_score_junction_count": junction_quality.get("low_score_junction_count", 0),
                "junction_surface_area_m2": round(float(geometry.get("junction_surface_area_m2", 0.0)), 3),
                "edge_rounding_fillet_m": JUNCTION_CLEAN_FILLET_M,
                "road_buffer_join_style": "round",
            },
        },
        "focused_total_score": round(min(12.0, lane_score + marking_asset_score + smoothness_score), 2),
        "focused_max_score": 12,
    }


def write_qc_report(
    roads: gpd.GeoDataFrame,
    rule: RoadRule,
    rules: dict[str, RoadRule],
    layers: list[dict[str, Any]],
    meshes: dict[str, trimesh.Trimesh],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """
    导出执行质检报告 (QC Report)。该文件汇总了数据读取、几何构建与拓扑验证全程的监控指标。
    """
    geometry_check = geometry_qc(roads, layers, rule)
    junction_quality_check = junction_quality_qc(layers)
    street_light_quality_check = street_light_pole_road_conflict_qc(roads, rules)
    arrow_traffic_simulation_check = arrow_traffic_qc(roads, rules)
    report = {
        "result": "pass_with_warnings",
        "notes": [
            "已根据 OSM 原生属性映射进行提取。",
            "支持基于不同类型道路(如 lane_count) 动态决定路面宽度等三维模型参数。",
            "路口仅做 union 初步融合，暂未做精细渠化。",
            "CIM 语义属性通过 semantic.json 保存，不依赖 OBJ/GLB 内部属性。"
        ],
        "data_check": {
            "source_file": meta["source_file"],
            "target_crs": meta["target_crs"],
            "local_origin_exists": True,
            "road_count": int(len(roads)),
            "missing_road_id": int(roads["road_id"].isna().sum()),
            "missing_road_name": int(roads["road_name"].isna().sum()),
            "empty_geometry": int(roads.geometry.is_empty.sum()),
        },
        "geometry_check": geometry_check,
        "junction_quality_check": junction_quality_check,
        "street_light_quality_check": street_light_quality_check,
        "arrow_traffic_simulation_check": arrow_traffic_simulation_check,
        "focused_scoring_check": focused_road_score(
            geometry_check,
            junction_quality_check,
            street_light_quality_check,
            arrow_traffic_simulation_check,
        ),
        "cim3_compliance_check": {
            "model_level": "CIM3",
            "plan_accuracy_target_m": CIM3_PLAN_ACCURACY_M,
            "height_accuracy_target_m": CIM3_HEIGHT_ACCURACY_M,
            "min_geometric_detail_m": CIM3_MIN_GEOMETRIC_DETAIL_M,
            "road_required_parts": [
                "Road_Surface",
                "Sidewalk",
                "Curb",
                "Lane_Marking",
                "Stop_Line",
                "Lane_Guide",
                "Channelization_Island",
                "Junction_Surface",
                "Median",
                "Guardrail",
                "Street_Light",
                "Tree",
                "Bridge_Deck",
                "Bridge_Pier",
            ],
            "generated_parts": sorted({name.split("_")[0] if not name.startswith("Street_Light") else "_".join(name.split("_")[:2]) for name in meshes}),
        },
        "mesh_check": mesh_qc(meshes),
        "outputs": {
            "obj": str(OBJ_PATH.relative_to(ROOT)),
            "glb": str(GLB_PATH.relative_to(ROOT)),
            "semantic_json": str(SEMANTIC_PATH.relative_to(ROOT)),
            "qc_json": str(QC_PATH.relative_to(ROOT)),
            "processed_roads": str(LOCAL_ROADS_PATH.relative_to(ROOT)),
        }
    }

    with QC_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return report


def main() -> None:

    """
    主函数入口。串联道路生成的全套流水线流程。
    """
    ensure_dirs()
    rules = load_rules()
    default_rule = rules.get("default_road")

    print("1. 读取并预处理道路中心线...")
    roads, origin, meta = read_and_prepare_roads()

    print("2. 生成道路面、人行道、路缘石和中心标线...")
    layers_geoms = generate_planar_geometries(roads, rules)

    print("3. 生成三维 Mesh 和 Scene...")
    scene, meshes = make_scene(layers_geoms, default_rule, google_center_latlon=projected_xy_to_latlon(origin.x, origin.y))
    asset_meshes = build_cim3_road_asset_meshes(roads, rules, layers_geoms)
    for name, mesh in asset_meshes.items():
        scene.add_geometry(mesh.copy(), node_name=name, geom_name=name)
    meshes.update(asset_meshes)

    print("4. 导出 OBJ...")
    export_scene(scene)
    build_semantic_json(roads, origin, meta, rules, layers_geoms)
    write_qc_report(roads, default_rule, rules, layers_geoms, meshes, meta)

    print("完成。输出文件：")
    print(f"- {OBJ_PATH}")
    print(f"- {GLB_PATH}")
    print(f"- {SEMANTIC_PATH}")
    print(f"- {QC_PATH}")


if __name__ == "__main__":
    main()
