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
import re
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
from shapely.ops import unary_union, triangulate
import trimesh
from trimesh.creation import triangulate_polygon

try:
    import rasterio
except ImportError:
    rasterio = None

ROOT = Path(__file__).resolve().parents[1]

RAW_ROADS = ROOT / "data" / "raw" / "road_centerline.geojson"
PROCESSED_DIR = ROOT / "data" / "processed"
RULE_PATH = ROOT / "data" / "rules" / "road_rules.json"

OBJ_PATH = ROOT / "output" / "obj" / "road_test.obj"
GLB_PATH = ROOT / "output" / "gltf" / "road_test.glb"
SEMANTIC_PATH = ROOT / "output" / "semantic" / "road_test_semantic.json"
QC_PATH = ROOT / "output" / "qc_report" / "road_test_qc_report.json"
DGM_DIR = ROOT / "data" / "raw" / "road_centerline"  # 高程切片所在的文件夹

LOCAL_ROADS_PATH = PROCESSED_DIR / "road_centerline_local.geojson"

# 安联球场位于德国慕尼黑，适合用 UTM Zone 32N
TARGET_CRS = "EPSG:32632"
ROAD_LINK_GAP_TOLERANCE_M = 2.5
MIN_CONNECTOR_LENGTH_M = 0.05
BRIDGE_ELEVATION_GROUP_M = 3.0


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


def ensure_dirs() -> None:
    """确保所有需要用到的输出目录都已存在，若不存在则自动创建。"""
    for path in [
        PROCESSED_DIR,
        OBJ_PATH.parent,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def load_rules() -> dict[str, RoadRule]:
    """从规则 JSON 文件中读取并加载所有模板信息，返回规则字典。"""
    with RULE_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: RoadRule(**v) for k, v in data.items() if isinstance(v, dict)}


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

    # 将 MultiLineString 拆分（Explode）为单条的 LineString
    roads = roads.explode(index_parts=False).reset_index(drop=True)
    roads = roads[roads.geometry.geom_type == "LineString"].copy()

    # 标准化处理道路 ID
    if "osmid" in roads.columns:
        roads["road_id"] = roads["osmid"].apply(normalize_osmid)
    elif "id" in roads.columns:
        roads["road_id"] = roads["id"].astype(str)
    else:
        # 若无数据源 ID，则自动生成五位序列化编号
        roads["road_id"] = [f"R{i:05d}" for i in range(len(roads))]

    # 标准化处理道路名称
    if "name" in roads.columns:
        roads["road_name"] = roads["name"].apply(lambda v: "unknown" if v is None or str(v) == "nan" else str(v))
    else:
        roads["road_name"] = "unknown"

    # 映射与提取 OSM 原生道路属性
    roads["road_class"] = roads["highway"] if "highway" in roads.columns else "unclassified"
    roads["lane_count"] = roads["lanes"] if "lanes" in roads.columns else None
    roads["maxspeed"] = roads["maxspeed"] if "maxspeed" in roads.columns else None
    roads["oneway"] = roads["oneway"] if "oneway" in roads.columns else None
    roads["is_bridge"] = roads["bridge"] if "bridge" in roads.columns else None
    roads["road_ref"] = roads["ref"] if "ref" in roads.columns else None
    roads["access"] = roads["access"] if "access" in roads.columns else None
    roads["length_m"] = roads.geometry.length

    # 尝试加载指定目录下的所有 DGM 高程切片，支持多图幅 tif 自动拼接与读取
    dgm_srcs = []
    if rasterio and DGM_DIR.exists() and DGM_DIR.is_dir():
        for tif_file in DGM_DIR.glob("*.tif*"):  # 匹配 .tif 或 .tiff
            try:
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

    # 提取核心所需的属性列，保存处理完毕的局部路网 GeoJSON 
    keep_cols = [
        "road_id", "road_name", "road_class", "lane_count", "maxspeed", 
        "oneway", "is_bridge", "road_ref", "access", "elevation", "length_m", 
        "bridge_clearance", "height_source", "height_mode", "ground_z_start", 
        "ground_z_end", "road_z_start", "road_z_end", "road_z_mean", "geometry"
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


def parse_lane_count(val: Any, default: int = 2) -> int:
    """从字符串或列表中解析出车道数（提取首个数字），若解析失败则返回默认值 default。"""
    val_str = safe_str(val)
    if not val_str:
        return default
    m = re.search(r'\d+', val_str)
    return int(m.group()) if m else default


def get_road_rule(row: pd.Series, rules: dict[str, RoadRule]) -> RoadRule:
    """依据原始道路属性（如车道数）动态计算并返回适用于该道路的规则与宽度配置。"""
    road_class = safe_str(row.get("road_class")) or "default_road"
    base_rule = rules.get(road_class, rules.get("default_road"))
    rule = copy.copy(base_rule)
    
    # 覆盖车道数并重新计算道路总宽度
    lanes = parse_lane_count(row.get("lane_count"), default=rule.lane_count)
    rule.lane_count = lanes
    rule.road_width = lanes * rule.lane_width
    return rule


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
            "road_z_mean": 0.0, "height_mode": "unknown", "height_source": "default"
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
                        print(f"坐标点 ({x:.2f}, {y:.2f}) 被高程切片覆盖，采样高度: {float(val):.2f}m")
                        return float(val)
                    except Exception:
                        pass
        # 如果没有 DGM 或图幅均未覆盖该点，使用安联球场基准高度 (约 505m) 模拟
        print(f"坐标点 ({pt[0]:.2f}, {pt[1]:.2f}) 未被覆盖，使用默认模拟高度: 505.0m")
        return 505.0

    ground_z_start = sample_z(start_pt)
    ground_z_end = sample_z(end_pt)

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

    road_z_mean = (road_z_start + road_z_end) / 2.0

    return pd.Series({
        "ground_z_start": round(ground_z_start, 3),
        "ground_z_end": round(ground_z_end, 3),
        "road_z_start": round(road_z_start, 3),
        "road_z_end": round(road_z_end, 3),
        "road_z_mean": round(road_z_mean, 3),
        "height_mode": height_mode,
        "height_source": height_source
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
    return line.buffer(width / 2.0, cap_style=2, join_style=2)


def road_endpoint_points(line: LineString) -> list[Point]:
    coords = list(line.coords)
    if len(coords) < 2:
        return []
    return [Point(coords[0]), Point(coords[-1])]


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


def road_topology_group(row: pd.Series) -> str:
    """Group roads for planar merging without letting bridges fuse into ground roads."""
    if bool(row.get("is_bridge", False)):
        elevation = float(row.get("elevation", 0.0))
        bucket = round(elevation / BRIDGE_ELEVATION_GROUP_M)
        return f"bridge_{bucket}"
    return "ground"


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
        road_entries = []
        default_rule = rules.get("default_road")

        for _, row in group.iterrows():
            line: LineString = row.geometry
            rule = get_road_rule(row, rules)
            if line is None or line.is_empty:
                continue
            road_entries.append((line, rule))

        for line, rule in road_entries:
            road_surface = line_buffer(line, rule.road_width)
            total_surface = line_buffer(line, rule.road_width + 2 * rule.sidewalk_width)

            if not road_surface.is_empty:
                road_surfaces.append(road_surface)

            if not total_surface.is_empty:
                total_surfaces.append(total_surface)

            marking = line_buffer(line, rule.lane_marking_width)
            if not marking.is_empty:
                lane_markings.append(marking)

        for connector in build_short_road_connectors([line for line, _ in road_entries]):
            road_surface = line_buffer(connector, default_rule.road_width)
            total_surface = line_buffer(connector, default_rule.road_width + 2 * default_rule.sidewalk_width)

            if not road_surface.is_empty:
                road_surfaces.append(road_surface)

            if not total_surface.is_empty:
                total_surfaces.append(total_surface)

        if not road_surfaces or not total_surfaces:
            continue

        # [核心] 仅对相同高程组内部的道路对象执行布尔并集 (初步路口与渠化融合)
        merged_road = clean_polygonal(unary_union(road_surfaces))
        merged_total = clean_polygonal(unary_union(total_surfaces))

        if merged_road is None or merged_total is None:
            continue

        sidewalk = clean_polygonal(merged_total.difference(merged_road))

        curb = clean_polygonal(merged_road.buffer(default_rule.curb_width, join_style=2).difference(merged_road))

        lane_marking_union = clean_polygonal(unary_union(lane_markings).intersection(merged_road))

        layers_geoms.append({
            "elevation": float(elevation),
            "road_surface": merged_road,
            "sidewalk": sidewalk,
            "curb": curb,
            "lane_marking": lane_marking_union,
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


def make_scene(layers: list[dict[str, Any]], rule: RoadRule) -> tuple[trimesh.Scene, dict[str, trimesh.Trimesh]]:
    """
    场景构建器：接收加工好的 2D 几何信息，映射相关高度与颜色预设，转化为真正的 3D Mesh 对象，
    并将它们统筹装配合并至 Trimesh 核心场景容器 (Scene) 内部。
    """
    # RGBA 颜色，仅用于 POC 视觉区分
    road_color = [45, 45, 45, 255]
    sidewalk_color = [170, 170, 170, 255]
    curb_color = [210, 210, 210, 255]
    marking_color = [255, 255, 255, 255]

    scene = trimesh.Scene()
    all_meshes = {}

    for i, layer in enumerate(layers):
        z = layer["elevation"] + rule.road_z

        # 分层级生成路面、人行道、路缘石和标线的独立 Mesh 对象
        road_mesh = polygon_to_top_mesh(
            layer["road_surface"],
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

        layer_meshes = {
            f"Road_Surface_{i}": road_mesh,
            f"Sidewalk_{i}": sidewalk_mesh,
            f"Curb_{i}": curb_mesh,
            f"Lane_Marking_{i}": lane_mesh,
        }

        # 过滤掉空数据的网格，将有效模型添加到场景容器中
        for name, mesh in layer_meshes.items():
            if len(mesh.vertices) > 0:
                scene.add_geometry(mesh, node_name=name, geom_name=name)
                all_meshes[name] = mesh

    return scene, all_meshes


def export_scene(scene: trimesh.Scene) -> None:
    """
    将整个 trimesh 场景导出为 OBJ。FBX 在 Blender 材质流程中导出。
    """
    scene.export(OBJ_PATH)


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
        objects.append({
            "object_id": f"{row['road_id']}_source_centerline",
            "source_road_id": row["road_id"],
            "road_name": row["road_name"],
            "road_class": safe_str(row.get("road_class")),
            "lane_count": safe_str(row.get("lane_count")),
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
            "rule_id": safe_str(row.get("road_class")) if safe_str(row.get("road_class")) in rules else "default_road",
        })

    for i, layer in enumerate(layers):
        ele = layer["elevation"]
        merged_objects = [
            ("Road_Surface", layer["road_surface"], default_rule.material),
            ("Sidewalk", layer["sidewalk"], default_rule.sidewalk_material),
            ("Curb", layer["curb"], default_rule.curb_material),
            ("Lane_Marking", layer["lane_marking"], default_rule.marking_material),
        ]

        for base_type, geom, material in merged_objects:
            area = 0.0 if geom is None or geom.is_empty else float(geom.area)
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

    semantic = {
        "project": "cim_road_poc",
        "model_level": "CIM3",
        "description": "第一轮 POC：统一道路模板生成公共道路模型",
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


def geometry_qc(roads: gpd.GeoDataFrame, layers: list[dict[str, Any]], rule: RoadRule) -> dict[str, Any]:
    """
    2D 几何平面质检：统计生成道路的总中心线长及分类铺装面（如人行道、路缘石）覆盖面积。
    """
    road_area = sum([0.0 if l["road_surface"] is None else float(l["road_surface"].area) for l in layers])
    sidewalk_area = sum([0.0 if l["sidewalk"] is None else float(l["sidewalk"].area) for l in layers])
    curb_area = sum([0.0 if l["curb"] is None else float(l["curb"].area) for l in layers])
    marking_area = sum([0.0 if l["lane_marking"] is None else float(l["lane_marking"].area) for l in layers])

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
    }


def write_qc_report(
    roads: gpd.GeoDataFrame,
    rule: RoadRule,
    layers: list[dict[str, Any]],
    meshes: dict[str, trimesh.Trimesh],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """
    导出执行质检报告 (QC Report)。该文件汇总了数据读取、几何构建与拓扑验证全程的监控指标。
    """
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
        "geometry_check": geometry_qc(roads, layers, rule),
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
    scene, meshes = make_scene(layers_geoms, default_rule)

    print("4. 导出 OBJ...")
    export_scene(scene)

    print("完成。输出文件：")
    print(f"- {OBJ_PATH}")


if __name__ == "__main__":
    main()
