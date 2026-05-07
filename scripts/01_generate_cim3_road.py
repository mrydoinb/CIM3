#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
第一轮测试主脚本：

输入：
- data/raw/road_centerline.geojson
- data/rules/road_rules.json

输出：
- data/processed/road_centerline_local.geojson
- output/obj/road_test.obj
- output/gltf/road_test.glb
- output/semantic/road_test_semantic.json
- output/qc_report/road_test_qc_report.json

当前策略：
- 不区分道路等级
- 区分道路等级，根据 OSM 属性动态映射字段
- 支持依据 lanes 字段动态计算每条道路对应的路面宽度
- 生成道路面、人行道、路缘石、中心标线
- 路口通过 union 初步融合
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
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
    Polygon,
    MultiPolygon,
    GeometryCollection,
    mapping,
)
from shapely.ops import unary_union, triangulate
import trimesh
from trimesh.creation import triangulate_polygon


ROOT = Path(__file__).resolve().parents[1]

RAW_ROADS = ROOT / "data" / "raw" / "road_centerline.geojson"
PROCESSED_DIR = ROOT / "data" / "processed"
RULE_PATH = ROOT / "data" / "rules" / "road_rules.json"

OBJ_PATH = ROOT / "output" / "obj" / "road_test.obj"
GLB_PATH = ROOT / "output" / "gltf" / "road_test.glb"
SEMANTIC_PATH = ROOT / "output" / "semantic" / "road_test_semantic.json"
QC_PATH = ROOT / "output" / "qc_report" / "road_test_qc_report.json"

LOCAL_ROADS_PATH = PROCESSED_DIR / "road_centerline_local.geojson"

# 安联球场位于德国慕尼黑，适合用 UTM Zone 32N
TARGET_CRS = "EPSG:32632"


@dataclass
class RoadRule:
    """定义道路生成的规则参数，包括车道数、各部分宽度、高度、材质等。"""
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
"""
  "default_road": {
    "lane_count": 2,    # 车道数
    "lane_width": 3.5,  # 车道宽度
    "road_width": 7.0,   # 道路宽度
    "sidewalk_width": 2.0, # 人行道宽度
    "curb_width": 0.3,    # 路缘石宽度
    "curb_height": 0.15,  # 路缘石高度
    "lane_marking_width": 0.15,  # 标线宽度
    "road_z": 0.0,  # 道路高度
    "lane_marking_z_offset": 0.015,  # 标线高度偏移
    "material": "asphalt",  # 道路材质
    "sidewalk_material": "concrete",  # 人行道材质
    "curb_material": "curb_concrete",  # 路缘石材质   
    "marking_material": "white_marking"  # 标线材质
  }
"""
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
        GLB_PATH.parent,
        SEMANTIC_PATH.parent,
        QC_PATH.parent,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def load_rules() -> dict[str, RoadRule]:
    """从规则 JSON 文件中读取并加载所有模板信息，返回规则字典。"""
    with RULE_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: RoadRule(**v) for k, v in data.items() if isinstance(v, dict)}


def read_and_prepare_roads() -> tuple[gpd.GeoDataFrame, LocalOrigin, dict[str, Any]]:
    """
    读取 OSM 道路中心线，投影到 EPSG:32632，然后转换到局部坐标。
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
        # OSM GeoJSON 通常为 WGS84
        roads = roads.set_crs("EPSG:4326")

    # 清洗数据：只保留有效的线段几何体（LineString 和 MultiLineString）
    roads = roads[roads.geometry.notna()].copy()
    roads = roads[roads.geometry.geom_type.isin(["LineString", "MultiLineString"])].copy()

    # 投影到目标坐标系 (EPSG:32632, UTM Zone 32N)，单位转换为米
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
        # 如果没有源 ID 数据，则自动生成五位序列号
        roads["road_id"] = [f"R{i:05d}" for i in range(len(roads))]

    # 标准化处理道路名称
    if "name" in roads.columns:
        roads["road_name"] = roads["name"].apply(lambda v: "unknown" if v is None or str(v) == "nan" else str(v))
    else:
        roads["road_name"] = "unknown"

    # OSM 字段到 CIM 测试字段映射
    roads["road_class"] = roads["highway"] if "highway" in roads.columns else "unclassified"
    roads["lane_count"] = roads["lanes"] if "lanes" in roads.columns else None
    roads["maxspeed"] = roads["maxspeed"] if "maxspeed" in roads.columns else None
    roads["oneway"] = roads["oneway"] if "oneway" in roads.columns else None
    roads["is_bridge"] = roads["bridge"] if "bridge" in roads.columns else None
    roads["road_ref"] = roads["ref"] if "ref" in roads.columns else None
    roads["access"] = roads["access"] if "access" in roads.columns else None
    roads["length_m"] = roads.geometry.length

    roads["elevation"] = 0.0
    roads["elevation"] = roads.apply(extract_elevation, axis=1)

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

    # 提取所需属性列，保存处理好的局部路网 GeoJSON
    keep_cols = [
        "road_id", "road_name", "road_class", "lane_count", "maxspeed", 
        "oneway", "is_bridge", "road_ref", "access", "elevation", "length_m", "geometry"
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
    OSMnx 导出的 osmid 有时是 int，有时是 list。此函数将其统一转为字符串形式。
    """
    if isinstance(value, (list, tuple, set)):
        return "_".join(str(v) for v in value)
    return str(value)


def translate_to_local(geom, origin: LocalOrigin):
    """
    利用 Shapely 的仿射变换 (translate)，将全局投影坐标转换为以 origin 为原点的局部坐标。
    """
    return shapely.affinity.translate(geom, xoff=-origin.x, yoff=-origin.y, zoff=-origin.z)


def safe_str(val: Any) -> str | None:
    """安全转换属性值为字符串，若是空值则返回 None。"""
    if val is None:
        return None
    val_str = str(val)
    if val_str in ("nan", "None", "<NA>", "NaN", "NaT"):
        return None
    return val_str


def parse_lane_count(val: Any, default: int = 2) -> int:
    """从字符串或列表中解析出车道数（提取第一个数字），如果无效则返回 default。"""
    val_str = safe_str(val)
    if not val_str:
        return default
    m = re.search(r'\d+', val_str)
    return int(m.group()) if m else default


def get_road_rule(row: pd.Series, rules: dict[str, RoadRule]) -> RoadRule:
    """依据原始道路属性（如车道数）动态计算并重写道路宽度模板。"""
    road_class = safe_str(row.get("road_class")) or "default_road"
    base_rule = rules.get(road_class, rules.get("default_road"))
    rule = copy.copy(base_rule)
    
    # 覆盖车道数并重新计算道路总宽度
    lanes = parse_lane_count(row.get("lane_count"), default=rule.lane_count)
    rule.lane_count = lanes
    rule.road_width = lanes * rule.lane_width
    return rule


def extract_elevation(row: pd.Series) -> float:
    """尝试从 OSM 属性中解析高程或层级(layer)转换为 Z 坐标偏移。"""
    # 1. 尝试解析 ele 标签 (绝对高程)
    ele = row.get("ele")
    if pd.notna(ele):
        try:
            m = re.search(r"[-+]?\d*\.\d+|[-+]?\d+", str(ele))
            if m:
                return float(m.group())
        except Exception:
            pass
            
    # 2. 尝试解析 layer 标签 (相对层级，OSM 中通常用于立交桥/隧道)
    layer = row.get("layer")
    if pd.notna(layer):
        try:
            m = re.search(r"[-+]?\d+", str(layer))
            if m:
                return float(m.group()) * 5.0  # 假设每层相对层高为 5 米
        except Exception:
            pass
            
    return 0.0


def clean_polygonal(geom):
    """
    清理 polygon / multipolygon。
    buffer(0) 是一种修复包含微小自交（Self-intersection）和无效多边形的常用技巧。
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

    # 如果转换后变成了混合集合，仅提取其中的面级对象并进行合并
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ["Polygon", "MultiPolygon"] and not g.is_empty]
        if not polys:
            return None
        return unary_union(polys)

    return None


def iter_polygons(geom) -> Iterable[Polygon]:
    """
    将 Polygon / MultiPolygon / GeometryCollection 展开为 Polygon。
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
    将线段沿法向等距缓冲区展开，生成多边形面 (Buffer)。
    cap_style=2 (平头/平端裁剪)，join_style=2 (Mitre，即拐角处尖角延伸)。
    """
    return line.buffer(width / 2.0, cap_style=2, join_style=2)


def generate_planar_geometries(roads: gpd.GeoDataFrame, rules: dict[str, RoadRule]) -> dict[str, Any]:
def generate_planar_geometries(roads: gpd.GeoDataFrame, rules: dict[str, RoadRule]) -> list[dict[str, Any]]:
    """
    生成道路面、人行道、路缘石、标线的二维面。
    按高程分组生成道路面、人行道、路缘石、标线的二维面。
    不同高程的路网（例如高架桥与地面道路）互相独立，互不融合。
    """
    road_surfaces = []
    total_surfaces = []
    lane_markings = []
    layers_geoms = []

    for _, row in roads.iterrows():
        line: LineString = row.geometry
        rule = get_road_rule(row, rules)
    # 依据高程对道路进行分组处理
    for elevation, group in roads.groupby("elevation"):
        road_surfaces = []
        total_surfaces = []
        lane_markings = []

        # 生成基础道路面（主车道）
        road_surface = line_buffer(line, rule.road_width)
        # 生成涵盖道路及两侧人行道的总范围面
        total_surface = line_buffer(line, rule.road_width + 2 * rule.sidewalk_width)
        for _, row in group.iterrows():
            line: LineString = row.geometry
            rule = get_road_rule(row, rules)

        if not road_surface.is_empty:
            road_surfaces.append(road_surface)
            road_surface = line_buffer(line, rule.road_width)
            total_surface = line_buffer(line, rule.road_width + 2 * rule.sidewalk_width)

        if not total_surface.is_empty:
            total_surfaces.append(total_surface)
            if not road_surface.is_empty:
                road_surfaces.append(road_surface)

        # 中心线标线
        marking = line_buffer(line, rule.lane_marking_width)
        if not marking.is_empty:
            lane_markings.append(marking)
            if not total_surface.is_empty:
                total_surfaces.append(total_surface)

    # 合并相交的道路面，并通过 clean_polygonal 清洗消除拓扑错误
    merged_road = clean_polygonal(unary_union(road_surfaces))
    merged_total = clean_polygonal(unary_union(total_surfaces))
            marking = line_buffer(line, rule.lane_marking_width)
            if not marking.is_empty:
                lane_markings.append(marking)

    if merged_road is None:
        raise ValueError("道路面生成失败。")
        # 仅在相同高度的道路间进行布尔合并 (初步渠化融合)
        merged_road = clean_polygonal(unary_union(road_surfaces))
        merged_total = clean_polygonal(unary_union(total_surfaces))

    if merged_total is None:
        raise ValueError("总道路面生成失败。")
        if merged_road is None or merged_total is None:
            continue

    # 人行道 = 涵盖两侧的总范围面 - 基础道路面
    sidewalk = clean_polygonal(merged_total.difference(merged_road))
        sidewalk = clean_polygonal(merged_total.difference(merged_road))

    # 路缘石窄带：基础道路面统向外扩张 curb_width，再减去基础道路面本身
    default_rule = rules.get("default_road")
    curb = clean_polygonal(merged_road.buffer(default_rule.curb_width, join_style=2).difference(merged_road))
        default_rule = rules.get("default_road")
        curb = clean_polygonal(merged_road.buffer(default_rule.curb_width, join_style=2).difference(merged_road))

    # 标线裁剪：标线只保留在道路面内部
    lane_marking_union = clean_polygonal(unary_union(lane_markings).intersection(merged_road))
        lane_marking_union = clean_polygonal(unary_union(lane_markings).intersection(merged_road))

    return {
        "road_surface": merged_road,
        "sidewalk": sidewalk,
        "curb": curb,
        "lane_marking": lane_marking_union,
    }
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
    将 Polygon / MultiPolygon 转成顶部三角面 mesh。
    说明：
    - 使用 shapely.ops.triangulate 做初步三角化
    - 只保留三角形代表点在原 polygon 内部的三角面
    - 适合 POC，不作为最终工程级网格算法
    """
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    v_index: dict[tuple[float, float, float], int] = {}

    # 内部辅助函数：用于添加顶点并去重，返回对应的顶点索引值
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

            # 通过计算向量叉乘来判断法线方向，如果Z分量朝下则翻转
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
    将 Polygon / MultiPolygon 垂直拉伸生成三维实体网格 (Mesh)。
    常用于需要体积感和厚度的模型部分（如路缘石）。

    说明：
    - 顶面和底面通过 triangulate (三角化) 生成。
    - 遍历外环 (exterior) 和所有内孔洞环 (interiors) 生成垂直侧边面。
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

            # 每个垂直侧边四边形均由两个三角形组装而成
            all_faces.append((b1, b2, t2))
            all_faces.append((b1, t2, t1))

    for poly in iter_polygons(geom):
        if poly.area <= 1e-6:
            continue

        # 1. 生成 顶面 与 底面 的网格 (采用约束多边形算法)
        try:
            verts, fcs = triangulate_polygon(poly)
        except Exception:
            continue

        for f in fcs:
            v0, v1, v2 = verts[f[0]], verts[f[1]], verts[f[2]]
            top = [add_vertex(v0[0], v0[1], z_top), add_vertex(v1[0], v1[1], z_top), add_vertex(v2[0], v2[1], z_top)]
            bottom = [add_vertex(v0[0], v0[1], z_bottom), add_vertex(v1[0], v1[1], z_bottom), add_vertex(v2[0], v2[1], z_bottom)]

            p0 = np.array(all_vertices[top[0]])
            p1 = np.array(all_vertices[top[1]])
            p2 = np.array(all_vertices[top[2]])
            normal_z = np.cross(p1 - p0, p2 - p0)[2]

            # 保证顶面法线统一向上
            if normal_z < 0:
                top = [top[0], top[2], top[1]]
            all_faces.append(tuple(top))

            # 保证底面法线统一向下
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


def make_scene(geoms: dict[str, Any], rule: RoadRule) -> tuple[trimesh.Scene, dict[str, trimesh.Trimesh]]:
def make_scene(layers: list[dict[str, Any]], rule: RoadRule) -> tuple[trimesh.Scene, dict[str, trimesh.Trimesh]]:
    """
    将二维平面几何组装并转换为具有高度及材质预设的三维 Mesh，最终组合为一个三维场景 (trimesh.Scene)。
    现已支持多图层(层级)立体装配。
    """
    # RGBA 颜色，仅用于 POC 视觉区分
    road_color = [45, 45, 45, 255]
    sidewalk_color = [170, 170, 170, 255]
    curb_color = [210, 210, 210, 255]
    marking_color = [255, 255, 255, 255]

    z = rule.road_z
    scene = trimesh.Scene()
    all_meshes = {}

    # 分层级生成路面、人行道、路缘石和标线的独立 Mesh 对象
    road_mesh = polygon_to_top_mesh(
        geoms["road_surface"],
        z=z,
        name="Road_Surface",
        visual_color=road_color,
    )
    for i, layer in enumerate(layers):
        z = layer["elevation"] + rule.road_z

    sidewalk_mesh = polygon_to_top_mesh(
        geoms["sidewalk"],
        z=z + rule.curb_height,
        name="Sidewalk",
        visual_color=sidewalk_color,
    )
        # 分层级生成路面、人行道、路缘石和标线的独立 Mesh 对象
        road_mesh = polygon_to_top_mesh(
            layer["road_surface"],
            z=z,
            name=f"Road_Surface_{i}",
            visual_color=road_color,
        )

    curb_mesh = polygon_to_extruded_mesh(
        geoms["curb"],
        z_bottom=z,
        z_top=z + rule.curb_height,
        name="Curb",
        visual_color=curb_color,
    )
        sidewalk_mesh = polygon_to_top_mesh(
            layer["sidewalk"],
            z=z + rule.curb_height,
            name=f"Sidewalk_{i}",
            visual_color=sidewalk_color,
        )

    lane_mesh = polygon_to_top_mesh(
        geoms["lane_marking"],
        z=z + rule.lane_marking_z_offset,
        name="Lane_Marking",
        visual_color=marking_color,
    )
        curb_mesh = polygon_to_extruded_mesh(
            layer["curb"],
            z_bottom=z,
            z_top=z + rule.curb_height,
            name=f"Curb_{i}",
            visual_color=curb_color,
        )

    meshes = {
        "Road_Surface": road_mesh,
        "Sidewalk": sidewalk_mesh,
        "Curb": curb_mesh,
        "Lane_Marking": lane_mesh,
    }
        lane_mesh = polygon_to_top_mesh(
            layer["lane_marking"],
            z=z + rule.lane_marking_z_offset,
            name=f"Lane_Marking_{i}",
            visual_color=marking_color,
        )

    # 过滤掉空数据的网格，将有效模型添加到场景容器中
    scene = trimesh.Scene()
    for name, mesh in meshes.items():
        if len(mesh.vertices) == 0:
            continue
        scene.add_geometry(mesh, node_name=name, geom_name=name)
        layer_meshes = {
            f"Road_Surface_{i}": road_mesh,
            f"Sidewalk_{i}": sidewalk_mesh,
            f"Curb_{i}": curb_mesh,
            f"Lane_Marking_{i}": lane_mesh,
        }

    return scene, meshes
        # 过滤掉空数据的网格，将有效模型添加到场景容器中
        for name, mesh in layer_meshes.items():
            if len(mesh.vertices) > 0:
                scene.add_geometry(mesh, node_name=name, geom_name=name)
                all_meshes[name] = mesh

    return scene, all_meshes


def export_scene(scene: trimesh.Scene) -> None:
    """
    将整个 trimesh 场景批量导出为 OBJ 和 GLTF(GLB) 文件。
    """
    scene.export(OBJ_PATH)
    scene.export(GLB_PATH)


def build_semantic_json(
    roads: gpd.GeoDataFrame,
    origin: LocalOrigin,
    meta: dict[str, Any],
    rules: dict[str, RoadRule],
    geoms: dict[str, Any],
    layers: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    构建语义信息 JSON，记录各对象的 ID、长面积等属性，提供外部系统引用并关联 3D 模型。
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
            "is_bridge": safe_str(row.get("is_bridge")),
            "road_ref": safe_str(row.get("road_ref")),
            "access": safe_str(row.get("access")),
            "object_type": "Source_Centerline",
            "geometry_type": row.geometry.geom_type,
            "length_m": float(row["length_m"]),
            "elevation": float(row["elevation"]),
            "rule_id": safe_str(row.get("road_class")) if safe_str(row.get("road_class")) in rules else "default_road",
        })

    merged_objects = [
        ("Road_Surface", geoms["road_surface"], default_rule.material),
        ("Sidewalk", geoms["sidewalk"], default_rule.sidewalk_material),
        ("Curb", geoms["curb"], default_rule.curb_material),
        ("Lane_Marking", geoms["lane_marking"], default_rule.marking_material),
    ]
    for i, layer in enumerate(layers):
        ele = layer["elevation"]
        merged_objects = [
            ("Road_Surface", layer["road_surface"], default_rule.material),
            ("Sidewalk", layer["sidewalk"], default_rule.sidewalk_material),
            ("Curb", layer["curb"], default_rule.curb_material),
            ("Lane_Marking", layer["lane_marking"], default_rule.marking_material),
        ]

    for object_type, geom, material in merged_objects:
        area = 0.0 if geom is None or geom.is_empty else float(geom.area)
        objects.append({
            "object_id": object_type,
            "object_type": object_type,
            "source_road_id": "merged",
            "rule_id": "default_road",
            "material": material,
            "area_m2": area,
            "mesh_files": [
                str(OBJ_PATH.relative_to(ROOT)),
                str(GLB_PATH.relative_to(ROOT)),
            ],
        })
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
    基础 3D 拓扑结构质检。检查各类组件是否为空、计算面数顶点数，并验证模型是否闭合 (水密)。
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


def geometry_qc(roads: gpd.GeoDataFrame, geoms: dict[str, Any], rule: RoadRule) -> dict[str, Any]:
def geometry_qc(roads: gpd.GeoDataFrame, layers: list[dict[str, Any]], rule: RoadRule) -> dict[str, Any]:
    """
    2D 几何与规范参数的指标质检，用以监控生成的总路线长度以及各个要素分区的面积是否异常。
    """
    road_area = 0.0 if geoms["road_surface"] is None else float(geoms["road_surface"].area)
    sidewalk_area = 0.0 if geoms["sidewalk"] is None else float(geoms["sidewalk"].area)
    curb_area = 0.0 if geoms["curb"] is None else float(geoms["curb"].area)
    marking_area = 0.0 if geoms["lane_marking"] is None else float(geoms["lane_marking"].area)
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
    geoms: dict[str, Any],
    layers: list[dict[str, Any]],
    meshes: dict[str, trimesh.Trimesh],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """
    生成并导出质检分析报告（QC Report JSON），记录执行环境与输入输出的异常情况。
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
        "geometry_check": geometry_qc(roads, geoms, rule),
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
    geoms = generate_planar_geometries(roads, rules)
    layers_geoms = generate_planar_geometries(roads, rules)

    print("3. 生成三维 Mesh 和 Scene...")
    scene, meshes = make_scene(geoms, default_rule)
    scene, meshes = make_scene(layers_geoms, default_rule)

    print("4. 导出 OBJ 和 GLB...")
    export_scene(scene)

    print("5. 生成语义 JSON...")
    build_semantic_json(roads, origin, meta, rules, geoms)
    build_semantic_json(roads, origin, meta, rules, layers_geoms)

    print("6. 生成质检报告...")
    qc = write_qc_report(roads, default_rule, geoms, meshes, meta)
    qc = write_qc_report(roads, default_rule, layers_geoms, meshes, meta)

    print("完成。输出文件：")
    print(f"- {OBJ_PATH}")
    print(f"- {GLB_PATH}")
    print(f"- {SEMANTIC_PATH}")
    print(f"- {QC_PATH}")
    print(f"- {LOCAL_ROADS_PATH}")


if __name__ == "__main__":
    main()
