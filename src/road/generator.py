#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Shared road, cross-section, junction, basemap, and roadside asset helpers."""

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
from shapely.geometry import (
    LineString,
    MultiLineString,
    Point,
    Polygon,
    MultiPolygon,
    GeometryCollection,
)
from shapely.ops import nearest_points, substring, unary_union
import trimesh
from trimesh.visual.texture import TextureVisuals
from trimesh.creation import triangulate_polygon

try:
    import rasterio
except ImportError:
    rasterio = None

ROOT = Path(__file__).resolve().parents[2]

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
RULE_PATH = ROOT / "data" / "rules" / "road_rules.json"
SECTION_RULE_PATH = ROOT / "data" / "rules" / "road_section_requirements.json"

TEXTURE_DIR = ROOT / "output" / "textures"
GOOGLE_MAP_TEXTURE_PATH = TEXTURE_DIR / "google_static_map.png"
WORLD_IMAGERY_TEXTURE_PATH = TEXTURE_DIR / "world_imagery_basemap.png"
LOCAL_BASEMAP_TEXTURE_PATH = TEXTURE_DIR / "local_basemap.png"

# 当前主数据采用 CGCS2000 3 度带高斯克吕格投影，中央经线 114E。
TARGET_CRS = "EPSG:4547" # 目标坐标系
MIN_CONNECTOR_LENGTH_M = 0.05 # 最小连接长度
BRIDGE_ELEVATION_GROUP_M = 3.0 # 桥梁高程分组
SWEEP_SAMPLE_INTERVAL_M = 4.0 # 扫掠采样间隔
JUNCTION_NODE_TOLERANCE_M = 3.0 # 路口节点容差
JUNCTION_RADIUS_M = 8.0 # 路口半径
JUNCTION_CLIP_EXTRA_M = 1.5 # 路口剪裁额外距离
MIN_JUNCTION_CLIP_DISTANCE_M = 8.0 # 最小路口剪裁距离
MAX_JUNCTION_CLIP_DISTANCE_M = 14.0 # 最大路口剪裁距离
JUNCTION_ENDPOINT_SNAP_TOLERANCE_M = 3.5 # 路口端点捕捉容差
JUNCTION_LINE_INTERSECTION_TOLERANCE_M = 0.35 # 路口线交集容差
ENABLE_CORRIDOR_JUNCTION_CLUSTERING = True # 启用路口聚类
CORRIDOR_JUNCTION_CLUSTER_DISTANCE_M = 24.0 # 路口聚类距离
CORRIDOR_JUNCTION_CONNECT_TOLERANCE_M = 18.0 # 路口聚类连接容差
CORRIDOR_CROSSING_MIN_ANGLE_DEG = 25.0 # 路口交叉最小角度
CORRIDOR_CROSSING_EXTRA_WIDTH_M = 1.5 # 路口交叉额外宽度
ROAD_SURFACE_OVERLAP_MIN_AREA_M2 = 18.0 # 路面重叠最小面积
ROAD_SURFACE_OVERLAP_MIN_COMPACTNESS = 0.015 # 路面重叠最小紧凑度
JUNCTION_EDGE_MAX_INTERSECTION_FACTOR = 2.6 # 路口边缘最大交集因子
JUNCTION_MIN_SURFACE_AREA_M2 = 4.0 # 路口最小表面面积
JUNCTION_CLEAN_FILLET_M = 2.0 # 路口清洁圆角
JUNCTION_SOCKET_OVERLAP_M = 1.2 # 路口socket重叠距离
JUNCTION_CITYENGINE_SMOOTH_M = 1.1 # 路口城市引擎平滑距离
LANE_CONNECTOR_SAMPLE_COUNT = 16 # 车道连接采样数量
LANE_CONNECTOR_HANDLE_RATIO = 0.45 # 车道连接处理比例
TRANSITION_MIN_ANGLE_DEG = 8.0 # 过渡最小角度
TRANSITION_MIN_LENGTH_M = 4.0 # 过渡最小长度 4.0米
TRANSITION_MAX_LENGTH_M = 24.0 # 过渡最大长度 24.0米
TRANSITION_SAMPLES = 10 # 过渡采样数量 10
TURN_ARROW_LENGTH_M = 4.0 # 转向箭头长度 4.0米
GENERATE_TURN_ARROWS = True # 生成转向箭头
APPROACH_ARROW_NEAR_M = 14.0 # 接近箭头近端距离 14.0米
APPROACH_ARROW_FAR_M = 27.0 # 接近箭头远端距离 27.0米
APPROACH_ARROW_MIN_GAP_M = 6.0 # 接近箭头最小间距 6.0米
CHANNELIZATION_MIN_ANGLE_DEG = 35.0 # 通道化最小角度 35.0度
CHANNELIZATION_MAX_ANGLE_DEG = 125.0 # 通道化最大角度 125.0度
CROSSWALK_DISTANCE_FROM_JUNCTION_M = 7.0 # 人行横道距离路口距离 7.0米
CROSSWALK_STRIPE_WIDTH_M = 0.32 # 人行横道条纹宽度 0.32米
CROSSWALK_STRIPE_GAP_M = 0.42 # 人行横道条纹间隔 0.42米
CROSSWALK_BAND_LENGTH_M = 3.0 # 人行横道带长度 3.0米
GENERATE_CROSSWALKS = True # 生成人行横道
TREE_JUNCTION_CLEARANCE_M = 20.0 # 树与路口的距离 20.0米
STREET_LIGHT_JUNCTION_CLEARANCE_M = 14.0 # 路灯与路口的距离 14.0米
STREET_LIGHT_POLE_ROAD_CLEARANCE_M = 0.45 # 路灯杆与道路的距离 0.45米
MAX_STREET_LIGHTS_PER_ROAD = 800 # 每条道路最多800盏路灯
MAX_TREES_PER_ROAD = 900 # 每条道路最多900棵树

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


@dataclass
class LaneSocket:  # 车道socket
    lane_socket_id: str # 车道socketID
    parent_socket_id: str # 父socketID
    road_id: str # 道路ID
    lane_index: int # 车道索引
    lane_role: str # 车道角色
    center: tuple[float, float] # 中心点
    tangent: tuple[float, float] # 切线
    normal: tuple[float, float] # 法线
    lane_width: float # 车道宽度
    allowed_movements: list[str] # 允许移动
    lane_id: str = "" # 车道ID
    movement: str = "through" # 移动
    allowed_turns: list[str] | None = None # 允许转向
    traffic_direction: str = "unknown" # 交通方向
    signal_group: str = "unsignalized" # 信号组
    stop_control: str = "yield_or_stop_line" # 停止控制


@dataclass
class RoadSocket:
    socket_id: str # 路口ID
    road_id: str # 道路ID
    junction_id: str # 路口ID
    distance_m: float # 距离 米
    center: tuple[float, float] # 中心点
    tangent: tuple[float, float] # 切线
    normal: tuple[float, float] # 法线
    road_width: float # 道路宽度
    lane_count: int # 车道数
    lane_width: float # 车道宽度
    forward_lane_count: int # 前向车道数
    backward_lane_count: int # 后向车道数
    sidewalk_width: float # 人行道宽度
    curb_width: float # 路缘石宽度
    clip_radius: float # 剪裁半径
    clip_distance: float # 剪裁距离
    line_direction_sign: float # 线方向标志
    elevation: float # 高程
    approach_type: str # 接近类型
    left_edge: tuple[float, float] # 左边缘
    right_edge: tuple[float, float] # 右边缘
    lane_sockets: list[LaneSocket] # 车道socket
    road_class: str = "unknown" # 道路等级
    node_distance_m: float = 0.0 # 节点距离 米
    generation_method: str = "socket" # 生成方法
    road_name: str = "unknown" # 道路名称


@dataclass
class JunctionNode: # 路口节点
    junction_id: str # 路口ID
    center: Point # 中心点
    radius: float # 半径
    sockets: list[RoadSocket] # 路口socket
    junction_type: str = "UNKNOWN" # 路口类型
    hierarchy: str = "LOCAL_JUNCTION" # 路口层次
    surface_strategy: str = "edge_intersection" # 表面策略
    detection_method: str = "endpoint_cluster" # 检测方法
    metadata: dict[str, Any] | None = None # 元数据


@dataclass
class LaneConnector: # 车道连接器
    connector_id: str # 连接器ID
    junction_id: str # 路口ID
    from_lane: LaneSocket # 来自车道
    to_lane: LaneSocket # 到车道
    movement_type: str # 移动类型
    centerline: LineString # 中心线
    width: float # 宽度
    surface_polygon: Polygon # 表面多边形   
    marking_guides: list[LineString] # 标记引导线


@dataclass(frozen=True)
class LaneLayout: # 车道布局
    lane_count: int # 车道数
    forward_count: int # 前向车道数
    backward_count: int # 后向车道数
    center_turn_count: int = 0 # 中心转向数




def load_rules() -> dict[str, RoadRule]:
    """从规则 JSON 文件中读取并加载所有模板信息，返回规则字典。"""
    with RULE_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: RoadRule(**v) for k, v in data.items() if isinstance(v, dict)}






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






def line_sample_distances(line: LineString, interval: float) -> list[float]:
    if line.length <= 0.0:
        return [0.0]
    count = max(int(math.ceil(line.length / interval)), 1)
    distances = [min(line.length, i * interval) for i in range(count + 1)]
    if distances[-1] < line.length:
        distances.append(float(line.length))
    return sorted(set(round(float(d), 3) for d in distances))




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




def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}




def int_env(name: str, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = int(default)
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


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


def web_mercator_tile_fraction(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    lat = max(-85.05112878, min(85.05112878, float(lat)))
    lon = ((float(lon) + 180.0) % 360.0) - 180.0
    lat_rad = math.radians(lat)
    n = 2.0 ** int(zoom)
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def world_imagery_zoom_for_bounds(
    center_latlon: tuple[float, float],
    bounds: tuple[float, float, float, float] | None,
) -> int:
    default_zoom = int_env("WORLD_IMAGERY_ZOOM", int_env("GOOGLE_MAP_ZOOM", 16, 1, 19), 1, 19)
    if bounds is None or not env_flag("WORLD_IMAGERY_AUTO_ZOOM", True):
        return default_zoom

    minx, miny, maxx, maxy = bounds
    extent_m = max(float(maxx) - float(minx), float(maxy) - float(miny), 1.0)
    target_px = int_env("WORLD_IMAGERY_TARGET_PX", 1536, 512, 4096)
    lat = max(-85.0, min(85.0, float(center_latlon[0])))
    meters_per_pixel_at_z0 = 156543.03392804097 * max(math.cos(math.radians(lat)), 0.05)
    zoom = math.floor(math.log2((meters_per_pixel_at_z0 * target_px) / extent_m))
    return max(int_env("WORLD_IMAGERY_MIN_ZOOM", 1, 1, 19), min(int_env("WORLD_IMAGERY_MAX_ZOOM", 19, 1, 19), int(zoom)))


def ensure_world_imagery_texture(
    center_latlon: tuple[float, float] | None,
    bounds: tuple[float, float, float, float] | None,
) -> Path | None:
    if center_latlon is None:
        return None
    if WORLD_IMAGERY_TEXTURE_PATH.exists() and WORLD_IMAGERY_TEXTURE_PATH.stat().st_size > 1024:
        return WORLD_IMAGERY_TEXTURE_PATH

    try:
        import requests
        from PIL import Image
        from io import BytesIO
    except Exception as exc:
        logging.warning("World imagery basemap requires requests and Pillow: %s", exc)
        return None

    zoom = world_imagery_zoom_for_bounds(center_latlon, bounds)
    tile_size = 256
    max_px = int_env("WORLD_IMAGERY_MAX_PX", 2048, 512, 4096)
    minx, miny, maxx, maxy = bounds or (-512.0, -512.0, 512.0, 512.0)
    width_m = max(float(maxx) - float(minx), 1.0)
    height_m = max(float(maxy) - float(miny), 1.0)

    while True:
        meters_per_pixel = 156543.03392804097 * max(math.cos(math.radians(center_latlon[0])), 0.05) / (2 ** zoom)
        width_px = max(1, int(math.ceil(width_m / meters_per_pixel)))
        height_px = max(1, int(math.ceil(height_m / meters_per_pixel)))
        if max(width_px, height_px) <= max_px or zoom <= int_env("WORLD_IMAGERY_MIN_ZOOM", 1, 1, 19):
            break
        zoom -= 1

    center_tile_x, center_tile_y = web_mercator_tile_fraction(center_latlon[0], center_latlon[1], zoom)
    center_px_x = center_tile_x * tile_size
    center_px_y = center_tile_y * tile_size
    x0 = center_px_x - width_px / 2.0
    y0 = center_px_y - height_px / 2.0
    x1 = center_px_x + width_px / 2.0
    y1 = center_px_y + height_px / 2.0
    tile_x0 = math.floor(x0 / tile_size)
    tile_y0 = math.floor(y0 / tile_size)
    tile_x1 = math.floor((x1 - 1.0) / tile_size)
    tile_y1 = math.floor((y1 - 1.0) / tile_size)

    tile_count = (tile_x1 - tile_x0 + 1) * (tile_y1 - tile_y0 + 1)
    max_tiles = int_env("WORLD_IMAGERY_MAX_TILES", 96, 4, 256)
    if tile_count > max_tiles:
        logging.warning(
            "World imagery tile request would need %s tiles at zoom %s; lower WORLD_IMAGERY_ZOOM or raise WORLD_IMAGERY_MAX_TILES.",
            tile_count,
            zoom,
        )
        return None

    mosaic = Image.new("RGB", ((tile_x1 - tile_x0 + 1) * tile_size, (tile_y1 - tile_y0 + 1) * tile_size), (88, 118, 94))
    n = 2 ** zoom
    headers = {"User-Agent": "cim-road-poc/1.0"}
    for tile_y in range(tile_y0, tile_y1 + 1):
        if tile_y < 0 or tile_y >= n:
            continue
        for tile_x in range(tile_x0, tile_x1 + 1):
            wrapped_x = tile_x % n
            url = f"https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{tile_y}/{wrapped_x}"
            try:
                response = requests.get(url, timeout=30, headers=headers)
                response.raise_for_status()
                tile = Image.open(BytesIO(response.content)).convert("RGB")
            except Exception as exc:
                logging.warning("Unable to download world imagery tile z=%s x=%s y=%s: %s", zoom, wrapped_x, tile_y, exc)
                continue
            mosaic.paste(tile, ((tile_x - tile_x0) * tile_size, (tile_y - tile_y0) * tile_size))

    crop_box = (
        int(round(x0 - tile_x0 * tile_size)),
        int(round(y0 - tile_y0 * tile_size)),
        int(round(x1 - tile_x0 * tile_size)),
        int(round(y1 - tile_y0 * tile_size)),
    )
    image = mosaic.crop(crop_box)
    TEXTURE_DIR.mkdir(parents=True, exist_ok=True)
    image.save(WORLD_IMAGERY_TEXTURE_PATH)
    return WORLD_IMAGERY_TEXTURE_PATH if WORLD_IMAGERY_TEXTURE_PATH.exists() else None


def raster_bands_to_uint8(data: np.ndarray, source_dtype: str | None = None) -> np.ndarray:
    if data.ndim == 2:
        data = np.stack([data, data, data], axis=0)
    if data.shape[0] == 1:
        data = np.repeat(data, 3, axis=0)
    data = data[:3].astype(np.float32)
    if source_dtype == "uint8":
        return np.clip(data, 0, 255).astype(np.uint8)

    result = np.zeros_like(data, dtype=np.uint8)
    for idx in range(data.shape[0]):
        band = data[idx]
        valid = np.isfinite(band)
        if not np.any(valid):
            continue
        low, high = np.percentile(band[valid], [2, 98])
        if high <= low:
            high = float(np.nanmax(band[valid]))
            low = float(np.nanmin(band[valid]))
        if high <= low:
            result[idx] = np.clip(band, 0, 255).astype(np.uint8)
        else:
            result[idx] = np.clip((band - low) * 255.0 / (high - low), 0, 255).astype(np.uint8)
    return result


def ensure_local_raster_texture(
    raster_path: Path,
    bounds: tuple[float, float, float, float] | None,
) -> Path | None:
    if bounds is None:
        logging.warning("BASEMAP_RASTER_PATH needs model bounds; using generated GIS basemap fallback.")
        return None
    if rasterio is None:
        logging.warning("BASEMAP_RASTER_PATH requires rasterio; using generated GIS basemap fallback.")
        return None
    if not raster_path.exists():
        logging.warning("BASEMAP_RASTER_PATH does not exist: %s", raster_path)
        return None

    try:
        from PIL import Image
        from rasterio.transform import from_bounds
        from rasterio.warp import Resampling, reproject

        minx, miny, maxx, maxy = bounds
        width_m = max(float(maxx) - float(minx), 1.0)
        height_m = max(float(maxy) - float(miny), 1.0)
        max_px = int_env("LOCAL_BASEMAP_MAX_PX", int_env("WORLD_IMAGERY_MAX_PX", 2048, 512, 4096), 512, 4096)
        if width_m >= height_m:
            out_w = max(1, max_px)
            out_h = max(1, int(round(max_px * height_m / width_m)))
        else:
            out_h = max(1, max_px)
            out_w = max(1, int(round(max_px * width_m / height_m)))

        with rasterio.open(raster_path) as src:
            if src.crs is None:
                logging.warning("BASEMAP_RASTER_PATH has no CRS; use BASEMAP_TEXTURE_PATH for non-georeferenced images.")
                return None
            indexes = [1, 2, 3] if src.count >= 3 else [1]
            destination = np.zeros((len(indexes), out_h, out_w), dtype=np.float32)
            dst_transform = from_bounds(float(minx), float(miny), float(maxx), float(maxy), out_w, out_h)
            for dst_idx, src_idx in enumerate(indexes):
                reproject(
                    source=rasterio.band(src, src_idx),
                    destination=destination[dst_idx],
                    src_transform=src.transform,
                    src_crs=src.crs,
                    src_nodata=src.nodata,
                    dst_transform=dst_transform,
                    dst_crs=TARGET_CRS,
                    dst_nodata=0,
                    resampling=Resampling.bilinear,
                )
            rgb = raster_bands_to_uint8(destination, src.dtypes[0] if src.dtypes else None)

        image = Image.fromarray(np.transpose(rgb, (1, 2, 0)), mode="RGB")
        TEXTURE_DIR.mkdir(parents=True, exist_ok=True)
        image.save(LOCAL_BASEMAP_TEXTURE_PATH)
    except Exception as exc:
        logging.warning("Unable to build local raster basemap texture: %s", exc)
        return None
    return LOCAL_BASEMAP_TEXTURE_PATH if LOCAL_BASEMAP_TEXTURE_PATH.exists() else None


def ensure_basemap_texture(
    center_latlon: tuple[float, float] | None,
    bounds: tuple[float, float, float, float] | None,
) -> Path | None:
    raster_texture = os.environ.get("BASEMAP_RASTER_PATH")
    if raster_texture:
        texture = ensure_local_raster_texture(Path(raster_texture), bounds)
        if texture is not None:
            return texture

    local_texture = os.environ.get("BASEMAP_TEXTURE_PATH")
    if local_texture:
        path = Path(local_texture)
        if path.exists() and path.stat().st_size > 1024:
            return path
        logging.warning("BASEMAP_TEXTURE_PATH does not exist or is too small: %s", path)

    provider = os.environ.get("BASEMAP_PROVIDER", "esri").strip().lower()
    if provider in {"none", "off", "false", "0"}:
        return None
    if provider in {"google", "google_static"}:
        return ensure_google_map_texture(center_latlon)
    if provider in {"esri", "arcgis", "world_imagery", "world-imagery", "imagery"}:
        return ensure_world_imagery_texture(center_latlon, bounds)

    logging.warning("Unknown BASEMAP_PROVIDER=%s; using Esri World Imagery.", provider)
    return ensure_world_imagery_texture(center_latlon, bounds)


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
    mesh.metadata["name"] = "GIS_WorldImagery" if texture_path is not None else "GIS_BaseMap"
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
    global_origin_xy: tuple[float, float] | None = None,
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

    texture_bounds = (minx, miny, maxx, maxy)
    if global_origin_xy is not None:
        ox, oy = global_origin_xy
        texture_bounds = (minx + ox, miny + oy, maxx + ox, maxy + oy)
    texture_path = ensure_basemap_texture(google_center_latlon, texture_bounds)
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




































def main() -> None:
    """Deprecated road-only entry; use the city pipeline instead."""
    raise SystemExit(
        "road-only road_test generation has been retired. "
        "Use scripts/05_generate_cim_city.py or python -m cli.generate_city."
    )


if __name__ == "__main__":
    main()
