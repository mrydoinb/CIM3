#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Generate a CIM city massing model from OSM-derived inputs.

Inputs:
- data/raw/road_centerline.geojson
- data/raw/building_footprint.geojson
- data/raw/transport_points.geojson
- data/raw/railway_centerline.geojson

Output:
- output/obj/cim_city.obj
- output/obj/modules/cim_city_roads.obj
- output/obj/modules/cim_city_buildings.obj
- output/obj/modules/cim_city_subway_tunnels.obj
- output/obj/modules/cim_city_subway_stations.obj
- output/obj/modules/cim_city_bus_stops.obj
- output/obj/modules/cim_city_utility_pipes.obj
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import math
import re
from typing import Iterable, Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, MultiLineString, Point, MultiPoint, Polygon, MultiPolygon, GeometryCollection
from shapely.ops import substring, unary_union
import trimesh

from road import generator as road_gen


ROOT = Path(__file__).resolve().parents[2]
road_gen.SWEEP_SAMPLE_INTERVAL_M = 20.0

RAW_DIR = road_gen.RAW_SOURCE_DIR
OUT_PATH = ROOT / "output" / "obj" / "cim_city.obj"
MODULE_OBJ_DIR = ROOT / "output" / "obj" / "modules"
MODULE_OBJ_PATHS = {
    "roads": MODULE_OBJ_DIR / "cim_city_roads.obj",
    "buildings": MODULE_OBJ_DIR / "cim_city_buildings.obj",
    "subway_tunnels": MODULE_OBJ_DIR / "cim_city_subway_tunnels.obj",
    "subway_stations": MODULE_OBJ_DIR / "cim_city_subway_stations.obj",
    "bus_stops": MODULE_OBJ_DIR / "cim_city_bus_stops.obj",
    "utility_pipes": MODULE_OBJ_DIR / "cim_city_utility_pipes.obj",
}
CITY_ROAD_SEMANTIC_PATH = ROOT / "output" / "semantic" / "cim_city_roads_semantic.json"
CITY_JUNCTION_SEMANTIC_PATH = ROOT / "output" / "semantic" / "cim_city_junctions_semantic.json"
CITY_UTILITY_SEMANTIC_PATH = ROOT / "output" / "semantic" / "cim_city_utility_pipes_semantic.json"
CITY_ROAD_SCORE_PATH = ROOT / "output" / "qc_report" / "cim_city_roads_model_score.json"
CITY_JUNCTION_SCORE_PATH = ROOT / "output" / "qc_report" / "cim_city_junction_score.json"
CITY_MARKING_QC_PATH = ROOT / "output" / "qc_report" / "cim_city_marking_alignment_qc.json"
CITY_UTILITY_QC_PATH = ROOT / "output" / "qc_report" / "cim_city_utility_pipe_qc.json"
TARGET_CRS = road_gen.TARGET_CRS
SOURCE_PROJECTED_CRS = "EPSG:4547"


def first_existing_path(patterns: list[str], base_dir: Path = RAW_DIR) -> Path | None:
    for pattern in patterns:
        matches = sorted(base_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


BUILDINGS_PATH = first_existing_path(["building_footprint.geojson", "**/building_footprint.geojson"])
BUS_STOPS_PATH = first_existing_path(["**/公交站*.shp"])
RAIL_LINES_PATH = first_existing_path(["**/轨道线2000.shp", "**/*线2000.shp"])
RAIL_STATIONS_PATH = first_existing_path(["**/轨道站点2000.shp", "**/*站点2000.shp"])
WATER_LINES_PATH = first_existing_path(["**/供水管线.shp"])
SEWER_LINES_PATH = first_existing_path(["**/污水管线.shp"])
GAS_LINES_PATH = first_existing_path(["**/rq*.shp"])

BUILDING_DEFAULT_HEIGHT_M = 12.0
BUILDING_LEVEL_HEIGHT_M = 3.2
SUBWAY_TUNNEL_RADIUS_M = 2.6
SUBWAY_TUNNEL_DEPTH_M = -14.0
SUBWAY_STATION_DEPTH_M = -11.0
SUBWAY_STATION_SIZE_M = (34.0, 16.0, 7.0)
GENERATE_ROAD_ASSETS = True
GENERATE_SUBWAY_TUNNELS = True
GENERATE_UTILITY_PIPES = True
ENABLE_TRANSITION_CURVES = True
ENABLE_ROUNDED_JUNCTION_SURFACES = True
GENERATE_JUNCTION_CROSSWALKS = True
GENERATE_JUNCTION_STOP_LINES = True
JUNCTION_MARKING_CLEARANCE_M = 11.0
JUNCTION_SURFACE_Z_OFFSET_M = 0.026
JUNCTION_PATCH_SMOOTH_M = 2.2
JUNCTION_PATCH_MIN_THROAT_M = 8.0
JUNCTION_PATCH_MAX_THROAT_M = 38.0
JUNCTION_BUCKET_CLUSTER_M = 16.0
JUNCTION_APPROACH_BLEND_OVERLAP_M = 0.25
JUNCTION_DRIVABLE_BLEND_OVERLAP_M = 0.65
JUNCTION_DRIVABLE_CORE_CLIP_INSET_M = 0.72
JUNCTION_ROADSIDE_RETREAT_M = 0.75
JUNCTION_MARKING_RETREAT_M = 0.35
JUNCTION_APPROACH_MARKING_MAX_OFFSET_M = 52.0
JUNCTION_MARKING_SURFACE_CLEARANCE_M = 2.6
JUNCTION_DESIGN_SCORE_THRESHOLD = 72.0
CROSSWALK_BAND_LENGTH_M = 4.0
CROSSWALK_STRIPE_WIDTH_M = 0.45
CROSSWALK_STRIPE_GAP_M = 0.60
STOP_LINE_WIDTH_M = 0.45
STOP_LINE_TO_CROSSWALK_GAP_M = 3.0
JUNCTION_SEMANTIC_SAMPLE_DISTANCE_M = 28.0
JUNCTION_MARKING_LATERAL_INSET_M = 0.30
MARKING_SWEEP_SAMPLE_INTERVAL_M = 8.0

UTILITY_PIPE_STANDARD_REFERENCES = {
    "GB 50289-2016": "城市工程管线综合规划规范，用于管线综合、覆土和交叉避让控制。",
    "GB 50013-2018": "室外给水设计标准，用于给水管网设计语义和管径合理性控制。",
    "GB 50014-2021": "室外排水设计标准，用于污水管网设计语义、最小管径和重力管线控制。",
    "GB 50268-2008": "给水排水管道工程施工及验收规范，用于施工验收和回填语义控制。",
}

UTILITY_PIPE_SPECS: dict[str, dict[str, Any]] = {
    "Water": {
        "label_zh": "供水",
        "default_dn_mm": 400,
        "min_dn_mm": 100,
        "cover_depth_m": 1.0,
        "min_cover_depth_m": 0.7,
        "synthetic_lateral_offset_m": -1.4,
        "material_class": "ductile_iron_or_pe_pressure_pipe",
        "flow_model": "pressure",
        "color": [40, 120, 230, 255],
        "mesh_sections": 12,
        "standards": ["GB 50289-2016", "GB 50013-2018", "GB 50268-2008"],
    },
    "Sewer": {
        "label_zh": "污水",
        "default_dn_mm": 600,
        "min_dn_mm": 300,
        "cover_depth_m": 1.8,
        "min_cover_depth_m": 0.7,
        "synthetic_lateral_offset_m": 0.0,
        "material_class": "reinforced_concrete_or_hdpe_gravity_pipe",
        "flow_model": "gravity",
        "color": [120, 90, 55, 255],
        "mesh_sections": 12,
        "standards": ["GB 50289-2016", "GB 50014-2021", "GB 50268-2008"],
    },
    "Gas": {
        "label_zh": "燃气",
        "default_dn_mm": 400,
        "min_dn_mm": 50,
        "cover_depth_m": 1.6,
        "min_cover_depth_m": 0.7,
        "synthetic_lateral_offset_m": 0.8,
        "material_class": "steel_or_pe_pressure_pipe",
        "flow_model": "pressure",
        "color": [230, 140, 45, 255],
        "mesh_sections": 12,
        "standards": ["GB 50289-2016"],
    },
    "Power": {
        "label_zh": "电力",
        "default_dn_mm": 320,
        "min_dn_mm": 50,
        "cover_depth_m": 1.34,
        "min_cover_depth_m": 0.7,
        "synthetic_lateral_offset_m": 1.4,
        "material_class": "power_duct_bank",
        "flow_model": "duct",
        "color": [230, 190, 40, 255],
        "mesh_sections": 10,
        "standards": ["GB 50289-2016"],
    },
    "Telecom": {
        "label_zh": "通信",
        "default_dn_mm": 200,
        "min_dn_mm": 50,
        "cover_depth_m": 1.0,
        "min_cover_depth_m": 0.7,
        "synthetic_lateral_offset_m": 2.2,
        "material_class": "telecom_duct",
        "flow_model": "duct",
        "color": [230, 80, 180, 255],
        "mesh_sections": 10,
        "standards": ["GB 50289-2016"],
    },
}

UTILITY_DIAMETER_FIELDS = (
    "管径",
    "DN",
    "D",
    "Diameter",
    "diameter",
    "DIAMETER",
    "规格",
    "Spec",
    "spec",
    "备注",
    "RefName",
    "RefName_1",
)
UTILITY_SOURCE_ATTRIBUTE_FIELDS = (
    "FID_",
    "Entity",
    "Layer",
    "Color",
    "Linetype",
    "Elevation",
    "LineWt",
    "RefName",
    "RefName_1",
    "管径",
    "备注",
)
UTILITY_DIAMETER_PATTERNS = (
    re.compile(r"\bDN\s*[-:]?\s*(\d{2,5})", re.IGNORECASE),
    re.compile(r"[ΦØ]\s*(\d{2,5})", re.IGNORECASE),
    re.compile(r"(?:管径|直径|规格)\D{0,8}(\d{2,5})", re.IGNORECASE),
    re.compile(r"\bD\s*[-:]?\s*(\d{2,5})", re.IGNORECASE),
    re.compile(r"(\d{2,5})\s*(?:mm|毫米|㎜)", re.IGNORECASE),
)

COLORS = {
    "gis_basemap": [88, 118, 94, 255],
    "gis_grid": [132, 150, 132, 255],
    "road_surface": [38, 40, 42, 255],
    "road_surface_main": [36, 38, 40, 255],
    "road_surface_service": [48, 50, 50, 255],
    "road_surface_branch": [58, 60, 58, 255],
    "non_motor_lane": [54, 72, 66, 255],
    "parking_lane": [66, 68, 66, 255],
    "sidewalk": [188, 186, 174, 255],
    "curb": [220, 216, 202, 255],
    "green_belt": [58, 116, 68, 255],
    "facility_belt": [116, 130, 108, 255],
    "side_divider": [66, 102, 62, 255],
    "median": [76, 118, 78, 255],
    "shoulder": [62, 64, 62, 255],
    "lane_marking": [242, 242, 226, 255],
    "center_marking": [244, 196, 48, 255],
    "crosswalk": [245, 245, 235, 255],
    "stop_line": [245, 245, 235, 255],
    "building": [185, 185, 178, 255],
    "subway_tunnel": [95, 95, 105, 255],
    "subway_station": [70, 95, 150, 255],
    "bus_stop": [40, 170, 95, 255],
}


def safe_float(value: Any, default: float) -> float:
    if value is None or pd.isna(value):
        return default
    text = str(value).strip().lower().replace("m", "")
    try:
        return float(text)
    except ValueError:
        return default


def load_layer(path: Path | None) -> gpd.GeoDataFrame:
    if path is None or not path.exists():
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)

    gdf = gpd.read_file(path)
    if gdf.empty:
        return gdf
    if gdf.crs is None:
        bounds = gdf.total_bounds
        max_abs_coord = max(abs(float(value)) for value in bounds if np.isfinite(value))
        gdf = gdf.set_crs(SOURCE_PROJECTED_CRS if max_abs_coord > 1000.0 else "EPSG:4326")
    gdf = gdf.to_crs(TARGET_CRS)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf = gdf[gdf.geometry.is_valid].copy()
    return gdf


def combine_layers(*layers: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    non_empty = [layer for layer in layers if layer is not None and not layer.empty]
    if not non_empty:
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    return gpd.GeoDataFrame(pd.concat(non_empty, ignore_index=True), crs=TARGET_CRS)


def iter_lines(geom) -> Iterable[LineString]:
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, LineString):
        yield geom
    elif isinstance(geom, MultiLineString):
        for line in geom.geoms:
            if not line.is_empty:
                yield line
    elif isinstance(geom, GeometryCollection):
        for part in geom.geoms:
            yield from iter_lines(part)


def iter_polygons(geom) -> Iterable[Polygon]:
    yield from road_gen.iter_polygons(geom)


def representative_point(geom) -> Point | None:
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, Point):
        return geom
    if isinstance(geom, MultiPoint):
        return next(iter(geom.geoms), None)
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom.representative_point()
    if isinstance(geom, (LineString, MultiLineString)):
        return geom.interpolate(0.5, normalized=True)
    return None


def make_box(name: str, center: tuple[float, float, float], size: tuple[float, float, float], color) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=size)
    mesh.apply_translation(center)
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def cylinder_between(name: str, start, end, radius: float, color, sections: int = 16) -> trimesh.Trimesh | None:
    p0 = np.array(start, dtype=float)
    p1 = np.array(end, dtype=float)
    if np.linalg.norm(p1 - p0) <= 0.05:
        return None
    mesh = trimesh.creation.cylinder(radius=radius, sections=sections, segment=np.vstack([p0, p1]))
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def localize(gdf: gpd.GeoDataFrame, origin: tuple[float, float]) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf
    result = gdf.copy()
    result["geometry"] = result.geometry.apply(
        lambda geom: None
        if geom is None or geom.is_empty
        else shapely.affinity.translate(geom, xoff=-origin[0], yoff=-origin[1])
    )
    result = result[result.geometry.notna()].copy()
    return result


def compute_origin(*layers: gpd.GeoDataFrame) -> tuple[float, float]:
    bounds = [layer.total_bounds for layer in layers if not layer.empty]
    if not bounds:
        return (0.0, 0.0)
    stacked = np.array(bounds)
    minx = float(stacked[:, 0].min())
    miny = float(stacked[:, 1].min())
    maxx = float(stacked[:, 2].max())
    maxy = float(stacked[:, 3].max())
    return ((minx + maxx) / 2.0, (miny + maxy) / 2.0)


def localized_bounds(*layers: gpd.GeoDataFrame) -> tuple[float, float, float, float] | None:
    bounds = [layer.total_bounds for layer in layers if not layer.empty]
    if not bounds:
        return None
    stacked = np.array(bounds)
    return (
        float(stacked[:, 0].min()),
        float(stacked[:, 1].min()),
        float(stacked[:, 2].max()),
        float(stacked[:, 3].max()),
    )


def build_gis_basemap_meshes(*layers: gpd.GeoDataFrame) -> dict[str, trimesh.Trimesh]:
    bounds = localized_bounds(*layers)
    center = None
    if bounds is not None:
        center = road_gen.projected_xy_to_latlon((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
    return road_gen.make_gis_basemap_meshes(
        bounds,
        z=-0.08,
        padding=80.0,
        grid_spacing=100.0,
        google_center_latlon=center,
    )


def building_height(row) -> float:
    height = safe_float(row.get("height"), 0.0)
    if height > 0:
        return height
    levels = safe_float(row.get("building:levels"), 0.0)
    if levels > 0:
        return max(levels * BUILDING_LEVEL_HEIGHT_M, BUILDING_LEVEL_HEIGHT_M)
    return BUILDING_DEFAULT_HEIGHT_M


def prepare_roads_for_surfaces(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if roads.empty:
        return roads

    required_columns = {
        "road_id",
        "road_name",
        "road_class",
        "lane_count",
        "osm_width",
        "transition_curve_count",
        "junction_distances_json",
        "length_m",
    }
    if required_columns.issubset(set(roads.columns)):
        return roads.copy()

    prepared = roads.copy()
    prepared = prepared[prepared.geometry.notna()].copy()
    prepared = prepared[prepared.geometry.geom_type.isin(["LineString", "MultiLineString"])].copy()
    prepared = prepared.explode(index_parts=False).reset_index(drop=True)
    prepared = prepared[prepared.geometry.geom_type == "LineString"].copy()
    prepared = road_gen.deduplicate_bidirectional_osm_edges(prepared)

    if "osmid" in prepared.columns:
        prepared["road_id"] = prepared["osmid"].apply(road_gen.normalize_osmid)
    elif "id" in prepared.columns:
        prepared["road_id"] = prepared["id"].astype(str)
    elif "Id" in prepared.columns:
        prepared["road_id"] = prepared["Id"].astype(str)
    else:
        prepared["road_id"] = [f"R{i:05d}" for i in range(len(prepared))]
    prepared["road_id"] = road_gen.make_unique_road_ids(prepared["road_id"])

    prepared["road_name"] = prepared.apply(road_gen.source_road_name, axis=1)
    prepared["road_class"] = prepared.apply(road_gen.source_road_class, axis=1)
    prepared["lane_count"] = prepared["lanes"] if "lanes" in prepared.columns else None
    prepared["lanes_forward"] = prepared["lanes:forward"] if "lanes:forward" in prepared.columns else None
    prepared["lanes_backward"] = prepared["lanes:backward"] if "lanes:backward" in prepared.columns else None
    prepared["osm_width"] = prepared["width"] if "width" in prepared.columns else None
    prepared["maxspeed"] = prepared["maxspeed"] if "maxspeed" in prepared.columns else None
    prepared["oneway"] = prepared["oneway"] if "oneway" in prepared.columns else None
    prepared["junction_type"] = prepared["junction"] if "junction" in prepared.columns else None
    prepared["road_ref"] = prepared["ref"] if "ref" in prepared.columns else None
    prepared["access"] = prepared["access"] if "access" in prepared.columns else None
    prepared["lane_count"] = road_gen.normalize_corridor_lane_counts(prepared)
    prepared["is_bridge"] = prepared["bridge"].apply(road_gen.check_is_bridge) if "bridge" in prepared.columns else False
    prepared["bridge_clearance"] = prepared["road_class"].apply(road_gen.get_bridge_clearance)
    prepared["ground_z_start"] = 0.0
    prepared["ground_z_end"] = 0.0
    prepared["road_z_start"] = prepared["bridge_clearance"].where(prepared["is_bridge"], 0.0)
    prepared["road_z_end"] = prepared["road_z_start"]
    prepared["road_z_mean"] = prepared["road_z_start"]
    prepared["elevation"] = prepared["road_z_mean"]
    prepared["length_m"] = prepared.geometry.length
    prepared["height_mode"] = "city_surface"
    prepared["height_source"] = "osm_layer_rule"
    prepared["transition_curve_count"] = prepared.geometry.apply(road_gen.count_transition_curve_candidates)
    if ENABLE_TRANSITION_CURVES:
        prepared["geometry"] = prepared.geometry.apply(road_gen.smooth_line_with_clothoid_transitions)
    prepared["length_m"] = prepared.geometry.length
    road_gen.attach_junction_distances(prepared)
    return prepared


def roads_are_prepared_for_surfaces(roads: gpd.GeoDataFrame) -> bool:
    required_columns = {
        "road_id",
        "road_class",
        "lane_count",
        "length_m",
        "road_z_mean",
        "transition_curve_count",
        "junction_distances_json",
    }
    return required_columns <= set(roads.columns)


def swept_band_polygon(line: LineString, left_offset: float, right_offset: float):
    if right_offset <= left_offset or line is None or line.is_empty or line.length <= 0.05:
        return None
    distances = road_gen.sample_line_for_sweep(line)
    if len(distances) < 2:
        return None

    left_points = []
    right_points = []
    for distance in distances:
        point, _, normal = road_gen.line_frame_at_distance(line, distance)
        nx, ny = normal
        left_points.append((point.x + nx * left_offset, point.y + ny * left_offset))
        right_points.append((point.x + nx * right_offset, point.y + ny * right_offset))

    try:
        return road_gen.clean_polygonal(Polygon(left_points + list(reversed(right_points))))
    except Exception:
        return None


def local_segment_clip_mask(
    line: LineString,
    left_offset: float,
    right_offset: float,
    clip_mask,
    margin: float = 0.35,
):
    if clip_mask is None or clip_mask.is_empty or line is None or line.is_empty:
        return None
    radius = max(abs(float(left_offset)), abs(float(right_offset))) + max(float(margin), 0.0)
    try:
        envelope = line.buffer(max(radius, 0.1), cap_style=1, join_style=1, resolution=3)
    except Exception:
        return clip_mask
    if envelope is None or envelope.is_empty or not clip_mask.intersects(envelope):
        return None
    try:
        local = clip_mask.intersection(envelope)
        return road_gen.clean_polygonal(local) if local is not None and not local.is_empty else None
    except Exception:
        return clip_mask


def swept_band_mesh(
    row: pd.Series,
    line: LineString,
    left_offset: float,
    right_offset: float,
    name: str,
    color: list[int],
    z_offset: float = 0.0,
    distance_offset: float = 0.0,
    clip_mask=None,
) -> trimesh.Trimesh:
    if right_offset <= left_offset:
        return road_gen.empty_mesh(name)
    local_clip = local_segment_clip_mask(line, left_offset, right_offset, clip_mask)
    if local_clip is not None and not local_clip.is_empty:
        geom = swept_band_polygon(line, left_offset, right_offset)
        if geom is None or geom.is_empty:
            return road_gen.empty_mesh(name)
        try:
            geom = road_gen.clean_polygonal(geom.difference(local_clip))
        except Exception:
            geom = None
        if geom is None or geom.is_empty:
            return road_gen.empty_mesh(name)
        z = road_gen.elevation_at_distance(
            row,
            min(float(row.get("length_m", distance_offset + line.length)), distance_offset + line.length * 0.5),
            default_z=float(row.get("road_z_mean", row.get("elevation", 0.0))),
        )
        return road_gen.polygon_to_top_mesh(geom, z + z_offset, name, visual_color=color)

    distances = road_gen.sample_line_for_sweep(line)
    if len(distances) < 2:
        return road_gen.empty_mesh(name)

    vertices = []
    for distance in distances:
        point, _, normal = road_gen.line_frame_at_distance(line, distance)
        center_z = road_gen.elevation_at_distance(
            row,
            distance + distance_offset,
            default_z=float(row.get("road_z_mean", row.get("elevation", 0.0))),
        )
        nx, ny = normal
        for offset in (left_offset, right_offset):
            vertices.append((point.x + nx * offset, point.y + ny * offset, center_z + z_offset))

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
    mesh.visual.face_colors = color
    return mesh


def road_section_category(row: pd.Series) -> str:
    return road_gen.road_asset_category(row)


def walkway_width_for_section(row: pd.Series, side_reserve_width: float) -> float:
    if side_reserve_width <= 0.05:
        return 0.0
    category = road_section_category(row)
    preferred = {
        "expressway": 4.0,
        "arterial": 4.5,
        "secondary": 3.5,
        "branch": 2.5,
    }.get(category, 2.5)
    return max(0.8, min(preferred, side_reserve_width))


def median_width_for_section(row: pd.Series, rule: Any) -> float:
    category = road_section_category(row)
    if rule.lane_count < 4:
        return 0.0
    preferred = {
        "expressway": 3.2,
        "arterial": 2.4,
        "secondary": 1.4,
        "branch": 0.0,
    }.get(category, 0.0)
    return min(preferred, max(0.0, rule.road_width * 0.16))


def subtract_blocked_spans(
    spans: list[tuple[float, float]],
    blocked_distances: list[float],
    clearance: float,
    line_length: float,
) -> list[tuple[float, float]]:
    if not blocked_distances or clearance <= 0.0:
        return spans

    blocked = [
        (max(0.0, float(distance) - clearance), min(float(line_length), float(distance) + clearance))
        for distance in blocked_distances
    ]
    blocked = [(start, end) for start, end in blocked if end > start]
    if not blocked:
        return spans
    blocked.sort()

    result: list[tuple[float, float]] = []
    for start, end in spans:
        pieces = [(float(start), float(end))]
        for block_start, block_end in blocked:
            next_pieces: list[tuple[float, float]] = []
            for piece_start, piece_end in pieces:
                if block_end <= piece_start or block_start >= piece_end:
                    next_pieces.append((piece_start, piece_end))
                    continue
                if block_start - piece_start > 0.05:
                    next_pieces.append((piece_start, block_start))
                if piece_end - block_end > 0.05:
                    next_pieces.append((block_end, piece_end))
            pieces = next_pieces
            if not pieces:
                break
        result.extend((piece_start, piece_end) for piece_start, piece_end in pieces if piece_end - piece_start > 0.05)
    return result


def local_blocked_distances(
    blocked_distances: list[float] | None,
    distance_offset: float,
    line_length: float,
    clearance: float,
) -> list[float]:
    if not blocked_distances:
        return []
    local: list[float] = []
    for distance in blocked_distances:
        local_distance = float(distance) - float(distance_offset)
        if -clearance <= local_distance <= float(line_length) + clearance:
            local.append(local_distance)
    return local


def junction_marking_clearance_for_row(row: pd.Series, rule: Any) -> float:
    total_width = road_gen.component_total_width(road_gen.cross_section_components_for_row(row))
    width = max(float(rule.road_width), total_width * 0.35)
    return max(JUNCTION_MARKING_CLEARANCE_M, min(26.0, width * 0.32))


def marking_span_distances(start: float, end: float) -> list[float]:
    start = float(start)
    end = float(end)
    if end <= start:
        return []
    interval = max(float(MARKING_SWEEP_SAMPLE_INTERVAL_M), 0.5)
    distances = [start]
    distance = math.floor(start / interval) * interval + interval
    while distance < end - 1e-6:
        if distance > start + 1e-6:
            distances.append(distance)
        distance += interval
    if end - distances[-1] > 1e-6:
        distances.append(end)
    return distances


def strip_mesh(
    row: pd.Series,
    line: LineString,
    center_offset: float,
    width: float,
    name: str,
    color: list[int],
    z_offset: float = 0.0,
    dash_length: float | None = None,
    gap_length: float = 9.0,
    blocked_distances: list[float] | None = None,
    blocked_clearance: float = 0.0,
    distance_offset: float = 0.0,
    clip_mask=None,
) -> trimesh.Trimesh:
    if width <= 0.01 or line is None or line.is_empty:
        return road_gen.empty_mesh(name)

    spans: list[tuple[float, float]] = []
    if dash_length is None:
        spans.append((0.0, float(line.length)))
    else:
        period = max(dash_length + gap_length, 0.1)
        distance = gap_length * 0.5
        while distance < line.length:
            start = distance
            end = min(float(line.length), distance + dash_length)
            if end - start >= max(1.0, dash_length * 0.35):
                spans.append((start, end))
            distance += period
    local_blocked = local_blocked_distances(blocked_distances, distance_offset, float(line.length), blocked_clearance)
    spans = subtract_blocked_spans(spans, local_blocked, blocked_clearance, float(line.length))

    half = width / 2.0
    if clip_mask is not None and not clip_mask.is_empty:
        span_meshes = []
        for span_idx, (start, end) in enumerate(spans):
            if end <= start:
                continue
            try:
                part = substring(line, start, end)
            except Exception:
                continue
            if part is None or part.is_empty or not isinstance(part, LineString):
                continue
            geom = swept_band_polygon(part, center_offset - half, center_offset + half)
            if geom is None or geom.is_empty:
                continue
            try:
                geom = road_gen.clean_polygonal(geom.difference(clip_mask))
            except Exception:
                geom = None
            if geom is None or geom.is_empty:
                continue
            z_distance = distance_offset + (start + end) * 0.5
            center_z = road_gen.elevation_at_distance(
                row,
                z_distance,
                default_z=float(row.get("road_z_mean", row.get("elevation", 0.0))),
            )
            mesh = road_gen.polygon_to_top_mesh(
                geom,
                center_z + z_offset,
                f"{name}_{span_idx}",
                visual_color=color,
            )
            if len(mesh.vertices) > 0:
                span_meshes.append(mesh)
        return road_gen.merge_named_meshes(name, span_meshes, color)

    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for start, end in spans:
        if end <= start:
            continue
        base_index = len(vertices)
        distances = marking_span_distances(start, end)
        if len(distances) < 2:
            continue
        for distance in distances:
            point, _, normal = road_gen.line_frame_at_distance(line, distance)
            center_z = road_gen.elevation_at_distance(
                row,
                distance + distance_offset,
                default_z=float(row.get("road_z_mean", row.get("elevation", 0.0))),
            )
            nx, ny = normal
            for edge_offset in (center_offset - half, center_offset + half):
                vertices.append((point.x + nx * edge_offset, point.y + ny * edge_offset, center_z + z_offset))
        for segment_idx in range(len(distances) - 1):
            a = base_index + segment_idx * 2
            b = a + 1
            c = a + 3
            d = a + 2
            faces.append((a, b, c))
            faces.append((a, c, d))

    if not vertices:
        return road_gen.empty_mesh(name)
    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=False)
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


COMPONENT_LAYER_NAMES = {
    "main_carriageway": "Road_Surface_Main",
    "carriageway": "Road_Surface_Main",
    "service_lane": "Road_Surface_Service",
    "non_motor_lane": "Non_Motor_Lane",
    "parking_lane": "Parking_Lane",
    "sidewalk": "Sidewalk",
    "green_belt": "Green_Belt",
    "facility_belt": "Facility_Belt",
    "side_divider": "Side_Divider",
    "divider": "Side_Divider",
    "median": "Median",
}

COMPONENT_COLORS = {
    "Road_Surface_Main": COLORS["road_surface_main"],
    "Road_Surface_Service": COLORS["road_surface_service"],
    "Road_Surface_Branch": COLORS["road_surface_branch"],
    "Non_Motor_Lane": COLORS["non_motor_lane"],
    "Parking_Lane": COLORS["parking_lane"],
    "Sidewalk": COLORS["sidewalk"],
    "Green_Belt": COLORS["green_belt"],
    "Facility_Belt": COLORS["facility_belt"],
    "Side_Divider": COLORS["side_divider"],
    "Median": COLORS["median"],
}

DRIVABLE_COMPONENT_TYPES = {"main_carriageway", "carriageway", "service_lane", "non_motor_lane", "parking_lane"}
LANE_MARKING_COMPONENT_TYPES = {"main_carriageway", "carriageway", "service_lane"}
RAISED_COMPONENT_TYPES = {"sidewalk", "green_belt", "facility_belt", "side_divider", "divider", "median"}
OPPOSING_CARRIAGEWAY_TYPES = {"main_carriageway", "carriageway"}


def component_layer_name(component_type: str) -> str:
    return COMPONENT_LAYER_NAMES.get(component_type, "Green_Belt")


def component_layer_name_for_row(component_type: str, row: pd.Series) -> str:
    layer_name = component_layer_name(component_type)
    if layer_name != "Road_Surface_Main":
        return layer_name

    section_code = road_gen.row_section_code(row)
    road_class = road_gen.safe_str(row.get("road_class")) or ""
    category = road_gen.road_class_category_name(road_class)
    if category == "branch" or (section_code and section_code.startswith("D")):
        return "Road_Surface_Branch"
    return layer_name


def component_z_offset(component_type: str, rule: Any) -> float:
    if component_type == "sidewalk":
        return max(0.035, rule.curb_height * 0.65)
    if component_type in {"green_belt", "facility_belt", "side_divider", "divider", "median"}:
        return max(0.04, rule.curb_height * 0.45)
    if component_type == "parking_lane":
        return 0.006
    if component_type == "non_motor_lane":
        return 0.004
    return 0.0


def fallback_cross_section_components(rule: Any) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    if rule.sidewalk_width > 0.05:
        components.append({"type": "sidewalk", "width": rule.sidewalk_width})
    components.append({"type": "main_carriageway", "width": rule.road_width})
    if rule.sidewalk_width > 0.05:
        components.append({"type": "sidewalk", "width": rule.sidewalk_width})
    return components


def component_spans(components: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float, float]]:
    total_width = road_gen.component_total_width(components)
    cursor = -total_width / 2.0
    spans: list[tuple[dict[str, Any], float, float]] = []
    for component in components:
        width = float(component.get("width", 0.0) or 0.0)
        if width <= 0.01:
            continue
        left = cursor
        right = cursor + width
        spans.append((component, left, right))
        cursor = right
    return spans


def carriageway_boundary_edge_suppression(
    spans: list[tuple[dict[str, Any], float, float]],
) -> dict[int, set[str]]:
    suppression: dict[int, set[str]] = {}
    for idx in range(len(spans) - 1):
        left_component, _, boundary = spans[idx]
        right_component, next_left, _ = spans[idx + 1]
        if abs(boundary - next_left) > 0.01:
            continue
        if str(left_component.get("type", "")) not in OPPOSING_CARRIAGEWAY_TYPES:
            continue
        if str(right_component.get("type", "")) not in OPPOSING_CARRIAGEWAY_TYPES:
            continue
        suppression.setdefault(idx, set()).add("right")
        suppression.setdefault(idx + 1, set()).add("left")
    return suppression


def add_component_lane_markings(
    row: pd.Series,
    line: LineString,
    rule: Any,
    component: dict[str, Any],
    left_offset: float,
    right_offset: float,
    white_marking_meshes: list[trimesh.Trimesh],
    suppress_left_edge: bool = False,
    suppress_right_edge: bool = False,
    distance_offset: float = 0.0,
    clip_mask=None,
) -> None:
    component_type = str(component.get("type", ""))
    if component_type not in LANE_MARKING_COMPONENT_TYPES:
        return
    width = right_offset - left_offset
    if width < 3.2:
        return

    edge_width = max(0.10, min(rule.lane_marking_width, 0.16))
    junction_distances = road_gen.row_junction_distances(row)
    junction_clearance = junction_marking_clearance_for_row(row, rule)
    edge_specs = []
    if not suppress_left_edge:
        edge_specs.append((left_offset + min(0.35, width * 0.15), "Left_Edge"))
    if not suppress_right_edge:
        edge_specs.append((right_offset - min(0.35, width * 0.15), "Right_Edge"))
    for edge_offset, suffix in edge_specs:
        edge_mesh = strip_mesh(
            row,
            line,
            edge_offset,
            edge_width,
            f"Lane_Marking_White_{suffix}_{row.name}_{round(edge_offset, 2)}",
            COLORS["lane_marking"],
            z_offset=rule.lane_marking_z_offset + 0.008,
            blocked_distances=junction_distances,
            blocked_clearance=junction_clearance,
            distance_offset=distance_offset,
            clip_mask=clip_mask,
        )
        if len(edge_mesh.vertices) > 0:
            white_marking_meshes.append(edge_mesh)

    lane_count = max(1, int(round(width / max(rule.lane_width, 2.8))))
    if lane_count <= 1:
        return
    lane_step = width / lane_count
    for lane_idx in range(1, lane_count):
        offset = left_offset + lane_step * lane_idx
        marking_mesh = strip_mesh(
            row,
            line,
            offset,
            edge_width,
            f"Lane_Marking_White_{row.name}_{lane_idx}_{round(offset, 2)}",
            COLORS["lane_marking"],
            z_offset=rule.lane_marking_z_offset + 0.012,
            dash_length=6.0,
            gap_length=10.0,
            blocked_distances=junction_distances,
            blocked_clearance=junction_clearance,
            distance_offset=distance_offset,
            clip_mask=clip_mask,
        )
        if len(marking_mesh.vertices) > 0:
            white_marking_meshes.append(marking_mesh)


def add_center_markings_for_adjacent_carriageways(
    row: pd.Series,
    line: LineString,
    rule: Any,
    spans: list[tuple[dict[str, Any], float, float]],
    yellow_marking_meshes: list[trimesh.Trimesh],
    distance_offset: float = 0.0,
    clip_mask=None,
) -> None:
    junction_distances = road_gen.row_junction_distances(row)
    junction_clearance = junction_marking_clearance_for_row(row, rule)
    for idx in range(len(spans) - 1):
        left_component, _, boundary = spans[idx]
        right_component, next_left, _ = spans[idx + 1]
        if abs(boundary - next_left) > 0.01:
            continue
        if left_component.get("type") not in {"main_carriageway", "carriageway"}:
            continue
        if right_component.get("type") not in {"main_carriageway", "carriageway"}:
            continue
        for offset in (-0.16, 0.16):
            mesh = strip_mesh(
                row,
                line,
                boundary + offset,
                max(0.09, min(rule.lane_marking_width, 0.14)),
                f"Lane_Marking_Yellow_{row.name}_{round(boundary + offset, 2)}",
                COLORS["center_marking"],
                z_offset=rule.lane_marking_z_offset + 0.014,
                blocked_distances=junction_distances,
                blocked_clearance=junction_clearance,
                distance_offset=distance_offset,
                clip_mask=clip_mask,
            )
            if len(mesh.vertices) > 0:
                yellow_marking_meshes.append(mesh)


def add_component_curbs(
    row: pd.Series,
    line: LineString,
    rule: Any,
    spans: list[tuple[dict[str, Any], float, float]],
    curb_meshes: list[trimesh.Trimesh],
    distance_offset: float = 0.0,
    clip_mask=None,
) -> None:
    curb_width = max(0.18, min(float(rule.curb_width), 0.45))
    for idx in range(len(spans) - 1):
        left_component, _, boundary = spans[idx]
        right_component, next_left, _ = spans[idx + 1]
        if abs(boundary - next_left) > 0.01:
            continue
        left_type = str(left_component.get("type", ""))
        right_type = str(right_component.get("type", ""))
        if not (
            (left_type in DRIVABLE_COMPONENT_TYPES and right_type in RAISED_COMPONENT_TYPES)
            or (right_type in DRIVABLE_COMPONENT_TYPES and left_type in RAISED_COMPONENT_TYPES)
        ):
            continue
        curb_mesh = swept_band_mesh(
            row,
            line,
            boundary - curb_width / 2.0,
            boundary + curb_width / 2.0,
            f"Curb_{row.name}_{idx}",
            COLORS["curb"],
            z_offset=max(0.055, rule.curb_height * 0.45),
            distance_offset=distance_offset,
            clip_mask=clip_mask,
        )
        if len(curb_mesh.vertices) > 0:
            curb_meshes.append(curb_mesh)


def drivable_width_for_row(row: pd.Series, rule: Any) -> float:
    components = road_gen.cross_section_components_for_row(row)
    drivable_width = road_gen.component_width_by_type(components, DRIVABLE_COMPONENT_TYPES)
    return max(float(rule.road_width), drivable_width)


def junction_surface_throat_distance_for_row(row: pd.Series, rule: Any) -> float:
    width = drivable_width_for_row(row, rule)
    return max(
        JUNCTION_PATCH_MIN_THROAT_M,
        min(JUNCTION_PATCH_MAX_THROAT_M, width * 0.75 + 6.0),
    )


def junction_approach_offsets_for_row(row: pd.Series, rule: Any) -> tuple[float, float]:
    clearance = junction_marking_clearance_for_row(row, rule)
    throat_distance = junction_surface_throat_distance_for_row(row, rule)
    crosswalk_offset = max(
        6.0,
        min(
            JUNCTION_APPROACH_MARKING_MAX_OFFSET_M,
            max(clearance + CROSSWALK_BAND_LENGTH_M * 0.5, throat_distance + CROSSWALK_BAND_LENGTH_M * 0.65),
        ),
    )
    stop_line_offset = crosswalk_offset + STOP_LINE_TO_CROSSWALK_GAP_M + STOP_LINE_WIDTH_M * 0.5
    return crosswalk_offset, stop_line_offset


def junction_marking_across_width(road_width: float) -> float:
    return max(0.2, float(road_width) - 2.0 * JUNCTION_MARKING_LATERAL_INSET_M)


def crosswalk_stripe_layout_for_width(road_width: float) -> tuple[int, float, float]:
    across_width = junction_marking_across_width(road_width)
    stripe_step = max(CROSSWALK_STRIPE_WIDTH_M + CROSSWALK_STRIPE_GAP_M, 0.1)
    if across_width <= CROSSWALK_STRIPE_WIDTH_M:
        stripe_count = 1
    else:
        stripe_count = max(1, int(math.floor((across_width - CROSSWALK_STRIPE_WIDTH_M) / stripe_step)) + 1)
    lateral_start = -((stripe_count - 1) * stripe_step) / 2.0
    return stripe_count, lateral_start, stripe_step


def oriented_rect_mesh(
    center: tuple[float, float],
    tangent: tuple[float, float],
    normal: tuple[float, float],
    along_length: float,
    across_width: float,
    z: float,
    name: str,
    color: list[int],
) -> trimesh.Trimesh:
    hx = max(float(along_length), 0.05) / 2.0
    hy = max(float(across_width), 0.05) / 2.0
    tx, ty = tangent
    nx, ny = normal
    vertices = np.array(
        [
            (center[0] - tx * hx - nx * hy, center[1] - ty * hx - ny * hy, z),
            (center[0] + tx * hx - nx * hy, center[1] + ty * hx - ny * hy, z),
            (center[0] + tx * hx + nx * hy, center[1] + ty * hx + ny * hy, z),
            (center[0] - tx * hx + nx * hy, center[1] - ty * hx + ny * hy, z),
        ]
    )
    mesh = trimesh.Trimesh(vertices=vertices, faces=np.array([(0, 1, 2), (0, 2, 3)]), process=False)
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def add_junction_crosswalks_and_stop_lines(
    row: pd.Series,
    line: LineString,
    rule: Any,
    crosswalk_meshes: list[trimesh.Trimesh],
    stop_line_meshes: list[trimesh.Trimesh],
) -> Counter:
    stats: Counter[str] = Counter()
    if line is None or line.is_empty or line.length <= 1.0:
        return stats

    road_width = drivable_width_for_row(row, rule)
    if road_width <= 2.0:
        return stats

    crosswalk_offset, stop_line_offset = junction_approach_offsets_for_row(row, rule)
    min_margin = max(CROSSWALK_BAND_LENGTH_M * 0.5, STOP_LINE_WIDTH_M * 0.5, 0.4)
    marking_across_width = junction_marking_across_width(road_width)
    stripe_count, lateral_start, stripe_step = crosswalk_stripe_layout_for_width(road_width)
    default_z = float(row.get("road_z_mean", row.get("elevation", 0.0)))

    for junction_idx, junction_distance in enumerate(road_gen.row_junction_distances(row)):
        if junction_distance < 0.0 or junction_distance > line.length:
            continue
        for side_sign, side_name in [(-1.0, "Before"), (1.0, "After")]:
            crosswalk_distance = float(junction_distance) + side_sign * crosswalk_offset
            stop_line_distance = float(junction_distance) + side_sign * stop_line_offset
            if not (
                min_margin < crosswalk_distance < line.length - min_margin
                and min_margin < stop_line_distance < line.length - min_margin
            ):
                continue

            stats["candidate_approach_count"] += 1

            point, tangent, normal = road_gen.line_frame_at_distance(line, crosswalk_distance)
            crosswalk_z = road_gen.elevation_at_distance(row, crosswalk_distance, default_z=default_z)
            for stripe_idx in range(stripe_count):
                lateral_offset = lateral_start + stripe_idx * stripe_step
                center = (
                    point.x + normal[0] * lateral_offset,
                    point.y + normal[1] * lateral_offset,
                )
                if not GENERATE_JUNCTION_CROSSWALKS:
                    continue
                mesh = oriented_rect_mesh(
                    center,
                    tangent,
                    normal,
                    CROSSWALK_BAND_LENGTH_M,
                    CROSSWALK_STRIPE_WIDTH_M,
                    crosswalk_z + rule.lane_marking_z_offset + 0.026,
                    f"Crosswalk_{row.name}_{junction_idx}_{side_name}_{stripe_idx}",
                    COLORS["crosswalk"],
                )
                if len(mesh.vertices) > 0:
                    crosswalk_meshes.append(mesh)
                    stats["crosswalk_stripe_count"] += 1

            if not GENERATE_JUNCTION_STOP_LINES:
                continue
            stop_point, stop_tangent, stop_normal = road_gen.line_frame_at_distance(line, stop_line_distance)
            stop_z = road_gen.elevation_at_distance(row, stop_line_distance, default_z=default_z)
            mesh = oriented_rect_mesh(
                (stop_point.x, stop_point.y),
                stop_tangent,
                stop_normal,
                STOP_LINE_WIDTH_M,
                marking_across_width,
                stop_z + rule.lane_marking_z_offset + 0.024,
                f"Stop_Line_{row.name}_{junction_idx}_{side_name}",
                COLORS["stop_line"],
            )
            if len(mesh.vertices) > 0:
                stop_line_meshes.append(mesh)
                stats["stop_line_count"] += 1

    return stats


def junction_point_buckets(prepared_roads: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    buckets: dict[tuple[int, int], dict[str, Any]] = {}
    tolerance = max(road_gen.JUNCTION_NODE_TOLERANCE_M, 3.0)
    for road_idx, row in prepared_roads.iterrows():
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue
        for distance in road_gen.row_junction_distances(row):
            if distance < 0.0 or distance > line.length:
                continue
            point = line.interpolate(distance)
            key = (round(point.x / tolerance), round(point.y / tolerance))
            bucket = buckets.setdefault(key, {"points": [], "members": []})
            bucket["points"].append(point)
            bucket["members"].append((road_idx, float(distance)))

    result = []
    for bucket in buckets.values():
        if not bucket["points"] or len({road_idx for road_idx, _ in bucket["members"]}) < 2:
            continue
        x = sum(point.x for point in bucket["points"]) / len(bucket["points"])
        y = sum(point.y for point in bucket["points"]) / len(bucket["points"])
        result.append({"point": Point(x, y), "members": bucket["members"]})
    return cluster_nearby_junction_buckets(result)


def cluster_nearby_junction_buckets(buckets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(buckets) <= 1:
        return buckets

    cluster_distance = max(road_gen.JUNCTION_NODE_TOLERANCE_M, JUNCTION_BUCKET_CLUSTER_M)
    remaining = set(range(len(buckets)))
    clustered: list[dict[str, Any]] = []
    while remaining:
        seed = remaining.pop()
        members = [seed]
        queue = [seed]
        while queue:
            current = queue.pop()
            current_point = buckets[current]["point"]
            nearby = [
                idx
                for idx in remaining
                if current_point.distance(buckets[idx]["point"]) <= cluster_distance
            ]
            for idx in nearby:
                remaining.remove(idx)
                queue.append(idx)
                members.append(idx)

        points = [buckets[idx]["point"] for idx in members]
        joined_members: list[tuple[Any, float]] = []
        for idx in members:
            joined_members.extend(buckets[idx]["members"])
        if len({road_idx for road_idx, _ in joined_members}) < 2:
            continue
        x = sum(point.x for point in points) / len(points)
        y = sum(point.y for point in points) / len(points)
        clustered.append({"point": Point(x, y), "members": joined_members})

    clustered.sort(key=lambda item: (item["point"].x, item["point"].y))
    return clustered


def rounded_junction_polygon(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    point: Point,
    members: list[tuple[Any, float]],
    design_parameters: dict[str, Any] | None = None,
):
    parts = []
    widths = []
    design_parameters = design_parameters or {}
    throat_multiplier = float(design_parameters.get("throat_multiplier", 1.0) or 1.0)
    core_radius_multiplier = float(design_parameters.get("core_radius_multiplier", 1.0) or 1.0)
    smooth_multiplier = float(design_parameters.get("smooth_multiplier", 1.0) or 1.0)
    min_throat = float(design_parameters.get("min_throat_m", JUNCTION_PATCH_MIN_THROAT_M) or JUNCTION_PATCH_MIN_THROAT_M)
    max_throat = float(design_parameters.get("max_throat_m", JUNCTION_PATCH_MAX_THROAT_M) or JUNCTION_PATCH_MAX_THROAT_M)
    member_distances: dict[Any, list[float]] = {}
    for road_idx, distance_hint in members:
        member_distances.setdefault(road_idx, []).append(float(distance_hint))

    for road_idx, distance_hints in member_distances.items():
        if road_idx not in prepared_roads.index:
            continue
        row = prepared_roads.loc[road_idx]
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue

        rule = road_gen.get_road_rule(row, rules)
        width = drivable_width_for_row(row, rule)
        if width <= 0.1:
            continue
        widths.append(width)
        base_throat = max(
            min_throat,
            min(max_throat, width * 0.75 + 6.0),
        )
        throat = max(min_throat, min(max_throat, base_throat * throat_multiplier))
        if line.distance(point) <= max(road_gen.junction_connection_tolerance(), JUNCTION_BUCKET_CLUSTER_M):
            distance_hints.append(float(line.project(point)))
        clamped_distances = [max(0.0, min(float(line.length), float(distance))) for distance in distance_hints]
        start = max(0.0, min(clamped_distances) - throat)
        end = min(float(line.length), max(clamped_distances) + throat)
        if end - start <= 0.1:
            continue
        try:
            center_segment = substring(line, start, end)
        except Exception:
            continue
        if center_segment is None or center_segment.is_empty:
            continue
        parts.append(center_segment.buffer(width / 2.0, cap_style=2, join_style=1, resolution=12))

    if len(parts) < 2:
        return None

    core_radius = max(max(widths, default=0.0) * 0.45 * core_radius_multiplier, 4.0)
    parts.append(point.buffer(core_radius, resolution=18))
    try:
        geom = unary_union(parts)
        smooth = max(0.4, JUNCTION_PATCH_SMOOTH_M * smooth_multiplier)
        geom = geom.buffer(smooth, join_style=1, resolution=12).buffer(-smooth, join_style=1, resolution=12)
        return road_gen.clean_polygonal(geom)
    except Exception:
        return None


JUNCTION_DESIGN_REFERENCES = [
    {
        "source": "FHWA Intersection Control Evaluation / Intersection Safety",
        "principle": "Compare feasible control and geometric alternatives instead of applying one default treatment to every node.",
    },
    {
        "source": "FHWA Signalized Intersections Informational Guide",
        "principle": "Use channelization, markings, and approach organization to improve operational clarity at larger intersections.",
    },
    {
        "source": "NACTO Urban Street Design Guide - Intersection Design Elements",
        "principle": "Prefer compact, legible intersections with visible crossings and controlled curb/asset placement.",
    },
]


JUNCTION_DESIGN_OPTION_LIBRARY: dict[str, dict[str, Any]] = {
    "LOCAL_COMPACT": {
        "label": "local compact priority junction",
        "fits_types": {"T_JUNCTION", "Y_JUNCTION", "CROSS_JUNCTION", "TWO_ARM_CONNECTION"},
        "fits_hierarchies": {"LOCAL_JUNCTION"},
        "max_arms": 4,
        "target_max_lane": 2,
        "throat_multiplier": 0.82,
        "core_radius_multiplier": 0.82,
        "smooth_multiplier": 0.85,
        "min_throat_m": 6.5,
        "max_throat_m": 22.0,
        "control": "yield_or_minor_stop",
        "marking_policy": "minimal_stop_lines_where_fit",
    },
    "MINOR_MAJOR_PRIORITY": {
        "label": "minor-major priority junction",
        "fits_types": {"T_JUNCTION", "Y_JUNCTION", "CROSS_JUNCTION"},
        "fits_hierarchies": {"SECONDARY_COLLECTOR", "LOCAL_JUNCTION"},
        "max_arms": 4,
        "target_max_lane": 3,
        "throat_multiplier": 0.96,
        "core_radius_multiplier": 0.95,
        "smooth_multiplier": 1.0,
        "min_throat_m": 8.0,
        "max_throat_m": 30.0,
        "control": "major_priority_minor_stop",
        "marking_policy": "crosswalk_and_stop_on_minor_approaches",
    },
    "SIGNALIZED_ARTERIAL": {
        "label": "signalized arterial intersection",
        "fits_types": {"CROSS_JUNCTION", "T_JUNCTION", "SKEWED_CROSS_OR_MULTI_ARM"},
        "fits_hierarchies": {"MAJOR_ARTERIAL", "SECONDARY_COLLECTOR"},
        "max_arms": 4,
        "target_max_lane": 6,
        "throat_multiplier": 1.12,
        "core_radius_multiplier": 1.08,
        "smooth_multiplier": 1.05,
        "min_throat_m": 12.0,
        "max_throat_m": 38.0,
        "control": "signalized",
        "marking_policy": "crosswalks_stop_lines_and_turn_pockets",
    },
    "CHANNELIZED_MULTI_ARM": {
        "label": "channelized multi-arm junction",
        "fits_types": {"MULTI_ARM_JUNCTION", "SKEWED_CROSS_OR_MULTI_ARM"},
        "fits_hierarchies": {"COMPLEX_MULTI_ARM", "MAJOR_ARTERIAL"},
        "max_arms": 8,
        "target_max_lane": 8,
        "throat_multiplier": 1.22,
        "core_radius_multiplier": 1.18,
        "smooth_multiplier": 1.18,
        "min_throat_m": 14.0,
        "max_throat_m": 44.0,
        "control": "signalized_or_channelized_priority",
        "marking_policy": "channelized_crossings_and_larger_clear_zone",
    },
    "ROUNDABOUT_COMPACT": {
        "label": "compact roundabout-like node",
        "fits_types": {"ROUNDABOUT_LIKE", "MULTI_ARM_JUNCTION"},
        "fits_hierarchies": {"ROUNDABOUT_JUNCTION", "COMPLEX_MULTI_ARM", "SECONDARY_COLLECTOR"},
        "max_arms": 6,
        "target_max_lane": 4,
        "throat_multiplier": 1.08,
        "core_radius_multiplier": 1.35,
        "smooth_multiplier": 1.3,
        "min_throat_m": 10.0,
        "max_throat_m": 36.0,
        "control": "roundabout_yield",
        "marking_policy": "set_back_crossings_and_no_stop_lines_inside_circulatory_area",
    },
    "RAMP_MERGE_TAPER": {
        "label": "ramp merge taper junction",
        "fits_types": {"RAMP_MERGE", "TWO_ARM_CONNECTION", "Y_JUNCTION"},
        "fits_hierarchies": {"RAMP_OR_GRADE_SEPARATED", "MAJOR_ARTERIAL"},
        "max_arms": 4,
        "target_max_lane": 6,
        "throat_multiplier": 1.35,
        "core_radius_multiplier": 0.72,
        "smooth_multiplier": 1.45,
        "min_throat_m": 14.0,
        "max_throat_m": 48.0,
        "control": "merge_diverge_taper",
        "marking_policy": "no_crosswalk_on_ramp_merge_unless_local_fit",
    },
}


def junction_source_node_summary(arms: list[dict[str, Any]]) -> dict[str, Any]:
    priorities = [int(arm["road_priority"]) for arm in arms]
    lane_counts = [int(arm["lane_count_estimate"]) for arm in arms]
    widths = [float(arm["drivable_width_m"]) for arm in arms]
    gaps = angular_gaps_deg([arm["direction_out"] for arm in arms])
    return {
        "arm_count": len(arms),
        "road_class_counts": dict(Counter(arm["road_class"] for arm in arms)),
        "category_counts": dict(Counter(arm["category"] for arm in arms)),
        "max_priority": max(priorities, default=0),
        "min_priority": min(priorities, default=0),
        "priority_spread": max(priorities, default=0) - min(priorities, default=0),
        "max_lane_count": max(lane_counts, default=1),
        "max_drivable_width_m": round(max(widths, default=0.0), 3),
        "marked_approach_fit_count": sum(1 for arm in arms if arm["marked_approach_fit"]),
        "angular_gaps_deg": gaps,
        "min_angular_gap_deg": min(gaps) if gaps else None,
        "max_angular_gap_deg": max(gaps) if gaps else None,
    }


def score_junction_design_option(
    option_id: str,
    option: dict[str, Any],
    junction_type: str,
    hierarchy: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    arm_count = int(summary["arm_count"])
    max_lane = int(summary["max_lane_count"])
    priority_spread = int(summary["priority_spread"])
    min_gap = float(summary["min_angular_gap_deg"] or 0.0)
    max_gap = float(summary["max_angular_gap_deg"] or 0.0)

    type_fit = 1.0 if junction_type in option["fits_types"] else 0.25
    hierarchy_fit = 1.0 if hierarchy in option["fits_hierarchies"] else 0.35
    arm_fit = 1.0 if arm_count <= int(option["max_arms"]) else max(0.0, 1.0 - 0.18 * (arm_count - int(option["max_arms"])))
    lane_delta = abs(max_lane - int(option["target_max_lane"]))
    lane_fit = max(0.0, 1.0 - 0.16 * lane_delta)
    angle_fit = 1.0
    if junction_type in {"CROSS_JUNCTION", "T_JUNCTION"}:
        angle_fit = 1.0 if min_gap >= 38.0 else max(0.35, min_gap / 38.0)
    elif junction_type in {"SKEWED_CROSS_OR_MULTI_ARM", "MULTI_ARM_JUNCTION"}:
        angle_fit = 1.0 if min_gap >= 24.0 else max(0.25, min_gap / 24.0)
    if max_gap > 210.0 and option_id not in {"TWO_ARM_CONNECTION", "LOCAL_COMPACT", "MINOR_MAJOR_PRIORITY"}:
        angle_fit *= 0.88

    priority_fit = 1.0
    if option_id == "MINOR_MAJOR_PRIORITY":
        priority_fit = 1.0 if priority_spread >= 1 else 0.78
    elif option_id in {"SIGNALIZED_ARTERIAL", "CHANNELIZED_MULTI_ARM"}:
        priority_fit = 1.0 if int(summary["max_priority"]) >= 3 else 0.55
    elif option_id == "RAMP_MERGE_TAPER":
        priority_fit = 1.0 if junction_type == "RAMP_MERGE" else 0.55
    elif option_id == "ROUNDABOUT_COMPACT":
        priority_fit = 1.0 if arm_count >= 3 and max_lane <= 4 else 0.58

    score = round(
        100.0
        * (
            0.26 * type_fit
            + 0.22 * hierarchy_fit
            + 0.16 * arm_fit
            + 0.14 * lane_fit
            + 0.12 * angle_fit
            + 0.10 * priority_fit
        ),
        2,
    )
    return {
        "option_id": option_id,
        "label": option["label"],
        "score": score,
        "control": option["control"],
        "marking_policy": option["marking_policy"],
        "score_breakdown": {
            "type_fit": round(type_fit, 3),
            "hierarchy_fit": round(hierarchy_fit, 3),
            "arm_fit": round(arm_fit, 3),
            "lane_fit": round(lane_fit, 3),
            "angle_fit": round(angle_fit, 3),
            "priority_fit": round(priority_fit, 3),
        },
        "surface_parameters": {
            key: option[key]
            for key in (
                "throat_multiplier",
                "core_radius_multiplier",
                "smooth_multiplier",
                "min_throat_m",
                "max_throat_m",
            )
        },
    }


def evaluate_junction_design_options(
    arms: list[dict[str, Any]],
    junction_type: str,
    hierarchy: str,
) -> dict[str, Any]:
    summary = junction_source_node_summary(arms)
    candidates = [
        score_junction_design_option(option_id, option, junction_type, hierarchy, summary)
        for option_id, option in JUNCTION_DESIGN_OPTION_LIBRARY.items()
    ]
    candidates.sort(key=lambda item: item["score"], reverse=True)
    selected = candidates[0] if candidates else {}
    if selected and selected["score"] < JUNCTION_DESIGN_SCORE_THRESHOLD:
        selected = dict(selected)
        selected["review_required"] = True
    elif selected:
        selected = dict(selected)
        selected["review_required"] = False
    return {
        "source_node_summary": summary,
        "candidate_design_options": candidates,
        "selected_design_option": selected,
        "design_references": JUNCTION_DESIGN_REFERENCES,
    }


def selected_junction_design_for_bucket(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    point: Point,
    members: list[tuple[Any, float]],
) -> dict[str, Any]:
    arms = junction_arm_records(prepared_roads, rules, point, members)
    if len(arms) < 2:
        return {
            "source_node_summary": {"arm_count": len(arms)},
            "candidate_design_options": [],
            "selected_design_option": {
                "option_id": "LOCAL_COMPACT",
                "label": "fallback compact node",
                "score": 0.0,
                "surface_parameters": JUNCTION_DESIGN_OPTION_LIBRARY["LOCAL_COMPACT"],
                "review_required": True,
            },
            "design_references": JUNCTION_DESIGN_REFERENCES,
        }
    junction_type = classify_city_junction_type(arms)
    hierarchy = classify_city_junction_hierarchy(arms, junction_type)
    return evaluate_junction_design_options(arms, junction_type, hierarchy)


def build_rounded_junction_surface_geometries(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    buckets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not ENABLE_ROUNDED_JUNCTION_SURFACES or prepared_roads.empty:
        return []

    surfaces: list[dict[str, Any]] = []
    for idx, bucket in enumerate(buckets if buckets is not None else junction_point_buckets(prepared_roads)):
        design = selected_junction_design_for_bucket(prepared_roads, rules, bucket["point"], bucket["members"])
        selected = design.get("selected_design_option") or {}
        geom = rounded_junction_polygon(
            prepared_roads,
            rules,
            bucket["point"],
            bucket["members"],
            selected.get("surface_parameters"),
        )
        if geom is None or geom.is_empty:
            continue
        surfaces.append(
            {
                "index": idx,
                "geometry": geom,
                "members": bucket["members"],
                "point": bucket["point"],
                "design": design,
            }
        )
    return surfaces


def build_rounded_junction_surface_meshes(
    surface_geometries: list[dict[str, Any]],
) -> list[trimesh.Trimesh]:
    meshes = []
    for surface in surface_geometries:
        idx = int(surface["index"])
        geom = surface["geometry"]
        mesh = road_gen.polygon_to_top_mesh(
            geom,
            JUNCTION_SURFACE_Z_OFFSET_M,
            f"Junction_Surface_{idx}",
            visual_color=COLORS["road_surface_main"],
        )
        if len(mesh.vertices) > 0:
            mesh.metadata["name"] = f"Junction_Surface_{idx}"
            meshes.append(mesh)
    return meshes


def junction_surface_union(surface_geometries: list[dict[str, Any]], clearance: float = 0.0):
    geoms = [
        surface["geometry"]
        for surface in surface_geometries
        if surface.get("geometry") is not None and not surface["geometry"].is_empty
    ]
    if not geoms:
        return None
    geom = road_gen.clean_polygonal(unary_union(geoms))
    if geom is None or geom.is_empty:
        return None
    if clearance > 0.0:
        geom = road_gen.clean_polygonal(geom.buffer(clearance, resolution=6, join_style=1))
    return geom


def mesh_xy_center_point(mesh: trimesh.Trimesh) -> Point | None:
    if mesh is None or len(mesh.vertices) == 0:
        return None
    bounds = mesh.bounds
    return Point(
        (float(bounds[0][0]) + float(bounds[1][0])) / 2.0,
        (float(bounds[0][1]) + float(bounds[1][1])) / 2.0,
    )


def mesh_xy_footprint(mesh: trimesh.Trimesh):
    if mesh is None or len(mesh.vertices) == 0:
        return None
    points = []
    seen = set()
    for vertex in mesh.vertices:
        key = (round(float(vertex[0]), 6), round(float(vertex[1]), 6))
        if key in seen:
            continue
        seen.add(key)
        points.append((float(vertex[0]), float(vertex[1])))
    if len(points) < 3:
        return None
    try:
        return Polygon(points).convex_hull
    except Exception:
        return None


def mesh_center_inside_polygon(mesh: trimesh.Trimesh, geom) -> bool:
    if geom is None or geom.is_empty:
        return False
    point = mesh_xy_center_point(mesh)
    return bool(point is not None and geom.intersects(point))


def filter_meshes_outside_polygon(meshes: list[trimesh.Trimesh], geom) -> tuple[list[trimesh.Trimesh], int]:
    if geom is None or geom.is_empty:
        return meshes, 0
    kept = []
    removed = 0
    for mesh in meshes:
        if mesh_center_inside_polygon(mesh, geom):
            removed += 1
            continue
        kept.append(mesh)
    return kept, removed


def filter_meshes_without_polygon_overlap(meshes: list[trimesh.Trimesh], geom) -> tuple[list[trimesh.Trimesh], int]:
    if geom is None or geom.is_empty:
        return meshes, 0
    kept = []
    removed = 0
    for mesh in meshes:
        footprint = mesh_xy_footprint(mesh)
        if footprint is not None and geom.intersects(footprint):
            removed += 1
            continue
        kept.append(mesh)
    return kept, removed


def line_intersection_distance_ranges(line: LineString, geom) -> list[tuple[float, float]]:
    if line is None or line.is_empty or geom is None or geom.is_empty or not line.intersects(geom):
        return []

    ranges: list[tuple[float, float]] = []

    def add_range_from_geom(part) -> None:
        if part is None or part.is_empty:
            return
        geom_type = part.geom_type
        if geom_type == "Point":
            distance = float(line.project(part))
            ranges.append((distance, distance))
            return
        if geom_type == "MultiPoint":
            for point in part.geoms:
                add_range_from_geom(point)
            return
        if geom_type == "LineString":
            coords = list(part.coords)
            if len(coords) < 2:
                return
            start = float(line.project(Point(coords[0])))
            end = float(line.project(Point(coords[-1])))
            ranges.append((min(start, end), max(start, end)))
            return
        if geom_type == "MultiLineString":
            for subline in part.geoms:
                add_range_from_geom(subline)
            return
        if geom_type == "GeometryCollection":
            for subpart in part.geoms:
                add_range_from_geom(subpart)

    try:
        add_range_from_geom(line.intersection(geom))
    except Exception:
        return []
    return [(start, end) for start, end in ranges if end >= start]


def merge_clip_ranges_by_road(
    ranges_by_road: dict[Any, list[tuple[float, float]]],
    merge_tolerance: float = 0.25,
) -> dict[Any, list[tuple[float, float]]]:
    merged_by_road: dict[Any, list[tuple[float, float]]] = {}
    for road_idx, ranges in ranges_by_road.items():
        valid_ranges = [(start, end) for start, end in ranges if end - start > 0.05]
        if not valid_ranges:
            continue
        valid_ranges.sort()
        merged: list[tuple[float, float]] = [valid_ranges[0]]
        for start, end in valid_ranges[1:]:
            last_start, last_end = merged[-1]
            if start <= last_end + merge_tolerance:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))
        merged_by_road[road_idx] = merged
    return merged_by_road


def add_adjusted_clip_range(
    ranges_by_road: dict[Any, list[tuple[float, float]]],
    road_idx: Any,
    line_length: float,
    start: float,
    end: float,
    outward_pad: float = 0.0,
    edge_overlap: float = 0.0,
) -> None:
    if end < start:
        start, end = end, start
    line_length = max(float(line_length), 0.0)
    start = max(0.0, min(line_length, float(start)))
    end = max(0.0, min(line_length, float(end)))
    if end - start <= 0.05:
        return

    adjusted_start = start - outward_pad
    adjusted_end = end + outward_pad
    if edge_overlap > 0.0:
        if start > 0.05:
            adjusted_start += edge_overlap
        if end < line_length - 0.05:
            adjusted_end -= edge_overlap

    adjusted_start = max(0.0, min(line_length, adjusted_start))
    adjusted_end = max(0.0, min(line_length, adjusted_end))
    if adjusted_end - adjusted_start <= 0.05:
        return
    ranges_by_road.setdefault(road_idx, []).append((adjusted_start, adjusted_end))


def junction_clip_range_profiles_by_road(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    surface_geometries: list[dict[str, Any]],
) -> dict[str, dict[Any, list[tuple[float, float]]]]:
    raw_ranges: dict[str, dict[Any, list[tuple[float, float]]]] = {
        "drivable": {},
        "roadside": {},
        "marking": {},
    }
    surface_union = junction_surface_union(surface_geometries)
    if surface_union is None or surface_union.is_empty:
        return raw_ranges

    buffered_surface_cache: dict[float, Any] = {}
    for road_idx, row in prepared_roads.iterrows():
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue

        rule = road_gen.get_road_rule(row, rules)
        components = road_gen.cross_section_components_for_row(row)
        if not components:
            components = fallback_cross_section_components(rule)
        total_width = max(
            road_gen.component_total_width(components),
            drivable_width_for_row(row, rule),
            float(rule.road_width),
        )
        drivable_width = max(drivable_width_for_row(row, rule), float(rule.road_width))

        for start, end in line_intersection_distance_ranges(line, surface_union):
            add_adjusted_clip_range(
                raw_ranges["drivable"],
                road_idx,
                float(line.length),
                start,
                end,
                edge_overlap=JUNCTION_DRIVABLE_BLEND_OVERLAP_M,
            )

        roadside_radius = round(max(total_width * 0.5, 0.0) + JUNCTION_ROADSIDE_RETREAT_M, 1)
        marking_radius = round(max(drivable_width * 0.5, 0.0) + JUNCTION_MARKING_RETREAT_M, 1)
        for profile, radius, pad in [
            ("roadside", roadside_radius, 0.25),
            ("marking", marking_radius, 0.10),
        ]:
            if radius not in buffered_surface_cache:
                buffered_surface_cache[radius] = surface_union.buffer(
                    radius,
                    resolution=4,
                    join_style=1,
                )
            for start, end in line_intersection_distance_ranges(line, buffered_surface_cache[radius]):
                add_adjusted_clip_range(
                    raw_ranges[profile],
                    road_idx,
                    float(line.length),
                    start,
                    end,
                    outward_pad=pad,
                )

    return {
        profile: merge_clip_ranges_by_road(ranges)
        for profile, ranges in raw_ranges.items()
    }


def junction_component_clip_ranges_by_road(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    surface_geometries: list[dict[str, Any]],
) -> dict[Any, list[tuple[float, float]]]:
    return junction_clip_range_profiles_by_road(
        prepared_roads,
        rules,
        surface_geometries,
    ).get("roadside", {})


def build_road_surface_meshes(roads: gpd.GeoDataFrame) -> dict[str, trimesh.Trimesh]:
    if roads.empty:
        return {}

    prepared_roads = roads.copy() if roads_are_prepared_for_surfaces(roads) else prepare_roads_for_surfaces(roads)
    if prepared_roads.empty:
        return {}

    rules = road_gen.load_rules()
    default_rule = rules.get("default_road")
    if default_rule is None:
        raise ValueError("Missing default_road rule in road_rules.json")

    component_mesh_groups: dict[str, list[trimesh.Trimesh]] = {
        "Road_Surface_Main": [],
        "Road_Surface_Service": [],
        "Road_Surface_Branch": [],
        "Non_Motor_Lane": [],
        "Parking_Lane": [],
        "Sidewalk": [],
        "Green_Belt": [],
        "Facility_Belt": [],
        "Side_Divider": [],
        "Median": [],
    }
    curb_meshes: list[trimesh.Trimesh] = []
    white_marking_meshes: list[trimesh.Trimesh] = []
    yellow_marking_meshes: list[trimesh.Trimesh] = []
    crosswalk_meshes: list[trimesh.Trimesh] = []
    stop_line_meshes: list[trimesh.Trimesh] = []
    junction_marking_stats: Counter[str] = Counter()
    junction_buckets = junction_point_buckets(prepared_roads)
    junction_surface_geometries = build_rounded_junction_surface_geometries(prepared_roads, rules, junction_buckets)
    junction_surface_meshes = build_rounded_junction_surface_meshes(junction_surface_geometries)
    junction_mask_geom = junction_surface_union(junction_surface_geometries)
    junction_marking_filter_geom = junction_surface_union(
        junction_surface_geometries,
        clearance=JUNCTION_MARKING_SURFACE_CLEARANCE_M,
    )
    junction_asset_filter_geom = junction_surface_union(junction_surface_geometries, clearance=1.0)
    junction_drivable_core_clip_geom = None
    if junction_mask_geom is not None and not junction_mask_geom.is_empty:
        junction_drivable_core_clip_geom = road_gen.clean_polygonal(
            junction_mask_geom.buffer(
                -JUNCTION_DRIVABLE_CORE_CLIP_INSET_M,
                resolution=4,
                join_style=1,
            )
        )
    junction_clip_profiles = junction_clip_range_profiles_by_road(
        prepared_roads,
        rules,
        junction_surface_geometries,
    )
    drivable_clip_ranges = junction_clip_profiles.get("drivable", {})
    roadside_clip_ranges = junction_clip_profiles.get("roadside", {})
    marking_clip_ranges = junction_clip_profiles.get("marking", {})
    asset_mesh_groups: dict[str, list[trimesh.Trimesh]] = {}
    for road_idx, row in prepared_roads.iterrows():
        row = row.copy()
        row.name = road_idx
        rule = road_gen.get_road_rule(row, rules)
        components = road_gen.cross_section_components_for_row(row)
        if not components:
            components = fallback_cross_section_components(rule)
        spans = component_spans(components)
        white_edge_suppression = carriageway_boundary_edge_suppression(spans)
        for line_idx, line in enumerate(iter_lines(row.geometry)):
            line_spans = spans
            segment_cache: dict[str, list[tuple[LineString, float]]] = {}

            def clipped_segments_for(profile: str) -> list[tuple[LineString, float]]:
                if profile not in segment_cache:
                    profile_ranges = {
                        "drivable": drivable_clip_ranges,
                        "roadside": roadside_clip_ranges,
                        "marking": marking_clip_ranges,
                    }.get(profile, {})
                    clip_ranges = profile_ranges.get(road_idx, [])
                    segment_cache[profile] = (
                        road_gen.line_segments_outside_ranges(line, clip_ranges)
                        if clip_ranges
                        else [(line, 0.0)]
                    )
                return segment_cache[profile]

            for component_idx, (component, left_offset, right_offset) in enumerate(line_spans):
                component_type = str(component.get("type", ""))
                layer_name = component_layer_name_for_row(component_type, row)
                color = COMPONENT_COLORS.get(layer_name, COLORS["green_belt"])
                profile = "drivable" if component_type in DRIVABLE_COMPONENT_TYPES else "roadside"
                for segment_idx, (segment, distance_offset) in enumerate(clipped_segments_for(profile)):
                    if segment is None or segment.is_empty:
                        continue
                    mesh = swept_band_mesh(
                        row,
                        segment,
                        left_offset,
                        right_offset,
                        f"{layer_name}_{road_idx}_{line_idx}_{segment_idx}_{component_idx}",
                        color,
                        z_offset=component_z_offset(component_type, rule),
                        distance_offset=distance_offset,
                        clip_mask=junction_drivable_core_clip_geom
                        if component_type in DRIVABLE_COMPONENT_TYPES
                        else None,
                    )
                    if len(mesh.vertices) > 0:
                        component_mesh_groups.setdefault(layer_name, []).append(mesh)

            for segment, distance_offset in clipped_segments_for("marking"):
                if segment is None or segment.is_empty:
                    continue
                for component_idx, (component, left_offset, right_offset) in enumerate(line_spans):
                    suppressed_edges = white_edge_suppression.get(component_idx, set())
                    add_component_lane_markings(
                        row,
                        segment,
                        rule,
                        component,
                        left_offset,
                        right_offset,
                        white_marking_meshes,
                        suppress_left_edge="left" in suppressed_edges,
                        suppress_right_edge="right" in suppressed_edges,
                        distance_offset=distance_offset,
                    )

                add_center_markings_for_adjacent_carriageways(
                    row,
                    segment,
                    rule,
                    line_spans,
                    yellow_marking_meshes,
                    distance_offset=distance_offset,
                )

            for segment, distance_offset in clipped_segments_for("roadside"):
                if segment is None or segment.is_empty:
                    continue
                add_component_curbs(
                    row,
                    segment,
                    rule,
                    line_spans,
                    curb_meshes,
                    distance_offset=distance_offset,
                )
            junction_marking_stats.update(
                add_junction_crosswalks_and_stop_lines(
                    row,
                    line,
                    rule,
                    crosswalk_meshes,
                    stop_line_meshes,
                )
            )

            if GENERATE_ROAD_ASSETS:
                for mesh in road_gen.build_street_light_meshes(row, rule):
                    if mesh_center_inside_polygon(mesh, junction_asset_filter_geom):
                        continue
                    name = f"{mesh.metadata.get('name', 'Street_Light')}_{road_idx}_{line_idx}"
                    mesh.metadata["name"] = name
                    asset_mesh_groups.setdefault(road_gen.asset_mesh_group_name(mesh), []).append(mesh)
                for mesh in road_gen.build_tree_meshes(row, rule):
                    if mesh_center_inside_polygon(mesh, junction_asset_filter_geom):
                        continue
                    name = f"{mesh.metadata.get('name', 'Tree')}_{road_idx}_{line_idx}"
                    mesh.metadata["name"] = name
                    asset_mesh_groups.setdefault(road_gen.asset_mesh_group_name(mesh), []).append(mesh)

    if junction_mask_geom is not None:
        crosswalk_meshes, removed_crosswalks = filter_meshes_outside_polygon(
            crosswalk_meshes,
            junction_marking_filter_geom,
        )
        stop_line_meshes, removed_stop_lines = filter_meshes_outside_polygon(
            stop_line_meshes,
            junction_marking_filter_geom,
        )
        crosswalk_meshes, removed_crosswalk_overlaps = filter_meshes_without_polygon_overlap(
            crosswalk_meshes,
            junction_mask_geom,
        )
        stop_line_meshes, removed_stop_line_overlaps = filter_meshes_without_polygon_overlap(
            stop_line_meshes,
            junction_mask_geom,
        )
        removed_crosswalks += removed_crosswalk_overlaps
        removed_stop_lines += removed_stop_line_overlaps
        junction_marking_stats["crosswalk_stripe_count"] = max(
            0,
            int(junction_marking_stats.get("crosswalk_stripe_count", 0)) - removed_crosswalks,
        )
        junction_marking_stats["stop_line_count"] = max(
            0,
            int(junction_marking_stats.get("stop_line_count", 0)) - removed_stop_lines,
        )
        junction_marking_stats["filtered_crosswalk_inside_junction_count"] = removed_crosswalks
        junction_marking_stats["filtered_stop_line_inside_junction_count"] = removed_stop_lines
        junction_marking_stats["filtered_crosswalk_overlap_count"] = removed_crosswalk_overlaps
        junction_marking_stats["filtered_stop_line_overlap_count"] = removed_stop_line_overlaps

    combined_meshes = {}
    for group_name, parts in component_mesh_groups.items():
        mesh = combine_mesh_list(f"{group_name}_All", parts, COMPONENT_COLORS.get(group_name, COLORS["road_surface"]))
        if mesh is not None:
            combined_meshes[mesh.metadata["name"]] = mesh

    layer_meshes = [
        ("Curb_All", combine_mesh_list("Curb_All", curb_meshes, COLORS["curb"])),
        ("Junction_Surface_All", combine_mesh_list("Junction_Surface_All", junction_surface_meshes, COLORS["road_surface_main"])),
        ("Lane_Marking_White_All", combine_mesh_list("Lane_Marking_White_All", white_marking_meshes, COLORS["lane_marking"])),
        ("Lane_Marking_Yellow_All", combine_mesh_list("Lane_Marking_Yellow_All", yellow_marking_meshes, COLORS["center_marking"])),
        ("Crosswalk_All", combine_mesh_list("Crosswalk_All", crosswalk_meshes, COLORS["crosswalk"])),
        ("Stop_Line_All", combine_mesh_list("Stop_Line_All", stop_line_meshes, COLORS["stop_line"])),
    ]
    for name, mesh in layer_meshes:
        if mesh is not None:
            if name in {"Crosswalk_All", "Stop_Line_All"}:
                mesh.metadata.update({f"junction_{key}": int(value) for key, value in junction_marking_stats.items()})
            combined_meshes[name] = mesh

    for group_name, parts in sorted(asset_mesh_groups.items()):
        mesh_name = f"{group_name}_All"
        mesh = combine_mesh_list(mesh_name, parts, road_gen.asset_group_color(group_name) or COLORS["road_surface"])
        if mesh is not None:
            combined_meshes[mesh_name] = mesh
    return combined_meshes


def build_building_meshes(buildings: gpd.GeoDataFrame) -> dict[str, trimesh.Trimesh]:
    meshes = {}
    for idx, row in buildings.iterrows():
        height = building_height(row)
        name = f"Building_{idx}"
        mesh = road_gen.polygon_to_extruded_mesh(
            row.geometry,
            z_bottom=0.0,
            z_top=height,
            name=name,
            visual_color=COLORS["building"],
        )
        if len(mesh.vertices) == 0:
            continue
        meshes[name] = mesh
    return meshes


def subway_like(row) -> bool:
    railway = str(row.get("railway", "")).lower()
    tunnel = str(row.get("tunnel", "")).lower()
    layer = safe_float(row.get("layer"), 0.0)
    line_type = str(row.get("type", "")).lower()
    name = str(row.get("name", "")).lower()
    if railway == "subway" or tunnel in {"yes", "true"} or layer < 0:
        return True
    if line_type in {"subway", "light_rail"} or any(token in line_type for token in ["地铁", "轨道", "铁路", "高铁"]):
        return True
    return any(token in name for token in ["地铁", "轨道", "铁路", "高铁"])


def build_subway_tunnel_meshes(railways: gpd.GeoDataFrame) -> dict[str, trimesh.Trimesh]:
    tunnel_meshes: list[trimesh.Trimesh] = []
    for idx, row in railways.iterrows():
        if not subway_like(row):
            continue
        for line in iter_lines(row.geometry):
            coords = list(line.coords)
            for part_idx, (a, b) in enumerate(zip(coords, coords[1:])):
                mesh = cylinder_between(
                    f"Subway_Tunnel_{idx}_{part_idx}",
                    (a[0], a[1], SUBWAY_TUNNEL_DEPTH_M),
                    (b[0], b[1], SUBWAY_TUNNEL_DEPTH_M),
                    SUBWAY_TUNNEL_RADIUS_M,
                    COLORS["subway_tunnel"],
                    sections=24,
                )
                if mesh is None:
                    continue
                tunnel_meshes.append(mesh)
    combined = combine_mesh_list("Subway_Tunnel_All", tunnel_meshes, COLORS["subway_tunnel"])
    return {combined.metadata["name"]: combined} if combined is not None else {}


def is_subway_station(row) -> bool:
    values = " ".join(str(row.get(col, "")) for col in ["railway", "station", "subway", "public_transport", "type", "name"])
    values = values.lower()
    return "subway" in values or "u-bahn" in values or "station" in values


def is_bus_stop(row) -> bool:
    if str(row.get("highway", "")).lower() == "bus_stop" or str(row.get("bus", "")).lower() == "yes":
        return True
    return any(pd.notna(row.get(column)) for column in ["station_ui", "line_name", "raw_name"])


def build_transit_node_meshes(
    transport: gpd.GeoDataFrame,
) -> tuple[dict[str, trimesh.Trimesh], dict[str, trimesh.Trimesh]]:
    subway_station_meshes = {}
    bus_stop_meshes = {}
    for idx, row in transport.iterrows():
        point = representative_point(row.geometry)
        if point is None:
            continue
        if is_subway_station(row):
            name = f"Subway_Station_{idx}"
            mesh = make_box(
                name,
                (point.x, point.y, SUBWAY_STATION_DEPTH_M),
                SUBWAY_STATION_SIZE_M,
                COLORS["subway_station"],
            )
            subway_station_meshes[name] = mesh
        elif is_bus_stop(row):
            name = f"Bus_Stop_{idx}"
            base = make_box(f"{name}_Base", (point.x, point.y, 0.12), (4.2, 1.8, 0.24), COLORS["bus_stop"])
            shelter = make_box(f"{name}_Shelter", (point.x, point.y, 1.45), (3.6, 0.18, 2.3), COLORS["bus_stop"])
            canopy = make_box(f"{name}_Canopy", (point.x, point.y, 2.65), (4.4, 2.0, 0.18), COLORS["bus_stop"])
            mesh = trimesh.util.concatenate([base, shelter, canopy])
            mesh.metadata["name"] = name
            mesh.visual.face_colors = COLORS["bus_stop"]
            bus_stop_meshes[name] = mesh
    return subway_station_meshes, bus_stop_meshes


def offset_segment(a, b, offset: float) -> tuple[tuple[float, float], tuple[float, float]]:
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


def json_safe_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def extract_pipe_diameter_mm(row: pd.Series, pipe_name: str) -> tuple[int, str]:
    spec = UTILITY_PIPE_SPECS[pipe_name]
    min_dn = int(spec["min_dn_mm"])
    for field in UTILITY_DIAMETER_FIELDS:
        if field not in row.index:
            continue
        value = row.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none", "null"}:
            continue
        candidates: list[int] = []
        for pattern in UTILITY_DIAMETER_PATTERNS:
            for match in pattern.finditer(text):
                try:
                    candidates.append(int(match.group(1)))
                except ValueError:
                    continue
        if not candidates and text.isdigit():
            candidates.append(int(text))
        plausible = [dn for dn in candidates if 50 <= dn <= 5000]
        if plausible:
            return max(max(plausible), min_dn), f"attribute:{field}"
    return int(spec["default_dn_mm"]), "fallback_standard_default"


def pipe_radius_m(dn_mm: int, pipe_name: str) -> float:
    min_dn = int(UTILITY_PIPE_SPECS[pipe_name]["min_dn_mm"])
    return max(float(max(dn_mm, min_dn)) / 1000.0 / 2.0, 0.05)


def pipe_center_z_m(pipe_name: str, radius_m: float) -> float:
    return -(float(UTILITY_PIPE_SPECS[pipe_name]["cover_depth_m"]) + radius_m)


def collect_pipe_source_attributes(row: pd.Series) -> dict[str, Any]:
    attrs = {}
    for field in UTILITY_SOURCE_ATTRIBUTE_FIELDS:
        if field in row.index:
            attrs[field] = json_safe_value(row.get(field))
    return attrs


def build_pipe_semantic_record(
    pipe_name: str,
    source_feature_index: Any,
    source_kind: str,
    dn_mm: int,
    diameter_source: str,
    length_m: float,
    segment_count: int,
    source_attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = UTILITY_PIPE_SPECS[pipe_name]
    radius_m = pipe_radius_m(dn_mm, pipe_name)
    cover_depth_m = float(spec["cover_depth_m"])
    center_z_m = pipe_center_z_m(pipe_name, radius_m)
    source_id = json_safe_value(source_feature_index)
    if isinstance(source_id, float) and source_id.is_integer():
        source_id = int(source_id)
    return {
        "object_id": f"Utility_{pipe_name}_{source_id}",
        "pipe_type": pipe_name,
        "pipe_type_zh": spec["label_zh"],
        "source_kind": source_kind,
        "source_feature_index": source_id,
        "source_attributes": source_attributes or {},
        "dn_mm": int(max(dn_mm, int(spec["min_dn_mm"]))),
        "diameter_source": diameter_source,
        "radius_m": round(radius_m, 3),
        "cover_depth_m": round(cover_depth_m, 3),
        "center_z_m": round(center_z_m, 3),
        "center_depth_m": round(abs(center_z_m), 3),
        "top_z_m": round(-cover_depth_m, 3),
        "bottom_z_m": round(center_z_m - radius_m, 3),
        "min_cover_depth_m": round(float(spec["min_cover_depth_m"]), 3),
        "min_dn_mm": int(spec["min_dn_mm"]),
        "material_class": spec["material_class"],
        "flow_model": spec["flow_model"],
        "standard_references": spec["standards"],
        "length_m": round(float(length_m), 3),
        "geometry_segment_count": int(segment_count),
        "quality_flags": {
            "diameter_assigned": True,
            "diameter_meets_minimum": int(max(dn_mm, int(spec["min_dn_mm"]))) >= int(spec["min_dn_mm"]),
            "cover_depth_meets_minimum": cover_depth_m >= float(spec["min_cover_depth_m"]),
            "has_source_traceability": source_kind != "unknown",
        },
    }


def build_synthetic_utility_pipe_meshes(
    roads: gpd.GeoDataFrame,
) -> tuple[dict[str, trimesh.Trimesh], list[dict[str, Any]]]:
    meshes = {}
    records: list[dict[str, Any]] = []
    for road_idx, row in roads.iterrows():
        for line in iter_lines(row.geometry):
            coords = list(line.coords)
            for seg_idx, (a, b) in enumerate(zip(coords, coords[1:])):
                for pipe_name in ("Water", "Sewer", "Power", "Telecom"):
                    spec = UTILITY_PIPE_SPECS[pipe_name]
                    dn_mm = int(spec["default_dn_mm"])
                    radius = pipe_radius_m(dn_mm, pipe_name)
                    z = pipe_center_z_m(pipe_name, radius)
                    lateral_offset = float(spec["synthetic_lateral_offset_m"])
                    color = spec["color"]
                    start_xy, end_xy = offset_segment(a, b, lateral_offset)
                    name = f"Utility_{pipe_name}_{road_idx}_{seg_idx}"
                    mesh = cylinder_between(
                        name,
                        (start_xy[0], start_xy[1], z),
                        (end_xy[0], end_xy[1], z),
                        radius,
                        color,
                        sections=int(spec["mesh_sections"]),
                    )
                    if mesh is None:
                        continue
                    meshes[name] = mesh
                    records.append(
                        build_pipe_semantic_record(
                            pipe_name,
                            f"{road_idx}_{seg_idx}",
                            "synthetic_road_offset",
                            dn_mm,
                            "fallback_synthetic_standard_default",
                            LineString([start_xy, end_xy]).length,
                            1,
                            {"road_index": json_safe_value(road_idx)},
                        )
                    )
    return meshes, records


def build_utility_pipe_meshes(
    utility_layers: list[dict[str, Any]],
    roads: gpd.GeoDataFrame,
) -> tuple[dict[str, trimesh.Trimesh], list[dict[str, Any]]]:
    has_real_utilities = any(not item["layer"].empty for item in utility_layers)
    if not has_real_utilities:
        return build_synthetic_utility_pipe_meshes(roads)

    meshes = {}
    records: list[dict[str, Any]] = []
    for item in utility_layers:
        pipe_name = item["pipe_type"]
        layer = item["layer"]
        spec = UTILITY_PIPE_SPECS[pipe_name]
        color = spec["color"]
        pipe_meshes: list[trimesh.Trimesh] = []
        for line_idx, row in layer.iterrows():
            dn_mm, diameter_source = extract_pipe_diameter_mm(row, pipe_name)
            radius = pipe_radius_m(dn_mm, pipe_name)
            z = pipe_center_z_m(pipe_name, radius)
            feature_length = 0.0
            feature_segment_count = 0
            for line in iter_lines(row.geometry):
                coords = list(line.coords)
                feature_length += float(line.length)
                for seg_idx, (a, b) in enumerate(zip(coords, coords[1:])):
                    name = f"Utility_{pipe_name}_{line_idx}_{seg_idx}"
                    mesh = cylinder_between(
                        name,
                        (a[0], a[1], z),
                        (b[0], b[1], z),
                        radius,
                        color,
                        sections=int(spec["mesh_sections"]),
                    )
                    if mesh is None:
                        continue
                    pipe_meshes.append(mesh)
                    feature_segment_count += 1
            if feature_segment_count > 0:
                records.append(
                    build_pipe_semantic_record(
                        pipe_name,
                        line_idx,
                        "source_shp",
                        dn_mm,
                        diameter_source,
                        feature_length,
                        feature_segment_count,
                        collect_pipe_source_attributes(row),
                    )
                )
        combined = combine_mesh_list(f"Utility_{pipe_name}_All", pipe_meshes, color)
        if combined is not None:
            combined.metadata["pipe_type"] = pipe_name
            combined.metadata["feature_count"] = sum(1 for record in records if record["pipe_type"] == pipe_name)
            combined.metadata["standardized_pipe_model"] = True
            meshes[combined.metadata["name"]] = combined
    return meshes, records


def scene_from_meshes(meshes: dict[str, trimesh.Trimesh]) -> trimesh.Scene:
    scene = trimesh.Scene()
    for name, mesh in meshes.items():
        scene.add_geometry(mesh.copy(), node_name=name, geom_name=name)
    return scene


def combine_mesh_list(name: str, meshes: list[trimesh.Trimesh], color: list[int] | None = None) -> trimesh.Trimesh | None:
    valid = [mesh for mesh in meshes if mesh is not None and len(mesh.vertices) > 0]
    if not valid:
        return None
    combined = trimesh.util.concatenate(valid)
    combined.metadata["name"] = name
    combined.metadata["part_count"] = len(valid)
    if color is not None:
        combined.visual.face_colors = color
    return combined


REQUIRED_COMPONENT_TYPES_BY_CATEGORY = {
    "expressway": {"main_carriageway", "median", "sidewalk", "side_divider"},
    "arterial": {"main_carriageway", "median", "sidewalk"},
    "primary": {"main_carriageway", "median", "sidewalk"},
    "secondary": {"main_carriageway", "sidewalk"},
    "branch": {"main_carriageway", "sidewalk"},
}

MODEL_SCORE_WEIGHTS = {
    "geometry_symmetry_and_width": 25.0,
    "section_rule_fit": 18.0,
    "component_completeness": 17.0,
    "semantic_attribute_completeness": 15.0,
    "visual_layer_material_separation": 15.0,
    "intersection_curve_treatment": 10.0,
}

JUNCTION_SCORE_WEIGHTS = {
    "junction_surface_continuity": 16.0,
    "approach_marking_clearance": 12.0,
    "pedestrian_crossing": 12.0,
    "stop_control_marking": 10.0,
    "topology_semantic_completeness": 16.0,
    "lane_movement_semantic": 14.0,
    "asset_visibility_clearance": 8.0,
    "layer_material_separation": 7.0,
    "semantic_traceability": 5.0,
}


def rounded_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rounded = []
    for component in components:
        item = dict(component)
        if "width" in item:
            item["width"] = round(float(item["width"]), 3)
        if "scaled_from_width" in item:
            item["scaled_from_width"] = round(float(item["scaled_from_width"]), 3)
        rounded.append(item)
    return rounded


def road_cross_section_record(row: pd.Series) -> dict[str, Any]:
    source_rule = road_gen.row_section_requirement(row)
    modeled_rule = road_gen.modeled_section_requirement(row, source_rule)
    rule = road_gen.get_road_rule(row, road_gen.load_rules())
    components = road_gen.cross_section_components_for_row(row)
    source_components = road_gen.source_cross_section_components_for_row(row)
    source_section = road_gen.row_section_code(row) or (
        road_gen.safe_str(source_rule.get("section_id") or source_rule.get("inferred_section"))
        if source_rule
        else None
    )
    modeled_section = (
        road_gen.safe_str(modeled_rule.get("section_id") or modeled_rule.get("inferred_section"))
        if modeled_rule
        else None
    )
    road_class = road_gen.safe_str(row.get("road_class")) or "unclassified"
    category = road_gen.section_category_name(source_rule, road_class) if source_rule else road_gen.road_class_category_name(road_class)
    component_types = {str(component.get("type", "")) for component in components}
    required_types = REQUIRED_COMPONENT_TYPES_BY_CATEGORY.get(category, REQUIRED_COMPONENT_TYPES_BY_CATEGORY["branch"])
    missing_required = sorted(required_types - component_types)
    source_width = road_gen.row_target_total_width(row, source_rule)
    target_width = source_width
    modeled_width = road_gen.component_total_width(components)
    if (
        source_section
        and modeled_section
        and source_section != modeled_section
        and source_section in road_gen.SYMMETRIC_FALLBACK_KEEP_MODEL_WIDTH
    ):
        target_width = modeled_width
    width_error = None if target_width is None else modeled_width - float(target_width)
    spans = component_spans(components)
    junction_distances = road_gen.row_junction_distances(row)

    return {
        "source_road_id": road_gen.safe_str(row.get("road_id")) or "",
        "road_name": road_gen.safe_str(row.get("road_name")) or "",
        "road_class": road_class,
        "category": category,
        "source_section_code": source_section,
        "modeled_section_code": modeled_section,
        "symmetry_normalized": bool(source_section and modeled_section and source_section != modeled_section),
        "source_is_symmetric": road_gen.section_components_are_symmetric(source_components),
        "modeled_is_symmetric": road_gen.section_components_are_symmetric(components),
        "source_width_m": round(float(source_width), 3) if source_width is not None else None,
        "target_width_m": round(float(target_width), 3) if target_width is not None else None,
        "modeled_width_m": round(float(modeled_width), 3),
        "width_error_m": round(float(width_error), 3) if width_error is not None else None,
        "transition_curve_count": int(row.get("transition_curve_count", 0) or 0),
        "junction_distances_m": [round(float(distance), 3) for distance in junction_distances],
        "junction_marking_clearance_m": round(junction_marking_clearance_for_row(row, rule), 3),
        "missing_required_components": missing_required,
        "source_cross_section_components": rounded_components(source_components),
        "modeled_cross_section_components": rounded_components(components),
        "component_spans": [
            {
                "index": idx,
                "type": str(component.get("type", "")),
                "layer": component_layer_name_for_row(str(component.get("type", "")), row),
                "left_offset_m": round(float(left_offset), 3),
                "right_offset_m": round(float(right_offset), 3),
                "width_m": round(float(right_offset - left_offset), 3),
            }
            for idx, (component, left_offset, right_offset) in enumerate(spans)
        ],
    }


def build_city_road_semantic(prepared_roads: gpd.GeoDataFrame, origin: tuple[float, float]) -> dict[str, Any]:
    records = [road_cross_section_record(row) for _, row in prepared_roads.iterrows()]
    return {
        "project": "cim_road_poc",
        "model": "cim_city_roads",
        "unit": "meter",
        "coordinate": {
            "model_crs": TARGET_CRS,
            "local_origin": {"x": origin[0], "y": origin[1], "z": 0.0},
        },
        "symmetry_policy": {
            "enabled": road_gen.MODEL_CROSS_SECTIONS_AS_SYMMETRIC,
            "fallbacks": road_gen.SYMMETRIC_SECTION_FALLBACKS,
            "default_sections": road_gen.SYMMETRIC_DEFAULT_SECTION_BY_CATEGORY,
        },
        "objects": records,
    }


def write_city_road_semantic(prepared_roads: gpd.GeoDataFrame, origin: tuple[float, float]) -> dict[str, Any]:
    semantic = build_city_road_semantic(prepared_roads, origin)
    CITY_ROAD_SEMANTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CITY_ROAD_SEMANTIC_PATH.open("w", encoding="utf-8") as f:
        json.dump(semantic, f, ensure_ascii=False, indent=2)
    return semantic


def vector_angle_deg(vector: tuple[float, float]) -> float:
    return (math.degrees(math.atan2(vector[1], vector[0])) + 360.0) % 360.0


def signed_angle_deg(from_vector: tuple[float, float], to_vector: tuple[float, float]) -> float:
    cross = from_vector[0] * to_vector[1] - from_vector[1] * to_vector[0]
    dot = from_vector[0] * to_vector[0] + from_vector[1] * to_vector[1]
    return math.degrees(math.atan2(cross, dot))


def turn_movement_type(turn_angle_deg: float) -> str:
    abs_angle = abs(turn_angle_deg)
    if abs_angle <= 35.0:
        return "through"
    if abs_angle >= 145.0:
        return "u_turn"
    return "left" if turn_angle_deg > 0.0 else "right"


def angular_gaps_deg(vectors: list[tuple[float, float]]) -> list[float]:
    if len(vectors) < 2:
        return []
    angles = sorted(vector_angle_deg(vector) for vector in vectors)
    return [
        round(float((angles[(idx + 1) % len(angles)] - angle) % 360.0), 3)
        for idx, angle in enumerate(angles)
    ]


def classify_city_junction_type(arms: list[dict[str, Any]]) -> str:
    arm_count = len(arms)
    if arm_count <= 1:
        return "UNKNOWN"
    if any("roundabout" in arm["road_class"].lower() or "circular" in arm["road_class"].lower() for arm in arms):
        return "ROUNDABOUT_LIKE"
    if any("link" in arm["road_class"].lower() or "ramp" in arm["road_class"].lower() for arm in arms):
        return "RAMP_MERGE"
    priorities = [int(arm["road_priority"]) for arm in arms]
    if arm_count <= 2:
        return "TWO_ARM_CONNECTION"
    if arm_count == 3 and max(priorities, default=0) - min(priorities, default=0) >= 3:
        return "RAMP_MERGE"

    gaps = angular_gaps_deg([arm["direction_out"] for arm in arms])
    max_gap = max(gaps) if gaps else 0.0
    min_gap = min(gaps) if gaps else 0.0
    if arm_count == 3:
        return "T_JUNCTION" if max_gap > 150.0 else "Y_JUNCTION"
    if arm_count == 4:
        return "CROSS_JUNCTION" if min_gap > 45.0 and max_gap < 145.0 else "SKEWED_CROSS_OR_MULTI_ARM"
    return "MULTI_ARM_JUNCTION"


def classify_city_junction_hierarchy(arms: list[dict[str, Any]], junction_type: str) -> str:
    if junction_type == "ROUNDABOUT_LIKE":
        return "ROUNDABOUT_JUNCTION"
    if junction_type == "RAMP_MERGE":
        return "RAMP_OR_GRADE_SEPARATED"
    if junction_type in {"MULTI_ARM_JUNCTION", "SKEWED_CROSS_OR_MULTI_ARM"} or len(arms) >= 5:
        return "COMPLEX_MULTI_ARM"
    max_priority = max((int(arm["road_priority"]) for arm in arms), default=0)
    max_lanes = max((int(arm["lane_count_estimate"]) for arm in arms), default=1)
    max_width = max((float(arm["drivable_width_m"]) for arm in arms), default=0.0)
    if max_priority >= 4 or max_lanes >= 4 or max_width >= 14.0:
        return "MAJOR_ARTERIAL"
    if max_priority >= 3 or max_lanes >= 3 or max_width >= 10.0:
        return "SECONDARY_COLLECTOR"
    return "LOCAL_JUNCTION"


def lane_movement_policy(lane_count: int, hierarchy: str) -> dict[str, Any]:
    if lane_count >= 4 or hierarchy in {"MAJOR_ARTERIAL", "COMPLEX_MULTI_ARM", "RAMP_OR_GRADE_SEPARATED"}:
        return {
            "left_turn_pocket": lane_count >= 3,
            "right_turn_channel": lane_count >= 3,
            "through_lane_min": max(1, lane_count - 2),
            "policy": "dedicated_turn_pockets_when_width_allows",
        }
    if lane_count >= 2:
        return {
            "left_turn_pocket": False,
            "right_turn_channel": False,
            "through_lane_min": 1,
            "policy": "shared_left_through_and_shared_right",
        }
    return {
        "left_turn_pocket": False,
        "right_turn_channel": False,
        "through_lane_min": 1,
        "policy": "single_mixed_lane",
    }


def junction_arm_records(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    point: Point,
    members: list[tuple[Any, float]],
) -> list[dict[str, Any]]:
    arms: list[dict[str, Any]] = []
    connection_tolerance = road_gen.junction_connection_tolerance()
    member_distances: dict[Any, list[float]] = {}
    for road_idx, distance_hint in members:
        member_distances.setdefault(road_idx, []).append(float(distance_hint))

    for road_idx, distance_hints in member_distances.items():
        if road_idx not in prepared_roads.index:
            continue
        row = prepared_roads.loc[road_idx]
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue
        rule = road_gen.get_road_rule(row, rules)
        projected_distance = float(line.project(point))
        if line.distance(point) <= max(connection_tolerance, JUNCTION_BUCKET_CLUSTER_M):
            node_distance = projected_distance
        else:
            node_distance = min(distance_hints, key=lambda value: abs(float(value) - projected_distance))
        node_distance = max(0.0, min(float(line.length), node_distance))
        is_internal = connection_tolerance < node_distance < line.length - connection_tolerance
        signs = [-1.0, 1.0] if is_internal else ([1.0] if node_distance <= line.length * 0.5 else [-1.0])
        drivable_width = drivable_width_for_row(row, rule)
        source_rule = road_gen.row_section_requirement(row)
        modeled_rule = road_gen.modeled_section_requirement(row, source_rule)
        source_section = road_gen.row_section_code(row) or (
            road_gen.safe_str(source_rule.get("section_id") or source_rule.get("inferred_section"))
            if source_rule
            else None
        )
        modeled_section = (
            road_gen.safe_str(modeled_rule.get("section_id") or modeled_rule.get("inferred_section"))
            if modeled_rule
            else None
        )
        road_class = road_gen.safe_str(row.get("road_class")) or "unclassified"
        category = road_gen.section_category_name(source_rule, road_class) if source_rule else road_gen.road_class_category_name(road_class)
        components = road_gen.cross_section_components_for_row(row)
        lane_count_estimate = max(1, int(round(drivable_width / max(float(rule.lane_width), 2.8))))
        crosswalk_offset, stop_line_offset = junction_approach_offsets_for_row(row, rule)
        min_margin = max(CROSSWALK_BAND_LENGTH_M * 0.5, STOP_LINE_WIDTH_M * 0.5, 0.4)
        sample_step = max(JUNCTION_SEMANTIC_SAMPLE_DISTANCE_M, drivable_width * 0.65, 8.0)
        for sign in signs:
            sample_distance = max(0.0, min(float(line.length), node_distance + sign * sample_step))
            if abs(sample_distance - node_distance) < 0.2:
                continue
            node = line.interpolate(node_distance)
            outside = line.interpolate(sample_distance)
            direction = road_gen.unit_vector((node.x, node.y), (outside.x, outside.y))
            if road_gen.vector_length(direction) <= 0.01:
                continue
            side = "internal_before" if sign < 0.0 else "internal_after"
            if not is_internal:
                side = "from_start" if sign > 0.0 else "from_end"
            crosswalk_distance = node_distance + sign * crosswalk_offset
            stop_line_distance = node_distance + sign * stop_line_offset
            marking_fit = (
                min_margin < crosswalk_distance < line.length - min_margin
                and min_margin < stop_line_distance < line.length - min_margin
            )
            arm_id = f"{road_idx}_{'neg' if sign < 0 else 'pos'}"
            arms.append(
                {
                    "arm_id": arm_id,
                    "source_road_id": road_gen.safe_str(row.get("road_id")) or "",
                    "road_name": road_gen.safe_str(row.get("road_name")) or "",
                    "road_class": road_class,
                    "category": category,
                    "source_section_code": source_section,
                    "modeled_section_code": modeled_section,
                    "road_priority": int(road_gen.road_priority(row)),
                    "node_distance_m": round(float(node_distance), 3),
                    "position_ratio": round(float(node_distance / line.length), 4) if line.length > 0 else 0.0,
                    "approach_side": side,
                    "direction_out": (round(float(direction[0]), 6), round(float(direction[1]), 6)),
                    "bearing_out_deg": round(vector_angle_deg(direction), 3),
                    "modeled_width_m": round(float(road_gen.component_total_width(components)), 3),
                    "drivable_width_m": round(float(drivable_width), 3),
                    "lane_count_estimate": lane_count_estimate,
                    "lane_width_m": round(float(rule.lane_width), 3),
                    "marked_approach_fit": bool(marking_fit),
                    "crosswalk_distance_m": round(float(crosswalk_distance), 3) if marking_fit else None,
                    "stop_line_distance_m": round(float(stop_line_distance), 3) if marking_fit else None,
                }
            )
    arms.sort(key=lambda item: item["bearing_out_deg"])
    return arms


def build_junction_movements(junction_id: str, arms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    movements = []
    for from_arm in arms:
        inbound = (-float(from_arm["direction_out"][0]), -float(from_arm["direction_out"][1]))
        for to_arm in arms:
            if from_arm["arm_id"] == to_arm["arm_id"]:
                continue
            turn_angle = signed_angle_deg(inbound, to_arm["direction_out"])
            movement_type = turn_movement_type(turn_angle)
            movements.append(
                {
                    "movement_id": f"{junction_id}_{from_arm['arm_id']}_to_{to_arm['arm_id']}",
                    "from_arm_id": from_arm["arm_id"],
                    "to_arm_id": to_arm["arm_id"],
                    "movement_type": movement_type,
                    "signed_turn_angle_deg": round(float(turn_angle), 3),
                    "allowed_by_default": movement_type != "u_turn",
                }
            )
    return movements


def build_city_junction_semantic_records(prepared_roads: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    if prepared_roads.empty:
        return []
    rules = road_gen.load_rules()
    records = []
    for idx, bucket in enumerate(junction_point_buckets(prepared_roads)):
        arms = junction_arm_records(prepared_roads, rules, bucket["point"], bucket["members"])
        if len(arms) < 2:
            continue
        junction_id = f"J{idx:04d}"
        junction_type = classify_city_junction_type(arms)
        hierarchy = classify_city_junction_hierarchy(arms, junction_type)
        design_profile = evaluate_junction_design_options(arms, junction_type, hierarchy)
        selected_design = design_profile["selected_design_option"]
        for arm in arms:
            arm["role"] = "major" if int(arm["road_priority"]) == max(int(item["road_priority"]) for item in arms) else "minor"
            arm["lane_movement_policy"] = lane_movement_policy(int(arm["lane_count_estimate"]), hierarchy)
        movements = build_junction_movements(junction_id, arms)
        allowed_movements = [movement for movement in movements if movement["allowed_by_default"]]
        type_counts = Counter(movement["movement_type"] for movement in movements)
        connected_road_ids = sorted({arm["source_road_id"] for arm in arms if arm["source_road_id"]})
        records.append(
            {
                "junction_id": junction_id,
                "junction_type": junction_type,
                "junction_hierarchy": hierarchy,
                "surface_strategy": selected_design.get("option_id", "clustered_rounded_conflict_area_with_clipped_approaches"),
                "source_node_summary": design_profile["source_node_summary"],
                "candidate_design_options": design_profile["candidate_design_options"],
                "selected_design_option": selected_design,
                "design_references": design_profile["design_references"],
                "center_local": {
                    "x": round(float(bucket["point"].x), 3),
                    "y": round(float(bucket["point"].y), 3),
                    "z": 0.0,
                },
                "connected_road_count": len(connected_road_ids),
                "arm_count": len(arms),
                "marked_approach_count": sum(1 for arm in arms if arm["marked_approach_fit"]),
                "connected_road_ids": connected_road_ids,
                "road_class_counts": dict(Counter(arm["road_class"] for arm in arms)),
                "section_counts": dict(Counter(arm["modeled_section_code"] or "unknown" for arm in arms)),
                "angular_gaps_deg": angular_gaps_deg([arm["direction_out"] for arm in arms]),
                "max_drivable_width_m": round(max(float(arm["drivable_width_m"]) for arm in arms), 3),
                "max_lane_count_estimate": max(int(arm["lane_count_estimate"]) for arm in arms),
                "arms": arms,
                "movements": movements,
                "movement_summary": {
                    "movement_count": len(movements),
                    "allowed_movement_count": len(allowed_movements),
                    "movement_type_counts": dict(type_counts),
                },
                "quality_flags": {
                    "topology_complete": junction_type != "UNKNOWN" and len(arms) >= 2,
                    "movement_semantic_complete": len(allowed_movements) > 0,
                    "has_marked_approach": any(arm["marked_approach_fit"] for arm in arms),
                    "has_major_minor_role": any(arm["role"] == "major" for arm in arms),
                    "design_option_selected": bool(selected_design.get("option_id")),
                    "design_review_required": bool(selected_design.get("review_required")),
                },
            }
        )
    return records


def build_city_junction_semantic(prepared_roads: gpd.GeoDataFrame, origin: tuple[float, float]) -> dict[str, Any]:
    records = build_city_junction_semantic_records(prepared_roads)
    for record in records:
        local = record["center_local"]
        record["center_global"] = {
            "x": round(float(local["x"] + origin[0]), 3),
            "y": round(float(local["y"] + origin[1]), 3),
            "z": local["z"],
        }
    return {
        "project": "cim_road_poc",
        "model": "cim_city_junctions",
        "unit": "meter",
        "coordinate": {
            "model_crs": TARGET_CRS,
            "local_origin": {"x": origin[0], "y": origin[1], "z": 0.0},
        },
        "semantic_level": "topology_node_with_arms_movements_and_scored_design_options",
        "objects": records,
        "summary": {
            "junction_count": len(records),
            "junction_type_counts": dict(Counter(record["junction_type"] for record in records)),
            "junction_hierarchy_counts": dict(Counter(record["junction_hierarchy"] for record in records)),
            "selected_design_option_counts": dict(
                Counter(record.get("selected_design_option", {}).get("option_id", "unknown") for record in records)
            ),
            "design_review_required_count": sum(
                1 for record in records if record.get("selected_design_option", {}).get("review_required")
            ),
            "total_arm_count": sum(int(record["arm_count"]) for record in records),
            "total_allowed_movement_count": sum(int(record["movement_summary"]["allowed_movement_count"]) for record in records),
        },
    }


def write_city_junction_semantic(prepared_roads: gpd.GeoDataFrame, origin: tuple[float, float]) -> dict[str, Any]:
    semantic = build_city_junction_semantic(prepared_roads, origin)
    CITY_JUNCTION_SEMANTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CITY_JUNCTION_SEMANTIC_PATH.open("w", encoding="utf-8") as f:
        json.dump(semantic, f, ensure_ascii=False, indent=2)
    return semantic


def score_ratio(success_count: int, total_count: int) -> float:
    if total_count <= 0:
        return 1.0
    return max(0.0, min(1.0, success_count / total_count))


def model_quality_grade(score: float) -> str:
    if score >= 90.0:
        return "A"
    if score >= 80.0:
        return "B"
    if score >= 70.0:
        return "C"
    return "D"


def build_city_road_model_score(prepared_roads: gpd.GeoDataFrame, road_meshes: dict[str, trimesh.Trimesh]) -> dict[str, Any]:
    records = [road_cross_section_record(row) for _, row in prepared_roads.iterrows()]
    total = len(records)
    symmetric_count = sum(1 for record in records if record["modeled_is_symmetric"])
    width_match_count = sum(
        1
        for record in records
        if record["width_error_m"] is None or abs(float(record["width_error_m"])) <= 0.1
    )
    section_fit_count = sum(1 for record in records if record["source_section_code"] and record["modeled_section_code"])
    complete_count = sum(1 for record in records if not record["missing_required_components"])
    semantic_complete_count = sum(
        1
        for record in records
        if all(record.get(key) for key in ("source_road_id", "road_class", "source_section_code", "modeled_section_code"))
        and bool(record.get("modeled_cross_section_components"))
    )
    transition_curve_count = sum(int(record.get("transition_curve_count", 0) or 0) for record in records)
    roads_with_transition_curves = sum(1 for record in records if int(record.get("transition_curve_count", 0) or 0) > 0)
    roads_with_junction_clearance = sum(1 for record in records if record.get("junction_distances_m"))

    expected_layers = {
        span["layer"]
        for record in records
        for span in record["component_spans"]
    } | {"Curb", "Lane_Marking_White", "Lane_Marking_Yellow"}
    if ENABLE_ROUNDED_JUNCTION_SURFACES and roads_with_junction_clearance:
        expected_layers.add("Junction_Surface")
    if roads_with_junction_clearance:
        if GENERATE_JUNCTION_CROSSWALKS:
            expected_layers.add("Crosswalk")
        if GENERATE_JUNCTION_STOP_LINES:
            expected_layers.add("Stop_Line")
    present_layers = {
        name[:-4] if name.endswith("_All") else name
        for name, mesh in road_meshes.items()
        if mesh is not None and len(mesh.vertices) > 0
    }
    layer_hit_count = sum(1 for layer in expected_layers if layer in present_layers)

    geometry_ratio = 0.7 * score_ratio(symmetric_count, total) + 0.3 * score_ratio(width_match_count, total)
    section_ratio = score_ratio(section_fit_count, total)
    component_ratio = score_ratio(complete_count, total)
    semantic_ratio = score_ratio(semantic_complete_count, total)
    layer_ratio = score_ratio(layer_hit_count, len(expected_layers))
    curve_ratio = 1.0 if transition_curve_count == 0 or ENABLE_TRANSITION_CURVES else 0.0
    junction_ratio = 1.0 if roads_with_junction_clearance == 0 else (1.0 if "Junction_Surface" in present_layers else 0.0)
    intersection_curve_ratio = 0.55 * curve_ratio + 0.45 * junction_ratio

    category_scores = {
        "geometry_symmetry_and_width": round(MODEL_SCORE_WEIGHTS["geometry_symmetry_and_width"] * geometry_ratio, 2),
        "section_rule_fit": round(MODEL_SCORE_WEIGHTS["section_rule_fit"] * section_ratio, 2),
        "component_completeness": round(MODEL_SCORE_WEIGHTS["component_completeness"] * component_ratio, 2),
        "semantic_attribute_completeness": round(MODEL_SCORE_WEIGHTS["semantic_attribute_completeness"] * semantic_ratio, 2),
        "visual_layer_material_separation": round(MODEL_SCORE_WEIGHTS["visual_layer_material_separation"] * layer_ratio, 2),
        "intersection_curve_treatment": round(MODEL_SCORE_WEIGHTS["intersection_curve_treatment"] * intersection_curve_ratio, 2),
    }
    total_score = round(sum(category_scores.values()), 2)

    source_section_counts = Counter(record["source_section_code"] or "unknown" for record in records)
    modeled_section_counts = Counter(record["modeled_section_code"] or "unknown" for record in records)
    normalized_records = [record for record in records if record["symmetry_normalized"]]
    failed_symmetry = [record for record in records if not record["modeled_is_symmetric"]]
    incomplete_records = [record for record in records if record["missing_required_components"]]

    findings = []
    if normalized_records:
        findings.append(
            f"{len(normalized_records)} road centerlines used symmetric fallback sections to remove one-sided cross-section artifacts."
        )
    if failed_symmetry:
        findings.append(f"{len(failed_symmetry)} modeled road centerlines are still asymmetric.")
    if incomplete_records:
        findings.append(f"{len(incomplete_records)} road centerlines are missing required component types.")
    if transition_curve_count:
        findings.append(f"{transition_curve_count} sharp polyline corners were converted to transition curves.")
    if roads_with_junction_clearance and "Junction_Surface" in present_layers:
        findings.append("Nearby junction nodes are clustered into rounded surfaces, and road components are clipped back at the junction throat.")
    if roads_with_junction_clearance and {"Crosswalk", "Stop_Line"} <= present_layers:
        findings.append("Crosswalk and stop-line layers are generated for valid junction approaches.")
    if not findings:
        findings.append("All modeled road centerlines pass the current symmetry, width, component, semantic, and layer checks.")

    return {
        "project": "cim_road_poc",
        "model": "cim_city_roads",
        "score": total_score,
        "grade": model_quality_grade(total_score),
        "weights": MODEL_SCORE_WEIGHTS,
        "category_scores": category_scores,
        "criteria_basis": [
            "Municipal road BIM delivery: geometry, position, direction, color, dimensions, and non-geometric information.",
            "CIM platform guidance: standardized data resources, model cleaning/conversion, query and visualization support.",
            "Data-quality dimensions: accuracy, completeness, consistency, uniqueness, validity, and monitoring.",
        ],
        "metrics": {
            "road_centerline_count": total,
            "symmetric_modeled_count": symmetric_count,
            "width_match_count": width_match_count,
            "section_fit_count": section_fit_count,
            "component_complete_count": complete_count,
            "semantic_complete_count": semantic_complete_count,
            "transition_curve_count": transition_curve_count,
            "roads_with_transition_curves": roads_with_transition_curves,
            "roads_with_junction_marking_clearance": roads_with_junction_clearance,
            "expected_layers": sorted(expected_layers),
            "present_layers": sorted(present_layers),
            "source_section_counts": dict(source_section_counts),
            "modeled_section_counts": dict(modeled_section_counts),
            "symmetry_normalized_count": len(normalized_records),
        },
        "findings": findings,
        "failed_examples": {
            "asymmetry": failed_symmetry[:10],
            "component_completeness": incomplete_records[:10],
        },
    }


def write_city_road_model_score(
    prepared_roads: gpd.GeoDataFrame,
    road_meshes: dict[str, trimesh.Trimesh],
) -> dict[str, Any]:
    report = build_city_road_model_score(prepared_roads, road_meshes)
    CITY_ROAD_SCORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CITY_ROAD_SCORE_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def count_valid_junction_approaches(prepared_roads: gpd.GeoDataFrame) -> int:
    rules = road_gen.load_rules()
    count = 0
    for _, row in prepared_roads.iterrows():
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue
        rule = road_gen.get_road_rule(row, rules)
        crosswalk_offset, stop_line_offset = junction_approach_offsets_for_row(row, rule)
        min_margin = max(CROSSWALK_BAND_LENGTH_M * 0.5, STOP_LINE_WIDTH_M * 0.5, 0.4)
        for junction_distance in road_gen.row_junction_distances(row):
            for side_sign in (-1.0, 1.0):
                crosswalk_distance = float(junction_distance) + side_sign * crosswalk_offset
                stop_line_distance = float(junction_distance) + side_sign * stop_line_offset
                if (
                    min_margin < crosswalk_distance < line.length - min_margin
                    and min_margin < stop_line_distance < line.length - min_margin
                ):
                    count += 1
    return count


def mesh_area_m2(mesh: trimesh.Trimesh | None) -> float:
    if mesh is None or len(mesh.vertices) == 0:
        return 0.0
    return float(mesh.area)


def build_city_junction_score(
    prepared_roads: gpd.GeoDataFrame,
    road_meshes: dict[str, trimesh.Trimesh],
    junction_semantic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records = [road_cross_section_record(row) for _, row in prepared_roads.iterrows()]
    junction_buckets = junction_point_buckets(prepared_roads)
    junction_count = len(junction_buckets)
    semantic_records = (junction_semantic or {}).get("objects") or build_city_junction_semantic_records(prepared_roads)
    semantic_junction_count = len(semantic_records)
    topology_complete_count = sum(
        1
        for record in semantic_records
        if record.get("quality_flags", {}).get("topology_complete")
    )
    movement_complete_count = sum(
        1
        for record in semantic_records
        if record.get("quality_flags", {}).get("movement_semantic_complete")
    )
    type_counts = Counter(record.get("junction_type", "UNKNOWN") for record in semantic_records)
    hierarchy_counts = Counter(record.get("junction_hierarchy", "UNKNOWN") for record in semantic_records)
    design_option_counts = Counter(
        record.get("selected_design_option", {}).get("option_id", "unknown")
        for record in semantic_records
    )
    avg_design_score = (
        sum(float(record.get("selected_design_option", {}).get("score", 0.0) or 0.0) for record in semantic_records)
        / len(semantic_records)
        if semantic_records
        else 0.0
    )
    design_review_required_count = sum(
        1 for record in semantic_records if record.get("selected_design_option", {}).get("review_required")
    )
    total_arm_count = sum(int(record.get("arm_count", 0) or 0) for record in semantic_records)
    total_allowed_movement_count = sum(
        int(record.get("movement_summary", {}).get("allowed_movement_count", 0) or 0)
        for record in semantic_records
    )
    roads_with_junctions = sum(1 for record in records if record.get("junction_distances_m"))
    roads_with_clearance = sum(
        1
        for record in records
        if record.get("junction_distances_m")
        and float(record.get("junction_marking_clearance_m", 0.0)) >= JUNCTION_MARKING_CLEARANCE_M
    )
    source_expected_approaches = count_valid_junction_approaches(prepared_roads)

    present_layers = {
        name[:-4] if name.endswith("_All") else name
        for name, mesh in road_meshes.items()
        if mesh is not None and len(mesh.vertices) > 0
    }
    junction_mesh = road_meshes.get("Junction_Surface_All")
    crosswalk_mesh = road_meshes.get("Crosswalk_All")
    stop_line_mesh = road_meshes.get("Stop_Line_All")
    junction_surface_count = int((junction_mesh.metadata or {}).get("part_count", 0)) if junction_mesh else 0
    crosswalk_stripe_count = int(
        (crosswalk_mesh.metadata or {}).get(
            "junction_crosswalk_stripe_count",
            (crosswalk_mesh.metadata or {}).get("part_count", 0),
        )
    ) if crosswalk_mesh else 0
    stop_line_count = int(
        (stop_line_mesh.metadata or {}).get(
            "junction_stop_line_count",
            (stop_line_mesh.metadata or {}).get("part_count", 0),
        )
    ) if stop_line_mesh else 0
    mesh_expected_approaches = max(
        int((crosswalk_mesh.metadata or {}).get("junction_candidate_approach_count", 0)) if crosswalk_mesh else 0,
        int((stop_line_mesh.metadata or {}).get("junction_candidate_approach_count", 0)) if stop_line_mesh else 0,
    )
    expected_approaches = mesh_expected_approaches or source_expected_approaches

    surface_ratio = score_ratio(junction_surface_count, junction_count)
    clearance_ratio = score_ratio(roads_with_clearance, roads_with_junctions)
    crossing_ratio = 1.0 if expected_approaches == 0 else min(1.0, crosswalk_stripe_count / max(1, expected_approaches * 3))
    stop_ratio = score_ratio(stop_line_count, expected_approaches)
    expected_layers = {"Junction_Surface", "Lane_Marking_White", "Lane_Marking_Yellow"}
    if expected_approaches:
        expected_layers |= {"Crosswalk", "Stop_Line"}
    layer_ratio = score_ratio(sum(1 for layer in expected_layers if layer in present_layers), len(expected_layers))
    light_present = any(layer.startswith("Street_Light") for layer in present_layers)
    tree_present = any(layer.startswith("Tree") for layer in present_layers)
    asset_ratio = 1.0 if not GENERATE_ROAD_ASSETS else (0.5 if light_present else 0.0) + (0.5 if tree_present else 0.0)
    topology_ratio = (
        score_ratio(semantic_junction_count, junction_count)
        * score_ratio(topology_complete_count, semantic_junction_count)
    )
    movement_ratio = score_ratio(movement_complete_count, semantic_junction_count)
    semantic_ratio = (
        score_ratio(semantic_junction_count, junction_count)
        if junction_count
        else 1.0
    )

    category_ratios = {
        "junction_surface_continuity": surface_ratio,
        "approach_marking_clearance": clearance_ratio,
        "pedestrian_crossing": crossing_ratio,
        "stop_control_marking": stop_ratio,
        "topology_semantic_completeness": topology_ratio,
        "lane_movement_semantic": movement_ratio,
        "asset_visibility_clearance": asset_ratio,
        "layer_material_separation": layer_ratio,
        "semantic_traceability": semantic_ratio,
    }
    category_scores = {
        key: round(JUNCTION_SCORE_WEIGHTS[key] * value, 2)
        for key, value in category_ratios.items()
    }
    total_score = round(sum(category_scores.values()), 2)

    findings = [
        f"Detected {junction_count} clustered junction nodes from centerline topology.",
        f"Generated {junction_surface_count} rounded junction surface patches, {crosswalk_stripe_count} crosswalk stripes, and {stop_line_count} stop lines.",
        f"{roads_with_clearance}/{roads_with_junctions} roads with junctions have lane-marking clearance zones.",
        f"{semantic_junction_count} junction semantic nodes include {total_arm_count} arms and {total_allowed_movement_count} allowed movement records.",
        f"Scored {len(design_option_counts)} junction design option families; average selected-option score is {avg_design_score:.2f}.",
    ]
    if expected_approaches and stop_line_count < expected_approaches:
        findings.append(
            f"{expected_approaches - stop_line_count} short or edge approaches could not fit a complete stop-line/crosswalk set."
        )
    if {"Junction_Surface", "Crosswalk", "Stop_Line"} <= present_layers:
        findings.append("The visible junction model now includes clipped approach roads, conflict-area asphalt, pedestrian crossings, and stop control markings.")

    return {
        "project": "cim_road_poc",
        "model": "cim_city_junctions",
        "score": total_score,
        "grade": model_quality_grade(total_score),
        "weights": JUNCTION_SCORE_WEIGHTS,
        "category_scores": category_scores,
        "category_ratios": {key: round(value, 4) for key, value in category_ratios.items()},
        "criteria_basis": [
            {
                "source": "CJJ 152 / urban road intersection practice",
                "principle": "Treat intersections as dedicated conflict areas with approach throat control and clear pavement-marking organization.",
            },
            {
                "source": "NACTO Urban Street Design Guide - Intersection Design Elements",
                "url": "https://nacto.org/publication/urban-street-design-guide/intersection-design-elements/",
                "principle": "Use compact intersections, visible crossings, and curb/asset placement that keeps conflicts legible.",
            },
            {
                "source": "FHWA Signalized Intersections Informational Guide",
                "url": "https://highways.dot.gov/safety/intersection-safety/intersection-types/signalized-intersections/signalized-intersections-informational-guide",
                "principle": "Evaluate intersections through user safety, markings, channelization, and operational clarity.",
            },
            {
                "source": "GB 51038 road traffic sign and marking practice",
                "principle": "Use crosswalk and stop-line markings as independent pavement-marking elements at controlled approaches.",
            },
        ],
        "metrics": {
            "junction_count": junction_count,
            "semantic_junction_count": semantic_junction_count,
            "topology_complete_count": topology_complete_count,
            "movement_complete_count": movement_complete_count,
            "total_arm_count": total_arm_count,
            "total_allowed_movement_count": total_allowed_movement_count,
            "junction_type_counts": dict(type_counts),
            "junction_hierarchy_counts": dict(hierarchy_counts),
            "selected_design_option_counts": dict(design_option_counts),
            "average_selected_design_score": round(float(avg_design_score), 3),
            "design_review_required_count": int(design_review_required_count),
            "road_centerline_count": len(records),
            "roads_with_junction_distances": roads_with_junctions,
            "roads_with_junction_marking_clearance": roads_with_clearance,
            "expected_marked_approach_count": expected_approaches,
            "source_expected_marked_approach_count": source_expected_approaches,
            "junction_surface_count": junction_surface_count,
            "junction_surface_area_m2": round(mesh_area_m2(junction_mesh), 3),
            "crosswalk_stripe_count": crosswalk_stripe_count,
            "crosswalk_area_m2": round(mesh_area_m2(crosswalk_mesh), 3),
            "stop_line_count": stop_line_count,
            "stop_line_area_m2": round(mesh_area_m2(stop_line_mesh), 3),
            "expected_layers": sorted(expected_layers),
            "present_layers": sorted(present_layers),
            "asset_layers_present": {
                "street_lights": light_present,
                "trees": tree_present,
            },
            "settings": {
                "rounded_junction_surfaces": ENABLE_ROUNDED_JUNCTION_SURFACES,
                "crosswalk_generation": GENERATE_JUNCTION_CROSSWALKS,
                "stop_line_generation": GENERATE_JUNCTION_STOP_LINES,
                "crosswalk_band_length_m": CROSSWALK_BAND_LENGTH_M,
                "crosswalk_stripe_width_m": CROSSWALK_STRIPE_WIDTH_M,
                "crosswalk_stripe_gap_m": CROSSWALK_STRIPE_GAP_M,
                "stop_line_width_m": STOP_LINE_WIDTH_M,
                "stop_line_to_crosswalk_gap_m": STOP_LINE_TO_CROSSWALK_GAP_M,
            },
        },
        "findings": findings,
    }


def write_city_junction_score(
    prepared_roads: gpd.GeoDataFrame,
    road_meshes: dict[str, trimesh.Trimesh],
    junction_semantic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = build_city_junction_score(prepared_roads, road_meshes, junction_semantic)
    CITY_JUNCTION_SCORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CITY_JUNCTION_SCORE_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def record_marking_issue(
    issues: list[dict[str, Any]],
    issue_type: str,
    road_idx: Any,
    overhang_m: float,
    detail: dict[str, Any],
) -> None:
    if overhang_m <= 1e-6:
        return
    issues.append(
        {
            "type": issue_type,
            "road_index": str(road_idx),
            "overhang_m": round(float(overhang_m), 3),
            **detail,
        }
    )


def max_span_segment_length(spans: list[tuple[float, float]]) -> float:
    max_length = 0.0
    for start, end in spans:
        distances = marking_span_distances(start, end)
        for a, b in zip(distances, distances[1:]):
            max_length = max(max_length, float(b - a))
    return max_length


def build_city_marking_alignment_qc(prepared_roads: gpd.GeoDataFrame) -> dict[str, Any]:
    rules = road_gen.load_rules()
    checks: Counter[str] = Counter()
    issues: list[dict[str, Any]] = []
    max_overhang = 0.0
    max_solid_segment = 0.0

    for road_idx, row in prepared_roads.iterrows():
        row = row.copy()
        row.name = road_idx
        rule = road_gen.get_road_rule(row, rules)
        components = road_gen.cross_section_components_for_row(row)
        spans = component_spans(components)
        white_edge_suppression = carriageway_boundary_edge_suppression(spans)

        for component_idx, (component, left_offset, right_offset) in enumerate(spans):
            component_type = str(component.get("type", ""))
            if component_type not in LANE_MARKING_COMPONENT_TYPES:
                continue
            width = right_offset - left_offset
            if width < 3.2:
                continue
            marking_width = max(0.10, min(rule.lane_marking_width, 0.16))
            junction_distances = road_gen.row_junction_distances(row)
            junction_clearance = junction_marking_clearance_for_row(row, rule)
            solid_spans = subtract_blocked_spans(
                [(0.0, float(row.geometry.length))],
                junction_distances,
                junction_clearance,
                float(row.geometry.length),
            ) if row.geometry is not None and not row.geometry.is_empty else []
            max_solid_segment = max(max_solid_segment, max_span_segment_length(solid_spans))
            edge_specs = []
            suppressed_edges = white_edge_suppression.get(component_idx, set())
            if "left" in suppressed_edges:
                checks["suppressed_white_edge_at_double_yellow"] += 1
            else:
                edge_specs.append((left_offset + min(0.35, width * 0.15), "left_edge"))
            if "right" in suppressed_edges:
                checks["suppressed_white_edge_at_double_yellow"] += 1
            else:
                edge_specs.append((right_offset - min(0.35, width * 0.15), "right_edge"))
            for edge_offset, suffix in edge_specs:
                checks["white_edge_marking"] += 1
                overhang = max(
                    left_offset - (edge_offset - marking_width / 2.0),
                    (edge_offset + marking_width / 2.0) - right_offset,
                    0.0,
                )
                max_overhang = max(max_overhang, overhang)
                record_marking_issue(
                    issues,
                    "white_edge_outside_component",
                    road_idx,
                    overhang,
                    {
                        "edge": suffix,
                        "component_type": component_type,
                        "component_left_m": round(float(left_offset), 3),
                        "component_right_m": round(float(right_offset), 3),
                        "marking_center_offset_m": round(float(edge_offset), 3),
                    },
                )

            lane_count = max(1, int(round(width / max(rule.lane_width, 2.8))))
            if lane_count <= 1:
                continue
            lane_step = width / lane_count
            for lane_idx in range(1, lane_count):
                offset = left_offset + lane_step * lane_idx
                checks["white_dash_marking"] += 1
                overhang = max(
                    left_offset - (offset - marking_width / 2.0),
                    (offset + marking_width / 2.0) - right_offset,
                    0.0,
                )
                max_overhang = max(max_overhang, overhang)
                record_marking_issue(
                    issues,
                    "white_dash_outside_component",
                    road_idx,
                    overhang,
                    {
                        "lane_index": lane_idx,
                        "component_type": component_type,
                        "component_left_m": round(float(left_offset), 3),
                        "component_right_m": round(float(right_offset), 3),
                        "marking_center_offset_m": round(float(offset), 3),
                    },
                )

        for idx in range(len(spans) - 1):
            left_component, union_left, boundary = spans[idx]
            right_component, next_left, union_right = spans[idx + 1]
            if abs(boundary - next_left) > 0.01:
                continue
            if left_component.get("type") not in {"main_carriageway", "carriageway"}:
                continue
            if right_component.get("type") not in {"main_carriageway", "carriageway"}:
                continue
            marking_width = max(0.09, min(rule.lane_marking_width, 0.14))
            junction_distances = road_gen.row_junction_distances(row)
            junction_clearance = junction_marking_clearance_for_row(row, rule)
            solid_spans = subtract_blocked_spans(
                [(0.0, float(row.geometry.length))],
                junction_distances,
                junction_clearance,
                float(row.geometry.length),
            ) if row.geometry is not None and not row.geometry.is_empty else []
            max_solid_segment = max(max_solid_segment, max_span_segment_length(solid_spans))
            for offset in (boundary - 0.16, boundary + 0.16):
                checks["yellow_center_marking"] += 1
                overhang = max(
                    union_left - (offset - marking_width / 2.0),
                    (offset + marking_width / 2.0) - union_right,
                    0.0,
                )
                max_overhang = max(max_overhang, overhang)
                record_marking_issue(
                    issues,
                    "yellow_center_outside_carriageway_pair",
                    road_idx,
                    overhang,
                    {
                        "pair_left_m": round(float(union_left), 3),
                        "pair_right_m": round(float(union_right), 3),
                        "marking_center_offset_m": round(float(offset), 3),
                    },
                )

        road_width = drivable_width_for_row(row, rule)
        road_half_width = road_width / 2.0
        stripe_count, lateral_start, stripe_step = crosswalk_stripe_layout_for_width(road_width)
        if stripe_count <= 1:
            crosswalk_extent = CROSSWALK_STRIPE_WIDTH_M / 2.0
        else:
            left_extent = abs(lateral_start) + CROSSWALK_STRIPE_WIDTH_M / 2.0
            right_extent = abs(lateral_start + (stripe_count - 1) * stripe_step) + CROSSWALK_STRIPE_WIDTH_M / 2.0
            crosswalk_extent = max(left_extent, right_extent)
        stop_extent = junction_marking_across_width(road_width) / 2.0
        for junction_idx, _ in enumerate(road_gen.row_junction_distances(row)):
            for side_name in ("before", "after"):
                checks["crosswalk_approach"] += 1
                crosswalk_overhang = max(0.0, crosswalk_extent - road_half_width)
                max_overhang = max(max_overhang, crosswalk_overhang)
                record_marking_issue(
                    issues,
                    "crosswalk_outside_drivable_width",
                    road_idx,
                    crosswalk_overhang,
                    {
                        "junction_index": junction_idx,
                        "side": side_name,
                        "road_width_m": round(float(road_width), 3),
                        "stripe_count": stripe_count,
                    },
                )

                checks["stop_line_approach"] += 1
                stop_overhang = max(0.0, stop_extent - road_half_width)
                max_overhang = max(max_overhang, stop_overhang)
                record_marking_issue(
                    issues,
                    "stop_line_outside_drivable_width",
                    road_idx,
                    stop_overhang,
                    {
                        "junction_index": junction_idx,
                        "side": side_name,
                        "road_width_m": round(float(road_width), 3),
                    },
                )

    issue_type_counts = Counter(issue["type"] for issue in issues)
    if max_solid_segment > MARKING_SWEEP_SAMPLE_INTERVAL_M * 1.5:
        issues.append(
            {
                "type": "solid_marking_under_sampled",
                "max_segment_length_m": round(float(max_solid_segment), 3),
                "allowed_segment_length_m": round(float(MARKING_SWEEP_SAMPLE_INTERVAL_M * 1.5), 3),
            }
        )
        issue_type_counts = Counter(issue["type"] for issue in issues)
    score = 100.0 if not issues else round(max(0.0, 100.0 - min(100.0, len(issues) * 0.02 + max_overhang * 10.0)), 2)
    return {
        "project": "cim_road_poc",
        "model": "cim_city_roads",
        "score": score,
        "grade": model_quality_grade(score),
        "policy": "all pavement markings must stay within their own drivable/component lateral envelope",
        "metrics": {
            "checked_marking_count": int(sum(checks.values())),
            "check_counts": dict(checks),
            "issue_count": len(issues),
            "issue_type_counts": dict(issue_type_counts),
            "max_lateral_overhang_m": round(float(max_overhang), 3),
            "junction_marking_lateral_inset_m": JUNCTION_MARKING_LATERAL_INSET_M,
            "marking_sweep_sample_interval_m": MARKING_SWEEP_SAMPLE_INTERVAL_M,
            "max_solid_marking_segment_m": round(float(max_solid_segment), 3),
        },
        "sample_issues": issues[:50],
        "findings": [
            "White lane markings and yellow center markings are checked against their source carriageway components.",
            "Crosswalk and stop-line markings are checked against drivable road width after lateral inset.",
            "No marking overhang was detected." if not issues else f"{len(issues)} marking overhang issues were detected.",
        ],
    }


def write_city_marking_alignment_qc(prepared_roads: gpd.GeoDataFrame) -> dict[str, Any]:
    report = build_city_marking_alignment_qc(prepared_roads)
    CITY_MARKING_QC_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CITY_MARKING_QC_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def build_city_utility_pipe_semantic(
    utility_records: list[dict[str, Any]],
    origin: tuple[float, float],
) -> dict[str, Any]:
    type_counts = Counter(record["pipe_type"] for record in utility_records)
    length_by_type: dict[str, float] = {}
    for record in utility_records:
        pipe_type = record["pipe_type"]
        length_by_type[pipe_type] = length_by_type.get(pipe_type, 0.0) + float(record.get("length_m", 0.0) or 0.0)
    return {
        "schema_version": "cim_city_utility_pipes_semantic_v1",
        "source_crs": SOURCE_PROJECTED_CRS,
        "model_crs": TARGET_CRS,
        "origin_xy": [round(float(origin[0]), 3), round(float(origin[1]), 3)],
        "semantic_level": "source_pipe_feature_with_standardized_3d_profile",
        "standard_references": UTILITY_PIPE_STANDARD_REFERENCES,
        "modeling_rules": {
            pipe_type: {
                "pipe_type_zh": spec["label_zh"],
                "default_dn_mm": spec["default_dn_mm"],
                "min_dn_mm": spec["min_dn_mm"],
                "cover_depth_m": spec["cover_depth_m"],
                "min_cover_depth_m": spec["min_cover_depth_m"],
                "material_class": spec["material_class"],
                "flow_model": spec["flow_model"],
                "standards": spec["standards"],
            }
            for pipe_type, spec in UTILITY_PIPE_SPECS.items()
        },
        "diameter_attribute_search_order": list(UTILITY_DIAMETER_FIELDS),
        "object_count": len(utility_records),
        "type_counts": dict(type_counts),
        "length_m_by_type": {pipe_type: round(length, 3) for pipe_type, length in sorted(length_by_type.items())},
        "objects": utility_records,
    }


def write_city_utility_pipe_semantic(
    utility_records: list[dict[str, Any]],
    origin: tuple[float, float],
) -> dict[str, Any]:
    semantic = build_city_utility_pipe_semantic(utility_records, origin)
    CITY_UTILITY_SEMANTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CITY_UTILITY_SEMANTIC_PATH.open("w", encoding="utf-8") as f:
        json.dump(semantic, f, ensure_ascii=False, indent=2)
    return semantic


def build_city_utility_pipe_qc(utility_records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(utility_records)
    type_counts = Counter(record["pipe_type"] for record in utility_records)
    diameter_source_counts = Counter(record["diameter_source"] for record in utility_records)
    fallback_diameter_count = sum(
        1 for record in utility_records if str(record.get("diameter_source", "")).startswith("fallback")
    )
    attribute_diameter_count = sum(
        1 for record in utility_records if str(record.get("diameter_source", "")).startswith("attribute:")
    )
    cover_ok_count = sum(
        1 for record in utility_records if record.get("quality_flags", {}).get("cover_depth_meets_minimum")
    )
    diameter_ok_count = sum(
        1 for record in utility_records if record.get("quality_flags", {}).get("diameter_meets_minimum")
    )
    semantic_ok_count = sum(
        1
        for record in utility_records
        if record.get("dn_mm")
        and record.get("cover_depth_m")
        and record.get("source_kind")
        and record.get("geometry_segment_count", 0) > 0
    )
    total_length_m_by_type: dict[str, float] = {}
    center_depths_by_type: dict[str, list[float]] = {}
    cover_depths_by_type: dict[str, list[float]] = {}
    dn_by_type: dict[str, list[int]] = {}
    for record in utility_records:
        pipe_type = record["pipe_type"]
        total_length_m_by_type[pipe_type] = total_length_m_by_type.get(pipe_type, 0.0) + float(
            record.get("length_m", 0.0) or 0.0
        )
        center_depths_by_type.setdefault(pipe_type, []).append(float(record.get("center_depth_m", 0.0) or 0.0))
        cover_depths_by_type.setdefault(pipe_type, []).append(float(record.get("cover_depth_m", 0.0) or 0.0))
        dn_by_type.setdefault(pipe_type, []).append(int(record.get("dn_mm", 0) or 0))

    def ratio(count: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return max(0.0, min(1.0, count / denominator))

    water_depths = center_depths_by_type.get("Water", [])
    sewer_depths = center_depths_by_type.get("Sewer", [])
    vertical_order_ok = bool(
        water_depths and sewer_depths and min(sewer_depths) > max(water_depths)
    )
    score_components = {
        "geometry_generated": 25.0 if total > 0 else 0.0,
        "semantic_traceability": round(20.0 * ratio(semantic_ok_count, total), 2),
        "diameter_assignment": round(20.0 * ratio(diameter_ok_count, total), 2),
        "cover_depth_compliance": round(20.0 * ratio(cover_ok_count, total), 2),
        "water_sewer_vertical_order": 15.0 if vertical_order_ok or not (water_depths and sewer_depths) else 0.0,
    }
    score = round(sum(score_components.values()), 2)
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"

    findings = []
    if total:
        findings.append(
            f"Generated {total} utility pipe semantic records across {len(type_counts)} pipe types."
        )
        findings.append(
            f"Diameter sources: {attribute_diameter_count} from attributes, {fallback_diameter_count} from explicit standard defaults."
        )
    if vertical_order_ok:
        findings.append("Sewer pipes are modeled below water pipes in the standardized vertical profile.")
    elif water_depths and sewer_depths:
        findings.append("Water/sewer vertical order needs review because standardized center depths overlap.")
    if fallback_diameter_count:
        findings.append("Water and sewer source layers do not expose diameter fields; fallback DN values are documented per object.")

    def metric_range(values: list[float]) -> dict[str, float] | None:
        if not values:
            return None
        return {"min": round(min(values), 3), "max": round(max(values), 3)}

    def int_metric_range(values: list[int]) -> dict[str, int] | None:
        if not values:
            return None
        return {"min": int(min(values)), "max": int(max(values))}

    return {
        "score": score,
        "grade": grade,
        "score_components": score_components,
        "pipe_feature_count": total,
        "pipe_feature_count_by_type": dict(type_counts),
        "total_length_m_by_type": {
            pipe_type: round(length, 3) for pipe_type, length in sorted(total_length_m_by_type.items())
        },
        "diameter_source_counts": dict(diameter_source_counts),
        "attribute_diameter_count": attribute_diameter_count,
        "fallback_diameter_count": fallback_diameter_count,
        "cover_depth_compliant_count": cover_ok_count,
        "diameter_compliant_count": diameter_ok_count,
        "center_depth_range_m_by_type": {
            pipe_type: metric_range(values) for pipe_type, values in sorted(center_depths_by_type.items())
        },
        "cover_depth_range_m_by_type": {
            pipe_type: metric_range(values) for pipe_type, values in sorted(cover_depths_by_type.items())
        },
        "dn_range_mm_by_type": {
            pipe_type: int_metric_range(values) for pipe_type, values in sorted(dn_by_type.items())
        },
        "water_sewer_vertical_order_ok": vertical_order_ok,
        "standards_used": UTILITY_PIPE_STANDARD_REFERENCES,
        "findings": findings,
    }


def write_city_utility_pipe_qc(utility_records: list[dict[str, Any]]) -> dict[str, Any]:
    report = build_city_utility_pipe_qc(utility_records)
    CITY_UTILITY_QC_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CITY_UTILITY_QC_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def compose_city_scene(modules: dict[str, dict[str, trimesh.Trimesh]]) -> trimesh.Scene:
    scene = trimesh.Scene()
    for meshes in modules.values():
        for name, mesh in meshes.items():
            scene.add_geometry(mesh.copy(), node_name=name, geom_name=name)
    return scene


def export_module_scenes(modules: dict[str, dict[str, trimesh.Trimesh]]) -> dict[str, Path | None]:
    exported = {}
    MODULE_OBJ_DIR.mkdir(parents=True, exist_ok=True)
    for module_name, meshes in modules.items():
        path = MODULE_OBJ_PATHS[module_name]
        if not meshes:
            if path.exists():
                path.unlink()
            exported[module_name] = None
            continue
        scene_from_meshes(meshes).export(path)
        exported[module_name] = path
    return exported


def main() -> None:
    print("[1/6] Loading data/Data layers...", flush=True)
    roads = load_layer(road_gen.RAW_ROADS)
    buildings = load_layer(BUILDINGS_PATH)
    bus_stops = load_layer(BUS_STOPS_PATH)
    railways = load_layer(RAIL_LINES_PATH)
    railway_stations = load_layer(RAIL_STATIONS_PATH)
    water_lines = load_layer(WATER_LINES_PATH)
    sewer_lines = load_layer(SEWER_LINES_PATH)
    gas_lines = load_layer(GAS_LINES_PATH)
    transport = combine_layers(bus_stops, railway_stations)

    print("[2/6] Localizing layers...", flush=True)
    origin = compute_origin(roads, buildings, transport, railways, water_lines, sewer_lines, gas_lines)
    roads = localize(roads, origin)
    buildings = localize(buildings, origin)
    transport = localize(transport, origin)
    railways = localize(railways, origin)
    water_lines = localize(water_lines, origin)
    sewer_lines = localize(sewer_lines, origin)
    gas_lines = localize(gas_lines, origin)

    print("[3/6] Building transit meshes...", flush=True)
    subway_station_meshes, bus_stop_meshes = build_transit_node_meshes(transport)
    utility_layers = [
        {"pipe_type": "Water", "layer": water_lines},
        {"pipe_type": "Sewer", "layer": sewer_lines},
        {"pipe_type": "Gas", "layer": gas_lines},
    ]
    print("[4/6] Building road cross-section meshes...", flush=True)
    prepared_roads_for_qc = prepare_roads_for_surfaces(roads)
    road_meshes = build_road_surface_meshes(prepared_roads_for_qc)
    print("[5/6] Building optional rail and utility meshes...", flush=True)
    utility_meshes, utility_records = (
        build_utility_pipe_meshes(utility_layers, roads) if GENERATE_UTILITY_PIPES else ({}, [])
    )
    modules = {
        "roads": road_meshes,
        "buildings": build_building_meshes(buildings),
        "subway_tunnels": build_subway_tunnel_meshes(railways) if GENERATE_SUBWAY_TUNNELS else {},
        "subway_stations": subway_station_meshes,
        "bus_stops": bus_stop_meshes,
        "utility_pipes": utility_meshes,
    }
    stats = {module_name: len(meshes) for module_name, meshes in modules.items()}

    print("[6/6] Exporting OBJ modules...", flush=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    scene = compose_city_scene(modules)
    scene.export(OUT_PATH)
    module_outputs = export_module_scenes(modules)
    road_semantic = write_city_road_semantic(prepared_roads_for_qc, origin)
    junction_semantic = write_city_junction_semantic(prepared_roads_for_qc, origin)
    road_score = write_city_road_model_score(prepared_roads_for_qc, road_meshes)
    junction_score = write_city_junction_score(prepared_roads_for_qc, road_meshes, junction_semantic)
    marking_qc = write_city_marking_alignment_qc(prepared_roads_for_qc)
    utility_semantic = write_city_utility_pipe_semantic(utility_records, origin)
    utility_qc = write_city_utility_pipe_qc(utility_records)

    print("CIM city OBJ generated:")
    print(f"- {OUT_PATH}")
    print("- module OBJ outputs:")
    for module_name, path in module_outputs.items():
        output_text = str(path) if path is not None else "skipped (no geometry)"
        print(f"  - {module_name}: {output_text}")
    for key, value in stats.items():
        print(f"- {key}: {value}")
    print(f"- road semantic objects: {len(road_semantic['objects'])} -> {CITY_ROAD_SEMANTIC_PATH}")
    print(f"- junction semantic objects: {len(junction_semantic['objects'])} -> {CITY_JUNCTION_SEMANTIC_PATH}")
    print(f"- utility semantic objects: {len(utility_semantic['objects'])} -> {CITY_UTILITY_SEMANTIC_PATH}")
    print(f"- road model score: {road_score['score']} ({road_score['grade']}) -> {CITY_ROAD_SCORE_PATH}")
    print(f"- junction score: {junction_score['score']} ({junction_score['grade']}) -> {CITY_JUNCTION_SCORE_PATH}")
    print(f"- marking alignment qc: {marking_qc['score']} ({marking_qc['grade']}) -> {CITY_MARKING_QC_PATH}")
    print(f"- utility pipe qc: {utility_qc['score']} ({utility_qc['grade']}) -> {CITY_UTILITY_QC_PATH}")


if __name__ == "__main__":
    main()
