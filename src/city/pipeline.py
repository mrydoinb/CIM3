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

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
import json
import math
import os
import re
from typing import Iterable, Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, MultiLineString, Point, MultiPoint, Polygon, MultiPolygon, GeometryCollection
from shapely.affinity import translate as shapely_translate
from shapely.ops import linemerge, nearest_points, substring, unary_union
from shapely.strtree import STRtree
import trimesh

from road import generator as road_gen


from city.geodata import (
    build_gis_basemap_meshes,
    combine_layers,
    compute_origin,
    load_layer,
    localize,
    localized_bounds,
)
from city.junction_debug import (
    CITY_JUNCTION_DEBUG_MANIFEST_PATH,
    CITY_JUNCTION_DEBUG_OBJ_DIR,
    write_junction_debug_models,
)
from city.mesh_utils import (
    combine_mesh_list,
    cylinder_between,
    iter_lines,
    iter_polygons,
    json_safe_value,
    make_box,
    offset_segment,
    representative_point,
    safe_float,
    scene_from_meshes,
)
from city.utility_pipes import (
    CITY_UTILITY_QC_PATH,
    CITY_UTILITY_SEMANTIC_PATH,
    UTILITY_DIAMETER_FIELDS,
    UTILITY_DIAMETER_PATTERNS,
    UTILITY_PIPE_SPECS,
    UTILITY_PIPE_STANDARD_REFERENCES,
    UTILITY_SOURCE_ATTRIBUTE_FIELDS,
    build_city_utility_pipe_qc,
    build_city_utility_pipe_semantic,
    build_utility_pipe_meshes,
    utility_mesh_attributes_path_for_level,
    utility_obj_path_for_level,
    utility_qc_path_for_level,
    utility_semantic_path_for_level,
    validate_utility_subway_vertical_clearance,
    write_city_utility_pipe_qc,
    write_city_utility_pipe_mesh_attributes,
    write_city_utility_pipe_semantic,
)

ROOT = Path(__file__).resolve().parents[2]
road_gen.SWEEP_SAMPLE_INTERVAL_M = 20.0

RAW_DIR = road_gen.RAW_SOURCE_DIR
OUT_PATH = ROOT / "output" / "obj" / "cim_city.obj"
MODULE_OBJ_DIR = ROOT / "output" / "obj" / "modules"
MODULE_OBJ_PATHS = {
    "roads": MODULE_OBJ_DIR / "cim4_city_roads.obj",
    "buildings": MODULE_OBJ_DIR / "cim_city_buildings.obj",
    "subway_tunnels": MODULE_OBJ_DIR / "cim_city_subway_tunnels.obj",
    "subway_stations": MODULE_OBJ_DIR / "cim_city_subway_stations.obj",
    "bus_stops": MODULE_OBJ_DIR / "cim_city_bus_stops.obj",
    "utility_pipes": MODULE_OBJ_DIR / "cim_city_utility_pipes.obj",
}
CITY_ROAD_SEMANTIC_PATH = ROOT / "output" / "semantic" / "cim4_city_roads_semantic.json"
CITY_ROAD_CLASSIFICATION_PATH = ROOT / "output" / "semantic" / "cim4_city_roads_classification.json"
CITY_ROAD_MESH_ATTRIBUTES_PATH = ROOT / "output" / "semantic" / "cim4_city_roads_mesh_attributes.json"
CITY_JUNCTION_SEMANTIC_PATH = ROOT / "output" / "semantic" / "cim4_city_junctions_semantic.json"
CITY_UTILITY_SEMANTIC_PATH = ROOT / "output" / "semantic" / "cim_city_utility_pipes_semantic.json"
CITY_ROAD_SCORE_PATH = ROOT / "output" / "qc_report" / "cim_city_roads_model_score.json"
CITY_JUNCTION_SCORE_PATH = ROOT / "output" / "qc_report" / "cim_city_junction_score.json"
CITY_MARKING_QC_PATH = ROOT / "output" / "qc_report" / "cim_city_marking_alignment_qc.json"
CITY_UTILITY_QC_PATH = ROOT / "output" / "qc_report" / "cim_city_utility_pipe_qc.json"
TARGET_CRS = road_gen.TARGET_CRS
SOURCE_PROJECTED_CRS = "EPSG:4547"
ROAD_SURFACE_BASE_Z_M = 3.0


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
SEWER_NODE_POINTS_PATH = first_existing_path(["**/污水管点.shp", "**/*管点.shp"])
PROJECTED_ROAD_REFERENCE_PATH = first_existing_path(["road50kms/*.shp", "**/道路修改50kms.shp", "**/*道路修改*.shp"])


def _bounds_size(bounds: np.ndarray) -> tuple[float, float]:
    return float(bounds[2] - bounds[0]), float(bounds[3] - bounds[1])


def road_projected_translation_xy(roads: gpd.GeoDataFrame) -> tuple[float, float] | None:
    if roads.empty:
        return None
    bounds = roads.total_bounds
    if float(bounds[0]) > 100000.0 and float(bounds[1]) > 100000.0:
        return None
    if PROJECTED_ROAD_REFERENCE_PATH is None or PROJECTED_ROAD_REFERENCE_PATH == road_gen.RAW_ROADS:
        return None

    reference = load_layer(PROJECTED_ROAD_REFERENCE_PATH)
    if reference.empty:
        return None

    width, height = _bounds_size(bounds)
    ref_width, ref_height = _bounds_size(reference.total_bounds)
    tolerance = max(5.0, 0.02 * max(width, height, ref_width, ref_height))
    if abs(width - ref_width) > tolerance or abs(height - ref_height) > tolerance:
        return None

    return float(reference.total_bounds[0] - bounds[0]), float(reference.total_bounds[1] - bounds[1])


def align_layer_to_road_coordinates(
    layer: gpd.GeoDataFrame,
    roads: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    if layer.empty:
        return layer
    translation = road_projected_translation_xy(roads)
    if translation is None:
        return layer
    bounds = layer.total_bounds
    if float(bounds[0]) < 100000.0 and float(bounds[1]) < 100000.0:
        return layer
    xoff, yoff = translation
    result = layer.copy()
    result["geometry"] = result.geometry.apply(
        lambda geom: None
        if geom is None or geom.is_empty
        else shapely_translate(geom, xoff=-xoff, yoff=-yoff)
    )
    result = result[result.geometry.notna()].copy()
    result.attrs["coordinate_normalization"] = "translated_projected_to_road_local"
    result.attrs["coordinate_translation_xy_m"] = (-xoff, -yoff)
    result.attrs["road_coordinate_reference_path"] = str(PROJECTED_ROAD_REFERENCE_PATH)
    return result


def align_layers_to_road_coordinates(
    roads: gpd.GeoDataFrame,
    *layers: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, ...]:
    translation = road_projected_translation_xy(roads)
    if translation is not None:
        xoff, yoff = translation
        print(
            "[coordinates] Using road local coordinates as model basis; "
            f"projected layers are shifted by xoff={-xoff:.3f}, yoff={-yoff:.3f}",
            flush=True,
        )
    return tuple(align_layer_to_road_coordinates(layer, roads) for layer in layers)


def normalize_roads_to_projected(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if roads.empty:
        return roads
    translation = road_projected_translation_xy(roads)
    if translation is None:
        roads.attrs["coordinate_normalization"] = "already_projected_or_without_reference"
        return roads
    xoff, yoff = translation
    result = roads.copy()
    result["geometry"] = result.geometry.apply(
        lambda geom: None
        if geom is None or geom.is_empty
        else shapely_translate(geom, xoff=xoff, yoff=yoff)
    )
    result = result[result.geometry.notna()].copy()
    result.attrs["coordinate_normalization"] = "translated_local_to_projected"
    result.attrs["coordinate_translation_xy_m"] = (xoff, yoff)
    result.attrs["coordinate_reference_path"] = str(PROJECTED_ROAD_REFERENCE_PATH)
    print(
        "[coordinates] Translated local road coordinates to projected CRS "
        f"using {PROJECTED_ROAD_REFERENCE_PATH}: xoff={xoff:.3f}, yoff={yoff:.3f}",
        flush=True,
    )
    return result


def compute_shared_model_origin(
    roads: gpd.GeoDataFrame | None = None,
    railways: gpd.GeoDataFrame | None = None,
    water_lines: gpd.GeoDataFrame | None = None,
    sewer_lines: gpd.GeoDataFrame | None = None,
    gas_lines: gpd.GeoDataFrame | None = None,
    sewer_points: gpd.GeoDataFrame | None = None,
) -> tuple[float, float]:
    layers = [
        layer
        for layer in (roads, railways, water_lines, sewer_lines, gas_lines, sewer_points)
        if layer is not None and not layer.empty
    ]
    if layers:
        return compute_origin(*layers)
    return (0.0, 0.0)


def compute_road_model_origin(roads: gpd.GeoDataFrame) -> tuple[float, float]:
    return compute_origin(roads)

BUILDING_DEFAULT_HEIGHT_M = 12.0
BUILDING_LEVEL_HEIGHT_M = 3.2
SUBWAY_TUNNEL_RADIUS_M = 3.010074
SUBWAY_TUNNEL_LINING_THICKNESS_M = 0.35
SUBWAY_TUNNEL_OUTER_RADIUS_M = SUBWAY_TUNNEL_RADIUS_M + SUBWAY_TUNNEL_LINING_THICKNESS_M
SUBWAY_TUNNEL_SWEEP_MAX_SEGMENT_M = 2.0
SUBWAY_TUNNEL_MIN_BEND_RADIUS_M = SUBWAY_TUNNEL_OUTER_RADIUS_M + 0.50
SUBWAY_TUNNEL_DEPTH_M = -14.0
URBAN_RAIL_TUNNEL_DEPTH_M = -12.0
RAILWAY_TUNNEL_DEPTH_M = -8.0
HIGH_SPEED_RAIL_TUNNEL_DEPTH_M = -6.0
SUBWAY_TUNNEL_VERTICAL_CLEARANCE_M = 11.0
SUBWAY_TUNNEL_INTERSECTION_TOLERANCE_M = 0.5
SUBWAY_TUNNEL_OVERLAP_CLEARANCE_M = 1.0
SUBWAY_CORRIDOR_MERGE_DISTANCE_M = 10.6
SUBWAY_CORRIDOR_NEAR_OVERLAP_RATIO = 0.25
SUBWAY_PARALLEL_SHIFT_TRIGGER_M = 18.0
SUBWAY_PARALLEL_LATERAL_SPACING_M = 18.0
SUBWAY_SAME_LINE_TRACK_SPACING_M = 36.0
SUBWAY_LATERAL_TRANSLATION_LINE_TOKENS = ("1号线", "一号线", "11号线", "十一号线")
SUBWAY_PAIR_AXIS_LATERAL_SPACING_M = 44.0
SUBWAY_LINE_11_PAIR_AXIS_LATERAL_SPACING_M = 52.0
SUBWAY_SOURCE_PART_ENDPOINT_JOIN_TOLERANCE_M = 5.0
SUBWAY_SOURCE_PART_ENDPOINT_TANGENT_DOT_MAX = -0.50
SUBWAY_TRACK_GAUGE_M = 1.435
SUBWAY_SLEEPER_INTERVAL_M = 0.6
SUBWAY_CABLE_BRACKET_INTERVAL_M = 8.0
SUBWAY_LINING_RING_INTERVAL_M = 6.0
SUBWAY_LINING_RING_WIDTH_M = 0.18
SUBWAY_LINING_PANEL_SEAM_RADIUS_M = 0.018
SUBWAY_LINING_BOLT_RADIUS_M = 0.055
SUBWAY_LINING_BOLT_INTERVAL_M = 12.0
SUBWAY_LIGHTING_INTERVAL_M = 18.0
SUBWAY_EVACUATION_SIGN_INTERVAL_M = 36.0
SUBWAY_CONTACT_HANGER_INTERVAL_M = 12.0
SUBWAY_REFERENCE_BENCHMARK_DIMENSIONS_M = (23.147, 88.787, 6.009)
SUBWAY_REFERENCE_COMPONENTS_41: tuple[dict[str, Any], ...] = (
    {"component": "Ref01_Guardrail", "reference_mesh": "Mesh.001", "reference_dimensions_m": (17.261, 90.190, 0.110), "system": "guardrail"},
    {"component": "Ref02_Aggregate_Base", "reference_mesh": "Mesh.002", "reference_dimensions_m": (22.178, 88.517, 3.947), "system": "track_base"},
    {"component": "Ref03_Rubber_Isolation", "reference_mesh": "Mesh.003", "reference_dimensions_m": (31.091, 974.494, 4.200), "system": "rail_elastic_layer"},
    {"component": "Ref04_Concrete_Segment", "reference_mesh": "Mesh.004", "reference_dimensions_m": (23.147, 88.787, 6.009), "system": "segmental_lining"},
    {"component": "Ref05_Steel_Plate", "reference_mesh": "Mesh.005", "reference_dimensions_m": (11.947, 87.092, 0.210), "system": "segment_connector"},
    {"component": "Ref06_Seal_Ring", "reference_mesh": "Mesh.006", "reference_dimensions_m": (11.937, 87.092, 0.044), "system": "segment_connector"},
    {"component": "Ref07_Bolt", "reference_mesh": "Mesh.007", "reference_dimensions_m": (11.805, 87.009, 0.168), "system": "segment_connector"},
    {"component": "Ref08_Platform_Main", "reference_mesh": "Mesh.008", "reference_dimensions_m": (16.505, 1.681, 1.203), "system": "evacuation_platform"},
    {"component": "Ref09_Platform_Support", "reference_mesh": "Mesh.009", "reference_dimensions_m": (17.092, 1.790, 2.232), "system": "evacuation_platform"},
    {"component": "Ref10_Platform_Edge_Strip", "reference_mesh": "Mesh.010", "reference_dimensions_m": (16.791, 0.094, 0.011), "system": "evacuation_platform"},
    {"component": "Ref11_Platform_Steel_Frame", "reference_mesh": "Mesh.011", "reference_dimensions_m": (13.561, 85.772, 0.435), "system": "evacuation_platform"},
    {"component": "Ref12_Platform_Concrete_Panel", "reference_mesh": "Mesh.012", "reference_dimensions_m": (13.548, 86.992, 0.073), "system": "evacuation_platform"},
    {"component": "Ref13_Platform_Bracket", "reference_mesh": "Mesh.013", "reference_dimensions_m": (13.105, 85.716, 0.354), "system": "evacuation_platform"},
    {"component": "Ref14_Contact_Rail", "reference_mesh": "Mesh.014", "reference_dimensions_m": (17.925, 88.156, 0.073), "system": "contact_network"},
    {"component": "Ref15_Contact_Hanger", "reference_mesh": "Mesh.015", "reference_dimensions_m": (17.975, 88.106, 0.351), "system": "contact_network"},
    {"component": "Ref16_Contact_Clamp", "reference_mesh": "Mesh.016", "reference_dimensions_m": (17.192, 88.064, 0.021), "system": "contact_network"},
    {"component": "Ref17_High_Voltage_Cable_Bracket", "reference_mesh": "Mesh.017", "reference_dimensions_m": (14.024, 87.068, 3.724), "system": "power_cable"},
    {"component": "Ref18_Comm_Cable_Bracket_A", "reference_mesh": "Mesh.018", "reference_dimensions_m": (22.419, 87.073, 2.282), "system": "communication_cable"},
    {"component": "Ref19_Comm_Cable_Bracket_B", "reference_mesh": "Mesh.019", "reference_dimensions_m": (22.161, 87.043, 2.163), "system": "communication_cable"},
    {"component": "Ref20_Leakage_Cable_A", "reference_mesh": "Mesh.020", "reference_dimensions_m": (13.564, 80.624, 0.156), "system": "leakage_cable"},
    {"component": "Ref21_Leakage_Cable_B", "reference_mesh": "Mesh.021", "reference_dimensions_m": (13.629, 80.624, 0.219), "system": "leakage_cable"},
    {"component": "Ref22_Leakage_Cable_C", "reference_mesh": "Mesh.022", "reference_dimensions_m": (13.621, 80.624, 0.212), "system": "leakage_cable"},
    {"component": "Ref23_Evacuation_Sign_Panel", "reference_mesh": "Mesh.023", "reference_dimensions_m": (11.431, 51.777, 0.099), "system": "evacuation_signage"},
    {"component": "Ref24_Evacuation_Sign_Frame", "reference_mesh": "Mesh.024", "reference_dimensions_m": (11.442, 51.817, 0.152), "system": "evacuation_signage"},
    {"component": "Ref25_Evacuation_Sign_Lamp", "reference_mesh": "Mesh.025", "reference_dimensions_m": (11.430, 51.527, 0.088), "system": "evacuation_signage"},
    {"component": "Ref26_Lighting_Fixture", "reference_mesh": "Mesh.026", "reference_dimensions_m": (20.784, 0.556, 0.363), "system": "lighting"},
    {"component": "Ref27_Lighting_Cable", "reference_mesh": "Mesh.027", "reference_dimensions_m": (20.641, 0.066, 0.044), "system": "lighting"},
    {"component": "Ref28_Lighting_Bracket", "reference_mesh": "Mesh.028", "reference_dimensions_m": (21.126, 0.616, 0.481), "system": "lighting"},
    {"component": "Ref29_Water_System_Bracket_A", "reference_mesh": "Mesh.029", "reference_dimensions_m": (21.038, 88.033, 0.175), "system": "water_supply"},
    {"component": "Ref30_Water_System_Bracket_B", "reference_mesh": "Mesh.030", "reference_dimensions_m": (21.059, 88.004, 0.233), "system": "water_supply"},
    {"component": "Ref31_Water_System_Bracket_C", "reference_mesh": "Mesh.031", "reference_dimensions_m": (21.246, 88.119, 0.124), "system": "water_supply"},
    {"component": "Ref32_Water_System_Bracket_D", "reference_mesh": "Mesh.032", "reference_dimensions_m": (21.216, 88.133, 0.107), "system": "water_supply"},
    {"component": "Ref33_Fire_Water_Bracket_A", "reference_mesh": "Mesh.033", "reference_dimensions_m": (21.768, 88.046, 0.367), "system": "fire_water"},
    {"component": "Ref34_Fire_Water_Bracket_B", "reference_mesh": "Mesh.034", "reference_dimensions_m": (21.572, 86.710, 0.175), "system": "fire_water"},
    {"component": "Ref35_Fire_Water_Bracket_C", "reference_mesh": "Mesh.035", "reference_dimensions_m": (21.593, 86.680, 0.233), "system": "fire_water"},
    {"component": "Ref36_Fire_Water_Bracket_D", "reference_mesh": "Mesh.036", "reference_dimensions_m": (21.726, 86.809, 0.121), "system": "fire_water"},
    {"component": "Ref37_Rail_Bed_Surface", "reference_mesh": "Mesh.037", "reference_dimensions_m": (24.773, 524.558, 0.071), "system": "track"},
    {"component": "Ref38_Rail_Aluminum_Part", "reference_mesh": "Mesh.038", "reference_dimensions_m": (24.760, 524.539, 0.096), "system": "track"},
    {"component": "Ref39_Rail_Cast_Iron_Part", "reference_mesh": "Mesh.039", "reference_dimensions_m": (24.734, 524.507, 0.024), "system": "track"},
    {"component": "Ref40_Rail_Chrome_Part", "reference_mesh": "Mesh.040", "reference_dimensions_m": (24.707, 524.517, 0.219), "system": "track"},
    {"component": "Ref41_Rail_Fastener", "reference_mesh": "Mesh.041", "reference_dimensions_m": (24.953, 524.629, 0.188), "system": "track"},
)
SUBWAY_REFERENCE_COMPONENT_BY_NAME = {record["component"]: record for record in SUBWAY_REFERENCE_COMPONENTS_41}
SUBWAY_PROFESSIONAL_SYSTEM_COMPONENTS: dict[str, frozenset[str]] = {
    "structure": frozenset(
        {
            "Ref04_Concrete_Segment",
        }
    ),
    "track": frozenset(
        {
            "Ref02_Aggregate_Base",
            "Ref03_Rubber_Isolation",
            "Ref37_Rail_Bed_Surface",
            "Ref38_Rail_Aluminum_Part",
            "Ref40_Rail_Chrome_Part",
            "Ref41_Rail_Fastener",
        }
    ),
    "mep": frozenset(
        {
            "Ref14_Contact_Rail",
            "Ref15_Contact_Hanger",
            "Ref16_Contact_Clamp",
            "Ref17_High_Voltage_Cable_Bracket",
            "Ref26_Lighting_Fixture",
            "Ref27_Lighting_Cable",
            "Ref28_Lighting_Bracket",
            "Ref29_Water_System_Bracket_A",
            "Ref30_Water_System_Bracket_B",
            "Ref31_Water_System_Bracket_C",
            "Ref32_Water_System_Bracket_D",
            "Ref33_Fire_Water_Bracket_A",
            "Ref34_Fire_Water_Bracket_B",
            "Ref35_Fire_Water_Bracket_C",
            "Ref36_Fire_Water_Bracket_D",
        }
    ),
    "communication": frozenset(
        {
            "Ref18_Comm_Cable_Bracket_A",
            "Ref19_Comm_Cable_Bracket_B",
            "Ref20_Leakage_Cable_A",
            "Ref21_Leakage_Cable_B",
            "Ref22_Leakage_Cable_C",
        }
    ),
    "evacuation": frozenset(
        {
            "Ref01_Guardrail",
            "Ref08_Platform_Main",
            "Ref09_Platform_Support",
            "Ref10_Platform_Edge_Strip",
            "Ref11_Platform_Steel_Frame",
            "Ref12_Platform_Concrete_Panel",
            "Ref13_Platform_Bracket",
            "Ref23_Evacuation_Sign_Panel",
            "Ref24_Evacuation_Sign_Frame",
            "Ref25_Evacuation_Sign_Lamp",
        }
    ),
}
# The active interval-tunnel deliverable intentionally excludes MEP,
# communication, and the legacy Ref39 sleeper representation.
SUBWAY_PROFESSIONAL_SYSTEMS = frozenset({"structure", "track", "evacuation"})
SUBWAY_COMPONENT_PROFESSIONAL_SYSTEM = {
    component: system
    for system, components in SUBWAY_PROFESSIONAL_SYSTEM_COMPONENTS.items()
    for component in components
}
SUBWAY_RULE_COMPONENT_SPECS: dict[str, dict[str, Any]] = {
    "Ref01_Guardrail": {
        "component_name_zh": "GJ-平台扶手",
        "rule_geometry_type": "linear_guardrail_with_posts",
        "placement_mode": "continuous_with_interval_supports",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "evacuation_platform_inner_edge_toward_tunnel_center",
        "installation_height_relative_m": 1.05,
    },
    "Ref02_Aggregate_Base": {
        "component_name_zh": "GJ-道床基础",
        "rule_geometry_type": "swept_box",
        "placement_mode": "continuous",
        "installation_side": "track_center",
        "installation_height_relative_m": -2.15,
    },
    "Ref03_Rubber_Isolation": {
        "component_name_zh": "GJ-轨道橡胶隔振垫",
        "rule_geometry_type": "arrayed_pad_box",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_SLEEPER_INTERVAL_M,
        "installation_side": "under_both_rails",
        "installation_height_relative_m": -1.72,
    },
    "Ref04_Concrete_Segment": {
        "component_name_zh": "JG-圆形隧道管片",
        "rule_geometry_type": "circular_section_sweep",
        "placement_mode": "continuous",
        "installation_side": "tunnel_envelope",
        "installation_height_relative_m": 0.0,
    },
    "Ref05_Steel_Plate": {
        "component_name_zh": "JG-管片连接钢板",
        "rule_geometry_type": "radial_seam_cylinder",
        "placement_mode": "lining_ring_array",
        "placement_interval_m": SUBWAY_LINING_RING_INTERVAL_M,
        "installation_side": "tunnel_envelope",
        "installation_height_relative_m": 0.0,
    },
    "Ref06_Seal_Ring": {
        "component_name_zh": "JG-管片密封圈",
        "rule_geometry_type": "circular_ring_sweep",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_LINING_RING_INTERVAL_M,
        "installation_side": "tunnel_envelope",
        "installation_height_relative_m": 0.0,
    },
    "Ref07_Bolt": {
        "component_name_zh": "JG-管片螺栓",
        "rule_geometry_type": "arrayed_bolt_cylinder",
        "placement_mode": "lining_ring_array",
        "placement_interval_m": SUBWAY_LINING_BOLT_INTERVAL_M,
        "installation_side": "tunnel_envelope",
        "installation_height_relative_m": 0.0,
    },
    "Ref08_Platform_Main": {
        "component_name_zh": "GJ-疏散平台主体",
        "rule_geometry_type": "swept_box",
        "placement_mode": "continuous",
        "installation_side": "platform_side",
        "installation_height_relative_m": -1.0,
    },
    "Ref09_Platform_Support": {
        "component_name_zh": "疏散平台_支撑1",
        "rule_geometry_type": "arrayed_support_box",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "platform_side",
        "installation_height_relative_m": -1.35,
    },
    "Ref10_Platform_Edge_Strip": {
        "component_name_zh": "GJ-疏散平台边条",
        "rule_geometry_type": "swept_edge_strip",
        "placement_mode": "continuous",
        "installation_side": "platform_track_edge",
        "installation_height_relative_m": -0.72,
    },
    "Ref11_Platform_Steel_Frame": {
        "component_name_zh": "GJ-疏散平台钢架",
        "rule_geometry_type": "swept_frame_beam",
        "placement_mode": "continuous",
        "installation_side": "platform_side",
        "installation_height_relative_m": -1.18,
    },
    "Ref12_Platform_Concrete_Panel": {
        "component_name_zh": "GJ-疏散平台混凝土板",
        "rule_geometry_type": "swept_panel_box",
        "placement_mode": "continuous",
        "installation_side": "platform_side",
        "installation_height_relative_m": -0.72,
    },
    "Ref13_Platform_Bracket": {
        "component_name_zh": "GJ-疏散平台托架",
        "rule_geometry_type": "arrayed_l_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "platform_wall_side",
        "installation_height_relative_m": -1.15,
    },
    "Ref14_Contact_Rail": {
        "component_name_zh": "DL-接触网",
        "rule_geometry_type": "longitudinal_cylinder",
        "placement_mode": "continuous",
        "installation_side": "contact_side",
        "installation_height_relative_m": 1.65,
    },
    "Ref15_Contact_Hanger": {
        "component_name_zh": "DL-接触网吊架",
        "rule_geometry_type": "arrayed_hanger_box",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CONTACT_HANGER_INTERVAL_M,
        "installation_side": "contact_side",
        "installation_height_relative_m": 1.95,
    },
    "Ref16_Contact_Clamp": {
        "component_name_zh": "DL-接触网卡具",
        "rule_geometry_type": "arrayed_clamp_box",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CONTACT_HANGER_INTERVAL_M,
        "installation_side": "contact_side",
        "installation_height_relative_m": 1.65,
    },
    "Ref17_High_Voltage_Cable_Bracket": {
        "component_name_zh": "GD-高压控制电缆支架",
        "rule_geometry_type": "arrayed_l_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "service_wall",
        "installation_height_relative_m": -0.45,
    },
    "Ref18_Comm_Cable_Bracket_A": {
        "component_name_zh": "TX-通信信号电缆支架A",
        "rule_geometry_type": "arrayed_l_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "service_wall",
        "installation_height_relative_m": 0.05,
    },
    "Ref19_Comm_Cable_Bracket_B": {
        "component_name_zh": "TX-通信信号电缆支架B",
        "rule_geometry_type": "arrayed_l_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "service_wall",
        "installation_height_relative_m": 0.55,
    },
    "Ref20_Leakage_Cable_A": {
        "component_name_zh": "TX-漏泄同轴电缆A",
        "rule_geometry_type": "longitudinal_cylinder",
        "placement_mode": "continuous",
        "installation_side": "service_wall",
        "installation_height_relative_m": 1.05,
    },
    "Ref21_Leakage_Cable_B": {
        "component_name_zh": "TX-漏泄同轴电缆B",
        "rule_geometry_type": "longitudinal_cylinder",
        "placement_mode": "continuous",
        "installation_side": "service_wall",
        "installation_height_relative_m": 1.30,
    },
    "Ref22_Leakage_Cable_C": {
        "component_name_zh": "TX-漏泄同轴电缆C",
        "rule_geometry_type": "longitudinal_cylinder",
        "placement_mode": "continuous",
        "installation_side": "service_wall",
        "installation_height_relative_m": 1.55,
    },
    "Ref23_Evacuation_Sign_Panel": {
        "component_name_zh": "疏散照明双向-面板",
        "rule_geometry_type": "arrayed_sign_panel",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_EVACUATION_SIGN_INTERVAL_M,
        "installation_side": "evacuation_wall",
        "installation_height_relative_m": 0.75,
    },
    "Ref24_Evacuation_Sign_Frame": {
        "component_name_zh": "疏散照明双向-边框",
        "rule_geometry_type": "arrayed_sign_frame",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_EVACUATION_SIGN_INTERVAL_M,
        "installation_side": "evacuation_wall",
        "installation_height_relative_m": 0.75,
    },
    "Ref25_Evacuation_Sign_Lamp": {
        "component_name_zh": "疏散照明双向-灯体",
        "rule_geometry_type": "arrayed_sign_lamp",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_EVACUATION_SIGN_INTERVAL_M,
        "installation_side": "evacuation_wall",
        "installation_height_relative_m": 0.92,
    },
    "Ref26_Lighting_Fixture": {
        "component_name_zh": "ZM-双管LED灯-壁装",
        "rule_geometry_type": "arrayed_light_box",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_LIGHTING_INTERVAL_M,
        "installation_side": "lighting_wall",
        "installation_height_relative_m": 1.45,
    },
    "Ref27_Lighting_Cable": {
        "component_name_zh": "ZM-照明电缆",
        "rule_geometry_type": "longitudinal_cylinder",
        "placement_mode": "continuous",
        "installation_side": "lighting_wall",
        "installation_height_relative_m": 1.75,
    },
    "Ref28_Lighting_Bracket": {
        "component_name_zh": "ZM-照明灯具支架",
        "rule_geometry_type": "arrayed_short_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_LIGHTING_INTERVAL_M,
        "installation_side": "lighting_wall",
        "installation_height_relative_m": 1.45,
    },
    "Ref29_Water_System_Bracket_A": {
        "component_name_zh": "GP-给水系统支架A",
        "rule_geometry_type": "arrayed_l_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "water_wall",
        "installation_height_relative_m": -1.15,
    },
    "Ref30_Water_System_Bracket_B": {
        "component_name_zh": "GP-给水系统支架B",
        "rule_geometry_type": "arrayed_l_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "water_wall",
        "installation_height_relative_m": -0.90,
    },
    "Ref31_Water_System_Bracket_C": {
        "component_name_zh": "GP-给水系统支架C",
        "rule_geometry_type": "arrayed_l_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "water_wall",
        "installation_height_relative_m": -0.65,
    },
    "Ref32_Water_System_Bracket_D": {
        "component_name_zh": "GP-给水系统支架D",
        "rule_geometry_type": "arrayed_l_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "water_wall",
        "installation_height_relative_m": -0.40,
    },
    "Ref33_Fire_Water_Bracket_A": {
        "component_name_zh": "GP-消防系统支架A",
        "rule_geometry_type": "arrayed_l_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "fire_water_wall",
        "installation_height_relative_m": -0.20,
    },
    "Ref34_Fire_Water_Bracket_B": {
        "component_name_zh": "GP-消防系统支架B",
        "rule_geometry_type": "arrayed_l_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "fire_water_wall",
        "installation_height_relative_m": 0.05,
    },
    "Ref35_Fire_Water_Bracket_C": {
        "component_name_zh": "GP-消防系统支架C",
        "rule_geometry_type": "arrayed_l_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "fire_water_wall",
        "installation_height_relative_m": 0.30,
    },
    "Ref36_Fire_Water_Bracket_D": {
        "component_name_zh": "GP-消防系统支架D",
        "rule_geometry_type": "arrayed_l_bracket",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_CABLE_BRACKET_INTERVAL_M,
        "installation_side": "fire_water_wall",
        "installation_height_relative_m": 0.55,
    },
    "Ref37_Rail_Bed_Surface": {
        "component_name_zh": "GJ-轨床面层",
        "rule_geometry_type": "swept_surface_box",
        "placement_mode": "continuous",
        "installation_side": "track_center",
        "installation_height_relative_m": -1.62,
    },
    "Ref38_Rail_Aluminum_Part": {
        "component_name_zh": "GJ-钢轨主体",
        "rule_geometry_type": "dual_longitudinal_cylinder",
        "placement_mode": "continuous",
        "installation_side": "both_rails",
        "installation_height_relative_m": -1.48,
    },
    "Ref39_Rail_Cast_Iron_Part": {
        "component_name_zh": "GJ-轨枕",
        "rule_geometry_type": "arrayed_sleeper_box",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_SLEEPER_INTERVAL_M,
        "installation_side": "track_center",
        "installation_height_relative_m": -1.65,
    },
    "Ref40_Rail_Chrome_Part": {
        "component_name_zh": "GJ-钢轨顶面",
        "rule_geometry_type": "dual_longitudinal_cylinder",
        "placement_mode": "continuous",
        "installation_side": "both_rails",
        "installation_height_relative_m": -1.42,
    },
    "Ref41_Rail_Fastener": {
        "component_name_zh": "GJ-扣件",
        "rule_geometry_type": "arrayed_fastener_box",
        "placement_mode": "fixed_interval_array",
        "placement_interval_m": SUBWAY_SLEEPER_INTERVAL_M,
        "installation_side": "both_rails",
        "installation_height_relative_m": -1.52,
    },
}
SUBWAY_TEMPLATE_SECTION_PROFILE_NAME = "subway01_mesh004_hull_35pt"
SUBWAY_TEMPLATE_SECTION_SOURCE_MESH = "Mesh.004"
SUBWAY_TEMPLATE_SECTION_AREA_M2 = 130.277595
SUBWAY_TEMPLATE_SECTION_CENTER_XZ_M = (135.573803, -22.152754)
SUBWAY_TEMPLATE_SECTION_HULL_XZ_M: tuple[tuple[float, float], ...] = (
    (124.034645, -22.054626),
    (124.151672, -23.099791),
    (124.693413, -23.857399),
    (125.271690, -24.564014),
    (126.261826, -25.039877),
    (127.003662, -25.144846),
    (127.189667, -25.157419),
    (127.308205, -25.157419),
    (143.783539, -25.155260),
    (144.129135, -25.150301),
    (144.893173, -25.034496),
    (145.007614, -24.992537),
    (145.045776, -24.977360),
    (145.910355, -24.559237),
    (146.512039, -23.964842),
    (146.950638, -23.080061),
    (147.112961, -21.988028),
    (146.984634, -21.390465),
    (146.919357, -21.212263),
    (146.527649, -20.462955),
    (146.406311, -20.269737),
    (146.075165, -19.846846),
    (145.376694, -19.432911),
    (144.629181, -19.180241),
    (144.323044, -19.169266),
    (143.614624, -19.148090),
    (126.949707, -19.156849),
    (126.817001, -19.157202),
    (126.699699, -19.164604),
    (126.518822, -19.182215),
    (126.001228, -19.350155),
    (125.753166, -19.440941),
    (125.128113, -19.856188),
    (124.613037, -20.425905),
    (124.140083, -21.220497),
)
SUBWAY_SINGLE_TUNNEL_SECTION_PROFILE_NAME = "subway01_mesh004_right_circular_single_tunnel_fit"
SUBWAY_SINGLE_TUNNEL_SECTION_SIDE = "right"
SUBWAY_SINGLE_TUNNEL_SECTION_SPLIT_X_M = 135.573803
SUBWAY_SINGLE_TUNNEL_SECTION_CENTER_XZ_M = (144.079249, -22.147192)
SUBWAY_SINGLE_TUNNEL_SECTION_RADIUS_M = 3.010074
SUBWAY_SINGLE_TUNNEL_SECTION_SEGMENTS = 48
SUBWAY_SINGLE_TUNNEL_SECTION_AREA_M2 = 28.464536
SUBWAY_SINGLE_TUNNEL_SECTION_SOURCE_ARC_XZ_M: tuple[tuple[float, float], ...] = (
    (143.614624, -19.148090),
    (144.323044, -19.169266),
    (144.629181, -19.180241),
    (145.376694, -19.432911),
    (146.075165, -19.846846),
    (146.406311, -20.269737),
    (146.527649, -20.462955),
    (146.919357, -21.212263),
    (146.984634, -21.390465),
    (147.112961, -21.988028),
    (146.950638, -23.080061),
    (146.512039, -23.964842),
    (145.910355, -24.559237),
    (145.045776, -24.977360),
    (145.007614, -24.992537),
    (144.893173, -25.034496),
    (144.129135, -25.150301),
    (143.783539, -25.155260),
)
SUBWAY_STATION_DEPTH_M = -11.0
SUBWAY_STATION_SIZE_M = (34.0, 16.0, 7.0)
# Road/junction generation switches. These settings are kept next to the city
# output configuration because they control the visible road model, semantic
# reports, and QC scores produced by this pipeline.
GENERATE_ROAD_ASSETS = True
GENERATE_SUBWAY_TUNNELS = True
GENERATE_UTILITY_PIPES = True
ENABLE_TRANSITION_CURVES = True
ENABLE_ROUNDED_JUNCTION_SURFACES = False
GENERATE_JUNCTION_CROSSWALKS = False
GENERATE_JUNCTION_STOP_LINES = False
GENERATE_JUNCTION_APPROACH_SURFACES = False
ENABLE_SIMPLE_ROUNDED_JUNCTIONS = True
RUN_GENERATION_QC = str(os.environ.get("CIM_ROAD_RUN_QC", "")).strip().lower() in {"1", "true", "yes", "on"}
RUN_SIDEWALK_TOPOLOGY_QC = RUN_GENERATION_QC or str(os.environ.get("CIM_ROAD_RUN_SIDEWALK_QC", "")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
GENERATE_JUNCTION_DEBUG_MODELS = (
    str(os.environ.get("CIM_ROAD_EXPORT_JUNCTION_DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}
)
JUNCTION_MARKING_CLEARANCE_M = 11.0
JUNCTION_MINOR_APPROACH_EXTRA_SETBACK_M = 6.0
JUNCTION_MINOR_APPROACH_EXTRA_WIDTH_GAP_M = 1.5
JUNCTION_MINOR_APPROACH_EXTRA_WIDTH_RATIO = 0.95
JUNCTION_MAJOR_ROAD_BASE_EXTRA_SETBACK_M = 2.0
JUNCTION_SKEW_EXTRA_SETBACK_M = 3.0
JUNCTION_MULTI_ARM_EXTRA_SETBACK_M = 4.0
JUNCTION_T_STEM_EXTRA_SETBACK_M = 4.0
JUNCTION_Y_JUNCTION_EXTRA_SETBACK_M = 2.0
JUNCTION_WIDE_ROAD_EXTRA_SETBACK_START_M = 32.0
JUNCTION_WIDE_ROAD_MINOR_APPROACH_RATIO = 0.32
JUNCTION_WIDE_ROAD_WIDTH_DIFFERENCE_RATIO = 0.22
JUNCTION_WIDE_ROAD_MINOR_EXTRA_SETBACK_MAX_M = 46.0
JUNCTION_WIDE_DRIVABLE_EXTRA_SETBACK_START_M = 24.0
JUNCTION_WIDE_DRIVABLE_MINOR_APPROACH_RATIO = 0.28
JUNCTION_WIDE_DRIVABLE_MINOR_EXTRA_SETBACK_MAX_M = 14.0
JUNCTION_EXPRESSWAY_MINOR_EXTRA_SETBACK_M = 10.0
JUNCTION_EXPRESSWAY_WIDE_MINOR_EXTRA_SETBACK_M = 6.0
JUNCTION_DYNAMIC_EXTRA_SETBACK_MAX_M = 70.0
JUNCTION_SIDEWALK_CONNECTOR_EXTRA_SETBACK_MAX_M = 18.0
JUNCTION_SIDEWALK_CONNECTOR_EXPRESSWAY_MINOR_EXTRA_MAX_M = 16.0
JUNCTION_WIDE_THROUGH_MIN_WIDTH_M = 14.0
JUNCTION_WIDE_THROUGH_CLEARANCE_M = 0.75
JUNCTION_WIDE_THROUGH_MIN_CROSSING_SIN = 0.35
JUNCTION_THROUGH_CORRIDOR_MIN_OPPOSING_DEG = 135.0
JUNCTION_THROUGH_CORRIDOR_PRIORITY_BONUS_M = 3.0
JUNCTION_THROUGH_CORRIDOR_WIDTH_MARGIN_M = 1.5
JUNCTION_SURFACE_Z_OFFSET_M = 0.026
JUNCTION_PATCH_SMOOTH_M = 2.8
JUNCTION_PATCH_MIN_THROAT_M = 10.0
JUNCTION_PATCH_MAX_THROAT_M = 48.0
JUNCTION_CURB_RETURN_MIN_RADIUS_M = 5.0
JUNCTION_CURB_RETURN_MAX_RADIUS_M = 12.0
SIMPLE_JUNCTION_STOP_LINE_SETBACK_M = 1.2
SIMPLE_JUNCTION_CROSSWALK_GAP_M = 1.2
SIMPLE_JUNCTION_CROSSWALK_RETREAT_M = 0.8
SIMPLE_JUNCTION_STOP_LINE_TO_CROSSWALK_GAP_M = 1.0
JUNCTION_CONNECTOR_LOCAL_MARGIN_M = 6.0
OUTPUT_JUNCTION_SURFACE_CLOSE_M = 0.18
OUTPUT_ROAD_COMPONENT_CLOSE_M = 0.18
OUTPUT_ROADSIDE_COMPONENT_CLOSE_M = 0.18
SIDEWALK_CONFLICT_CLEARANCE_M = 0.02
SIDEWALK_CONFLICT_OVERLAP_TOLERANCE_M2 = 0.01
SIDEWALK_MIN_FRAGMENT_AREA_M2 = 0.05
SIDEWALK_CONNECTIVITY_TOLERANCE_M = 0.25
SIDEWALK_NEAR_GAP_MAX_M = 1.2
SIDEWALK_OVERLAP_AREA_TOLERANCE_M2 = 0.01
JUNCTION_SIDE_COMPONENT_CONNECTOR_OVERLAP_M = 1.2 # 相邻道路组件连接处的重叠距离
JUNCTION_RAMP_MERGE_BRANCH_THROAT_MIN_M = 10.0
JUNCTION_RAMP_MERGE_BRANCH_THROAT_MAX_M = 24.0
JUNCTION_RAMP_MERGE_MAIN_SIDE_PATCH_MIN_M = 16.0
JUNCTION_RAMP_MERGE_MAIN_SIDE_PATCH_MAX_M = 30.0
JUNCTION_RAMP_MERGE_CENTER_FILL_MAX_RADIUS_M = 6.0
JUNCTION_RAMP_MERGE_SMOOTH_M = 0.65
JUNCTION_APPROACH_APRON_CORE_SCALE = 0.65
JUNCTION_APPROACH_APRON_MIN_CORE_RADIUS_M = 3.5
JUNCTION_APPROACH_APRON_MIN_AREA_M2 = 0.2
JUNCTION_STOP_LINE_CONTROL_ZONE_OVERLAP_M = 0.35
JUNCTION_LEVEL_ELEVATION_BUCKET_M = 0.5
JUNCTION_BUCKET_CLUSTER_M = 22.0
JUNCTION_APPROACH_BLEND_OVERLAP_M = 0.0
JUNCTION_DRIVABLE_BLEND_OVERLAP_M = 0.0
JUNCTION_ROADSIDE_SEAM_OVERLAP_M = 0.8
JUNCTION_APPROACH_ELEMENT_STOP_CLEARANCE_M = 0# 路口接近段停止线与道路边缘的距离
JUNCTION_DRIVABLE_CORE_CLIP_INSET_M = 0.0
JUNCTION_ROADSIDE_RETREAT_M = 0.0
JUNCTION_SIDE_COMPONENT_EDGE_RETREAT_M = 0.0
JUNCTION_SIDE_COMPONENT_EDGE_OVERLAP_M = 0.0
JUNCTION_MARKING_RETREAT_M = 0.35
SHORT_JUNCTION_GAP_AS_JUNCTION_MAX_LENGTH_M = 8.0
SHORT_JUNCTION_CONNECTOR_AS_JUNCTION_MAX_LENGTH_M = 10.0
JUNCTION_TARGET_OUTER_SIDEWALK_SOURCE_IDS = {"0"}
JUNCTION_TARGET_OUTER_SIDEWALK_LANDING_MAX_M = 2.0
JUNCTION_TARGET_OUTER_SIDEWALK_CONTROL_MAX_OUTWARD_M = 2.5
JUNCTION_COMPONENT_WIDTH_BUCKET_M = 0.05
JUNCTION_CHAINAGE_EPSILON_M = 0.02
JUNCTION_APPROACH_MARKING_MAX_OFFSET_M = 52.0
JUNCTION_MARKING_SURFACE_CLEARANCE_M = 2.6
JUNCTION_DESIGN_SCORE_THRESHOLD = 72.0
JUNCTION_TRANSVERSE_MARKING_MIN_APPROACH_SURFACE_M = 6.0
CROSSWALK_BAND_LENGTH_M = 4.0
CROSSWALK_STRIPE_WIDTH_M = 0.45
CROSSWALK_STRIPE_GAP_M = 0.60
STOP_LINE_WIDTH_M = 0.45
STOP_LINE_TO_CROSSWALK_GAP_M = 0.0
JUNCTION_STOP_LINE_SETBACK_M = 2.0
STREET_LIGHT_STOP_LINE_MARGIN_M = 1.2
TREE_STOP_LINE_MARGIN_M = 4.8
JUNCTION_SEMANTIC_SAMPLE_DISTANCE_M = 28.0
JUNCTION_MARKING_LATERAL_INSET_M = 0.30
STOP_LINE_LATERAL_INSET_M = 0.0
JUNCTION_TRANSVERSE_MARKING_CENTER_GAP_M = 0.75
JUNCTION_CROSSWALK_CENTER_GAP_M = 0.0
JUNCTION_STOP_LINE_CENTER_GAP_M = 0.0
JUNCTION_CROSSWALK_TOP_Z_OFFSET_M = 0.07
JUNCTION_STOP_LINE_TOP_Z_OFFSET_M = 0.07
MARKING_SWEEP_SAMPLE_INTERVAL_M = 8.0


@dataclass(frozen=True)
class RoadGenerationProfile:
    name: str
    mesh_granularity: str
    generate_assets: bool
    generate_trees: bool
    generate_lane_markings: bool
    generate_junction_markings: bool
    generate_side_component_connectors: bool
    generate_curbs: bool
    semantic_level: str


CIM4_PROFILE = RoadGenerationProfile(
    name="cim4",
    mesh_granularity="component",
    generate_assets=True,
    generate_trees=str(os.environ.get("CIM_ROAD_GENERATE_TREES", "")).strip().lower() not in {"0", "false", "no", "off"},
    generate_lane_markings=True,
    generate_junction_markings=True,
    generate_side_component_connectors=True,
    generate_curbs=True,
    semantic_level="fine_component_with_assets_markings_and_full_junction_semantics",
)
CIM3_PROFILE = RoadGenerationProfile(
    name="cim3",
    mesh_granularity="component",
    generate_assets=False,
    generate_trees=False,
    generate_lane_markings=False,
    generate_junction_markings=True,
    generate_side_component_connectors=True,
    generate_curbs=False,
    semantic_level="lightweight_component_layers_with_full_junction_handling",
)
ROAD_GENERATION_PROFILES = {
    CIM3_PROFILE.name: CIM3_PROFILE,
    CIM4_PROFILE.name: CIM4_PROFILE,
}


@dataclass(frozen=True)
class SubwayGenerationProfile:
    name: str
    mesh_granularity: str
    semantic_level: str
    generate_station_trim: bool
    generate_track_bed: bool
    generate_evacuation: bool


SUBWAY_CIM4_PROFILE = SubwayGenerationProfile(
    name="cim4",
    mesh_granularity="source_line_component",
    semantic_level="interval_tunnel_41_parameterized_rule_components",
    generate_station_trim=False,
    generate_track_bed=True,
    generate_evacuation=True,
)
SUBWAY_CIM3_PROFILE = SubwayGenerationProfile(
    name="cim3",
    mesh_granularity="source_line_component",
    semantic_level="interval_tunnel_structure_and_track_without_evacuation",
    generate_station_trim=False,
    generate_track_bed=True,
    generate_evacuation=False,
)
SUBWAY_GENERATION_PROFILES = {
    SUBWAY_CIM3_PROFILE.name: SUBWAY_CIM3_PROFILE,
    SUBWAY_CIM4_PROFILE.name: SUBWAY_CIM4_PROFILE,
}


def road_generation_profile(level: str | RoadGenerationProfile | None = None) -> RoadGenerationProfile:
    if isinstance(level, RoadGenerationProfile):
        return level
    key = str(level or CIM4_PROFILE.name).strip().lower()
    if key not in ROAD_GENERATION_PROFILES:
        raise ValueError(f"Unknown road generation level: {level!r}. Expected one of: {sorted(ROAD_GENERATION_PROFILES)}")
    return ROAD_GENERATION_PROFILES[key]


def road_generation_profile_with_tree_switch(
    level: str | RoadGenerationProfile | None = None,
    *,
    generate_trees: bool | None = None,
) -> RoadGenerationProfile:
    profile = road_generation_profile(level)
    if generate_trees is None or profile.name != CIM4_PROFILE.name:
        return profile
    if profile.generate_trees == generate_trees:
        return profile
    return replace(profile, generate_trees=generate_trees)


def subway_generation_profile(level: str | SubwayGenerationProfile | None = None) -> SubwayGenerationProfile:
    if isinstance(level, SubwayGenerationProfile):
        return level
    key = str(level or SUBWAY_CIM4_PROFILE.name).strip().lower()
    if key not in SUBWAY_GENERATION_PROFILES:
        raise ValueError(
            f"Unknown subway generation level: {level!r}. Expected one of: {sorted(SUBWAY_GENERATION_PROFILES)}"
        )
    return SUBWAY_GENERATION_PROFILES[key]


def subway_professional_systems_for_profile(
    profile: SubwayGenerationProfile,
    enabled_systems: Iterable[str] | None = None,
) -> frozenset[str]:
    if enabled_systems is not None:
        return normalize_subway_professional_systems(enabled_systems)
    systems = set(SUBWAY_PROFESSIONAL_SYSTEMS)
    if not profile.generate_evacuation:
        systems.discard("evacuation")
    return frozenset(systems)


def road_output_stem(profile: RoadGenerationProfile) -> str:
    return f"{profile.name}_city_roads"


def subway_professional_output_suffix(
    enabled_systems: Iterable[str] | None = None,
    default_systems: Iterable[str] | None = None,
) -> str:
    systems = normalize_subway_professional_systems(enabled_systems)
    expected = (
        normalize_subway_professional_systems(default_systems)
        if default_systems is not None
        else SUBWAY_PROFESSIONAL_SYSTEMS
    )
    if systems == expected:
        return ""
    return "_" + "_".join(sorted(systems))


def subway_output_stem(
    profile: SubwayGenerationProfile,
    enabled_systems: Iterable[str] | None = None,
) -> str:
    default_systems = subway_professional_systems_for_profile(profile)
    return (
        f"{profile.name}_subway_tunnels"
        f"{subway_professional_output_suffix(enabled_systems, default_systems)}"
    )


def road_obj_path_for_profile(profile: RoadGenerationProfile) -> Path:
    return MODULE_OBJ_DIR / profile.name / "city_roads.obj"


def subway_tunnel_obj_path_for_profile(
    profile: SubwayGenerationProfile,
    enabled_systems: Iterable[str] | None = None,
) -> Path:
    suffix = subway_professional_output_suffix(
        enabled_systems,
        subway_professional_systems_for_profile(profile),
    )
    return MODULE_OBJ_DIR / profile.name / f"subway_tunnels{suffix}.obj"


def road_semantic_path_for_profile(profile: RoadGenerationProfile) -> Path:
    return ROOT / "output" / "semantic" / profile.name / "city_roads_semantic.json"


def subway_tunnel_semantic_path_for_profile(
    profile: SubwayGenerationProfile,
    enabled_systems: Iterable[str] | None = None,
) -> Path:
    suffix = subway_professional_output_suffix(
        enabled_systems,
        subway_professional_systems_for_profile(profile),
    )
    return ROOT / "output" / "semantic" / profile.name / f"subway_tunnels{suffix}_semantic.json"


def road_classification_path_for_profile(profile: RoadGenerationProfile) -> Path:
    return ROOT / "output" / "semantic" / profile.name / "city_roads_classification.json"


def road_mesh_attributes_path_for_profile(profile: RoadGenerationProfile) -> Path:
    return ROOT / "output" / "semantic" / profile.name / "city_roads_mesh_attributes.json"


def road_sidewalk_qc_path_for_profile(profile: RoadGenerationProfile) -> Path:
    return ROOT / "output" / "qc_report" / profile.name / "city_roads_sidewalk_qc.json"


def subway_tunnel_mesh_attributes_path_for_profile(
    profile: SubwayGenerationProfile,
    enabled_systems: Iterable[str] | None = None,
) -> Path:
    suffix = subway_professional_output_suffix(
        enabled_systems,
        subway_professional_systems_for_profile(profile),
    )
    return ROOT / "output" / "semantic" / profile.name / f"subway_tunnels{suffix}_mesh_attributes.json"


def road_source_attributes_path_for_profile(profile: RoadGenerationProfile) -> Path:
    return ROOT / "output" / "semantic" / profile.name / "city_roads_source_attributes.json"


def subway_tunnel_source_attributes_path_for_profile(
    profile: SubwayGenerationProfile,
    enabled_systems: Iterable[str] | None = None,
) -> Path:
    suffix = subway_professional_output_suffix(
        enabled_systems,
        subway_professional_systems_for_profile(profile),
    )
    return ROOT / "output" / "semantic" / profile.name / f"subway_tunnels{suffix}_source_attributes.json"


def junction_semantic_path_for_profile(profile: RoadGenerationProfile) -> Path:
    return ROOT / "output" / "semantic" / profile.name / "city_junctions_semantic.json"


COLORS = {
    "gis_basemap": [88, 118, 94, 255],
    "gis_grid": [132, 150, 132, 255],
    "road_surface": [38, 40, 42, 255],
    "road_surface_main": [38, 40, 42, 255],
    "road_surface_service": [38, 40, 42, 255],
    "road_surface_branch": [38, 40, 42, 255],
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


def building_height(row) -> float:
    height = safe_float(row.get("height"), 0.0)
    if height > 0:
        return height
    levels = safe_float(row.get("building:levels"), 0.0)
    if levels > 0:
        return max(levels * BUILDING_LEVEL_HEIGHT_M, BUILDING_LEVEL_HEIGHT_M)
    return BUILDING_DEFAULT_HEIGHT_M


# ---------------------------------------------------------------------------
# 道路与路口生成
# ---------------------------------------------------------------------------
#
# 本段有意将完整的道路/路口流水线集中放在一起：
#
# 1. prepare_roads_for_surfaces()
#    将源中心线规范化为每个 LineString 一行，并附加生成器所需字段：
#    road_id、道路等级、车道数、高程、缓和曲线数量以及路口距离。
# 2. swept_band_mesh(), strip_mesh(), and component helpers
#    沿中心线扫掠偏移量，将横断面组件转换为路面、人行道、路缘石和
#    车道标线网格。
# 3. junction_point_buckets(), rounded_junction_polygon(), and related helpers
#    将道路级的路口距离提示聚合为成簇的路口节点，选择设计方案，并构建
#    圆角冲突区表面。
# 4. junction_clip_range_profiles_by_road()
#    将路口表面反算为每条道路上的距离范围，使道路组件、路缘石、标线和
#    路侧资产能够从冲突区退让，同时不留下明显空隙。
# 5. build_road_surface_meshes()
#    对外可见的道路/路口入口函数。它编排所有生成的道路图层，并返回城市
#    场景消费的合并网格。
# 6. 道路/路口语义与 QC 辅助函数
#    在网格生成后立即输出可追踪的 JSON 记录和评分报告，此时相关常量和
#    几何辅助函数仍在附近，便于对照维护。


def prepare_roads_for_surfaces(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Normalize raw road centerlines into the city road generation schema.

    The city pipeline may receive either OSM-like GeoJSON or local engineering
    SHP data. This function makes both look the same to the mesh generator:
    each row is a LineString, bidirectional duplicates are collapsed, IDs are
    stable, widths/classes/lane counts are normalized, bridge elevations are
    assigned, optional clothoid smoothing is applied, and junction distances are
    attached for later clipping and marking placement.
    """
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
    prepared["ground_z_start"] = ROAD_SURFACE_BASE_Z_M
    prepared["ground_z_end"] = ROAD_SURFACE_BASE_Z_M
    prepared["road_z_start"] = (
        ROAD_SURFACE_BASE_Z_M
        + prepared["bridge_clearance"].where(prepared["is_bridge"], 0.0)
    )
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


def swept_band_polygon_round_caps(line: LineString, left_offset: float, right_offset: float):
    if right_offset <= left_offset or line is None or line.is_empty or line.length <= 0.05:
        return None
    center_offset = (float(left_offset) + float(right_offset)) * 0.5
    half_width = abs(float(right_offset) - float(left_offset)) * 0.5
    if half_width <= 0.025:
        return None

    distances = road_gen.sample_line_for_sweep(line)
    if len(distances) < 2:
        return None

    center_points = []
    for distance in distances:
        point, _, normal = road_gen.line_frame_at_distance(line, distance)
        nx, ny = normal
        center_points.append((point.x + nx * center_offset, point.y + ny * center_offset))
    try:
        center_line = LineString(center_points)
        if center_line.is_empty or center_line.length <= 0.05:
            return None
        return road_gen.clean_polygonal(
            center_line.buffer(
                half_width,
                cap_style=1,
                join_style=1,
                resolution=8,
            )
        )
    except Exception:
        return swept_band_polygon(line, left_offset, right_offset)


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


def local_polygon_clip_mask(geom, clip_mask, margin: float = 0.35):
    if clip_mask is None or clip_mask.is_empty or geom is None or geom.is_empty:
        return None
    try:
        envelope = geom.envelope.buffer(max(float(margin), 0.0), resolution=2, join_style=1)
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
    """Sweep a left/right offset band along one road line and return a mesh.

    Most road layers are longitudinal strips: carriageways, sidewalks,
    non-motor lanes, green belts, and curbs. When a junction clip mask is
    supplied, the function first builds a planar polygon and subtracts the mask
    so drivable road strips do not stack on top of the dedicated junction
    surface. Otherwise it builds a lightweight sampled strip mesh directly.
    """
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
        mesh = road_gen.polygon_to_top_mesh(geom, z + z_offset, name, visual_color=color)
        return apply_road_feature_metadata(mesh, row)

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
    apply_road_feature_metadata(mesh, row)
    mesh.visual.face_colors = color
    return mesh


def road_section_category(row: pd.Series) -> str:
    return road_gen.road_asset_category(row)


def road_feature_metadata(row: pd.Series, rule: Any | None = None) -> dict[str, Any]:
    source_rule = road_gen.row_section_requirement(row)
    modeled_rule = road_gen.modeled_section_requirement(row, source_rule)
    if rule is None:
        rule = road_gen.get_road_rule(row, road_gen.load_rules())
    components = road_gen.cross_section_components_for_row(row)
    if not components:
        components = fallback_cross_section_components(rule)
    source_section = road_gen.row_section_code(row) or (
        road_gen.safe_str(source_rule.get("section_id") or source_rule.get("inferred_section"))
        if source_rule
        else ""
    )
    modeled_section = (
        road_gen.safe_str(modeled_rule.get("section_id") or modeled_rule.get("inferred_section"))
        if modeled_rule
        else ""
    )
    road_class = road_gen.safe_str(row.get("road_class")) or "unclassified"
    level_record = road_level_record_for_row(row, rule)
    line = row.geometry if isinstance(row.geometry, LineString) else None
    return {
        "cim_domain": "road",
        "cim_entity_type": "road_component",
        "road_idx": str(json_safe_value(row.name)),
        "source_road_id": road_gen.safe_str(row.get("road_id")) or "",
        "road_name": road_gen.safe_str(row.get("road_name")) or "",
        "road_class": road_class,
        "road_category": str(level_record.get("category") or road_section_category(row)),
        "source_section_code": source_section or "",
        "modeled_section_code": modeled_section or "",
        "road_level_key": road_level_key_for_record(level_record),
        "road_priority": int(level_record.get("priority", 0) or 0),
        "spatial_layer": int(level_record.get("spatial_layer", 0) or 0),
        "elevation_bucket_m": float(level_record.get("elevation_bucket_m", 0.0) or 0.0),
        "modeled_width_m": round(float(road_gen.component_total_width(components)), 3),
        "lane_count": int(getattr(rule, "lane_count", 0) or 0),
        "lane_width_m": round(float(getattr(rule, "lane_width", 0.0) or 0.0), 3),
        "length_m": round(float(line.length), 3) if line is not None else None,
    }


def apply_road_feature_metadata(
    mesh: trimesh.Trimesh,
    row: pd.Series,
    rule: Any | None = None,
    layer_name: str | None = None,
    component_type: str | None = None,
    component_idx: Any | None = None,
) -> trimesh.Trimesh:
    if mesh is None:
        return mesh
    metadata = road_feature_metadata(row, rule)
    if layer_name:
        metadata["layer_name"] = str(layer_name)
    if component_type:
        metadata["component_type"] = str(component_type)
    if component_idx is not None:
        metadata["component_idx"] = json_safe_value(component_idx)
    mesh.metadata.update(metadata)
    return mesh


def object_name_token(value: Any, fallback: str = "unknown") -> str:
    text = str(value if value is not None else "").strip()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^\w\-]+", "", text, flags=re.UNICODE).strip("_")
    return text[:80] if text else fallback


def mesh_road_category(mesh: trimesh.Trimesh) -> str:
    category = str((mesh.metadata or {}).get("road_category") or "").strip()
    return category if category else "shared"


def mesh_road_monomer_key(mesh: trimesh.Trimesh) -> tuple[str, str, str, str]:
    metadata = mesh.metadata or {}
    road_idx = str(metadata.get("road_idx") or "").strip()
    if not road_idx:
        return ("shared", "", "", mesh_road_category(mesh))
    return (
        road_idx,
        str(metadata.get("source_road_id") or "").strip(),
        str(metadata.get("road_name") or "").strip(),
        mesh_road_category(mesh),
    )


def road_monomer_mesh_name(layer_name: str, key: tuple[str, str, str, str]) -> str:
    road_idx, source_road_id, road_name, category = key
    if road_idx == "shared":
        return f"{layer_name}_Shared_{object_name_token(category, 'Unknown')}"
    name_token = object_name_token(road_name, "")
    if name_token:
        return f"{name_token}-{object_name_token(layer_name, 'Layer')}"
    source_token = object_name_token(source_road_id, "")
    road_token = object_name_token(road_idx, "unknown")
    suffix = road_token if not source_token or source_token == road_token else f"{road_token}_{source_token}"
    return f"{suffix}-{object_name_token(layer_name, 'Layer')}"


def combine_meshes_by_road_monomer(
    layer_name: str,
    meshes: list[trimesh.Trimesh],
    color: list[int] | None = None,
) -> dict[str, trimesh.Trimesh]:
    grouped: dict[tuple[str, str, str, str], list[trimesh.Trimesh]] = defaultdict(list)
    for mesh in meshes:
        if mesh is not None and len(mesh.vertices) > 0:
            grouped[mesh_road_monomer_key(mesh)].append(mesh)
    combined = {}
    for key, parts in sorted(grouped.items()):
        name = road_monomer_mesh_name(layer_name, key)
        mesh = combine_mesh_list(name, parts, color)
        if mesh is None:
            continue
        first_metadata = dict(parts[0].metadata or {})
        mesh.metadata.update(first_metadata)
        mesh.metadata.update(
            {
                "name": name,
                "layer_name": layer_name,
                "cim_monomer": True,
                "cim_monomer_granularity": "road_layer",
                "source_part_count": len(parts),
            }
        )
        combined[name] = mesh
    return combined


OUTPUT_ROAD_COMPONENT_CLOSE_LAYERS = {
    "Road_Surface_Main",
    "Road_Surface_Branch",
    "Road_Surface_Service",
    "Non_Motor_Lane",
    "Parking_Lane",
}

OUTPUT_ROADSIDE_COMPONENT_CLOSE_LAYERS = {
    "Sidewalk",
    "Green_Belt",
    "Facility_Belt",
}


def close_output_same_material_gaps(
    mesh: trimesh.Trimesh,
    close_m: float,
) -> tuple[trimesh.Trimesh | None, int, float]:
    if mesh is None or len(mesh.vertices) == 0 or close_m <= 0.0:
        return mesh, 0, 0.0
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    if len(vertices) == 0 or len(faces) == 0:
        return mesh, 0, 0.0

    face_polygons = []
    for face in faces:
        coords = [
            (float(vertices[int(vertex_idx)][0]), float(vertices[int(vertex_idx)][1]))
            for vertex_idx in face
        ]
        if len(coords) < 3:
            continue
        try:
            polygon = Polygon(coords)
            if polygon.is_valid and polygon.area > 1e-7:
                face_polygons.append(polygon)
        except Exception:
            continue
    if not face_polygons:
        return mesh, 0, 0.0
    try:
        original_geom = road_gen.clean_polygonal(unary_union(face_polygons))
    except Exception:
        original_geom = None
    if original_geom is None or original_geom.is_empty:
        return mesh, 0, 0.0
    try:
        closed_geom = road_gen.clean_polygonal(
            original_geom.buffer(close_m, resolution=2, join_style=1).buffer(
                -close_m,
                resolution=2,
                join_style=1,
            )
        )
    except Exception:
        closed_geom = None
    if closed_geom is None or closed_geom.is_empty:
        return mesh, 0, 0.0
    try:
        filled_geom = road_gen.clean_polygonal(unary_union([original_geom, closed_geom]))
    except Exception:
        filled_geom = original_geom
    if filled_geom is None or filled_geom.is_empty:
        return mesh, 0, 0.0
    added_area = max(float(filled_geom.area) - float(original_geom.area), 0.0)
    if added_area <= 1e-5:
        return mesh, 0, 0.0

    metadata = dict(mesh.metadata or {})
    name = str(metadata.get("name") or metadata.get("layer_name") or "mesh")
    try:
        z = float(np.mean(vertices[:, 2]))
    except Exception:
        z = 0.0
    visual_color = None
    try:
        face_colors = np.asarray(mesh.visual.face_colors)
        if len(face_colors) > 0:
            visual_color = face_colors[0].tolist()
    except Exception:
        pass
    filled_mesh = road_gen.polygon_to_top_mesh(filled_geom, z, name, visual_color=visual_color)
    if filled_mesh is None or len(filled_mesh.vertices) == 0:
        return mesh, 0, 0.0
    filled_mesh.metadata.update(metadata)
    filled_mesh.metadata["name"] = name
    return filled_mesh, 1, added_area


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


def longitudinal_marking_stop_clearance_for_row(row: pd.Series, rule: Any) -> float:
    """Distance from a junction node where longitudinal markings may resume."""
    _, stop_line_offset = junction_approach_offsets_for_row(row, rule)
    return max(
        junction_marking_clearance_for_row(row, rule),
        stop_line_offset + STOP_LINE_WIDTH_M * 0.5,
    )


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
    """Build a pavement-marking strip along a road centerline.

    The same primitive is used for solid lane edges, dashed lane dividers, and
    yellow center lines. `blocked_distances` removes spans around intersections,
    while `clip_mask` trims any remaining footprint out of the junction surface.
    This keeps approach markings legible without drawing through the conflict
    area.
    """
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
        mesh = road_gen.merge_named_meshes(name, span_meshes, color)
        return apply_road_feature_metadata(mesh, row)

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
    apply_road_feature_metadata(mesh, row)
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

VEHICULAR_ROAD_COMPONENT_TYPES = {"main_carriageway", "carriageway", "service_lane"}
DRIVABLE_COMPONENT_TYPES = VEHICULAR_ROAD_COMPONENT_TYPES | {"non_motor_lane", "parking_lane"}
JUNCTION_ASPHALT_COMPONENT_TYPES = {"main_carriageway", "carriageway"}
LANE_MARKING_COMPONENT_TYPES = set(VEHICULAR_ROAD_COMPONENT_TYPES)
RAISED_COMPONENT_TYPES = {"sidewalk", "green_belt", "facility_belt", "side_divider", "divider", "median"}
JUNCTION_DIVIDER_COMPONENT_TYPES = {"side_divider", "divider", "median"}
JUNCTION_STOP_LINE_COMPONENT_TYPES = {"main_carriageway", "carriageway"}
JUNCTION_APPROACH_CONTROL_COMPONENT_TYPES = DRIVABLE_COMPONENT_TYPES | JUNCTION_DIVIDER_COMPONENT_TYPES
JUNCTION_PLANAR_SURFACE_COMPONENT_TYPES = set(JUNCTION_ASPHALT_COMPONENT_TYPES)
JUNCTION_APPROACH_SURFACE_COMPONENT_TYPES = JUNCTION_ASPHALT_COMPONENT_TYPES | {"service_lane"}
JUNCTION_CONTINUOUS_ROADSIDE_COMPONENT_TYPES = {"sidewalk"}
REAL_WORLD_PLANAR_JUNCTION_TYPES = {"T_JUNCTION", "Y_JUNCTION", "CROSS_JUNCTION"}
JUNCTION_SIDE_DRIVABLE_RETRACT_COMPONENT_TYPES = {"non_motor_lane", "parking_lane"}
JUNCTION_EDGE_CLIPPED_COMPONENT_TYPES = {"service_lane"}
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
    if component_type == "curb":
        return max(0.055, rule.curb_height * 0.45)
    if component_type == "sidewalk":
        return max(0.035, rule.curb_height * 0.65)
    if component_type in {"green_belt", "facility_belt", "side_divider", "divider", "median"}:
        return max(0.04, rule.curb_height * 0.45)
    if component_type == "parking_lane":
        return 0.006
    if component_type == "non_motor_lane":
        return 0.004
    return 0.0


def road_surface_z_for_row(row) -> float:
    return float(row.get("road_z_mean", row.get("elevation", ROAD_SURFACE_BASE_Z_M)) or ROAD_SURFACE_BASE_Z_M)


def junction_surface_base_z(prepared_roads: gpd.GeoDataFrame, members: Iterable[Any]) -> float:
    values: list[float] = []
    for member in members or []:
        try:
            road_idx = member[0]
        except Exception:
            continue
        if road_idx not in prepared_roads.index:
            continue
        values.append(road_surface_z_for_row(prepared_roads.loc[road_idx]))
    if not values:
        return ROAD_SURFACE_BASE_Z_M
    return round(float(sum(values) / len(values)), 6)


def clamp_junction_chainage(line: LineString, distance: float) -> float | None:
    length = float(line.length)
    distance = float(distance)
    epsilon = max(float(JUNCTION_CHAINAGE_EPSILON_M), 0.0)
    if distance < -epsilon or distance > length + epsilon:
        return None
    return max(0.0, min(length, distance))


def road_level_record_for_row(row: pd.Series, rule: Any | None = None) -> dict[str, Any]:
    """Normalize the road-level fields used by junction matching.

    Road class, source section, layer, and elevation all affect whether two
    approaches should share roadside components at a junction. Keeping the
    normalization in one helper makes geometry generation and semantic records
    use the same definition of "same level".
    """
    source_rule = road_gen.row_section_requirement(row)
    road_class = road_gen.safe_str(row.get("road_class")) or "unclassified"
    category = road_gen.section_category_name(source_rule, road_class) if source_rule else road_gen.road_class_category_name(road_class)
    priority = int(road_gen.road_priority(row))
    layer = int(road_gen.row_layer_value(row))
    elevation = float(row.get("elevation", row.get("road_z_mean", 0.0)) or 0.0)
    bucket_size = max(float(JUNCTION_LEVEL_ELEVATION_BUCKET_M), 0.05)
    elevation_bucket = round(round(elevation / bucket_size) * bucket_size, 3)
    return {
        "category": category,
        "priority": priority,
        "spatial_layer": layer,
        "elevation_bucket_m": elevation_bucket,
    }


def road_level_key_for_record(record: dict[str, Any]) -> str:
    return (
        f"{record.get('category', 'unknown')}:"
        f"P{int(record.get('priority', 0) or 0)}:"
        f"L{int(record.get('spatial_layer', 0) or 0)}:"
        f"Z{float(record.get('elevation_bucket_m', 0.0) or 0.0):.2f}"
    )


def road_level_key_for_row(row: pd.Series, rule: Any | None = None) -> str:
    return road_level_key_for_record(road_level_record_for_row(row, rule))


def junction_connection_level_key_for_record(record: dict[str, Any]) -> str:
    """Key used for same-plane physical junction stitching.

    Road category and priority affect semantics, but solid surfaces at an
    at-grade junction still need to meet. Keep stitching constrained to the
    same spatial layer and elevation bucket so overpasses do not fuse.
    """
    record = record or {}
    return (
        f"L{int(record.get('spatial_layer', 0) or 0)}:"
        f"Z{float(record.get('elevation_bucket_m', 0.0) or 0.0):.2f}"
    )


def junction_connection_level_key_for_row(row: pd.Series, rule: Any | None = None) -> str:
    return junction_connection_level_key_for_record(road_level_record_for_row(row, rule))


def junction_connection_level_policy_from_arms(arms: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Classify same-plane junction arms by full road level.

    A connection level is physically connectable when at least two arms share
    the same spatial layer and elevation bucket. If those arms also share the
    same full road-level key and modeled component signature, all side
    components are allowed to connect across that level. Mixed full road levels
    or mixed component widths connect only matching component signatures.
    """
    connection_level_counts: Counter[str] = Counter()
    road_levels_by_connection: dict[str, set[str]] = defaultdict(set)
    component_signatures_by_connection: dict[str, set[str]] = defaultdict(set)
    for arm in arms:
        level_record = arm.get("road_level", {})
        connection_level = junction_connection_level_key_for_record(level_record)
        road_level = str(arm.get("road_level_key") or road_level_key_for_record(level_record))
        component_signature = str(arm.get("component_signature_key") or "")
        connection_level_counts[connection_level] += 1
        road_levels_by_connection[connection_level].add(road_level)
        if component_signature:
            component_signatures_by_connection[connection_level].add(component_signature)

    connectable_levels = {
        connection_level
        for connection_level, count in connection_level_counts.items()
        if count >= 2
    }
    same_road_level_connection_levels = {
        connection_level
        for connection_level in connectable_levels
        if len(road_levels_by_connection.get(connection_level, set())) == 1
        and len(component_signatures_by_connection.get(connection_level, set())) <= 1
    }
    return {
        "connectable_levels": connectable_levels,
        "same_road_level_connection_levels": same_road_level_connection_levels,
        "mixed_road_level_connection_levels": connectable_levels - same_road_level_connection_levels,
    }




def fallback_cross_section_components(rule: Any) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    if rule.sidewalk_width > 0.05:
        components.append({"type": "sidewalk", "width": rule.sidewalk_width})
    components.append({"type": "main_carriageway", "width": rule.road_width})
    if rule.sidewalk_width > 0.05:
        components.append({"type": "sidewalk", "width": rule.sidewalk_width})
    return components


def one_sided_sidewalk_spans(
    components: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], float, float]] | None:
    valid_components = [
        component
        for component in components
        if float(component.get("width", 0.0) or 0.0) > 0.01
    ]
    sidewalk_indexes = [
        idx
        for idx, component in enumerate(valid_components)
        if str(component.get("type", "")) == "sidewalk"
    ]
    if len(sidewalk_indexes) != 1:
        return None
    sidewalk_idx = sidewalk_indexes[0]
    if sidewalk_idx not in {0, len(valid_components) - 1}:
        return None
    if any(str(component.get("type", "")) not in DRIVABLE_COMPONENT_TYPES | {"sidewalk"} for component in valid_components):
        return None

    road_width = sum(
        float(component.get("width", 0.0) or 0.0)
        for component in valid_components
        if str(component.get("type", "")) != "sidewalk"
    )
    if road_width <= 0.01:
        return None

    road_left = -road_width / 2.0
    road_right = road_width / 2.0
    if sidewalk_idx == 0:
        cursor = road_left - float(valid_components[0].get("width", 0.0) or 0.0)
    else:
        cursor = road_left

    spans: list[tuple[dict[str, Any], float, float]] = []
    for component in valid_components:
        width = float(component.get("width", 0.0) or 0.0)
        component_type = str(component.get("type", ""))
        if component_type == "sidewalk" and sidewalk_idx == len(valid_components) - 1:
            left = road_right
            right = road_right + width
        else:
            left = cursor
            right = cursor + width
            cursor = right
        spans.append((component, left, right))
    return spans


def component_spans(components: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float, float]]:
    one_sided_spans = one_sided_sidewalk_spans(components)
    if one_sided_spans is not None:
        return one_sided_spans

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


def junction_component_width_bucket(width: float) -> float:
    bucket = max(float(JUNCTION_COMPONENT_WIDTH_BUCKET_M), 0.01)
    return round(round(float(width) / bucket) * bucket, 3)


def road_component_signature_key(components: list[dict[str, Any]]) -> str:
    parts = []
    for component in components:
        width = float(component.get("width", 0.0) or 0.0)
        if width <= 0.01:
            continue
        component_type = str(component.get("type", ""))
        parts.append(f"{component_type}:{junction_component_width_bucket(width):.2f}")
    return "|".join(parts)










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
    """Append white edge and dashed divider markings for one drivable component."""
    component_type = str(component.get("type", ""))
    if component_type not in LANE_MARKING_COMPONENT_TYPES:
        return
    width = right_offset - left_offset
    if width < 3.2:
        return

    edge_width = max(0.10, min(rule.lane_marking_width, 0.16))
    junction_distances = road_gen.row_junction_distances(row)
    junction_clearance = longitudinal_marking_stop_clearance_for_row(row, rule)
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
    junction_clearance = longitudinal_marking_stop_clearance_for_row(row, rule)
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
    include_boundary_component_types: set[str] | None = None,
    exclude_boundary_component_types: set[str] | None = None,
) -> None:
    curb_width = max(0.18, min(float(rule.curb_width), 0.45))
    for idx in range(len(spans) - 1):
        left_component, left_span_start, boundary = spans[idx]
        right_component, next_left, right_span_end = spans[idx + 1]
        if abs(boundary - next_left) > 0.01:
            continue
        left_type = str(left_component.get("type", ""))
        right_type = str(right_component.get("type", ""))
        if not (
            (left_type in DRIVABLE_COMPONENT_TYPES and right_type in RAISED_COMPONENT_TYPES)
            or (right_type in DRIVABLE_COMPONENT_TYPES and left_type in RAISED_COMPONENT_TYPES)
        ):
            continue
        boundary_types = {left_type, right_type}
        if include_boundary_component_types is not None and not (boundary_types & include_boundary_component_types):
            continue
        if exclude_boundary_component_types is not None and boundary_types & exclude_boundary_component_types:
            continue
        if left_type in DRIVABLE_COMPONENT_TYPES and right_type in RAISED_COMPONENT_TYPES:
            curb_left = boundary
            curb_right = min(right_span_end, boundary + curb_width)
        elif right_type in DRIVABLE_COMPONENT_TYPES and left_type in RAISED_COMPONENT_TYPES:
            curb_left = max(left_span_start, boundary - curb_width)
            curb_right = boundary
        else:
            continue
        if curb_right - curb_left <= 0.03:
            continue

        curb_mesh = swept_band_mesh(
            row,
            line,
            curb_left,
            curb_right,
            f"Curb_{row.name}_{idx}",
            COLORS["curb"],
            z_offset=max(0.055, rule.curb_height * 0.45),
            distance_offset=distance_offset,
            clip_mask=clip_mask,
        )
        if len(curb_mesh.vertices) > 0:
            apply_road_feature_metadata(
                curb_mesh,
                row,
                rule,
                layer_name="Curb",
                component_type="curb",
                component_idx=idx,
            )
            curb_meshes.append(curb_mesh)


def drivable_width_for_row(row: pd.Series, rule: Any) -> float:
    components = road_gen.cross_section_components_for_row(row)
    drivable_width = road_gen.component_width_by_type(components, DRIVABLE_COMPONENT_TYPES)
    return max(float(rule.road_width), drivable_width)


def vehicular_road_width_for_row(row: pd.Series, rule: Any) -> float:
    components = road_gen.cross_section_components_for_row(row)
    vehicular_width = road_gen.component_width_by_type(components, VEHICULAR_ROAD_COMPONENT_TYPES)
    return max(float(rule.road_width), vehicular_width)


def junction_asphalt_surface_width(
    row: pd.Series,
    rule: Any,
    components: list[dict[str, Any]],
    asphalt_spans: list[tuple[dict[str, Any], float, float]],
) -> float:
    """Return the width used to size the central asphalt conflict surface."""
    if not asphalt_spans:
        return vehicular_road_width_for_row(row, rule)

    asphalt_sum_width = road_gen.component_width_by_type(components, JUNCTION_ASPHALT_COMPONENT_TYPES)
    asphalt_extent_width = max(
        max(abs(float(left_offset)), abs(float(right_offset))) * 2.0
        for _, left_offset, right_offset in asphalt_spans
    )
    return max(float(asphalt_sum_width), float(asphalt_extent_width), float(rule.lane_width) * 2.0)


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
    stop_line_offset = (
        crosswalk_offset
        + CROSSWALK_BAND_LENGTH_M * 0.5
        + STOP_LINE_TO_CROSSWALK_GAP_M
        + JUNCTION_STOP_LINE_SETBACK_M
        + STOP_LINE_WIDTH_M * 0.5
    )
    return crosswalk_offset, stop_line_offset


def junction_approach_element_stop_offset_m(
    row: pd.Series,
    rule: Any,
    arm: dict[str, Any] | None = None,
    arms: list[dict[str, Any]] | None = None,
) -> float:
    """Offset from the junction node to the transverse line where road elements stop."""
    _crosswalk_offset, stop_line_offset = junction_approach_offsets_for_row(row, rule)
    if arm is not None and arms is not None:
        stop_line_offset += junction_arm_extra_setback_m(arm, arms)
    return max(
        0.0,
        float(stop_line_offset) - STOP_LINE_WIDTH_M * 0.5 - JUNCTION_CHAINAGE_EPSILON_M,
    )


def junction_approach_element_stop_distance(
    line: LineString,
    row: pd.Series,
    rule: Any,
    arm: dict[str, Any],
    arms: list[dict[str, Any]],
    outward_extra_m: float = 0.0,
) -> float | None:
    node_distance = clamp_junction_chainage(line, float(arm.get("node_distance_m", 0.0) or 0.0))
    if node_distance is None:
        return None
    side_sign = -1.0 if float(arm.get("line_direction_sign", 0.0) or 0.0) < 0.0 else 1.0
    stop_offset = junction_approach_element_stop_offset_m(row, rule, arm, arms) + max(float(outward_extra_m), 0.0)
    return clamp_junction_chainage(line, float(node_distance) + side_sign * stop_offset)


def junction_sidewalk_connector_extra_setback_m(arm: dict[str, Any], arms: list[dict[str, Any]]) -> float:
    raw_extra = junction_arm_extra_setback_m(arm, arms)
    category = str(arm.get("category", "") or "").strip().lower()
    has_expressway = any(str(item.get("category", "") or "").strip().lower() == "expressway" for item in arms)
    if has_expressway and category != "expressway":
        return min(raw_extra, float(JUNCTION_SIDEWALK_CONNECTOR_EXPRESSWAY_MINOR_EXTRA_MAX_M))
    return min(raw_extra, float(JUNCTION_SIDEWALK_CONNECTOR_EXTRA_SETBACK_MAX_M))


def junction_sidewalk_connector_distance(
    line: LineString,
    row: pd.Series,
    rule: Any,
    arm: dict[str, Any],
    arms: list[dict[str, Any]],
    outward_extra_m: float = 0.0,
) -> float | None:
    """Endpoint distance for outer sidewalk curves, independent from stop-line retreat."""
    node_distance = clamp_junction_chainage(line, float(arm.get("node_distance_m", 0.0) or 0.0))
    if node_distance is None:
        return None
    side_sign = -1.0 if float(arm.get("line_direction_sign", 0.0) or 0.0) < 0.0 else 1.0
    _crosswalk_offset, stop_line_offset = junction_approach_offsets_for_row(row, rule)
    capped_connector_offset = max(
        0.0,
        float(stop_line_offset)
        + junction_sidewalk_connector_extra_setback_m(arm, arms)
        - STOP_LINE_WIDTH_M * 0.5
        - JUNCTION_CHAINAGE_EPSILON_M
    )
    road_element_stop_offset = junction_approach_element_stop_offset_m(row, rule, arm, arms)
    connector_offset = max(capped_connector_offset, road_element_stop_offset) + max(float(outward_extra_m), 0.0)
    return clamp_junction_chainage(line, float(node_distance) + side_sign * connector_offset)


def simple_junction_crosswalk_center_distance(boundary_distance: float, side_sign: float) -> float:
    return float(boundary_distance) + float(side_sign) * (
        float(SIMPLE_JUNCTION_CROSSWALK_RETREAT_M) + CROSSWALK_BAND_LENGTH_M * 0.5
    )


def simple_junction_stop_line_center_distance(boundary_distance: float, side_sign: float) -> float:
    return float(boundary_distance) + float(side_sign) * (
        float(SIMPLE_JUNCTION_CROSSWALK_RETREAT_M)
        + CROSSWALK_BAND_LENGTH_M
        + float(SIMPLE_JUNCTION_STOP_LINE_TO_CROSSWALK_GAP_M)
        + STOP_LINE_WIDTH_M * 0.5
    )


def arm_direction_vector(arm: dict[str, Any]) -> tuple[float, float] | None:
    direction = arm.get("direction_out")
    if not direction or len(direction) < 2:
        return None
    try:
        dx = float(direction[0])
        dy = float(direction[1])
    except Exception:
        return None
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        return None
    return (dx / length, dy / length)


def arm_is_lower_order_than(arm: dict[str, Any], other: dict[str, Any]) -> bool:
    priority = int(arm.get("road_priority", 0) or 0)
    other_priority = int(other.get("road_priority", 0) or 0)
    width = float(arm.get("drivable_width_m", 0.0) or 0.0)
    other_width = float(other.get("drivable_width_m", 0.0) or 0.0)
    if other_priority > priority:
        return True
    return (
        other_width - width >= float(JUNCTION_MINOR_APPROACH_EXTRA_WIDTH_GAP_M)
        and width <= other_width * float(JUNCTION_MINOR_APPROACH_EXTRA_WIDTH_RATIO)
    )


def arm_signed_separation_deg(arm_a: dict[str, Any], arm_b: dict[str, Any]) -> float | None:
    direction_a = arm_direction_vector(arm_a)
    direction_b = arm_direction_vector(arm_b)
    if direction_a is None or direction_b is None:
        return None
    return abs(signed_angle_deg(direction_a, direction_b))


def junction_opposing_arm_pairs(
    arms: list[dict[str, Any]],
    min_opposing_deg: float | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    threshold = float(JUNCTION_THROUGH_CORRIDOR_MIN_OPPOSING_DEG if min_opposing_deg is None else min_opposing_deg)
    pairs: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    for idx, arm_a in enumerate(arms):
        for arm_b in arms[idx + 1:]:
            separation = arm_signed_separation_deg(arm_a, arm_b)
            if separation is None or separation < threshold:
                continue
            pairs.append((arm_a, arm_b, separation))
    pairs.sort(key=lambda item: corridor_pair_score(item[0], item[1], item[2]), reverse=True)
    return pairs


def corridor_pair_score(arm_a: dict[str, Any], arm_b: dict[str, Any], separation_deg: float) -> float:
    priority = max(int(arm_a.get("road_priority", 0) or 0), int(arm_b.get("road_priority", 0) or 0))
    width = max(float(arm_a.get("drivable_width_m", 0.0) or 0.0), float(arm_b.get("drivable_width_m", 0.0) or 0.0))
    opposite_fit = max(0.0, min(1.0, float(separation_deg) / 180.0))
    return width + priority * float(JUNCTION_THROUGH_CORRIDOR_PRIORITY_BONUS_M) + opposite_fit * 4.0


def arm_in_corridor_pair(arm: dict[str, Any], corridor: tuple[dict[str, Any], dict[str, Any], float]) -> bool:
    arm_id = str(arm.get("arm_id", ""))
    return arm_id in {str(corridor[0].get("arm_id", "")), str(corridor[1].get("arm_id", ""))}


def corridor_pair_direction(corridor: tuple[dict[str, Any], dict[str, Any], float]) -> tuple[float, float] | None:
    direction_a = arm_direction_vector(corridor[0])
    direction_b = arm_direction_vector(corridor[1])
    if direction_a is None and direction_b is None:
        return None
    if direction_a is None:
        return direction_b
    if direction_b is None:
        return direction_a
    # Opposing arms should point away from the junction in opposite directions;
    # flip one vector before averaging so the result follows the corridor axis.
    dx = direction_a[0] - direction_b[0]
    dy = direction_a[1] - direction_b[1]
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        return direction_a
    return (dx / length, dy / length)


def corridor_pair_priority(corridor: tuple[dict[str, Any], dict[str, Any], float]) -> int:
    return max(int(corridor[0].get("road_priority", 0) or 0), int(corridor[1].get("road_priority", 0) or 0))


def corridor_pair_width(corridor: tuple[dict[str, Any], dict[str, Any], float]) -> float:
    return max(
        float(corridor[0].get("drivable_width_m", 0.0) or 0.0),
        float(corridor[1].get("drivable_width_m", 0.0) or 0.0),
    )


def arm_marking_conflict_width_m(arm: dict[str, Any]) -> float:
    drivable_width = float(arm.get("drivable_width_m", 0.0) or 0.0)
    if str(arm.get("category", "")) != "expressway":
        return drivable_width
    return max(drivable_width, float(arm.get("modeled_width_m", 0.0) or 0.0))


def corridor_pair_marking_conflict_width_m(
    corridor: tuple[dict[str, Any], dict[str, Any], float],
) -> float:
    return max(
        arm_marking_conflict_width_m(corridor[0]),
        arm_marking_conflict_width_m(corridor[1]),
    )


def arm_is_lower_order_than_corridor(
    arm: dict[str, Any],
    corridor: tuple[dict[str, Any], dict[str, Any], float],
) -> bool:
    priority = int(arm.get("road_priority", 0) or 0)
    width = float(arm.get("drivable_width_m", 0.0) or 0.0)
    corridor_priority = corridor_pair_priority(corridor)
    corridor_width = corridor_pair_width(corridor)
    if priority < corridor_priority:
        return True
    return corridor_width - width >= float(JUNCTION_THROUGH_CORRIDOR_WIDTH_MARGIN_M)


def junction_type_from_arm_geometry(arms: list[dict[str, Any]]) -> str:
    arm_count = len(arms)
    if arm_count <= 1:
        return "UNKNOWN"
    if any("roundabout" in arm["road_class"].lower() or "circular" in arm["road_class"].lower() for arm in arms):
        return "ROUNDABOUT_LIKE"
    if any("link" in arm["road_class"].lower() or "ramp" in arm["road_class"].lower() for arm in arms):
        return "RAMP_MERGE"
    if arm_count <= 2:
        return "TWO_ARM_CONNECTION"
    opposing_pairs = junction_opposing_arm_pairs(arms)
    gaps = angular_gaps_deg([arm["direction_out"] for arm in arms])
    max_gap = max(gaps) if gaps else 0.0
    min_gap = min(gaps) if gaps else 0.0
    if arm_count == 3:
        return "T_JUNCTION" if opposing_pairs or max_gap > 150.0 else "Y_JUNCTION"
    if arm_count == 4:
        if len(opposing_pairs) >= 2 and min_gap > 35.0 and max_gap < 160.0:
            return "CROSS_JUNCTION"
        return "SKEWED_CROSS_OR_MULTI_ARM"
    return "MULTI_ARM_JUNCTION"


def transverse_marking_clearance_offset_m(
    corridor_extent_m: float,
    approach_width_m: float,
    crossing_sin: float,
) -> float:
    crossing_sin = max(float(crossing_sin), float(JUNCTION_WIDE_THROUGH_MIN_CROSSING_SIN), 0.05)
    crossing_cos = math.sqrt(max(0.0, 1.0 - min(crossing_sin, 1.0) ** 2))
    projected_approach_half_width = max(float(approach_width_m), 0.0) * 0.5 * crossing_cos
    return (
        (max(float(corridor_extent_m), 0.0) + projected_approach_half_width) / crossing_sin
        + CROSSWALK_BAND_LENGTH_M * 0.5
        + max(float(JUNCTION_WIDE_THROUGH_CLEARANCE_M), 0.0)
    )


def junction_corridor_required_crosswalk_offset_m(
    arm: dict[str, Any],
    arms: list[dict[str, Any]],
) -> float:
    junction_type = junction_type_from_arm_geometry(arms)
    if junction_type not in {"T_JUNCTION", "CROSS_JUNCTION", "SKEWED_CROSS_OR_MULTI_ARM", "MULTI_ARM_JUNCTION"}:
        return 0.0
    direction = arm_direction_vector(arm)
    if direction is None:
        return 0.0
    required = 0.0
    min_crossing_sin = max(float(JUNCTION_WIDE_THROUGH_MIN_CROSSING_SIN), 0.05)
    for corridor in junction_opposing_arm_pairs(arms):
        if arm_in_corridor_pair(arm, corridor):
            continue
        corridor_width = corridor_pair_marking_conflict_width_m(corridor)
        if corridor_width < float(JUNCTION_WIDE_THROUGH_MIN_WIDTH_M):
            continue
        if not arm_is_lower_order_than_corridor(arm, corridor):
            continue
        corridor_direction = corridor_pair_direction(corridor)
        if corridor_direction is None:
            continue
        crossing_sin = abs(direction[0] * corridor_direction[1] - direction[1] * corridor_direction[0])
        if crossing_sin < min_crossing_sin:
            continue
        # T-junction source centerlines often terminate at the through-road
        # edge, not at its centerline. Use the full through corridor for the
        # minor stem so its stop line/crosswalk clears the main-road envelope.
        corridor_extent = corridor_width if junction_type == "T_JUNCTION" else corridor_width * 0.5
        required = max(
            required,
            transverse_marking_clearance_offset_m(
                corridor_extent,
                float(arm.get("drivable_width_m", 0.0) or 0.0),
                crossing_sin,
            ),
        )
    return required


def junction_wide_through_required_crosswalk_offset_m(
    arm: dict[str, Any],
    arms: list[dict[str, Any]],
) -> float:
    """Required offset for an approach mark to clear a wider through road."""
    direction = arm_direction_vector(arm)
    if direction is None:
        return 0.0
    arm_id = str(arm.get("arm_id", ""))
    max_required_offset = 0.0
    min_crossing_sin = max(float(JUNCTION_WIDE_THROUGH_MIN_CROSSING_SIN), 0.05)
    for other in arms:
        if str(other.get("arm_id", "")) == arm_id:
            continue
        other_width = arm_marking_conflict_width_m(other)
        if other_width < float(JUNCTION_WIDE_THROUGH_MIN_WIDTH_M):
            continue
        if not arm_is_lower_order_than(arm, other):
            continue
        other_direction = arm_direction_vector(other)
        if other_direction is None:
            continue
        crossing_sin = abs(direction[0] * other_direction[1] - direction[1] * other_direction[0])
        if crossing_sin < min_crossing_sin:
            continue
        max_required_offset = max(
            max_required_offset,
            transverse_marking_clearance_offset_m(
                other_width * 0.5,
                float(arm.get("drivable_width_m", 0.0) or 0.0),
                crossing_sin,
            ),
        )
    return max(max_required_offset, junction_corridor_required_crosswalk_offset_m(arm, arms))


def junction_arm_extra_setback_m(arm: dict[str, Any], arms: list[dict[str, Any]]) -> float:
    if not arms:
        return 0.0
    priority = int(arm.get("road_priority", 0) or 0)
    width = float(arm.get("drivable_width_m", 0.0) or 0.0)
    modeled_width = max(float(arm.get("modeled_width_m", 0.0) or 0.0), width)
    category = str(arm.get("category", "") or "").strip().lower()
    max_priority = max((int(item.get("road_priority", 0) or 0) for item in arms), default=priority)
    max_width = max((float(item.get("drivable_width_m", 0.0) or 0.0) for item in arms), default=width)
    max_modeled_width = max(
        (
            max(float(item.get("modeled_width_m", 0.0) or 0.0), float(item.get("drivable_width_m", 0.0) or 0.0))
            for item in arms
        ),
        default=modeled_width,
    )
    has_expressway = any(str(item.get("category", "") or "").strip().lower() == "expressway" for item in arms)
    junction_type = junction_type_from_arm_geometry(arms)
    gaps = angular_gaps_deg([tuple(item.get("direction_out", (0.0, 0.0))) for item in arms])
    min_gap = min(gaps) if gaps else 90.0
    max_gap = max(gaps) if gaps else 90.0
    priority_is_minor = priority < max_priority
    width_is_minor = (
        max_width - width >= float(JUNCTION_MINOR_APPROACH_EXTRA_WIDTH_GAP_M)
        and width <= max_width * float(JUNCTION_MINOR_APPROACH_EXTRA_WIDTH_RATIO)
    )
    if priority_is_minor or width_is_minor:
        extra_setback = max(float(JUNCTION_MINOR_APPROACH_EXTRA_SETBACK_M), 0.0)
    else:
        extra_setback = 0.0

    if max_priority >= 4 or max_width >= float(JUNCTION_WIDE_THROUGH_MIN_WIDTH_M):
        extra_setback += float(JUNCTION_MAJOR_ROAD_BASE_EXTRA_SETBACK_M)

    if has_expressway and category != "expressway":
        extra_setback += float(JUNCTION_EXPRESSWAY_MINOR_EXTRA_SETBACK_M)
        if modeled_width <= max_modeled_width * 0.65:
            extra_setback += float(JUNCTION_EXPRESSWAY_WIDE_MINOR_EXTRA_SETBACK_M)

    is_minor_to_wide_road = (
        max_modeled_width >= float(JUNCTION_WIDE_ROAD_EXTRA_SETBACK_START_M)
        and modeled_width < max_modeled_width - 1.0
        and (priority_is_minor or width_is_minor or modeled_width <= max_modeled_width * 0.86)
    )
    if is_minor_to_wide_road:
        wide_road_extra = max(
            0.0,
            min(
                float(JUNCTION_WIDE_ROAD_MINOR_EXTRA_SETBACK_MAX_M),
                (max_modeled_width - float(JUNCTION_WIDE_ROAD_EXTRA_SETBACK_START_M))
                * float(JUNCTION_WIDE_ROAD_MINOR_APPROACH_RATIO)
                + (max_modeled_width - modeled_width) * float(JUNCTION_WIDE_ROAD_WIDTH_DIFFERENCE_RATIO),
            ),
        )
        wide_drivable_extra = max(
            0.0,
            min(
                float(JUNCTION_WIDE_DRIVABLE_MINOR_EXTRA_SETBACK_MAX_M),
                (max_width - float(JUNCTION_WIDE_DRIVABLE_EXTRA_SETBACK_START_M))
                * float(JUNCTION_WIDE_DRIVABLE_MINOR_APPROACH_RATIO),
            ),
        )
        extra_setback += wide_road_extra + wide_drivable_extra

    if junction_type in {"SKEWED_CROSS_OR_MULTI_ARM", "Y_JUNCTION"} or min_gap < 65.0 or max_gap > 155.0:
        skew_ratio = max(0.0, min(1.0, (75.0 - min(float(min_gap), 75.0)) / 40.0))
        extra_setback += float(JUNCTION_SKEW_EXTRA_SETBACK_M) * max(skew_ratio, 0.5)

    if junction_type == "MULTI_ARM_JUNCTION" or len(arms) >= 5:
        extra_setback += float(JUNCTION_MULTI_ARM_EXTRA_SETBACK_M)
    elif junction_type == "Y_JUNCTION":
        extra_setback += float(JUNCTION_Y_JUNCTION_EXTRA_SETBACK_M)
    elif junction_type == "T_JUNCTION":
        through_pairs = junction_opposing_arm_pairs(arms)
        is_through_arm = any(arm_in_corridor_pair(arm, pair) for pair in through_pairs)
        if not is_through_arm:
            extra_setback += float(JUNCTION_T_STEM_EXTRA_SETBACK_M)

    base_crosswalk_offset = float(arm.get("crosswalk_base_offset_m", 0.0) or 0.0)
    required_crosswalk_offset = junction_wide_through_required_crosswalk_offset_m(arm, arms)
    if base_crosswalk_offset > 0.0 and required_crosswalk_offset > 0.0:
        extra_setback = max(extra_setback, required_crosswalk_offset - base_crosswalk_offset)
    return max(0.0, min(float(extra_setback), float(JUNCTION_DYNAMIC_EXTRA_SETBACK_MAX_M)))


def junction_approach_extra_setbacks_by_road(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    surface_geometries: list[dict[str, Any]],
) -> dict[Any, list[tuple[float, float, float]]]:
    extra_by_road: dict[Any, list[tuple[float, float, float]]] = defaultdict(list)
    for surface in surface_geometries:
        point = surface.get("point")
        if point is None or getattr(point, "is_empty", False):
            continue
        try:
            arms = junction_arm_records(prepared_roads, rules, point, surface.get("members", []))
        except Exception:
            continue
        for arm in arms:
            extra = junction_arm_extra_setback_m(arm, arms)
            if extra <= 0.0:
                continue
            road_idx = arm.get("road_idx")
            if road_idx is None:
                continue
            try:
                node_distance = float(arm.get("node_distance_m", 0.0) or 0.0)
                sign = float(arm.get("line_direction_sign", 1.0) or 1.0)
            except Exception:
                continue
            extra_by_road[road_idx].append((node_distance, sign, extra))
    return dict(extra_by_road)


def lookup_junction_approach_extra_setback(
    extra_by_road: dict[Any, list[tuple[float, float, float]]] | None,
    road_idx: Any,
    junction_distance: float,
    side_sign: float,
) -> float:
    if not extra_by_road:
        return 0.0
    candidates = list(extra_by_road.get(road_idx, []))
    if not candidates:
        candidates = list(extra_by_road.get(str(road_idx), []))
    if not candidates:
        return 0.0
    best_extra = 0.0
    best_gap = float("inf")
    for node_distance, sign, extra in candidates:
        if float(sign) * float(side_sign) <= 0.0:
            continue
        gap = abs(float(node_distance) - float(junction_distance))
        if gap < best_gap:
            best_gap = gap
            best_extra = float(extra)
    return best_extra if best_gap <= max(JUNCTION_BUCKET_CLUSTER_M, 1.0) else 0.0


def junction_stop_line_asset_exclusion_ranges_for_row(
    row: pd.Series,
    line: LineString,
    rule: Any,
    approach_extra_setbacks_by_road: dict[Any, list[tuple[float, float, float]]] | None = None,
    asset_margin_m: float = 0.0,
) -> list[tuple[float, float]]:
    """Return chainage ranges where roadside assets would pass the stop line."""
    if line is None or line.is_empty or line.length <= 1.0:
        return []

    crosswalk_offset, stop_line_offset = junction_approach_offsets_for_row(row, rule)
    min_margin = max(CROSSWALK_BAND_LENGTH_M * 0.5, STOP_LINE_WIDTH_M * 0.5, 0.4)
    edge_margin = STOP_LINE_WIDTH_M * 0.5 + max(float(asset_margin_m), 0.0)
    ranges: list[tuple[float, float]] = []

    for junction_distance in road_gen.row_junction_distances(row):
        clamped_distance = clamp_junction_chainage(line, junction_distance)
        if clamped_distance is None:
            continue
        for side_sign in (-1.0, 1.0):
            extra_setback = lookup_junction_approach_extra_setback(
                approach_extra_setbacks_by_road,
                row.name,
                clamped_distance,
                side_sign,
            )
            crosswalk_distance = clamped_distance + side_sign * (crosswalk_offset + extra_setback)
            stop_line_distance = clamped_distance + side_sign * (stop_line_offset + extra_setback)
            if not (
                min_margin < crosswalk_distance < line.length - min_margin
                and min_margin < stop_line_distance < line.length - min_margin
            ):
                continue

            far_stop_line_edge = stop_line_distance + side_sign * edge_margin
            start = max(0.0, min(float(line.length), min(clamped_distance, far_stop_line_edge)))
            end = max(0.0, min(float(line.length), max(clamped_distance, far_stop_line_edge)))
            if end - start > 0.05:
                ranges.append((start, end))

    if not ranges:
        return []

    ranges.sort()
    merged: list[tuple[float, float]] = [ranges[0]]
    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 0.25:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def junction_marking_across_width(road_width: float) -> float:
    return max(0.2, float(road_width) - 2.0 * JUNCTION_MARKING_LATERAL_INSET_M)


def junction_stop_line_lateral_pieces_for_row(row: pd.Series, rule: Any) -> list[tuple[float, float]]:
    components = road_gen.cross_section_components_for_row(row)
    if not components:
        components = fallback_cross_section_components(rule)
    spans = component_spans(components)

    def pieces_for_types(target_types: set[str]) -> list[tuple[float, float]]:
        items: list[tuple[float, float]] = []
        for component, left_offset, right_offset in spans:
            if str(component.get("type", "")) not in target_types:
                continue
            piece_left = min(float(left_offset), float(right_offset))
            piece_right = max(float(left_offset), float(right_offset))
            width = piece_right - piece_left
            if width <= 0.05:
                continue
            inset = min(float(STOP_LINE_LATERAL_INSET_M), max((width - 0.2) * 0.5, 0.0))
            piece_left += inset
            piece_right -= inset
            piece_width = piece_right - piece_left
            if piece_width > 0.05:
                items.append(((piece_left + piece_right) * 0.5, piece_width))
        return items

    pieces = pieces_for_types({"main_carriageway"})
    if not pieces:
        pieces = pieces_for_types({"carriageway"})
    if pieces:
        return pieces

    fallback_width = junction_marking_across_width(vehicular_road_width_for_row(row, rule))
    return marking_lateral_pieces_around_center_gap(
        0.0,
        fallback_width,
        gap_width=JUNCTION_STOP_LINE_CENTER_GAP_M,
    )


def junction_crosswalk_lateral_range_for_row(row: pd.Series, rule: Any) -> tuple[float, float]:
    components = road_gen.cross_section_components_for_row(row)
    if not components:
        components = fallback_cross_section_components(rule)
    spans = component_spans(components)
    if road_gen.row_section_code(row) == "D6":
        d6_offsets = junction_d6_crosswalk_lateral_range_for_spans(spans)
        if d6_offsets is not None:
            return d6_offsets
    fill_offsets = junction_stop_line_fill_lateral_offsets_for_spans(spans)
    if fill_offsets is not None:
        return float(fill_offsets[0]), float(fill_offsets[1])

    across_width = junction_marking_across_width(drivable_width_for_row(row, rule))
    return -across_width / 2.0, across_width / 2.0


def junction_d6_crosswalk_lateral_range_for_spans(
    spans: list[tuple[dict[str, Any], float, float]],
) -> tuple[float, float] | None:
    sidewalk_spans = [
        (float(left), float(right))
        for component, left, right in spans
        if str(component.get("type", "")) == "sidewalk" and float(right) - float(left) > 0.05
    ]
    if len(sidewalk_spans) != 1:
        return None

    drivable_spans = [
        (float(left), float(right))
        for component, left, right in spans
        if str(component.get("type", "")) in JUNCTION_STOP_LINE_COMPONENT_TYPES and float(right) - float(left) > 0.05
    ]
    if not drivable_spans:
        drivable_spans = [
            (float(left), float(right))
            for component, left, right in spans
            if str(component.get("type", "")) in DRIVABLE_COMPONENT_TYPES and float(right) - float(left) > 0.05
        ]
    if not drivable_spans:
        return None

    left_offset = min(left for left, _ in drivable_spans)
    right_offset = max(right for _, right in drivable_spans)
    if right_offset - left_offset <= 0.05:
        return None
    return left_offset, right_offset


def crosswalk_stripe_layout_for_range(left_offset: float, right_offset: float) -> list[float]:
    left_offset = float(left_offset)
    right_offset = float(right_offset)
    if right_offset < left_offset:
        left_offset, right_offset = right_offset, left_offset
    across_width = max(0.0, right_offset - left_offset)
    stripe_step = max(CROSSWALK_STRIPE_WIDTH_M + CROSSWALK_STRIPE_GAP_M, 0.1)
    if across_width <= CROSSWALK_STRIPE_WIDTH_M:
        return [(left_offset + right_offset) * 0.5]
    stripe_count = max(2, int(math.floor((across_width - CROSSWALK_STRIPE_WIDTH_M) / stripe_step)) + 1)
    if stripe_count <= 1:
        return [(left_offset + right_offset) * 0.5]
    actual_step = (across_width - CROSSWALK_STRIPE_WIDTH_M) / float(stripe_count - 1)
    lateral_start = left_offset + CROSSWALK_STRIPE_WIDTH_M / 2.0
    return [lateral_start + stripe_idx * actual_step for stripe_idx in range(stripe_count)]


def crosswalk_stripe_layout_for_width(road_width: float) -> tuple[int, float, float]:
    across_width = junction_marking_across_width(road_width)
    offsets = crosswalk_stripe_layout_for_range(-across_width / 2.0, across_width / 2.0)
    stripe_count = len(offsets)
    if stripe_count <= 1:
        lateral_start = offsets[0] if offsets else 0.0
        stripe_step = 0.0
    else:
        lateral_start = offsets[0]
        stripe_step = offsets[1] - offsets[0]
    return stripe_count, lateral_start, stripe_step


def marking_lateral_pieces_around_center_gap(
    center_offset: float,
    width: float,
    gap_width: float | None = None,
) -> list[tuple[float, float]]:
    gap_width = JUNCTION_TRANSVERSE_MARKING_CENTER_GAP_M if gap_width is None else float(gap_width)
    width = float(width)
    if width <= 0.05:
        return []
    if gap_width <= 0.01:
        return [(float(center_offset), width)]

    half_width = width / 2.0
    left = float(center_offset) - half_width
    right = float(center_offset) + half_width
    gap_left = -gap_width / 2.0
    gap_right = gap_width / 2.0
    pieces: list[tuple[float, float]] = []
    for piece_left, piece_right in ((left, min(right, gap_left)), (max(left, gap_right), right)):
        piece_width = piece_right - piece_left
        if piece_width > 0.05:
            pieces.append(((piece_left + piece_right) / 2.0, piece_width))
    return pieces


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


def lookup_junction_approach_marking_control(
    approach_marking_controls_by_road: dict[Any, list[dict[str, Any]]] | None,
    road_idx: Any,
    junction_distance: float,
    side_sign: float,
) -> tuple[bool, bool]:
    if not approach_marking_controls_by_road:
        return True, True
    controls = approach_marking_controls_by_road.get(road_idx)
    if controls is None:
        controls = approach_marking_controls_by_road.get(str(json_safe_value(road_idx)))
    if not controls:
        return True, True
    for control in controls:
        try:
            control_distance = float(control.get("junction_distance_m", 0.0) or 0.0)
            control_side_sign = float(control.get("side_sign", 0.0) or 0.0)
        except Exception:
            continue
        if abs(float(junction_distance) - control_distance) > max(JUNCTION_BUCKET_CLUSTER_M, 2.0):
            continue
        if control_side_sign and abs(float(side_sign) - control_side_sign) > 0.1:
            continue
        return (
            bool(control.get("allow_crosswalk", True)),
            bool(control.get("allow_stop_line", True)),
        )
    return True, True


def add_junction_crosswalks_and_stop_lines(
    row: pd.Series,
    line: LineString,
    rule: Any,
    crosswalk_meshes: list[trimesh.Trimesh],
    stop_line_meshes: list[trimesh.Trimesh],
    approach_extra_setbacks_by_road: dict[Any, list[tuple[float, float, float]]] | None = None,
    approach_marking_controls_by_road: dict[Any, list[dict[str, Any]]] | None = None,
) -> Counter:
    """Generate crosswalk stripes and stop lines for every valid junction arm.

    The road already knows its junction distances from prepare_roads_for_surfaces().
    For each side of each junction, this function steps away from the node far
    enough to clear the rounded junction patch, checks that the marks fit on the
    road segment, then places crosswalk stripes and a transverse stop line using
    the local tangent/normal frame.
    """
    stats: Counter[str] = Counter()
    if line is None or line.is_empty or line.length <= 1.0:
        return stats

    road_width = drivable_width_for_row(row, rule)
    if road_width <= 2.0:
        return stats

    crosswalk_offset, stop_line_offset = junction_approach_offsets_for_row(row, rule)
    min_margin = max(CROSSWALK_BAND_LENGTH_M * 0.5, STOP_LINE_WIDTH_M * 0.5, 0.4)
    stop_line_lateral_pieces = junction_stop_line_lateral_pieces_for_row(row, rule)
    crosswalk_left, crosswalk_right = junction_crosswalk_lateral_range_for_row(row, rule)
    crosswalk_lateral_offsets = crosswalk_stripe_layout_for_range(crosswalk_left, crosswalk_right)
    default_z = float(row.get("road_z_mean", row.get("elevation", 0.0)))

    for junction_idx, junction_distance in enumerate(road_gen.row_junction_distances(row)):
        clamped_distance = clamp_junction_chainage(line, junction_distance)
        if clamped_distance is None:
            continue
        junction_distance = clamped_distance
        for side_sign, side_name in [(-1.0, "Before"), (1.0, "After")]:
            extra_setback = lookup_junction_approach_extra_setback(
                approach_extra_setbacks_by_road,
                row.name,
                junction_distance,
                side_sign,
            )
            adjusted_crosswalk_offset = crosswalk_offset + extra_setback
            adjusted_stop_line_offset = stop_line_offset + extra_setback
            crosswalk_distance = float(junction_distance) + side_sign * adjusted_crosswalk_offset
            stop_line_distance = float(junction_distance) + side_sign * adjusted_stop_line_offset
            if not (
                min_margin < crosswalk_distance < line.length - min_margin
                and min_margin < stop_line_distance < line.length - min_margin
            ):
                continue

            allow_crosswalk, allow_stop_line = lookup_junction_approach_marking_control(
                approach_marking_controls_by_road,
                row.name,
                junction_distance,
                side_sign,
            )
            if not allow_crosswalk and not allow_stop_line:
                stats["suppressed_ramp_merge_marking_approach_count"] += 1
                continue
            stats["candidate_approach_count"] += 1

            point, _, _ = road_gen.line_frame_at_distance(line, crosswalk_distance)
            stop_point, marking_tangent, marking_normal = road_gen.line_frame_at_distance(line, stop_line_distance)
            crosswalk_z = road_gen.elevation_at_distance(row, crosswalk_distance, default_z=default_z)
            for stripe_idx, lateral_offset in enumerate(crosswalk_lateral_offsets):
                if not GENERATE_JUNCTION_CROSSWALKS or not allow_crosswalk:
                    continue
                stripe_meshes = []
                mesh_name = f"Crosswalk_{row.name}_{junction_idx}_{side_name}_{stripe_idx}"
                for piece_offset, piece_width in marking_lateral_pieces_around_center_gap(
                    lateral_offset,
                    CROSSWALK_STRIPE_WIDTH_M,
                    gap_width=JUNCTION_CROSSWALK_CENTER_GAP_M,
                ):
                    center = (
                        point.x + marking_normal[0] * piece_offset,
                        point.y + marking_normal[1] * piece_offset,
                    )
                    piece_mesh = oriented_rect_mesh(
                        center,
                        marking_tangent,
                        marking_normal,
                        CROSSWALK_BAND_LENGTH_M,
                        piece_width,
                        crosswalk_z + rule.lane_marking_z_offset + JUNCTION_CROSSWALK_TOP_Z_OFFSET_M,
                        f"{mesh_name}_{round(piece_offset, 2)}",
                        COLORS["crosswalk"],
                    )
                    if len(piece_mesh.vertices) > 0:
                        stripe_meshes.append(piece_mesh)
                mesh = road_gen.merge_named_meshes(mesh_name, stripe_meshes, COLORS["crosswalk"])
                if len(mesh.vertices) > 0:
                    apply_road_feature_metadata(
                        mesh,
                        row,
                        rule,
                        layer_name="Crosswalk",
                        component_type="crosswalk",
                        component_idx=stripe_idx,
                    )
                    mesh.metadata.update(
                        {
                            "junction_marking_type": "crosswalk",
                        }
                    )
                    crosswalk_meshes.append(mesh)
                    stats["crosswalk_stripe_count"] += 1

            if not GENERATE_JUNCTION_STOP_LINES or not allow_stop_line:
                continue
            stop_z = road_gen.elevation_at_distance(row, stop_line_distance, default_z=default_z)
            stop_name = f"Stop_Line_{row.name}_{junction_idx}_{side_name}"
            stop_meshes = []
            for piece_offset, piece_width in stop_line_lateral_pieces:
                center = (
                    stop_point.x + marking_normal[0] * piece_offset,
                    stop_point.y + marking_normal[1] * piece_offset,
                )
                piece_mesh = oriented_rect_mesh(
                    center,
                    marking_tangent,
                    marking_normal,
                    STOP_LINE_WIDTH_M,
                    piece_width,
                    stop_z + rule.lane_marking_z_offset + JUNCTION_STOP_LINE_TOP_Z_OFFSET_M,
                    f"{stop_name}_{round(piece_offset, 2)}",
                    COLORS["stop_line"],
                )
                if len(piece_mesh.vertices) > 0:
                    stop_meshes.append(piece_mesh)
            mesh = road_gen.merge_named_meshes(stop_name, stop_meshes, COLORS["stop_line"])
            if len(mesh.vertices) > 0:
                apply_road_feature_metadata(
                    mesh,
                    row,
                    rule,
                    layer_name="Stop_Line",
                    component_type="stop_line",
                    component_idx=junction_idx,
                )
                mesh.metadata.update(
                    {
                        "junction_marking_type": "stop_line",
                    }
                )
                stop_line_meshes.append(mesh)
                stats["stop_line_count"] += 1

    return stats


def junction_point_buckets(prepared_roads: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    """Cluster per-road junction distance hints into shared junction buckets.

    `road_gen.attach_junction_distances()` stores distances on individual road
    rows. This function projects those distances back to XY points, groups
    nearby points, and keeps only groups touched by at least two roads. The
    resulting buckets are the city-level junction candidates used by both mesh
    generation and semantic export.
    """
    buckets: dict[tuple[int, int], dict[str, Any]] = {}
    tolerance = max(road_gen.JUNCTION_NODE_TOLERANCE_M, 3.0)
    for road_idx, row in prepared_roads.iterrows():
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue
        for distance in road_gen.row_junction_distances(row):
            clamped_distance = clamp_junction_chainage(line, distance)
            if clamped_distance is None:
                continue
            point = line.interpolate(clamped_distance)
            key = (round(point.x / tolerance), round(point.y / tolerance))
            bucket = buckets.setdefault(key, {"points": [], "members": []})
            bucket["points"].append(point)
            bucket["members"].append((road_idx, float(clamped_distance)))

    result = []
    for bucket in buckets.values():
        if not bucket["points"]:
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


def asphalt_component_spans_for_components(
    components: list[dict[str, Any]],
    use_planar_surface_types: bool = False,
) -> list[tuple[dict[str, Any], float, float]]:
    spans = component_spans(components)
    if use_planar_surface_types:
        fill_offsets = junction_stop_line_fill_lateral_offsets_for_spans(spans)
        if fill_offsets is not None:
            left_offset, right_offset = fill_offsets
            return [
                (
                    {"type": "main_carriageway", "junction_fill": "sidewalk_inner_to_sidewalk_inner"},
                    float(left_offset),
                    float(right_offset),
                )
            ]
    surface_component_types = (
        JUNCTION_PLANAR_SURFACE_COMPONENT_TYPES
        if use_planar_surface_types
        else JUNCTION_ASPHALT_COMPONENT_TYPES
    )
    return [
        (component, left_offset, right_offset)
        for component, left_offset, right_offset in spans
        if str(component.get("type", "")) in surface_component_types
    ]


def ramp_merge_major_arm_ids(arms: list[dict[str, Any]]) -> set[str]:
    expressway_ids = {
        str(arm.get("arm_id", ""))
        for arm in arms
        if str(arm.get("category", "")) == "expressway"
    }
    if expressway_ids:
        return expressway_ids
    max_priority = max((int(arm.get("road_priority", 0) or 0) for arm in arms), default=0)
    max_width = max((float(arm.get("drivable_width_m", 0.0) or 0.0) for arm in arms), default=0.0)
    return {
        str(arm.get("arm_id", ""))
        for arm in arms
        if int(arm.get("road_priority", 0) or 0) == max_priority
        and float(arm.get("drivable_width_m", 0.0) or 0.0) >= max_width - 0.5
    }


def ramp_merge_host_arm_for_branch(
    branch_arm: dict[str, Any],
    major_arms: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not major_arms:
        return None
    branch_direction = arm_direction_vector(branch_arm)
    if branch_direction is None:
        return major_arms[0]

    def score(host_arm: dict[str, Any]) -> tuple[float, float]:
        separation = arm_signed_separation_deg(branch_arm, host_arm)
        if separation is None:
            return (float("inf"), 0.0)
        return (abs(float(separation) - 90.0), -float(host_arm.get("drivable_width_m", 0.0) or 0.0))

    return min(major_arms, key=score)


def clipped_line_segment_around_distance(
    line: LineString,
    center_distance: float,
    reach_m: float,
) -> LineString | None:
    if line is None or line.is_empty or line.length <= 0.1:
        return None
    center_distance = max(0.0, min(float(line.length), float(center_distance)))
    reach_m = max(float(reach_m), 0.0)
    start = max(0.0, center_distance - reach_m)
    end = min(float(line.length), center_distance + reach_m)
    if end - start <= 0.1:
        return None
    try:
        return substring(line, start, end)
    except Exception:
        return None


def ramp_merge_junction_polygon(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    point: Point,
    members: list[tuple[Any, float]],
):
    """Build a compact side-entry surface for ramp/merge junctions.

    A ramp/merge is not a full signalized intersection. The main carriageway
    should keep flowing through; only the side where the branch enters receives
    a short asphalt collar so the branch throat ties into the host road.
    """
    try:
        arms = junction_arm_records(prepared_roads, rules, point, members)
    except Exception:
        arms = []
    if len(arms) < 2:
        return None

    major_ids = ramp_merge_major_arm_ids(arms)
    major_arms = [arm for arm in arms if str(arm.get("arm_id", "")) in major_ids]
    branch_arms = [arm for arm in arms if str(arm.get("arm_id", "")) not in major_ids]
    if not major_arms or not branch_arms:
        return None

    parts = []
    widths = []
    for branch_arm in branch_arms:
        branch_idx = prepared_road_index_from_arm(prepared_roads, branch_arm)
        host_arm = ramp_merge_host_arm_for_branch(branch_arm, major_arms)
        host_idx = prepared_road_index_from_arm(prepared_roads, host_arm or {})
        if branch_idx is None or host_idx is None:
            continue

        branch_row = prepared_roads.loc[branch_idx]
        host_row = prepared_roads.loc[host_idx]
        branch_line = branch_row.geometry
        host_line = host_row.geometry
        if (
            branch_line is None
            or branch_line.is_empty
            or not isinstance(branch_line, LineString)
            or host_line is None
            or host_line.is_empty
            or not isinstance(host_line, LineString)
        ):
            continue

        branch_rule = road_gen.get_road_rule(branch_row, rules)
        host_rule = road_gen.get_road_rule(host_row, rules)
        branch_components = road_gen.cross_section_components_for_row(branch_row) or fallback_cross_section_components(branch_rule)
        host_components = road_gen.cross_section_components_for_row(host_row) or fallback_cross_section_components(host_rule)
        branch_spans = asphalt_component_spans_for_components(branch_components)
        host_spans = asphalt_component_spans_for_components(host_components)
        branch_roadway_offsets = junction_stop_line_fill_lateral_offsets_for_spans(
            component_spans(branch_components)
        )
        if branch_roadway_offsets is not None:
            branch_spans = [
                (
                    {"type": "main_carriageway"},
                    float(branch_roadway_offsets[0]),
                    float(branch_roadway_offsets[1]),
                )
            ]
        if not branch_spans:
            branch_half_width = max(drivable_width_for_row(branch_row, branch_rule) * 0.5, float(branch_rule.lane_width))
            branch_spans = [({"type": "main_carriageway"}, -branch_half_width, branch_half_width)]
        if not host_spans:
            host_half_width = max(drivable_width_for_row(host_row, host_rule) * 0.5, float(host_rule.lane_width))
            host_spans = [({"type": "main_carriageway"}, -host_half_width, host_half_width)]

        try:
            branch_node_distance = float(branch_arm.get("node_distance_m", branch_line.project(point)) or 0.0)
            branch_sign = float(branch_arm.get("line_direction_sign", 1.0) or 1.0)
        except Exception:
            continue
        branch_node_distance = max(0.0, min(float(branch_line.length), branch_node_distance))
        branch_width = junction_asphalt_surface_width(branch_row, branch_rule, branch_components, branch_spans)
        branch_throat = max(
            float(JUNCTION_RAMP_MERGE_BRANCH_THROAT_MIN_M),
            min(
                float(JUNCTION_RAMP_MERGE_BRANCH_THROAT_MAX_M),
                branch_width * 0.8 + 8.0,
            ),
        )
        branch_start, branch_end = sorted(
            (
                branch_node_distance,
                max(0.0, min(float(branch_line.length), branch_node_distance + branch_sign * branch_throat)),
            )
        )
        if branch_end - branch_start > 0.1:
            try:
                branch_segment = substring(branch_line, branch_start, branch_end)
            except Exception:
                branch_segment = None
            if branch_segment is not None and not branch_segment.is_empty:
                for _, left_offset, right_offset in branch_spans:
                    geom = swept_band_polygon(branch_segment, left_offset, right_offset)
                    if geom is not None and not geom.is_empty:
                        parts.append(geom)
                        widths.append(max(abs(float(left_offset)), abs(float(right_offset))) * 2.0)

        host_node_distance = max(0.0, min(float(host_line.length), float(host_line.project(point))))
        host_point, _, host_normal = road_gen.line_frame_at_distance(host_line, host_node_distance)
        branch_sample_distance = max(
            0.0,
            min(float(branch_line.length), branch_node_distance + branch_sign * min(branch_throat, 8.0)),
        )
        branch_sample = branch_line.interpolate(branch_sample_distance)
        side_dot = (
            (float(branch_sample.x) - float(host_point.x)) * float(host_normal[0])
            + (float(branch_sample.y) - float(host_point.y)) * float(host_normal[1])
        )
        side_sign = 1.0 if side_dot >= 0.0 else -1.0
        host_side_spans = []
        for component, left_offset, right_offset in host_spans:
            left = float(left_offset)
            right = float(right_offset)
            if side_sign > 0.0:
                clipped_left = max(left, 0.0)
                clipped_right = right
            else:
                clipped_left = left
                clipped_right = min(right, 0.0)
            if clipped_right - clipped_left > 0.1:
                host_side_spans.append((component, clipped_left, clipped_right))
        if not host_side_spans:
            host_width = junction_asphalt_surface_width(host_row, host_rule, host_components, host_spans)
            side_width = min(max(branch_width * 0.75, 6.0), max(host_width * 0.5, 6.0))
            host_side_spans = [
                (
                    {"type": "main_carriageway"},
                    0.0 if side_sign > 0.0 else -side_width,
                    side_width if side_sign > 0.0 else 0.0,
                )
            ]

        host_reach = max(
            float(JUNCTION_RAMP_MERGE_MAIN_SIDE_PATCH_MIN_M),
            min(
                float(JUNCTION_RAMP_MERGE_MAIN_SIDE_PATCH_MAX_M),
                branch_width * 1.15 + 8.0,
            ),
        )
        host_segment = clipped_line_segment_around_distance(host_line, host_node_distance, host_reach)
        if host_segment is not None and not host_segment.is_empty:
            for _, left_offset, right_offset in host_side_spans:
                geom = swept_band_polygon(host_segment, left_offset, right_offset)
                if geom is not None and not geom.is_empty:
                    parts.append(geom)
                    widths.append(max(abs(float(left_offset)), abs(float(right_offset))) * 2.0)

        center_radius = min(
            float(JUNCTION_RAMP_MERGE_CENTER_FILL_MAX_RADIUS_M),
            max(branch_width * 0.35, 3.0),
        )
        if center_radius > 0.0:
            parts.append(point.buffer(center_radius, resolution=8))
            widths.append(center_radius * 2.0)

    if not parts:
        return None
    try:
        geom = road_gen.clean_polygonal(unary_union(parts))
        if geom is None or geom.is_empty:
            return None
        smooth = max(float(JUNCTION_RAMP_MERGE_SMOOTH_M), 0.0)
        if smooth > 0.0:
            geom = road_gen.clean_polygonal(
                geom.buffer(smooth, join_style=1, resolution=8).buffer(
                    -smooth,
                    join_style=1,
                    resolution=8,
                )
            )
        return geom
    except Exception:
        return None


def rounded_junction_polygon(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    point: Point,
    members: list[tuple[Any, float]],
    design_parameters: dict[str, Any] | None = None,
):
    """Build one rounded asphalt conflict-area polygon for a junction bucket.

    The polygon is made from short throat segments from every member road,
    adding a compact center core, unioning the pieces, and applying a small
    smooth/unsmooth pass. Ordinary planar intersections use flat road-edge
    throats with modest curb-return smoothing so the surface reads like a real
    street junction instead of a set of round-ended patches.
    """
    parts = []
    widths = []
    design_parameters = design_parameters or {}
    throat_multiplier = float(design_parameters.get("throat_multiplier", 1.0) or 1.0)
    core_radius_multiplier = float(design_parameters.get("core_radius_multiplier", 1.0) or 1.0)
    smooth_multiplier = float(design_parameters.get("smooth_multiplier", 1.0) or 1.0)
    min_throat = float(design_parameters.get("min_throat_m", JUNCTION_PATCH_MIN_THROAT_M) or JUNCTION_PATCH_MIN_THROAT_M)
    max_throat = float(design_parameters.get("max_throat_m", JUNCTION_PATCH_MAX_THROAT_M) or JUNCTION_PATCH_MAX_THROAT_M)
    junction_type = str(design_parameters.get("junction_type", ""))
    if junction_type == "RAMP_MERGE":
        ramp_geom = ramp_merge_junction_polygon(prepared_roads, rules, point, members)
        if ramp_geom is not None and not ramp_geom.is_empty:
            return ramp_geom
    use_real_world_planar_edges = junction_type in REAL_WORLD_PLANAR_JUNCTION_TYPES
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
        components = road_gen.cross_section_components_for_row(row)
        if not components:
            components = fallback_cross_section_components(rule)
        asphalt_spans = asphalt_component_spans_for_components(
            components,
            use_real_world_planar_edges,
        )
        width = junction_asphalt_surface_width(row, rule, components, asphalt_spans)
        if width <= 0.1:
            continue
        drivable_extent_width = max(
            [width]
            + [
                max(abs(float(left_offset)), abs(float(right_offset))) * 2.0
                for _, left_offset, right_offset in asphalt_spans
            ]
        )
        widths.append(drivable_extent_width)
        base_throat = max(
            min_throat,
            min(max_throat, width * 0.75 + 6.0),
        )
        throat = max(min_throat, min(max_throat, base_throat * throat_multiplier))
        if line.distance(point) <= max(road_gen.junction_connection_tolerance(), JUNCTION_BUCKET_CLUSTER_M):
            distance_hints.append(float(line.project(point)))
        clamped_distances = [max(0.0, min(float(line.length), float(distance))) for distance in distance_hints]
        endpoint_gap = min(
            min(clamped_distances),
            float(line.length) - max(clamped_distances),
        )
        is_through_t_junction_road = (
            junction_type == "T_JUNCTION"
            and endpoint_gap
            > max(
                road_gen.junction_connection_tolerance() * 2.0,
                width * 0.5,
                6.0,
            )
        )
        start = max(0.0, min(clamped_distances) - throat)
        end = min(float(line.length), max(clamped_distances) + throat)
        if is_through_t_junction_road:
            through_throat = max(
                min(float(throat), width * 0.72 + 4.0),
                10.0,
            )
            center_distance = sum(clamped_distances) / max(len(clamped_distances), 1)
            start = max(0.0, center_distance - through_throat)
            end = min(float(line.length), center_distance + through_throat)
        if end - start <= 0.1:
            continue
        try:
            center_segment = substring(line, start, end)
        except Exception:
            continue
        if center_segment is None or center_segment.is_empty:
            continue
        drivable_parts = []
        for _, left_offset, right_offset in asphalt_spans:
            geom = (
                swept_band_polygon(center_segment, left_offset, right_offset)
                if use_real_world_planar_edges
                else swept_band_polygon_round_caps(center_segment, left_offset, right_offset)
            )
            if geom is not None and not geom.is_empty:
                drivable_parts.append(geom)
        if drivable_parts:
            parts.extend(drivable_parts)
        else:
            parts.append(center_segment.buffer(width / 2.0, cap_style=2, join_style=1, resolution=12))

    if len(parts) < 2:
        return None

    if use_real_world_planar_edges:
        widest = max(widths, default=0.0)
        narrowest = min(widths) if widths else 0.0
        core_radius = max(
            2.5,
            min(
                widest * 0.22 * core_radius_multiplier + 1.0,
                max(narrowest * 0.35, 3.0),
                6.0,
            ),
        )
        curb_return_radius = min(
            max(narrowest * 0.34, float(JUNCTION_CURB_RETURN_MIN_RADIUS_M)),
            max(widest * 0.24, 5.5),
            float(JUNCTION_CURB_RETURN_MAX_RADIUS_M),
        )
        center_search_radius = max(
            core_radius + 1.5,
            min(max(widest * 0.58, narrowest * 0.9, 6.0), 18.0),
        )
        center_points = []
        center_x = float(point.x)
        center_y = float(point.y)
        search_radius_sq = center_search_radius * center_search_radius
        for geom_part in parts:
            for polygon in iter_polygons(geom_part):
                for x, y in polygon.exterior.coords:
                    dx = float(x) - center_x
                    dy = float(y) - center_y
                    if dx * dx + dy * dy <= search_radius_sq:
                        center_points.append((float(x), float(y)))
        center_fill = None
        if len(center_points) >= 3:
            try:
                center_fill = road_gen.clean_polygonal(MultiPoint(center_points).convex_hull)
            except Exception:
                center_fill = None
        if center_fill is None or center_fill.is_empty or center_fill.area < 1.0:
            center_fill = point.buffer(core_radius, resolution=1)
        if center_fill is not None and not center_fill.is_empty:
            parts.append(center_fill)
    else:
        core_radius = max(max(widths, default=0.0) * 0.45 * core_radius_multiplier, 4.0)
        curb_return_radius = 0.0
        parts.append(point.buffer(core_radius, resolution=18))
    try:
        geom = unary_union(parts)
        if use_real_world_planar_edges:
            # Real T/Y/cross intersections have curb-return paving outside the
            # through corridor. Closing the flat-edged throat union fills those
            # apron corners without going back to a round-ended blob.
            if curb_return_radius > 0.0:
                closed_geom = road_gen.clean_polygonal(
                    geom.buffer(curb_return_radius, join_style=1, resolution=10).buffer(
                        -curb_return_radius,
                        join_style=1,
                        resolution=10,
                    )
                )
                if closed_geom is not None and not closed_geom.is_empty:
                    geom = closed_geom
            smooth = min(max(0.55, JUNCTION_PATCH_SMOOTH_M * smooth_multiplier * 0.35), 1.15)
        else:
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
        "throat_multiplier": 1.04,
        "core_radius_multiplier": 1.02,
        "smooth_multiplier": 1.0,
        "min_throat_m": 8.0,
        "max_throat_m": 34.0,
        "control": "major_priority_minor_stop",
        "marking_policy": "crosswalk_and_stop_on_minor_approaches",
    },
    "SIGNALIZED_ARTERIAL": {
        "label": "signalized arterial intersection",
        "fits_types": {"CROSS_JUNCTION", "T_JUNCTION", "SKEWED_CROSS_OR_MULTI_ARM"},
        "fits_hierarchies": {"MAJOR_ARTERIAL", "SECONDARY_COLLECTOR"},
        "max_arms": 4,
        "target_max_lane": 6,
        "throat_multiplier": 1.06,
        "core_radius_multiplier": 0.92,
        "smooth_multiplier": 0.95,
        "min_throat_m": 11.0,
        "max_throat_m": 34.0,
        "control": "signalized",
        "marking_policy": "crosswalks_stop_lines_and_turn_pockets",
    },
    "CHANNELIZED_MULTI_ARM": {
        "label": "channelized multi-arm junction",
        "fits_types": {"MULTI_ARM_JUNCTION", "SKEWED_CROSS_OR_MULTI_ARM"},
        "fits_hierarchies": {"COMPLEX_MULTI_ARM", "MAJOR_ARTERIAL"},
        "max_arms": 8,
        "target_max_lane": 8,
        "throat_multiplier": 1.12,
        "core_radius_multiplier": 1.0,
        "smooth_multiplier": 1.05,
        "min_throat_m": 12.0,
        "max_throat_m": 38.0,
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
        "throat_multiplier": 1.08,
        "core_radius_multiplier": 0.78,
        "smooth_multiplier": 1.1,
        "min_throat_m": 10.0,
        "max_throat_m": 38.0,
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
        "road_level_counts": dict(Counter(arm.get("road_level_key", "unknown") for arm in arms)),
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
        "junction_type": junction_type,
        "junction_hierarchy": hierarchy,
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
            "junction_type": "UNKNOWN",
            "junction_hierarchy": "LOCAL_JUNCTION",
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
    design = evaluate_junction_design_options(arms, junction_type, hierarchy)
    design["junction_type"] = junction_type
    design["junction_hierarchy"] = hierarchy
    return design


def build_rounded_junction_surface_geometries(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    buckets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Create all city-level rounded junction surface geometries.

    Each returned item stores the polygon plus the source bucket, connected road
    members, and selected design option. Keeping this metadata with the geometry
    lets later steps derive clip ranges, semantic junction records, and QC scores
    from the same junction decision.
    """
    if not ENABLE_ROUNDED_JUNCTION_SURFACES or prepared_roads.empty:
        return []

    surfaces: list[dict[str, Any]] = []
    for idx, bucket in enumerate(buckets if buckets is not None else junction_point_buckets(prepared_roads)):
        design = selected_junction_design_for_bucket(prepared_roads, rules, bucket["point"], bucket["members"])
        selected = design.get("selected_design_option") or {}
        surface_parameters = dict(selected.get("surface_parameters") or {})
        surface_parameters["junction_type"] = design.get("junction_type", "UNKNOWN")
        surface_parameters["junction_hierarchy"] = design.get("junction_hierarchy", "LOCAL_JUNCTION")
        geom = rounded_junction_polygon(
            prepared_roads,
            rules,
            bucket["point"],
            bucket["members"],
            surface_parameters,
        )
        if geom is None or geom.is_empty:
            continue
        surfaces.append(
            {
                "index": idx,
                "geometry": geom,
                "members": bucket["members"],
                "point": bucket["point"],
                "junction_type": design.get("junction_type", "UNKNOWN"),
                "junction_hierarchy": design.get("junction_hierarchy", "LOCAL_JUNCTION"),
                "design": design,
            }
        )
    return surfaces


def build_rounded_junction_surface_meshes(
    prepared_roads: gpd.GeoDataFrame,
    surface_geometries: list[dict[str, Any]],
    subtract_geometries_by_surface: dict[int, list[Any]] | None = None,
    add_geometries_by_surface: dict[int, list[Any]] | None = None,
) -> list[trimesh.Trimesh]:
    meshes = []
    subtract_geometries_by_surface = subtract_geometries_by_surface or {}
    add_geometries_by_surface = add_geometries_by_surface or {}
    for surface in surface_geometries:
        idx = int(surface["index"])
        geom = surface["geometry"]
        add_parts = [
            part
            for part in add_geometries_by_surface.get(idx, [])
            if part is not None and not part.is_empty
        ]
        if add_parts:
            try:
                geom = road_gen.clean_polygonal(unary_union([geom, *add_parts]))
            except Exception:
                pass
        subtract_parts = [
            part
            for part in subtract_geometries_by_surface.get(idx, [])
            if part is not None and not part.is_empty
        ]
        if subtract_parts:
            try:
                geom = road_gen.clean_polygonal(geom.difference(unary_union(subtract_parts)))
            except Exception:
                pass
        if geom is None or geom.is_empty:
            continue
        mesh = road_gen.polygon_to_top_mesh(
            geom,
            junction_surface_base_z(prepared_roads, surface.get("members", [])) + JUNCTION_SURFACE_Z_OFFSET_M,
            f"Junction_Surface_{idx}",
            visual_color=COLORS["road_surface_main"],
        )
        if len(mesh.vertices) > 0:
            mesh.metadata["name"] = f"Junction_Surface_{idx}"
            meshes.append(mesh)
    return meshes


def junction_surface_boundary_distance_for_arm(
    line: LineString,
    surface_geom,
    arm: dict[str, Any],
) -> float | None:
    if line is None or line.is_empty or surface_geom is None or surface_geom.is_empty:
        return None
    side_sign = -1.0 if float(arm.get("line_direction_sign", 0.0) or 0.0) < 0.0 else 1.0
    node_distance = clamp_junction_chainage(line, float(arm.get("node_distance_m", 0.0) or 0.0))
    ranges = line_intersection_distance_ranges(line, surface_geom)
    if node_distance is not None:
        containing = [
            (start, end)
            for start, end in ranges
            if float(start) - JUNCTION_CHAINAGE_EPSILON_M <= float(node_distance) <= float(end) + JUNCTION_CHAINAGE_EPSILON_M
        ]
        if containing:
            start, end = min(containing, key=lambda item: abs((item[0] + item[1]) * 0.5 - float(node_distance)))
            return float(start) if side_sign < 0.0 else float(end)

    candidates = [
        float(start) if side_sign < 0.0 else float(end)
        for start, end in ranges
    ]
    if not candidates:
        return None
    if node_distance is None:
        return min(candidates)
    return min(candidates, key=lambda value: abs(float(value) - float(node_distance)))


def one_sided_sidewalk_protection_geometries_by_surface(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    surface_geometries: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    protected: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for surface in surface_geometries:
        surface_idx = int(surface.get("index", -1))
        surface_geom = surface.get("geometry")
        point = surface.get("point")
        if surface_idx < 0 or surface_geom is None or surface_geom.is_empty or point is None or getattr(point, "is_empty", False):
            continue
        try:
            arms = junction_arm_records(prepared_roads, rules, point, surface.get("members", []))
        except Exception:
            continue
        has_expressway_arm = any(
            str(item.get("category", "") or "").strip().lower() == "expressway"
            for item in arms
        )
        for arm in arms:
            category = str(arm.get("category", "") or "").strip().lower()
            if has_expressway_arm and category != "expressway":
                continue
            road_idx = prepared_road_index_from_arm(prepared_roads, arm)
            if road_idx is None:
                continue
            row = prepared_roads.loc[road_idx]
            line = row.geometry
            if line is None or line.is_empty or not isinstance(line, LineString):
                continue
            if road_gen.row_section_code(row) != "D6":
                continue
            rule = road_gen.get_road_rule(row, rules)
            components = road_gen.cross_section_components_for_row(row)
            if not components:
                components = fallback_cross_section_components(rule)
            spans = component_spans(components)
            sidewalk_spans = [
                (component_idx, component, float(left), float(right))
                for component_idx, (component, left, right) in enumerate(spans)
                if str(component.get("type", "")) == "sidewalk" and float(right) - float(left) > 0.05
            ]
            if len(sidewalk_spans) != 1:
                continue
            component_idx, _sidewalk_component, left_offset, right_offset = sidewalk_spans[0]
            if not (right_offset <= -0.05 or left_offset >= 0.05):
                continue
            boundary_distance = junction_surface_boundary_distance_for_arm(line, surface_geom, arm)
            if boundary_distance is None:
                boundary_distance = junction_sidewalk_connector_distance(
                    line,
                    row,
                    rule,
                    arm,
                    arms,
                    outward_extra_m=JUNCTION_ROADSIDE_SEAM_OVERLAP_M,
                )
            stop_distance = junction_approach_element_stop_distance(line, row, rule, arm, arms)
            if boundary_distance is None or stop_distance is None:
                continue
            start = max(0.0, min(float(boundary_distance), float(stop_distance)))
            end = min(float(line.length), max(float(boundary_distance), float(stop_distance)))
            if end - start <= 0.05:
                continue
            try:
                segment = substring(line, start, end)
            except Exception:
                continue
            if segment is None or segment.is_empty:
                continue
            sidewalk_geom = swept_band_polygon(segment, left_offset, right_offset)
            if sidewalk_geom is None or sidewalk_geom.is_empty:
                continue
            try:
                sidewalk_geom = road_gen.clean_polygonal(sidewalk_geom)
            except Exception:
                pass
            if sidewalk_geom is None or sidewalk_geom.is_empty:
                continue
            protected[surface_idx].append(
                {
                    "geometry": sidewalk_geom,
                    "road_idx": road_idx,
                    "row": row,
                    "rule": rule,
                    "component_type": "sidewalk",
                    "component_idx": component_idx,
                }
            )
    return protected


def one_sided_sidewalk_protection_meshes(
    protected_by_surface: dict[int, list[dict[str, Any]]],
) -> list[trimesh.Trimesh]:
    meshes: list[trimesh.Trimesh] = []
    for surface_idx, items in sorted(protected_by_surface.items()):
        for item_idx, item in enumerate(items):
            geom = item.get("geometry")
            if geom is None or geom.is_empty:
                continue
            mesh = road_gen.polygon_to_top_mesh(
                geom,
                road_surface_z_for_row(item["row"]) + component_z_offset("sidewalk", item["rule"]),
                f"Sidewalk_Junction_One_Sided_{surface_idx}_{item_idx}",
                visual_color=COLORS["sidewalk"],
            )
            if len(mesh.vertices) == 0:
                continue
            apply_road_feature_metadata(
                mesh,
                item["row"],
                item["rule"],
                layer_name="Sidewalk",
                component_type="sidewalk",
                component_idx=item.get("component_idx"),
            )
            mesh.metadata.update(
                {
                    "name": f"Sidewalk_Junction_One_Sided_{surface_idx}_{item_idx}",
                    "junction_index": surface_idx,
                    "cim_entity_type": "junction_one_sided_sidewalk_protection",
                }
            )
            meshes.append(mesh)
    return meshes


def junction_connector_footprints_by_surface(
    mesh_groups: dict[str, list[trimesh.Trimesh]],
) -> dict[int, list[Any]]:
    footprints: dict[int, list[Any]] = defaultdict(list)
    for meshes in mesh_groups.values():
        for mesh in meshes:
            metadata = mesh.metadata or {}
            wkt = metadata.get("footprint_wkt")
            if not wkt:
                continue
            try:
                surface_idx = int(metadata.get("junction_index", -1))
                geom = shapely.from_wkt(str(wkt))
            except Exception:
                continue
            if surface_idx >= 0 and geom is not None and not geom.is_empty:
                footprints[surface_idx].append(geom)
    return footprints


def merge_junction_footprints_by_surface(
    *mesh_groups_list: dict[str, list[trimesh.Trimesh]],
) -> dict[int, list[Any]]:
    merged: dict[int, list[Any]] = defaultdict(list)
    for mesh_groups in mesh_groups_list:
        for surface_idx, footprints in junction_connector_footprints_by_surface(mesh_groups).items():
            merged[int(surface_idx)].extend(footprints)
    return dict(merged)


def junction_surface_fill_geometries_by_surface(
    surface_geometries: list[dict[str, Any]],
    add_geometries_by_surface: dict[int, list[Any]] | None = None,
    subtract_geometries_by_surface: dict[int, list[Any]] | None = None,
) -> dict[int, Any]:
    add_geometries_by_surface = add_geometries_by_surface or {}
    subtract_geometries_by_surface = subtract_geometries_by_surface or {}
    filled: dict[int, Any] = {}
    for surface in surface_geometries:
        idx = int(surface["index"])
        geom = surface.get("geometry")
        if geom is None or geom.is_empty:
            continue
        add_parts = [
            part
            for part in add_geometries_by_surface.get(idx, [])
            if part is not None and not part.is_empty
        ]
        if add_parts:
            try:
                geom = road_gen.clean_polygonal(unary_union([geom, *add_parts]))
            except Exception:
                pass
        subtract_parts = [
            part
            for part in subtract_geometries_by_surface.get(idx, [])
            if part is not None and not part.is_empty
        ]
        if subtract_parts:
            try:
                geom = road_gen.clean_polygonal(geom.difference(unary_union(subtract_parts)))
            except Exception:
                pass
        if geom is not None and not geom.is_empty:
            filled[idx] = geom
    return filled


def polygonal_union(geoms: Iterable[Any], clearance: float = 0.0):
    parts = [geom for geom in geoms if geom is not None and not geom.is_empty]
    if not parts:
        return None
    try:
        geom = road_gen.clean_polygonal(unary_union(parts))
    except Exception:
        geom = None
    if geom is None or geom.is_empty:
        return None
    if clearance > 0.0:
        try:
            geom = road_gen.clean_polygonal(geom.buffer(clearance, resolution=6, join_style=1))
        except Exception:
            pass
    return geom














def junction_mesh_group_count(mesh_groups: dict[str, list[trimesh.Trimesh]]) -> int:
    return sum(len(meshes) for meshes in mesh_groups.values())


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
    vertices = np.asarray(mesh.vertices)
    face_polygons = []
    for face in np.asarray(mesh.faces):
        coords = [
            (float(vertices[int(vertex_idx)][0]), float(vertices[int(vertex_idx)][1]))
            for vertex_idx in face
        ]
        if len(coords) < 3:
            continue
        try:
            polygon = Polygon(coords)
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            if polygon is not None and not polygon.is_empty and float(polygon.area) > 1e-8:
                face_polygons.append(polygon)
        except Exception:
            continue
    if face_polygons:
        try:
            footprint = road_gen.clean_polygonal(unary_union(face_polygons))
            if footprint is not None and not footprint.is_empty:
                return footprint
        except Exception:
            pass

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


def mesh_top_z(mesh: trimesh.Trimesh, default: float = 0.0) -> float:
    if mesh is None or len(mesh.vertices) == 0:
        return float(default)
    try:
        return float(np.mean(np.asarray(mesh.vertices)[:, 2]))
    except Exception:
        return float(default)


def mesh_visual_color(mesh: trimesh.Trimesh) -> list[int] | None:
    try:
        colors = np.asarray(mesh.visual.face_colors)
        if len(colors) > 0:
            return colors[0].tolist()
    except Exception:
        pass
    return None


def polygonal_parts_above_area(geom, min_area_m2: float):
    if geom is None or geom.is_empty:
        return None
    parts = [
        polygon
        for polygon in iter_polygons(geom)
        if polygon is not None and not polygon.is_empty and float(polygon.area) >= float(min_area_m2)
    ]
    if not parts:
        return None
    try:
        return road_gen.clean_polygonal(unary_union(parts))
    except Exception:
        return None


def remesh_planar_footprint_like(mesh: trimesh.Trimesh, geom, name: str | None = None) -> trimesh.Trimesh | None:
    geom = polygonal_parts_above_area(geom, SIDEWALK_MIN_FRAGMENT_AREA_M2)
    if geom is None or geom.is_empty:
        return None
    metadata = dict(mesh.metadata or {})
    output_name = str(name or metadata.get("name") or "mesh")
    result = road_gen.polygon_to_top_mesh(
        geom,
        mesh_top_z(mesh),
        output_name,
        visual_color=mesh_visual_color(mesh),
    )
    if result is None or len(result.vertices) == 0:
        return None
    result.metadata.update(metadata)
    result.metadata["name"] = output_name
    return result


def bounds_gap_distance_m(bounds_a: tuple[float, float, float, float], bounds_b: tuple[float, float, float, float]) -> float:
    minx_a, miny_a, maxx_a, maxy_a = bounds_a
    minx_b, miny_b, maxx_b, maxy_b = bounds_b
    dx = max(float(minx_a) - float(maxx_b), float(minx_b) - float(maxx_a), 0.0)
    dy = max(float(miny_a) - float(maxy_b), float(miny_b) - float(maxy_a), 0.0)
    return math.hypot(dx, dy)


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


def filter_meshes_with_center_inside_polygon(meshes: list[trimesh.Trimesh], geom) -> tuple[list[trimesh.Trimesh], int]:
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


def filter_non_expressway_meshes_without_polygon_overlap(
    meshes: list[trimesh.Trimesh],
    geom,
) -> tuple[list[trimesh.Trimesh], int]:
    if geom is None or geom.is_empty:
        return meshes, 0
    kept = []
    removed = 0
    for mesh in meshes:
        if (mesh.metadata or {}).get("road_category") == "expressway":
            kept.append(mesh)
            continue
        footprint = mesh_xy_footprint(mesh)
        if footprint is not None and geom.intersects(footprint):
            removed += 1
            continue
        kept.append(mesh)
    return kept, removed


SIDEWALK_CONFLICT_LAYERS = {
    "Junction_Surface",
    "Road_Surface_Main",
    "Road_Surface_Service",
    "Road_Surface_Branch",
    "Non_Motor_Lane",
    "Parking_Lane",
}


def mesh_footprint_polygon_parts(
    mesh: trimesh.Trimesh,
    *,
    min_area_m2: float = SIDEWALK_MIN_FRAGMENT_AREA_M2,
) -> list[Polygon]:
    footprint = mesh_xy_footprint(mesh)
    if footprint is None or footprint.is_empty:
        return []
    return [
        polygon
        for polygon in iter_polygons(footprint)
        if polygon is not None and not polygon.is_empty and float(polygon.area) >= float(min_area_m2)
    ]


def footprint_union_for_meshes(meshes: Iterable[trimesh.Trimesh]):
    footprints = [
        footprint
        for footprint in (mesh_xy_footprint(mesh) for mesh in meshes)
        if footprint is not None and not footprint.is_empty
    ]
    if not footprints:
        return None
    try:
        return road_gen.clean_polygonal(unary_union(footprints))
    except Exception:
        return None


def sidewalk_conflict_meshes_from_groups(
    component_mesh_groups: dict[str, list[trimesh.Trimesh]],
    junction_surface_meshes: list[trimesh.Trimesh],
) -> list[trimesh.Trimesh]:
    conflict_meshes: list[trimesh.Trimesh] = list(junction_surface_meshes)
    for layer_name in sorted(SIDEWALK_CONFLICT_LAYERS - {"Junction_Surface"}):
        conflict_meshes.extend(component_mesh_groups.get(layer_name, []))
    return conflict_meshes


def sidewalk_conflict_footprint(
    component_mesh_groups: dict[str, list[trimesh.Trimesh]],
    junction_surface_meshes: list[trimesh.Trimesh],
):
    return footprint_union_for_meshes(
        sidewalk_conflict_meshes_from_groups(component_mesh_groups, junction_surface_meshes)
    )


def conflict_index_for_meshes(meshes: Iterable[trimesh.Trimesh]) -> dict[str, Any]:
    parts: list[Polygon] = []
    for mesh in meshes:
        parts.extend(mesh_footprint_polygon_parts(mesh, min_area_m2=SIDEWALK_MIN_FRAGMENT_AREA_M2))
    return {
        "parts": parts,
        "tree": STRtree(parts) if parts else None,
    }


def sidewalk_conflict_index(
    component_mesh_groups: dict[str, list[trimesh.Trimesh]],
    junction_surface_meshes: list[trimesh.Trimesh],
) -> dict[str, Any]:
    return conflict_index_for_meshes(
        sidewalk_conflict_meshes_from_groups(component_mesh_groups, junction_surface_meshes)
    )


def sidewalk_conflict_index_from_road_meshes(road_meshes: dict[str, trimesh.Trimesh]) -> dict[str, Any]:
    return conflict_index_for_meshes(
        mesh
        for object_name, mesh in road_meshes.items()
        if road_mesh_layer_name(object_name, mesh) in SIDEWALK_CONFLICT_LAYERS
    )


def indexed_conflict_candidates(index: dict[str, Any], geom) -> list[Polygon]:
    tree = index.get("tree")
    parts = index.get("parts") or []
    if tree is None or not parts or geom is None or geom.is_empty:
        return []
    try:
        hits = tree.query(geom.envelope)
    except Exception:
        return []
    candidates: list[Polygon] = []
    for item in hits:
        try:
            candidate = parts[int(item)] if isinstance(item, (int, np.integer)) else item
        except Exception:
            continue
        if candidate is not None and not candidate.is_empty:
            candidates.append(candidate)
    return candidates


def indexed_local_conflict_geom(
    index: dict[str, Any],
    geom,
    *,
    clearance_m: float = 0.0,
):
    if geom is None or geom.is_empty:
        return None
    query_geom = geom
    if clearance_m > 0.0:
        try:
            query_geom = geom.envelope.buffer(float(clearance_m), resolution=1, join_style=1)
        except Exception:
            query_geom = geom.envelope
    local_parts = []
    for candidate in indexed_conflict_candidates(index, query_geom):
        try:
            if clearance_m > 0.0:
                if float(candidate.distance(geom)) > float(clearance_m):
                    continue
                local_parts.append(candidate.buffer(float(clearance_m), resolution=1, join_style=1))
            elif candidate.intersects(geom):
                local_parts.append(candidate)
        except Exception:
            continue
    if not local_parts:
        return None
    try:
        return road_gen.clean_polygonal(unary_union(local_parts))
    except Exception:
        return None


def trim_sidewalk_meshes_against_conflicts(
    meshes: list[trimesh.Trimesh],
    conflict_geom,
    *,
    clearance_m: float = SIDEWALK_CONFLICT_CLEARANCE_M,
    overlap_tolerance_m2: float = SIDEWALK_CONFLICT_OVERLAP_TOLERANCE_M2,
    min_fragment_area_m2: float = SIDEWALK_MIN_FRAGMENT_AREA_M2,
) -> tuple[list[trimesh.Trimesh], dict[str, Any]]:
    stats: dict[str, Any] = {
        "input_count": len(meshes),
        "output_count": len(meshes),
        "trimmed_count": 0,
        "removed_count": 0,
        "overlap_area_m2": 0.0,
        "removed_area_m2": 0.0,
        "examples": [],
    }
    if conflict_geom is None or conflict_geom.is_empty or not meshes:
        return meshes, stats
    try:
        conflict = (
            road_gen.clean_polygonal(conflict_geom.buffer(float(clearance_m), resolution=2, join_style=1))
            if clearance_m > 0.0
            else conflict_geom
        )
    except Exception:
        conflict = conflict_geom
    if conflict is None or conflict.is_empty:
        return meshes, stats

    cleaned_meshes: list[trimesh.Trimesh] = []
    for mesh in meshes:
        footprint = mesh_xy_footprint(mesh)
        if footprint is None or footprint.is_empty or not conflict.intersects(footprint):
            cleaned_meshes.append(mesh)
            continue
        try:
            overlap_area = float(footprint.intersection(conflict).area)
        except Exception:
            overlap_area = 0.0
        if overlap_area <= float(overlap_tolerance_m2):
            cleaned_meshes.append(mesh)
            continue

        try:
            remaining = road_gen.clean_polygonal(footprint.difference(conflict))
        except Exception:
            remaining = None
        remaining = polygonal_parts_above_area(remaining, min_fragment_area_m2)
        original_area = float(footprint.area)
        remaining_area = float(remaining.area) if remaining is not None and not remaining.is_empty else 0.0
        removed_area = max(original_area - remaining_area, 0.0)
        stats["overlap_area_m2"] += overlap_area
        stats["removed_area_m2"] += removed_area
        if remaining is None or remaining.is_empty:
            stats["removed_count"] += 1
            if len(stats["examples"]) < 10:
                point = footprint.representative_point()
                stats["examples"].append(
                    {
                        "name": str((mesh.metadata or {}).get("name") or ""),
                        "action": "removed",
                        "x": round(float(point.x), 3),
                        "y": round(float(point.y), 3),
                        "overlap_area_m2": round(overlap_area, 4),
                    }
                )
            continue

        remeshed = remesh_planar_footprint_like(mesh, remaining)
        if remeshed is None or len(remeshed.vertices) == 0:
            stats["removed_count"] += 1
            continue
        cleaned_meshes.append(remeshed)
        stats["trimmed_count"] += 1
        if len(stats["examples"]) < 10:
            point = footprint.intersection(conflict).representative_point()
            stats["examples"].append(
                {
                    "name": str((mesh.metadata or {}).get("name") or ""),
                    "action": "trimmed",
                    "x": round(float(point.x), 3),
                    "y": round(float(point.y), 3),
                    "overlap_area_m2": round(overlap_area, 4),
                }
            )

    stats["output_count"] = len(cleaned_meshes)
    stats["overlap_area_m2"] = round(float(stats["overlap_area_m2"]), 6)
    stats["removed_area_m2"] = round(float(stats["removed_area_m2"]), 6)
    return cleaned_meshes, stats


def trim_sidewalk_meshes_against_indexed_conflicts(
    meshes: list[trimesh.Trimesh],
    conflict_index: dict[str, Any],
    *,
    clearance_m: float = SIDEWALK_CONFLICT_CLEARANCE_M,
    overlap_tolerance_m2: float = SIDEWALK_CONFLICT_OVERLAP_TOLERANCE_M2,
    min_fragment_area_m2: float = SIDEWALK_MIN_FRAGMENT_AREA_M2,
) -> tuple[list[trimesh.Trimesh], dict[str, Any]]:
    stats: dict[str, Any] = {
        "input_count": len(meshes),
        "output_count": len(meshes),
        "trimmed_count": 0,
        "removed_count": 0,
        "overlap_area_m2": 0.0,
        "removed_area_m2": 0.0,
        "examples": [],
    }
    if not meshes or not conflict_index.get("parts"):
        return meshes, stats

    cleaned_meshes: list[trimesh.Trimesh] = []
    for mesh in meshes:
        footprint_parts = mesh_footprint_polygon_parts(mesh, min_area_m2=min_fragment_area_m2)
        if not footprint_parts:
            cleaned_meshes.append(mesh)
            continue
        remaining_parts: list[Polygon] = []
        mesh_overlap_area = 0.0
        mesh_changed = False
        for footprint in footprint_parts:
            local_conflict = indexed_local_conflict_geom(
                conflict_index,
                footprint,
                clearance_m=clearance_m,
            )
            if local_conflict is None or local_conflict.is_empty or not local_conflict.intersects(footprint):
                remaining_parts.append(footprint)
                continue
            try:
                overlap = footprint.intersection(local_conflict)
                overlap_area = float(overlap.area)
            except Exception:
                overlap = None
                overlap_area = 0.0
            if overlap_area <= float(overlap_tolerance_m2):
                remaining_parts.append(footprint)
                continue
            mesh_changed = True
            mesh_overlap_area += overlap_area
            try:
                remaining = road_gen.clean_polygonal(footprint.difference(local_conflict))
            except Exception:
                remaining = None
            if remaining is None or remaining.is_empty:
                if len(stats["examples"]) < 10 and overlap is not None and not overlap.is_empty:
                    point = overlap.representative_point()
                    stats["examples"].append(
                        {
                            "name": str((mesh.metadata or {}).get("name") or ""),
                            "action": "removed_part",
                            "x": round(float(point.x), 3),
                            "y": round(float(point.y), 3),
                            "overlap_area_m2": round(overlap_area, 4),
                        }
                    )
                continue
            for part in iter_polygons(remaining):
                if part is not None and not part.is_empty and float(part.area) >= float(min_fragment_area_m2):
                    remaining_parts.append(part)
            if len(stats["examples"]) < 10 and overlap is not None and not overlap.is_empty:
                point = overlap.representative_point()
                stats["examples"].append(
                    {
                        "name": str((mesh.metadata or {}).get("name") or ""),
                        "action": "trimmed",
                        "x": round(float(point.x), 3),
                        "y": round(float(point.y), 3),
                        "overlap_area_m2": round(overlap_area, 4),
                    }
                )

        if not mesh_changed:
            cleaned_meshes.append(mesh)
            continue

        original_area = sum(float(part.area) for part in footprint_parts)
        remaining_area = sum(float(part.area) for part in remaining_parts)
        stats["overlap_area_m2"] += mesh_overlap_area
        stats["removed_area_m2"] += max(original_area - remaining_area, 0.0)
        if not remaining_parts:
            stats["removed_count"] += 1
            continue
        try:
            remaining_geom = road_gen.clean_polygonal(unary_union(remaining_parts))
        except Exception:
            remaining_geom = None
        remeshed = remesh_planar_footprint_like(mesh, remaining_geom)
        if remeshed is None or len(remeshed.vertices) == 0:
            stats["removed_count"] += 1
            continue
        cleaned_meshes.append(remeshed)
        stats["trimmed_count"] += 1

    stats["output_count"] = len(cleaned_meshes)
    stats["overlap_area_m2"] = round(float(stats["overlap_area_m2"]), 6)
    stats["removed_area_m2"] = round(float(stats["removed_area_m2"]), 6)
    return cleaned_meshes, stats


def trim_output_sidewalk_meshes_against_conflicts(
    road_meshes: dict[str, trimesh.Trimesh],
) -> dict[str, Any]:
    conflict_index = sidewalk_conflict_index_from_road_meshes(road_meshes)
    stats: dict[str, Any] = {
        "input_count": 0,
        "output_count": 0,
        "trimmed_count": 0,
        "removed_count": 0,
        "overlap_area_m2": 0.0,
        "removed_area_m2": 0.0,
        "examples": [],
    }
    if not conflict_index.get("parts"):
        return stats

    for mesh_name, mesh in list(road_meshes.items()):
        if road_mesh_layer_name(mesh_name, mesh) != "Sidewalk":
            continue
        stats["input_count"] += 1
        cleaned, item_stats = trim_sidewalk_meshes_against_indexed_conflicts([mesh], conflict_index)
        stats["trimmed_count"] += int(item_stats.get("trimmed_count", 0) or 0)
        stats["removed_count"] += int(item_stats.get("removed_count", 0) or 0)
        stats["overlap_area_m2"] += float(item_stats.get("overlap_area_m2", 0.0) or 0.0)
        stats["removed_area_m2"] += float(item_stats.get("removed_area_m2", 0.0) or 0.0)
        for example in item_stats.get("examples", []):
            if len(stats["examples"]) < 10:
                stats["examples"].append(example)
        if not cleaned:
            road_meshes.pop(mesh_name, None)
            continue
        output_mesh = cleaned[0]
        output_mesh.metadata["name"] = mesh_name
        output_mesh.metadata["layer_name"] = "Sidewalk"
        road_meshes[mesh_name] = output_mesh

    stats["output_count"] = sum(
        1 for mesh_name, mesh in road_meshes.items() if road_mesh_layer_name(mesh_name, mesh) == "Sidewalk"
    )
    stats["overlap_area_m2"] = round(float(stats["overlap_area_m2"]), 6)
    stats["removed_area_m2"] = round(float(stats["removed_area_m2"]), 6)
    return stats


def is_shared_sidewalk_output_mesh(mesh_name: str, mesh: trimesh.Trimesh) -> bool:
    return road_mesh_layer_name(mesh_name, mesh) == "Sidewalk" and str(mesh_name).startswith("Sidewalk_Shared_")


def trim_shared_sidewalk_overlaps(
    road_meshes: dict[str, trimesh.Trimesh],
) -> dict[str, Any]:
    reference_meshes = [
        mesh
        for mesh_name, mesh in road_meshes.items()
        if road_mesh_layer_name(mesh_name, mesh) == "Sidewalk"
        and not is_shared_sidewalk_output_mesh(mesh_name, mesh)
    ]
    reference_index = conflict_index_for_meshes(reference_meshes)
    stats: dict[str, Any] = {
        "input_count": 0,
        "output_count": 0,
        "trimmed_count": 0,
        "removed_count": 0,
        "overlap_area_m2": 0.0,
        "removed_area_m2": 0.0,
        "examples": [],
    }
    if not reference_index.get("parts"):
        return stats

    for mesh_name, mesh in list(road_meshes.items()):
        if not is_shared_sidewalk_output_mesh(mesh_name, mesh):
            continue
        stats["input_count"] += 1
        cleaned, item_stats = trim_sidewalk_meshes_against_indexed_conflicts(
            [mesh],
            reference_index,
            clearance_m=0.0,
            overlap_tolerance_m2=SIDEWALK_OVERLAP_AREA_TOLERANCE_M2,
        )
        stats["trimmed_count"] += int(item_stats.get("trimmed_count", 0) or 0)
        stats["removed_count"] += int(item_stats.get("removed_count", 0) or 0)
        stats["overlap_area_m2"] += float(item_stats.get("overlap_area_m2", 0.0) or 0.0)
        stats["removed_area_m2"] += float(item_stats.get("removed_area_m2", 0.0) or 0.0)
        for example in item_stats.get("examples", []):
            if len(stats["examples"]) < 10:
                stats["examples"].append(example)
        if not cleaned:
            road_meshes.pop(mesh_name, None)
            continue
        output_mesh = cleaned[0]
        output_mesh.metadata["name"] = mesh_name
        output_mesh.metadata["layer_name"] = "Sidewalk"
        road_meshes[mesh_name] = output_mesh

    stats["output_count"] = sum(
        1 for mesh_name, mesh in road_meshes.items() if is_shared_sidewalk_output_mesh(mesh_name, mesh)
    )
    stats["overlap_area_m2"] = round(float(stats["overlap_area_m2"]), 6)
    stats["removed_area_m2"] = round(float(stats["removed_area_m2"]), 6)
    return stats


def sidewalk_mesh_priority(mesh_name: str, mesh: trimesh.Trimesh) -> tuple[int, float, str]:
    metadata = mesh.metadata or {}
    try:
        priority = int(metadata.get("road_priority", 0) or 0)
    except (TypeError, ValueError):
        priority = 0
    try:
        area = float(mesh_xy_footprint(mesh).area)
    except Exception:
        area = 0.0
    if is_shared_sidewalk_output_mesh(mesh_name, mesh):
        priority -= 1000
    return priority, area, str(mesh_name)


def trim_sidewalk_pair_overlaps_by_priority(
    road_meshes: dict[str, trimesh.Trimesh],
) -> dict[str, Any]:
    sidewalk_items = [
        (mesh_name, mesh)
        for mesh_name, mesh in road_meshes.items()
        if road_mesh_layer_name(mesh_name, mesh) == "Sidewalk"
    ]
    priority_by_name = {
        mesh_name: sidewalk_mesh_priority(mesh_name, mesh)
        for mesh_name, mesh in sidewalk_items
    }
    sidewalk_items.sort(
        key=lambda item: (
            -priority_by_name[item[0]][0],
            -priority_by_name[item[0]][1],
            priority_by_name[item[0]][2],
        )
    )
    accepted_parts: list[Polygon] = []
    stats: dict[str, Any] = {
        "input_count": len(sidewalk_items),
        "output_count": len(sidewalk_items),
        "trimmed_count": 0,
        "removed_count": 0,
        "overlap_area_m2": 0.0,
        "removed_area_m2": 0.0,
        "examples": [],
    }

    for mesh_name, mesh in sidewalk_items:
        if accepted_parts:
            accepted_index = {"parts": accepted_parts, "tree": STRtree(accepted_parts)}
            cleaned, item_stats = trim_sidewalk_meshes_against_indexed_conflicts(
                [mesh],
                accepted_index,
                clearance_m=0.0,
                overlap_tolerance_m2=SIDEWALK_OVERLAP_AREA_TOLERANCE_M2,
            )
        else:
            cleaned = [mesh]
            item_stats = {}

        stats["trimmed_count"] += int(item_stats.get("trimmed_count", 0) or 0)
        stats["removed_count"] += int(item_stats.get("removed_count", 0) or 0)
        stats["overlap_area_m2"] += float(item_stats.get("overlap_area_m2", 0.0) or 0.0)
        stats["removed_area_m2"] += float(item_stats.get("removed_area_m2", 0.0) or 0.0)
        for example in item_stats.get("examples", []):
            if len(stats["examples"]) < 10:
                stats["examples"].append(example)
        if not cleaned:
            road_meshes.pop(mesh_name, None)
            continue

        output_mesh = cleaned[0]
        output_mesh.metadata["name"] = mesh_name
        output_mesh.metadata["layer_name"] = "Sidewalk"
        road_meshes[mesh_name] = output_mesh
        accepted_parts.extend(mesh_footprint_polygon_parts(output_mesh, min_area_m2=SIDEWALK_MIN_FRAGMENT_AREA_M2))

    stats["output_count"] = sum(
        1 for mesh_name, mesh in road_meshes.items() if road_mesh_layer_name(mesh_name, mesh) == "Sidewalk"
    )
    stats["overlap_area_m2"] = round(float(stats["overlap_area_m2"]), 6)
    stats["removed_area_m2"] = round(float(stats["removed_area_m2"]), 6)
    return stats


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


def merge_clip_range_maps(
    *range_maps: dict[Any, list[tuple[float, float]]],
) -> dict[Any, list[tuple[float, float]]]:
    combined: dict[Any, list[tuple[float, float]]] = defaultdict(list)
    for ranges_by_road in range_maps:
        for road_idx, ranges in ranges_by_road.items():
            combined[road_idx].extend(ranges)
    return merge_clip_ranges_by_road(combined)


def merge_distance_ranges(
    ranges: list[tuple[float, float]],
    merge_tolerance: float = 0.25,
) -> list[tuple[float, float]]:
    valid_ranges = [(min(start, end), max(start, end)) for start, end in ranges if abs(float(end) - float(start)) > 0.05]
    if not valid_ranges:
        return []
    valid_ranges.sort()
    merged: list[tuple[float, float]] = [valid_ranges[0]]
    for start, end in valid_ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + merge_tolerance:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def padded_distance_ranges(
    ranges: list[tuple[float, float]],
    line_length: float,
    padding_m: float,
) -> list[tuple[float, float]]:
    if not ranges:
        return []
    line_length = max(float(line_length), 0.0)
    padding_m = max(float(padding_m), 0.0)
    padded = [
        (
            max(0.0, min(line_length, float(start) - padding_m)),
            max(0.0, min(line_length, float(end) + padding_m)),
        )
        for start, end in ranges
    ]
    return merge_distance_ranges(padded)


def outward_unclipped_approach_length(
    boundary_distance: float,
    side_sign: float,
    line_length: float,
    clip_ranges: list[tuple[float, float]],
) -> float:
    boundary_distance = max(0.0, min(float(line_length), float(boundary_distance)))
    line_length = max(float(line_length), 0.0)
    side_sign = -1.0 if float(side_sign) < 0.0 else 1.0
    ranges = merge_distance_ranges(clip_ranges)
    epsilon = max(float(JUNCTION_CHAINAGE_EPSILON_M), 0.02)
    if side_sign > 0.0:
        next_clip_start = line_length
        for start, _end in ranges:
            if float(start) > boundary_distance + epsilon:
                next_clip_start = float(start)
                break
        return max(0.0, next_clip_start - boundary_distance)

    previous_clip_end = 0.0
    for start, end in ranges:
        if float(end) < boundary_distance - epsilon:
            previous_clip_end = float(end)
            continue
        if float(start) >= boundary_distance - epsilon:
            break
    return max(0.0, boundary_distance - previous_clip_end)


def transverse_marking_has_approach_surface(
    boundary_distance: float,
    side_sign: float,
    line_length: float,
    stop_distance: float,
    crosswalk_distance: float,
    approach_clip_ranges: list[tuple[float, float]],
) -> bool:
    available_length = outward_unclipped_approach_length(
        boundary_distance,
        side_sign,
        line_length,
        approach_clip_ranges,
    )
    required_marking_extent = max(
        abs(float(stop_distance) - float(boundary_distance)) + STOP_LINE_WIDTH_M * 0.5,
        abs(float(crosswalk_distance) - float(boundary_distance)) + CROSSWALK_BAND_LENGTH_M * 0.5,
    )
    required_length = required_marking_extent + max(float(JUNCTION_TRANSVERSE_MARKING_MIN_APPROACH_SURFACE_M), 0.0)
    return available_length + 0.05 >= required_length


def transverse_marking_is_inside_merged_junction_clip(
    boundary_distance: float,
    line_length: float,
    approach_clip_ranges: list[tuple[float, float]],
) -> bool:
    boundary_distance = max(0.0, min(float(line_length), float(boundary_distance)))
    line_length = max(float(line_length), 0.0)
    epsilon = max(float(JUNCTION_CHAINAGE_EPSILON_M), 0.02)
    for start, end in merge_distance_ranges(approach_clip_ranges):
        start = max(0.0, min(line_length, float(start)))
        end = max(0.0, min(line_length, float(end)))
        if start + epsilon < boundary_distance < end - epsilon:
            return True
    return False


def is_outermost_sidewalk_span(
    spans: list[tuple[dict[str, Any], float, float]],
    component_idx: int,
) -> bool:
    if component_idx < 0 or component_idx >= len(spans):
        return False
    component, left_offset, right_offset = spans[component_idx]
    if str(component.get("type", "")) != "sidewalk":
        return False
    valid_edges = [
        (float(left), float(right))
        for _component, left, right in spans
        if float(right) - float(left) > 0.05
    ]
    if not valid_edges:
        return False
    min_left = min(left for left, _right in valid_edges)
    max_right = max(right for _left, right in valid_edges)
    return abs(float(left_offset) - min_left) <= 0.01 or abs(float(right_offset) - max_right) <= 0.01


def short_road_junction_surface_mesh(
    row: pd.Series,
    line: LineString,
    rule: Any,
    components: list[dict[str, Any]],
    line_idx: int,
) -> trimesh.Trimesh | None:
    spans = component_spans(components)
    valid_spans = [
        (float(left_offset), float(right_offset))
        for component_idx, (_component, left_offset, right_offset) in enumerate(spans)
        if not is_outermost_sidewalk_span(spans, component_idx)
        and float(right_offset) - float(left_offset) > 0.05
    ]
    if not valid_spans:
        half_width = max(float(rule.road_width) * 0.5, 0.5)
        valid_spans = [(-half_width, half_width)]
    left_offset = min(left for left, _right in valid_spans)
    right_offset = max(right for _left, right in valid_spans)
    geom = swept_band_polygon(line, left_offset, right_offset)
    if geom is None or geom.is_empty:
        return None
    z = road_gen.elevation_at_distance(
        row,
        min(float(row.get("length_m", line.length)), line.length * 0.5),
        default_z=float(row.get("road_z_mean", row.get("elevation", 0.0))),
    )
    name = f"Junction_Surface_Short_Road_{row.name}_{line_idx}"
    mesh = road_gen.polygon_to_top_mesh(
        geom,
        z + JUNCTION_SURFACE_Z_OFFSET_M,
        name,
        visual_color=COLORS["road_surface_main"],
    )
    if mesh is None or len(mesh.vertices) == 0:
        return None
    apply_road_feature_metadata(
        mesh,
        row,
        rule,
        layer_name="Junction_Surface",
        component_type="short_road_junction_surface",
        component_idx=line_idx,
    )
    mesh.metadata.update(
        {
            "name": name,
            "cim_entity_type": "junction_surface",
            "short_junction_gap_surface": True,
            "source_line_length_m": round(float(line.length), 3),
        }
    )
    return mesh


def short_junction_gap_surface_meshes_and_clip_ranges(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    existing_clip_profiles: dict[str, dict[Any, list[tuple[float, float]]]] | None = None,
) -> tuple[list[trimesh.Trimesh], dict[str, dict[Any, list[tuple[float, float]]]]]:
    meshes: list[trimesh.Trimesh] = []
    raw_ranges: dict[str, dict[Any, list[tuple[float, float]]]] = {
        "drivable": {},
        "roadside": {},
        "divider": {},
        "marking": {},
    }
    for road_idx, row in prepared_roads.iterrows():
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue
        existing_road_clip_ranges = (
            merge_distance_ranges(existing_clip_profiles.get("drivable", {}).get(road_idx, []))
            if existing_clip_profiles
            else []
        )
        junction_distances = sorted(
            {
                max(0.0, min(float(line.length), float(distance)))
                for distance in road_gen.row_junction_distances(row)
            }
        )
        if not junction_distances and not existing_road_clip_ranges:
            continue
        rule = road_gen.get_road_rule(row, rules)
        components = road_gen.cross_section_components_for_row(row)
        if not components:
            components = fallback_cross_section_components(rule)
        source_row = row.copy()
        source_row.name = road_idx
        gap_idx = 0
        emitted_ranges: set[tuple[float, float]] = set()

        def add_short_gap_surface(start: float, end: float, max_length_m: float) -> None:
            nonlocal gap_idx
            start = max(0.0, min(float(line.length), float(start)))
            end = max(0.0, min(float(line.length), float(end)))
            if end < start:
                start, end = end, start
            gap_length = float(end) - float(start)
            if gap_length <= 0.05 or gap_length > float(max_length_m):
                return
            key = (round(float(start), 3), round(float(end), 3))
            if key in emitted_ranges:
                return
            try:
                segment = substring(line, float(start), float(end))
            except Exception:
                return
            if segment is None or segment.is_empty or not isinstance(segment, LineString):
                return
            mesh = short_road_junction_surface_mesh(
                source_row,
                segment,
                rule,
                components,
                gap_idx,
            )
            if mesh is None or len(mesh.vertices) == 0:
                return
            mesh.metadata.update(
                {
                    "component_type": "short_junction_gap_surface",
                    "short_junction_gap_start_m": round(float(start), 3),
                    "short_junction_gap_end_m": round(float(end), 3),
                    "short_junction_gap_length_m": round(gap_length, 3),
                    "short_junction_gap_threshold_m": round(float(max_length_m), 3),
                }
            )
            meshes.append(mesh)
            for profile in ("drivable", "roadside", "divider", "marking"):
                raw_ranges[profile].setdefault(road_idx, []).append((float(start), float(end)))
            emitted_ranges.add(key)
            gap_idx += 1

        breakpoints = sorted({0.0, *junction_distances, float(line.length)})
        for start, end in zip(breakpoints, breakpoints[1:]):
            touches_junction = start in junction_distances or end in junction_distances
            if touches_junction:
                add_short_gap_surface(float(start), float(end), SHORT_JUNCTION_GAP_AS_JUNCTION_MAX_LENGTH_M)

        if existing_road_clip_ranges:
            for left_range, right_range in zip(existing_road_clip_ranges, existing_road_clip_ranges[1:]):
                start = float(left_range[1])
                end = float(right_range[0])
                add_short_gap_surface(start, end, SHORT_JUNCTION_CONNECTOR_AS_JUNCTION_MAX_LENGTH_M)

    return (
        meshes,
        {
            profile: merge_clip_ranges_by_road(ranges)
            for profile, ranges in raw_ranges.items()
        },
    )


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
    """Map junction polygons back to road-chainage clip ranges.

    The mesh generator uses four retreat profiles:

    - drivable: trims road surfaces where the junction surface replaces them,
      keeping components touching at the boundary without coplanar overlap.
    - roadside: retreats sidewalks, curbs, belts, and other side components a
      little farther from the junction.
    - divider: retreats central medians/dividers only back to the near edge of
      the stop line, so the divider remains visible through the approach.
    - marking: removes lane markings around the junction so crosswalks and stop
      lines remain visually dominant.
    """
    raw_ranges: dict[str, dict[Any, list[tuple[float, float]]]] = {
        "drivable": {},
        "roadside": {},
        "divider": {},
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

        marking_radius = round(max(drivable_width * 0.5, 0.0) + JUNCTION_MARKING_RETREAT_M, 1)
        for profile, radius, pad in [("marking", marking_radius, 0.10)]:
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
                    edge_overlap=JUNCTION_ROADSIDE_SEAM_OVERLAP_M if profile == "roadside" else 0.0,
                )

    for surface in surface_geometries:
        point = surface.get("point")
        if point is None or getattr(point, "is_empty", False):
            continue
        try:
            arms = junction_arm_records(
                prepared_roads,
                rules,
                point,
                surface.get("members", []),
            )
        except Exception:
            arms = []
        for arm in arms:
            road_idx = arm.get("road_idx")
            if road_idx not in prepared_roads.index:
                continue
            row = prepared_roads.loc[road_idx]
            line = row.geometry
            if line is None or line.is_empty or not isinstance(line, LineString):
                continue
            rule = road_gen.get_road_rule(row, rules)
            node_distance = float(arm.get("node_distance_m", line.project(point)) or 0.0)
            node_distance = max(0.0, min(float(line.length), node_distance))
            sign = float(arm.get("line_direction_sign", 1.0) or 1.0)
            crosswalk_offset, stop_line_offset = junction_approach_offsets_for_row(row, rule)
            extra_setback = junction_arm_extra_setback_m(arm, arms)
            crosswalk_offset += extra_setback
            stop_line_offset += extra_setback
            min_marking_margin = max(CROSSWALK_BAND_LENGTH_M * 0.5, STOP_LINE_WIDTH_M * 0.5, 0.4)
            crosswalk_distance = node_distance + sign * float(crosswalk_offset)
            stop_line_distance = node_distance + sign * float(stop_line_offset)
            valid_stop_line_boundary = (
                min_marking_margin < crosswalk_distance < float(line.length) - min_marking_margin
                and min_marking_margin < stop_line_distance < float(line.length) - min_marking_margin
            )
            if valid_stop_line_boundary:
                add_adjusted_clip_range(
                    raw_ranges["drivable"],
                    road_idx,
                    float(line.length),
                    node_distance,
                    stop_line_distance,
                    edge_overlap=JUNCTION_DRIVABLE_BLEND_OVERLAP_M,
                )
                add_adjusted_clip_range(
                    raw_ranges["roadside"],
                    road_idx,
                    float(line.length),
                    node_distance,
                    stop_line_distance,
                    edge_overlap=JUNCTION_ROADSIDE_SEAM_OVERLAP_M,
                )
            else:
                for start, end in line_intersection_distance_ranges(line, surface_union):
                    add_adjusted_clip_range(
                        raw_ranges["roadside"],
                        road_idx,
                        float(line.length),
                        start,
                        end,
                        edge_overlap=JUNCTION_ROADSIDE_SEAM_OVERLAP_M,
                    )
            divider_stop_offset = max(0.0, float(stop_line_offset) - STOP_LINE_WIDTH_M * 0.5)
            stop_edge_distance = node_distance + sign * divider_stop_offset
            if valid_stop_line_boundary:
                add_adjusted_clip_range(
                    raw_ranges["marking"],
                    road_idx,
                    float(line.length),
                    node_distance,
                    stop_edge_distance,
                )
                add_adjusted_clip_range(
                    raw_ranges["divider"],
                    road_idx,
                    float(line.length),
                    node_distance,
                    stop_edge_distance,
                )

    return {
        profile: merge_clip_ranges_by_road(ranges)
        for profile, ranges in raw_ranges.items()
    }




def connector_clip_range_around_distance(
    line_length: float,
    node_distance: float,
    clip_ranges: list[tuple[float, float]],
    overlap_m: float = 0.0,
) -> tuple[float, float] | None:
    if not clip_ranges:
        return None
    line_length = max(float(line_length), 0.0)
    node_distance = max(0.0, min(line_length, float(node_distance)))
    best_range = None
    best_gap = float("inf")
    for start, end in clip_ranges:
        start = max(0.0, min(line_length, float(start)))
        end = max(0.0, min(line_length, float(end)))
        if end < start:
            start, end = end, start
        gap = 0.0 if start <= node_distance <= end else min(abs(node_distance - start), abs(node_distance - end))
        if gap < best_gap:
            best_gap = gap
            best_range = (start, end)
    if best_range is None or best_gap > max(JUNCTION_BUCKET_CLUSTER_M, JUNCTION_CONNECTOR_LOCAL_MARGIN_M):
        return None
    start, end = best_range
    start = max(0.0, start - max(float(overlap_m), 0.0))
    end = min(line_length, end + max(float(overlap_m), 0.0))
    return (start, end) if end - start > 0.05 else None


def clipped_component_existing_parts(
    line: LineString,
    left_offset: float,
    right_offset: float,
    clip_ranges: list[tuple[float, float]],
    local_limit,
) -> list[Any]:
    if line is None or line.is_empty or right_offset <= left_offset:
        return []
    segments = (
        road_gen.line_segments_outside_ranges(line, clip_ranges)
        if clip_ranges
        else [(line, 0.0)]
    )
    parts = []
    for segment, _ in segments:
        if segment is None or segment.is_empty:
            continue
        geom = swept_band_polygon(segment, left_offset, right_offset)
        if geom is None or geom.is_empty:
            continue
        if local_limit is not None and not local_limit.is_empty:
            try:
                geom = road_gen.clean_polygonal(geom.intersection(local_limit))
            except Exception:
                geom = None
        if geom is not None and not geom.is_empty:
            parts.append(geom)
    return parts




def build_component_conflict_clip_geom(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    clip_ranges_by_road: dict[Any, list[tuple[float, float]]],
    component_types: set[str],
    extra_geoms: Iterable[Any] | None = None,
):
    parts = [
        geom
        for geom in (extra_geoms or [])
        if geom is not None and not geom.is_empty
    ]
    for road_idx, row in prepared_roads.iterrows():
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue
        rule = road_gen.get_road_rule(row, rules)
        components = road_gen.cross_section_components_for_row(row)
        if not components:
            components = fallback_cross_section_components(rule)
        spans = component_spans(components)
        clip_ranges = clip_ranges_by_road.get(road_idx, [])
        segments = (
            road_gen.line_segments_outside_ranges(line, clip_ranges)
            if clip_ranges
            else [(line, 0.0)]
        )
        for segment, _ in segments:
            if segment is None or segment.is_empty:
                continue
            for component, left_offset, right_offset in spans:
                if str(component.get("type", "")) not in component_types:
                    continue
                geom = swept_band_polygon(segment, left_offset, right_offset)
                if geom is not None and not geom.is_empty:
                    parts.append(geom)
    if not parts:
        return None
    try:
        return road_gen.clean_polygonal(unary_union(parts))
    except Exception:
        return None


def component_conflict_profile_for_type(component_type: str) -> str:
    if component_type in JUNCTION_ASPHALT_COMPONENT_TYPES or component_type == "service_lane":
        return "drivable"
    if component_type in JUNCTION_DIVIDER_COMPONENT_TYPES:
        return "divider"
    return "roadside"


def build_component_conflict_clip_geom_by_profile(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    clip_ranges_by_profile: dict[str, dict[Any, list[tuple[float, float]]]],
    component_types: set[str],
    extra_geoms: Iterable[Any] | None = None,
):
    parts = [
        geom
        for geom in (extra_geoms or [])
        if geom is not None and not geom.is_empty
    ]
    for road_idx, row in prepared_roads.iterrows():
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue
        rule = road_gen.get_road_rule(row, rules)
        components = road_gen.cross_section_components_for_row(row)
        if not components:
            components = fallback_cross_section_components(rule)
        spans = component_spans(components)
        for component, left_offset, right_offset in spans:
            component_type = str(component.get("type", ""))
            if component_type not in component_types:
                continue
            profile = component_conflict_profile_for_type(component_type)
            clip_ranges = clip_ranges_by_profile.get(profile, {}).get(road_idx, [])
            component_parts = clipped_component_existing_parts(
                line,
                left_offset,
                right_offset,
                clip_ranges,
                None,
            )
            parts.extend(component_parts)
    if not parts:
        return None
    try:
        return road_gen.clean_polygonal(unary_union(parts))
    except Exception:
        return None




def junction_side_component_edge_clear_mask(surface_geom, overlap_m: float | None = None, retreat_m: float | None = None):
    if surface_geom is None or surface_geom.is_empty:
        return None
    clear_geom = surface_geom
    try:
        overlap = max(
            float(JUNCTION_SIDE_COMPONENT_EDGE_OVERLAP_M if overlap_m is None else overlap_m),
            0.0,
        )
        if overlap > 0.0:
            inset = road_gen.clean_polygonal(
                surface_geom.buffer(
                    -overlap,
                    resolution=4,
                    join_style=1,
                )
            )
            if inset is not None and not inset.is_empty:
                clear_geom = inset
        retreat = max(
            float(JUNCTION_SIDE_COMPONENT_EDGE_RETREAT_M if retreat_m is None else retreat_m),
            0.0,
        )
        if retreat > 0.0:
            clear_geom = road_gen.clean_polygonal(
                clear_geom.buffer(
                    retreat,
                    resolution=4,
                    join_style=1,
                )
            )
    except Exception:
        return surface_geom
    return clear_geom








































def junction_center_fill_core_geom(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    surface: dict[str, Any],
    scale: float | None = None,
):
    point = surface.get("point")
    if point is None or getattr(point, "is_empty", False):
        return None
    selected = ((surface.get("design") or {}).get("selected_design_option") or {})
    params = selected.get("surface_parameters") or {}
    core_radius_multiplier = float(params.get("core_radius_multiplier", 1.0) or 1.0)
    widths = []
    for road_idx, _ in surface.get("members", []):
        if road_idx not in prepared_roads.index:
            continue
        row = prepared_roads.loc[road_idx]
        rule = road_gen.get_road_rule(row, rules)
        widths.append(drivable_width_for_row(row, rule))
    radius = max(
        max(widths, default=0.0)
        * 0.45
        * core_radius_multiplier
        * max(float(JUNCTION_APPROACH_APRON_CORE_SCALE if scale is None else scale), 0.1),
        float(JUNCTION_APPROACH_APRON_MIN_CORE_RADIUS_M),
    )
    return point.buffer(radius, resolution=18)


def junction_selected_option_id(surface: dict[str, Any]) -> str:
    selected = ((surface.get("design") or {}).get("selected_design_option") or {})
    return str(selected.get("option_id", ""))


def junction_arm_is_minor_approach(arm: dict[str, Any], arms: list[dict[str, Any]]) -> bool:
    arm_id = str(arm.get("arm_id", ""))
    for other in arms:
        if str(other.get("arm_id", "")) == arm_id:
            continue
        if arm_is_lower_order_than(arm, other):
            return True
    return False










def junction_stop_line_fill_lateral_offsets_for_spans(
    spans: list[tuple[dict[str, Any], float, float]],
) -> tuple[float, float] | None:
    if not spans:
        return None
    total_left = min(float(left) for _, left, _ in spans)
    total_right = max(float(right) for _, _, right in spans)
    left_sidewalk_inner_edges = [
        float(right)
        for component, left, right in spans
        if str(component.get("type", "")) == "sidewalk" and float(right) <= 0.05
    ]
    right_sidewalk_inner_edges = [
        float(left)
        for component, left, right in spans
        if str(component.get("type", "")) == "sidewalk" and float(left) >= -0.05
    ]
    if left_sidewalk_inner_edges and right_sidewalk_inner_edges:
        left_offset = max(left_sidewalk_inner_edges)
        right_offset = min(right_sidewalk_inner_edges)
    else:
        left_offset = total_left
        right_offset = total_right
    if right_offset - left_offset <= 0.05:
        return None
    return left_offset, right_offset






def prepared_road_index_from_arm(
    prepared_roads: gpd.GeoDataFrame,
    arm: dict[str, Any],
):
    road_idx = arm.get("road_idx")
    if road_idx in prepared_roads.index:
        return road_idx
    road_idx_text = str(road_idx)
    for candidate in prepared_roads.index:
        if str(json_safe_value(candidate)) == road_idx_text:
            return candidate
    return None


def outer_sidewalk_span_for_outward_side(
    spans: list[tuple[dict[str, Any], float, float]],
    line_direction_sign: float,
    outward_left_side: bool,
) -> dict[str, float] | None:
    candidates: list[dict[str, float]] = []
    for component, left_offset, right_offset in spans:
        if str(component.get("type", "")) != "sidewalk":
            continue
        left_value = float(left_offset)
        right_value = float(right_offset)
        width = right_value - left_value
        if width <= 0.05:
            continue
        candidates.append(
            {
                "left_offset": left_value,
                "right_offset": right_value,
                "center_offset": (left_value + right_value) * 0.5,
                "width": width,
            }
        )
    if not candidates:
        return None

    # Convert "left/right of the outbound arm" to the source line's lateral
    # coordinate. Roads approached from the line end have the lateral sides
    # flipped relative to the stored LineString direction.
    use_positive_line_side = bool(outward_left_side) == (float(line_direction_sign) >= 0.0)
    side_candidates = [
        item
        for item in candidates
        if (item["center_offset"] > 0.05 if use_positive_line_side else item["center_offset"] < -0.05)
    ]
    if not side_candidates:
        return None
    return (
        max(side_candidates, key=lambda item: item["center_offset"])
        if use_positive_line_side
        else min(side_candidates, key=lambda item: item["center_offset"])
    )


def quadratic_curve_points(
    start_point: tuple[float, float],
    control_point: tuple[float, float],
    end_point: tuple[float, float],
    sample_count: int = 24,
) -> list[tuple[float, float]]:
    steps = max(int(sample_count), 6)
    points: list[tuple[float, float]] = []
    for idx in range(steps + 1):
        t = idx / steps
        inv = 1.0 - t
        x = inv * inv * start_point[0] + 2.0 * inv * t * control_point[0] + t * t * end_point[0]
        y = inv * inv * start_point[1] + 2.0 * inv * t * control_point[1] + t * t * end_point[1]
        points.append((float(x), float(y)))
    return points


def variable_width_curve_band(
    center_points: list[tuple[float, float]],
    start_width: float,
    end_width: float,
    reference_point: Point | None = None,
) -> tuple[Any | None, list[tuple[float, float]]]:
    if len(center_points) < 2:
        return None, []
    left_points: list[tuple[float, float]] = []
    right_points: list[tuple[float, float]] = []
    inner_edge_points: list[tuple[float, float]] = []
    last_idx = len(center_points) - 1
    for idx, point_xy in enumerate(center_points):
        if idx == 0:
            next_xy = center_points[idx + 1]
            tangent = (next_xy[0] - point_xy[0], next_xy[1] - point_xy[1])
        elif idx == last_idx:
            prev_xy = center_points[idx - 1]
            tangent = (point_xy[0] - prev_xy[0], point_xy[1] - prev_xy[1])
        else:
            prev_xy = center_points[idx - 1]
            next_xy = center_points[idx + 1]
            tangent = (next_xy[0] - prev_xy[0], next_xy[1] - prev_xy[1])
        direction = normalize_xy(tangent)
        if direction is None:
            continue
        normal = left_normal(direction)
        t = idx / max(last_idx, 1)
        smooth_t = t * t * (3.0 - 2.0 * t)
        width = max(0.05, float(start_width) * (1.0 - smooth_t) + float(end_width) * smooth_t)
        half_width = width * 0.5
        left_xy = (point_xy[0] + normal[0] * half_width, point_xy[1] + normal[1] * half_width)
        right_xy = (point_xy[0] - normal[0] * half_width, point_xy[1] - normal[1] * half_width)
        left_points.append(left_xy)
        right_points.append(right_xy)
        if reference_point is not None:
            left_dist = reference_point.distance(Point(left_xy))
            right_dist = reference_point.distance(Point(right_xy))
            inner_edge_points.append(left_xy if left_dist <= right_dist else right_xy)
    if len(left_points) < 2 or len(right_points) < 2:
        return None, []
    try:
        return road_gen.clean_polygonal(Polygon(left_points + list(reversed(right_points)))), inner_edge_points
    except Exception:
        return None, []


def clamp_sidewalk_curve_control_point(
    control_point: tuple[float, float],
    start_point: tuple[float, float],
    end_point: tuple[float, float],
    reference_point: Point,
    max_outward_m: float | None,
) -> tuple[float, float]:
    if max_outward_m is None or max_outward_m <= 0.0:
        return control_point
    chord = (float(end_point[0]) - float(start_point[0]), float(end_point[1]) - float(start_point[1]))
    chord_length_sq = chord[0] * chord[0] + chord[1] * chord[1]
    if chord_length_sq <= 1e-6:
        return control_point
    t = (
        (float(control_point[0]) - float(start_point[0])) * chord[0]
        + (float(control_point[1]) - float(start_point[1])) * chord[1]
    ) / chord_length_sq
    t = max(0.0, min(1.0, t))
    base_point = (
        float(start_point[0]) + chord[0] * t,
        float(start_point[1]) + chord[1] * t,
    )
    midpoint = ((float(start_point[0]) + float(end_point[0])) * 0.5, (float(start_point[1]) + float(end_point[1])) * 0.5)
    outward = normalize_xy((midpoint[0] - reference_point.x, midpoint[1] - reference_point.y))
    if outward is None:
        return control_point
    outward_distance = (
        (float(control_point[0]) - base_point[0]) * outward[0]
        + (float(control_point[1]) - base_point[1]) * outward[1]
    )
    if outward_distance <= max_outward_m:
        return control_point
    excess = outward_distance - max_outward_m
    return (
        float(control_point[0]) - outward[0] * excess,
        float(control_point[1]) - outward[1] * excess,
    )


def sidewalk_curve_control_point(
    start_point: tuple[float, float],
    start_direction: tuple[float, float],
    end_point: tuple[float, float],
    end_direction: tuple[float, float],
    reference_point: Point,
    max_outward_m: float | None = None,
) -> tuple[float, float]:
    chord = math.hypot(end_point[0] - start_point[0], end_point[1] - start_point[1])
    candidate = line_intersection_point(start_point, start_direction, end_point, (-end_direction[0], -end_direction[1]))
    if candidate is not None:
        start_distance = math.hypot(candidate[0] - start_point[0], candidate[1] - start_point[1])
        end_distance = math.hypot(candidate[0] - end_point[0], candidate[1] - end_point[1])
        if chord * 0.12 <= start_distance <= chord * 3.0 and chord * 0.12 <= end_distance <= chord * 3.0:
            return clamp_sidewalk_curve_control_point(candidate, start_point, end_point, reference_point, max_outward_m)

    midpoint = ((start_point[0] + end_point[0]) * 0.5, (start_point[1] + end_point[1]) * 0.5)
    outward = normalize_xy((midpoint[0] - reference_point.x, midpoint[1] - reference_point.y))
    if outward is None:
        outward = normalize_xy((start_direction[0] - end_direction[0], start_direction[1] - end_direction[1]))
    if outward is None:
        return midpoint
    fallback = (
        midpoint[0] + outward[0] * max(chord * 0.35, 2.0),
        midpoint[1] + outward[1] * max(chord * 0.35, 2.0),
    )
    return clamp_sidewalk_curve_control_point(fallback, start_point, end_point, reference_point, max_outward_m)


def surface_has_target_outer_sidewalk_source(
    prepared_roads: gpd.GeoDataFrame,
    arms: list[dict[str, Any]],
) -> bool:
    target_ids = {str(item) for item in JUNCTION_TARGET_OUTER_SIDEWALK_SOURCE_IDS}
    for arm in arms:
        source_id = str(arm.get("source_road_id", "") or "").strip()
        if source_id in target_ids:
            return True
        road_idx = prepared_road_index_from_arm(prepared_roads, arm)
        if road_idx is None or road_idx not in prepared_roads.index:
            continue
        row_source_id = str(prepared_roads.loc[road_idx].get("road_id", "") or "").strip()
        if row_source_id in target_ids:
            return True
    return False


def simple_junction_outer_sidewalk_connection_geometries(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    point: Point,
    members: list[tuple[Any, float]],
    surface_geom=None,
    endpoint_overlap_m: float = 0.0,
) -> tuple[list[Any], list[Any]]:
    if point is None or getattr(point, "is_empty", False):
        return [], []
    try:
        arms = junction_arm_records(prepared_roads, rules, point, members)
    except Exception:
        return [], []
    if len(arms) < 2:
        return [], []
    tighten_target_sidewalk = surface_has_target_outer_sidewalk_source(prepared_roads, arms)

    endpoint_by_arm_side: dict[tuple[str, str], dict[str, Any]] = {}
    for arm in arms:
        road_idx = prepared_road_index_from_arm(prepared_roads, arm)
        if road_idx is None:
            continue
        row = prepared_roads.loc[road_idx]
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue
        rule = road_gen.get_road_rule(row, rules)
        components = road_gen.cross_section_components_for_row(row)
        if not components:
            components = fallback_cross_section_components(rule)
        spans = component_spans(components)
        side_sign = -1.0 if float(arm.get("line_direction_sign", 0.0) or 0.0) < 0.0 else 1.0
        boundary_distance = (
            junction_surface_boundary_distance_for_arm(line, surface_geom, arm)
            if surface_geom is not None and not getattr(surface_geom, "is_empty", False)
            else None
        )
        if boundary_distance is None:
            boundary_distance = junction_sidewalk_connector_distance(
                line,
                row,
                rule,
                arm,
                arms,
                outward_extra_m=endpoint_overlap_m,
            )
        stop_distance = junction_approach_element_stop_distance(line, row, rule, arm, arms)
        if boundary_distance is None:
            continue
        if tighten_target_sidewalk and stop_distance is not None:
            landing_delta = float(boundary_distance) - float(stop_distance)
            max_landing = max(float(JUNCTION_TARGET_OUTER_SIDEWALK_LANDING_MAX_M), 0.0)
            if max_landing > 0.0 and abs(landing_delta) > max_landing:
                boundary_distance = float(stop_distance) + math.copysign(max_landing, landing_delta)
        boundary_point, _, line_normal = road_gen.line_frame_at_distance(line, boundary_distance)
        direction = arm_direction_vector(arm)
        if direction is None:
            continue
        for side_name, outward_left_side in (("left", True), ("right", False)):
            sidewalk_span = outer_sidewalk_span_for_outward_side(spans, side_sign, outward_left_side)
            if sidewalk_span is None:
                continue
            center_offset = float(sidewalk_span["center_offset"])
            endpoint_by_arm_side[(str(arm.get("arm_id", "")), side_name)] = {
                "point": (
                    float(boundary_point.x) + float(line_normal[0]) * center_offset,
                    float(boundary_point.y) + float(line_normal[1]) * center_offset,
                ),
                "direction": direction,
                "width": float(sidewalk_span["width"]),
                "landing_geom": None,
            }
            if stop_distance is not None and abs(float(boundary_distance) - float(stop_distance)) > 0.05:
                try:
                    landing_line = substring(
                        line,
                        min(float(stop_distance), float(boundary_distance)),
                        max(float(stop_distance), float(boundary_distance)),
                    )
                except Exception:
                    landing_line = None
                if landing_line is not None and not landing_line.is_empty:
                    landing_geom = swept_band_polygon(
                        landing_line,
                        float(sidewalk_span["left_offset"]),
                        float(sidewalk_span["right_offset"]),
                    )
                    if landing_geom is not None and not landing_geom.is_empty:
                        endpoint_by_arm_side[(str(arm.get("arm_id", "")), side_name)]["landing_geom"] = landing_geom

    sidewalk_parts: list[Any] = []
    asphalt_fill_parts: list[Any] = []
    used_landing_keys: set[tuple[str, str]] = set()
    skip_outer_curve_arm_pairs: set[frozenset[str]] = set()
    if junction_type_from_arm_geometry(arms) == "T_JUNCTION":
        opposing_pairs = junction_opposing_arm_pairs(arms)
        if opposing_pairs:
            through_a, through_b, _ = opposing_pairs[0]
            through_a_id = str(through_a.get("arm_id", ""))
            through_b_id = str(through_b.get("arm_id", ""))
            if through_a_id and through_b_id:
                skip_outer_curve_arm_pairs.add(frozenset((through_a_id, through_b_id)))
    ordered_arms = sorted(arms, key=lambda item: float(item.get("bearing_out_deg", 0.0) or 0.0))
    for idx, current_arm in enumerate(ordered_arms):
        next_arm = ordered_arms[(idx + 1) % len(ordered_arms)]
        current_arm_id = str(current_arm.get("arm_id", ""))
        next_arm_id = str(next_arm.get("arm_id", ""))
        if frozenset((current_arm_id, next_arm_id)) in skip_outer_curve_arm_pairs:
            continue
        gap = (float(next_arm.get("bearing_out_deg", 0.0) or 0.0) - float(current_arm.get("bearing_out_deg", 0.0) or 0.0)) % 360.0
        if gap <= 35.0 or gap >= 145.0:
            continue
        current = endpoint_by_arm_side.get((str(current_arm.get("arm_id", "")), "left"))
        next_endpoint = endpoint_by_arm_side.get((str(next_arm.get("arm_id", "")), "right"))
        if current is None or next_endpoint is None:
            continue
        for landing_key, endpoint in (
            ((str(current_arm.get("arm_id", "")), "left"), current),
            ((str(next_arm.get("arm_id", "")), "right"), next_endpoint),
        ):
            landing_geom = endpoint.get("landing_geom")
            if landing_key not in used_landing_keys and landing_geom is not None and not landing_geom.is_empty:
                sidewalk_parts.append(landing_geom)
                used_landing_keys.add(landing_key)
        start_point = current["point"]
        end_point = next_endpoint["point"]
        chord = math.hypot(end_point[0] - start_point[0], end_point[1] - start_point[1])
        if chord <= 0.5:
            continue
        control_point = sidewalk_curve_control_point(
            start_point,
            current["direction"],
            end_point,
            next_endpoint["direction"],
            point,
            max_outward_m=(
                float(JUNCTION_TARGET_OUTER_SIDEWALK_CONTROL_MAX_OUTWARD_M)
                if tighten_target_sidewalk
                else None
            ),
        )
        curve_points = quadratic_curve_points(
            start_point,
            control_point,
            end_point,
            sample_count=max(18, int(gap / 4.0)),
        )
        sidewalk_geom, inner_edge_points = variable_width_curve_band(
            curve_points,
            float(current["width"]),
            float(next_endpoint["width"]),
            reference_point=point,
        )
        if sidewalk_geom is not None and not sidewalk_geom.is_empty:
            sidewalk_parts.append(sidewalk_geom)
        if len(inner_edge_points) >= 2:
            try:
                fill_geom = road_gen.clean_polygonal(Polygon([(point.x, point.y), *inner_edge_points]))
            except Exception:
                fill_geom = None
            if fill_geom is not None and not fill_geom.is_empty:
                asphalt_fill_parts.append(fill_geom)
    return sidewalk_parts, asphalt_fill_parts


def simple_junction_outer_sidewalk_connection_meshes(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    surface_geometries: list[dict[str, Any]],
) -> list[trimesh.Trimesh]:
    meshes: list[trimesh.Trimesh] = []
    for surface in surface_geometries:
        surface_idx = int(surface.get("index", len(meshes)))
        point = surface.get("point")
        sidewalk_geometries, _ = simple_junction_outer_sidewalk_connection_geometries(
            prepared_roads,
            rules,
            point,
            surface.get("members", []),
            surface_geom=surface.get("geometry"),
            endpoint_overlap_m=JUNCTION_SIDE_COMPONENT_CONNECTOR_OVERLAP_M,
        )
        if not sidewalk_geometries:
            continue
        try:
            geom = road_gen.clean_polygonal(unary_union(sidewalk_geometries))
        except Exception:
            geom = None
        if geom is None or geom.is_empty:
            continue
        max_curb_height = 0.12
        surface_base_z = junction_surface_base_z(prepared_roads, surface.get("members", []))
        for road_idx, _ in surface.get("members", []):
            if road_idx not in prepared_roads.index:
                continue
            rule = road_gen.get_road_rule(prepared_roads.loc[road_idx], rules)
            max_curb_height = max(max_curb_height, float(getattr(rule, "curb_height", 0.12) or 0.12))
        mesh = road_gen.polygon_to_top_mesh(
            geom,
            surface_base_z + max(0.08, max_curb_height + 0.01),
            f"Sidewalk_Junction_Outer_Curve_{surface_idx}",
            visual_color=COLORS["sidewalk"],
        )
        if mesh is None or len(mesh.vertices) <= 0:
            continue
        mesh.metadata.update(
            {
                "name": f"Sidewalk_Junction_Outer_Curve_{surface_idx}",
                "layer_name": "Sidewalk",
                "component_type": "sidewalk",
                "cim_domain": "road",
                "cim_entity_type": "junction_outer_sidewalk_curve",
                "junction_index": surface_idx,
                "road_category": "shared",
            }
        )
        meshes.append(mesh)
    return meshes


def arm_stop_edge_distance(
    row: pd.Series,
    line: LineString,
    rule: Any,
    arm: dict[str, Any],
    arms: list[dict[str, Any]],
    endpoint_overlap_m: float = 0.0,
) -> float | None:
    return junction_approach_element_stop_distance(
        line,
        row,
        rule,
        arm,
        arms,
        outward_extra_m=endpoint_overlap_m,
    )


def simple_junction_through_component_connection_meshes(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    surface_geometries: list[dict[str, Any]],
) -> list[trimesh.Trimesh]:
    meshes: list[trimesh.Trimesh] = []
    for surface in surface_geometries:
        surface_point = surface.get("point")
        if surface_point is None or getattr(surface_point, "is_empty", False):
            continue
        try:
            arms = junction_arm_records(prepared_roads, rules, surface_point, surface.get("members", []))
        except Exception:
            continue
        if junction_type_from_arm_geometry(arms) != "T_JUNCTION":
            continue
        opposing_pairs = junction_opposing_arm_pairs(arms)
        if not opposing_pairs:
            continue
        through_a, through_b, _ = opposing_pairs[0]
        through_ids = {str(through_a.get("arm_id", "")), str(through_b.get("arm_id", ""))}
        branch_arms = [arm for arm in arms if str(arm.get("arm_id", "")) not in through_ids]
        if not branch_arms:
            continue
        branch_direction = arm_direction_vector(branch_arms[0])
        through_direction = arm_direction_vector(through_a)
        if branch_direction is None or through_direction is None:
            continue
        branch_side = through_direction[0] * branch_direction[1] - through_direction[1] * branch_direction[0]
        if abs(branch_side) <= 1e-6:
            continue
        branch_side_sign = 1.0 if branch_side > 0.0 else -1.0

        road_idx_a = prepared_road_index_from_arm(prepared_roads, through_a)
        road_idx_b = prepared_road_index_from_arm(prepared_roads, through_b)
        if road_idx_a is None or road_idx_b is None:
            continue
        row_a = prepared_roads.loc[road_idx_a].copy()
        row_a.name = road_idx_a
        row_b = prepared_roads.loc[road_idx_b].copy()
        row_b.name = road_idx_b
        line_a = row_a.geometry
        line_b = row_b.geometry
        if (
            line_a is None
            or line_a.is_empty
            or not isinstance(line_a, LineString)
            or line_b is None
            or line_b.is_empty
            or not isinstance(line_b, LineString)
        ):
            continue
        rule_a = road_gen.get_road_rule(row_a, rules)
        rule_b = road_gen.get_road_rule(row_b, rules)
        components_a = road_gen.cross_section_components_for_row(row_a)
        components_b = road_gen.cross_section_components_for_row(row_b)
        if not components_a:
            components_a = fallback_cross_section_components(rule_a)
        if not components_b:
            components_b = fallback_cross_section_components(rule_b)
        if road_component_signature_key(components_a) != road_component_signature_key(components_b):
            continue
        if junction_connection_level_key_for_row(row_a, rule_a) != junction_connection_level_key_for_row(row_b, rule_b):
            continue

        edge_a = arm_stop_edge_distance(
            row_a,
            line_a,
            rule_a,
            through_a,
            arms,
            endpoint_overlap_m=JUNCTION_SIDE_COMPONENT_CONNECTOR_OVERLAP_M,
        )
        edge_b = arm_stop_edge_distance(
            row_b,
            line_b,
            rule_b,
            through_b,
            arms,
            endpoint_overlap_m=JUNCTION_SIDE_COMPONENT_CONNECTOR_OVERLAP_M,
        )
        if edge_a is None or edge_b is None:
            continue
        if road_idx_a == road_idx_b:
            start_distance = min(float(edge_a), float(edge_b))
            end_distance = max(float(edge_a), float(edge_b))
            if end_distance - start_distance <= 0.1:
                continue
            try:
                connector_line = substring(line_a, start_distance, end_distance)
            except Exception:
                connector_line = None
        else:
            point_a = line_a.interpolate(float(edge_a))
            point_b = line_b.interpolate(float(edge_b))
            if point_a.distance(point_b) <= 0.1:
                continue
            connector_line = LineString([(point_a.x, point_a.y), (point_b.x, point_b.y)])
        if connector_line is None or connector_line.is_empty:
            continue

        surface_idx = int(surface.get("index", len(meshes)))
        spans = component_spans(components_a)
        line_direction_sign = -1.0 if float(through_a.get("line_direction_sign", 0.0) or 0.0) < 0.0 else 1.0
        for component_idx, (component, left_offset, right_offset) in enumerate(spans):
            component_type = str(component.get("type", ""))
            if component_type not in JUNCTION_CONTINUOUS_ROADSIDE_COMPONENT_TYPES:
                continue
            center_offset = (float(left_offset) + float(right_offset)) * 0.5
            if abs(center_offset) <= 0.05:
                continue
            component_side_sign = 1.0 if center_offset * line_direction_sign > 0.0 else -1.0
            if component_side_sign * branch_side_sign >= 0.0:
                continue
            geom = swept_band_polygon(connector_line, float(left_offset), float(right_offset))
            if geom is None or geom.is_empty:
                continue
            layer_name = component_layer_name_for_row(component_type, row_a)
            mesh = road_gen.polygon_to_top_mesh(
                geom,
                road_surface_z_for_row(row_a) + component_z_offset(component_type, rule_a),
                f"{layer_name}_Through_Connector_{surface_idx}_{road_idx_a}_{component_idx}",
                visual_color=COMPONENT_COLORS.get(layer_name, COLORS["road_surface"]),
            )
            if mesh is None or len(mesh.vertices) <= 0:
                continue
            apply_road_feature_metadata(
                mesh,
                row_a,
                rule_a,
                layer_name=layer_name,
                component_type=component_type,
                component_idx=component_idx,
            )
            mesh.metadata.update(
                {
                    "name": f"{layer_name}_Through_Connector_{surface_idx}_{road_idx_a}_{component_idx}",
                    "cim_entity_type": "junction_through_component_connector",
                    "junction_index": surface_idx,
                    "to_road_idx": str(json_safe_value(road_idx_b)),
                    "branch_side": "left" if branch_side_sign > 0.0 else "right",
                }
            )
            meshes.append(mesh)
    return meshes












def junction_ramp_merge_marking_controls_by_road(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    surface_geometries: list[dict[str, Any]],
) -> tuple[dict[Any, list[dict[str, Any]]], dict[str, float]]:
    controls: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    stats = {
        "suppressed_approach_count": 0.0,
    }
    if prepared_roads.empty or not surface_geometries:
        return {}, stats

    for surface in surface_geometries:
        if str(surface.get("junction_type", "")) != "RAMP_MERGE":
            continue
        surface_point = surface.get("point")
        if surface_point is None or getattr(surface_point, "is_empty", False):
            continue
        try:
            arms = junction_arm_records(prepared_roads, rules, surface_point, surface.get("members", []))
        except Exception:
            continue
        major_ids = ramp_merge_major_arm_ids(arms)
        if not major_ids:
            continue
        for arm in arms:
            if str(arm.get("arm_id", "")) not in major_ids:
                continue
            road_idx = prepared_road_index_from_arm(prepared_roads, arm)
            if road_idx is None:
                continue
            try:
                node_distance = float(arm.get("node_distance_m", 0.0) or 0.0)
                side_sign = float(arm.get("line_direction_sign", 0.0) or 0.0)
            except Exception:
                continue
            controls[road_idx].append(
                {
                    "junction_distance_m": node_distance,
                    "side_sign": side_sign,
                    "allow_crosswalk": False,
                    "allow_stop_line": False,
                    "reason": "ramp_merge_mainline",
                    "junction_index": int(surface.get("index", -1) or -1),
                }
            )
            stats["suppressed_approach_count"] += 1.0
    return dict(controls), stats












def junction_approach_surface_extension_m(
    surface: dict[str, Any],
    arm: dict[str, Any],
    arms: list[dict[str, Any]],
    rule: Any,
) -> float:
    """Small pavement collar beyond the central conflict surface.

    This is intentionally much shorter than the marking setback. Stop lines and
    crosswalks are controls on the approach, not a reason to flood-fill the
    entire road section into the intersection.
    """
    option_id = junction_selected_option_id(surface)
    junction_type = str(surface.get("junction_type", ""))
    hierarchy = str(surface.get("junction_hierarchy", ""))
    lane_width = max(float(getattr(rule, "lane_width", 3.2) or 3.2), 2.6)
    drivable_width = float(arm.get("drivable_width_m", 0.0) or 0.0)
    is_minor = junction_arm_is_minor_approach(arm, arms)

    if hierarchy == "RAMP_OR_GRADE_SEPARATED" or option_id == "RAMP_MERGE_TAPER":
        return max(9.0, min(18.0, drivable_width * 0.52 + lane_width * 1.8))
    if option_id == "SIGNALIZED_ARTERIAL" or hierarchy == "MAJOR_ARTERIAL":
        return max(4.5, min(8.5, lane_width * (2.25 if is_minor else 1.65)))
    if option_id == "MINOR_MAJOR_PRIORITY" or is_minor:
        return max(4.5, min(9.0, drivable_width * 0.42 + lane_width * 0.95))
    if junction_type in {"T_JUNCTION", "Y_JUNCTION", "CROSS_JUNCTION"}:
        return max(2.5, min(5.5, lane_width * 1.35))
    return max(2.0, min(5.0, lane_width * 1.25))


def approach_surface_candidate_sort_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(candidate.get("surface_idx", -1)),
        -int(candidate.get("road_priority", 0) or 0),
        str(candidate.get("road_level_key", "")),
        str(candidate.get("layer_name", "")),
        str(candidate.get("road_idx", "")),
        int(candidate.get("component_idx", 0) or 0),
    )


def build_junction_approach_surface_meshes(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    surface_geometries: list[dict[str, Any]],
    drivable_clip_ranges: dict[Any, list[tuple[float, float]]] | None = None,
) -> dict[str, list[trimesh.Trimesh]]:
    """Optionally extend incoming road surfaces into the junction outer apron."""
    mesh_groups: dict[str, list[trimesh.Trimesh]] = {}
    if not GENERATE_JUNCTION_APPROACH_SURFACES or prepared_roads.empty or not surface_geometries:
        return mesh_groups
    if drivable_clip_ranges is None:
        drivable_clip_ranges = junction_clip_range_profiles_by_road(
            prepared_roads,
            rules,
            surface_geometries,
        ).get("drivable", {})

    connection_tolerance = max(road_gen.junction_connection_tolerance(), JUNCTION_BUCKET_CLUSTER_M)
    candidates: list[dict[str, Any]] = []
    for surface in surface_geometries:
        surface_geom = surface.get("geometry")
        surface_point = surface.get("point")
        if surface_geom is None or surface_geom.is_empty or surface_point is None:
            continue
        surface_idx = int(surface.get("index", len(candidates)))
        center_core = junction_center_fill_core_geom(prepared_roads, rules, surface)
        arms = junction_arm_records(prepared_roads, rules, surface_point, surface.get("members", []))
        connection_policy = junction_connection_level_policy_from_arms(arms)
        connectable_levels = connection_policy["connectable_levels"]
        member_distances: dict[Any, list[float]] = {}
        for road_idx, distance_hint in surface.get("members", []):
            member_distances.setdefault(road_idx, []).append(float(distance_hint))

        for arm in arms:
            road_idx = arm.get("road_idx")
            if road_idx not in prepared_roads.index:
                continue
            row = prepared_roads.loc[road_idx].copy()
            row.name = road_idx
            line = row.geometry
            if line is None or line.is_empty or not isinstance(line, LineString):
                continue
            rule = road_gen.get_road_rule(row, rules)
            level_record = road_level_record_for_row(row, rule)
            connection_level_key = junction_connection_level_key_for_record(level_record)
            if connection_level_key not in connectable_levels:
                continue
            components = road_gen.cross_section_components_for_row(row)
            if not components:
                components = fallback_cross_section_components(rule)
            spans = component_spans(components)
            asphalt_spans = [
                (component_idx, component, left_offset, right_offset)
                for component_idx, (component, left_offset, right_offset) in enumerate(spans)
                if str(component.get("type", "")) in JUNCTION_APPROACH_SURFACE_COMPONENT_TYPES
            ]
            if not asphalt_spans:
                continue

            distance_hints = member_distances.get(road_idx)
            if distance_hints is None:
                distance_hints = member_distances.get(str(road_idx), [])
            projected_distance = float(line.project(surface_point))
            if arm.get("node_distance_m") is not None:
                node_distance = float(arm.get("node_distance_m"))
            elif line.distance(surface_point) <= connection_tolerance:
                node_distance = projected_distance
            elif distance_hints:
                node_distance = min(distance_hints, key=lambda value: abs(float(value) - projected_distance))
            else:
                node_distance = projected_distance
            node_distance = max(0.0, min(float(line.length), float(node_distance)))
            sign = float(arm.get("line_direction_sign", 1.0) or 1.0)
            _, stop_line_offset = junction_approach_offsets_for_row(row, rule)
            stop_line_offset += junction_arm_extra_setback_m(arm, arms)
            stop_line_boundary_distance = node_distance + sign * float(stop_line_offset)
            min_marking_margin = max(CROSSWALK_BAND_LENGTH_M * 0.5, STOP_LINE_WIDTH_M * 0.5, 0.4)
            valid_stop_line_boundary = (
                min_marking_margin
                < stop_line_boundary_distance
                < float(line.length) - min_marking_margin
            )
            extension_m = junction_approach_surface_extension_m(surface, arm, arms, rule)
            apron_range = connector_clip_range_around_distance(
                float(line.length),
                node_distance,
                drivable_clip_ranges.get(road_idx, []),
                overlap_m=max(extension_m, JUNCTION_DRIVABLE_BLEND_OVERLAP_M, 0.0),
            )
            if apron_range is None:
                reach = min(
                    JUNCTION_PATCH_MAX_THROAT_M,
                    junction_surface_throat_distance_for_row(row, rule) + extension_m,
                )
                start = max(0.0, node_distance - reach)
                end = min(float(line.length), node_distance + reach)
            else:
                start, end = apron_range
            local_apron_limit = surface_geom.buffer(
                max(
                    extension_m + road_gen.component_total_width(components) * 0.5,
                    extension_m + drivable_width_for_row(row, rule) * 0.5,
                    2.0,
                ),
                resolution=4,
                join_style=1,
            )
            if sign < 0.0:
                end = min(end, node_distance)
                if valid_stop_line_boundary:
                    start = max(start, stop_line_boundary_distance)
            else:
                start = max(start, node_distance)
                if valid_stop_line_boundary:
                    end = min(end, stop_line_boundary_distance)
            if end - start <= 0.05:
                continue
            try:
                segment = substring(line, start, end)
            except Exception:
                continue
            if segment is None or segment.is_empty:
                continue
            z = road_gen.elevation_at_distance(
                row,
                node_distance,
                default_z=float(row.get("road_z_mean", row.get("elevation", 0.0))),
            )
            road_level_key = road_level_key_for_record(level_record)
            for component_idx, component, left_offset, right_offset in asphalt_spans:
                component_type = str(component.get("type", ""))
                layer_name = component_layer_name_for_row(component_type, row)
                geom = swept_band_polygon(segment, left_offset, right_offset)
                if geom is None or geom.is_empty:
                    continue
                try:
                    geom = road_gen.clean_polygonal(geom.intersection(local_apron_limit))
                except Exception:
                    geom = None
                if geom is None or geom.is_empty:
                    continue
                if center_core is not None and not center_core.is_empty:
                    try:
                        geom = road_gen.clean_polygonal(geom.difference(center_core))
                    except Exception:
                        pass
                if geom is None or geom.is_empty:
                    continue
                candidates.append(
                    {
                        "geom": geom,
                        "surface_idx": surface_idx,
                        "road_idx": road_idx,
                        "road_priority": int(level_record.get("priority", 0) or 0),
                        "road_level_key": road_level_key,
                        "connection_level_key": connection_level_key,
                        "layer_name": layer_name,
                        "component_type": component_type,
                        "component_idx": int(component_idx),
                        "z": z + component_z_offset(component_type, rule),
                        "color": COMPONENT_COLORS.get(layer_name, COLORS["road_surface"]),
                        "arm_id": str(arm.get("arm_id", "")),
                    }
                )

    allocated_geoms_by_surface: dict[int, Any] = {}
    for candidate in sorted(candidates, key=approach_surface_candidate_sort_key):
        geom = candidate["geom"]
        surface_idx = int(candidate["surface_idx"])
        allocated = allocated_geoms_by_surface.get(surface_idx)
        if allocated is not None and not allocated.is_empty:
            try:
                geom = road_gen.clean_polygonal(geom.difference(allocated))
            except Exception:
                pass
        if geom is None or geom.is_empty:
            continue
        layer_name = str(candidate["layer_name"])
        for polygon_idx, polygon in enumerate(iter_polygons(geom)):
            if polygon.area < JUNCTION_APPROACH_APRON_MIN_AREA_M2:
                continue
            mesh_name = (
                f"{layer_name}_Junction_Approach_"
                f"{surface_idx}_{candidate['road_idx']}_{candidate['component_idx']}_{polygon_idx}"
            )
            mesh = road_gen.polygon_to_top_mesh(
                polygon,
                float(candidate["z"]),
                mesh_name,
                visual_color=candidate["color"],
            )
            if len(mesh.vertices) == 0:
                continue
            mesh.metadata.update(
                {
                    "name": mesh_name,
                    "junction_approach_surface": True,
                    "junction_index": surface_idx,
                    "road_idx": str(candidate["road_idx"]),
                    "road_level_key": candidate["road_level_key"],
                    "road_category": str(candidate["road_level_key"]).split(":", 1)[0],
                    "junction_connection_level_key": candidate["connection_level_key"],
                    "component_type": candidate["component_type"],
                    "component_idx": int(candidate["component_idx"]),
                    "arm_id": candidate["arm_id"],
                    "footprint_wkt": polygon.wkt,
                }
            )
            mesh_groups.setdefault(layer_name, []).append(mesh)
        try:
            allocated_geoms_by_surface[surface_idx] = (
                road_gen.clean_polygonal(unary_union([allocated, geom]))
                if allocated is not None and not allocated.is_empty
                else geom
            )
        except Exception:
            allocated_geoms_by_surface[surface_idx] = geom

    return mesh_groups


def junction_stop_line_control_zone_geom(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    surface: dict[str, Any],
    include_minor_extra_setback: bool = False,
):
    surface_geom = surface.get("geometry")
    surface_point = surface.get("point")
    if surface_geom is None or surface_geom.is_empty or surface_point is None:
        return None
    parts = []
    try:
        arms = junction_arm_records(
            prepared_roads,
            rules,
            surface_point,
            surface.get("members", []),
        )
    except Exception:
        arms = []
    for arm in arms:
        road_idx = arm.get("road_idx")
        if road_idx not in prepared_roads.index:
            continue
        row = prepared_roads.loc[road_idx]
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue
        rule = road_gen.get_road_rule(row, rules)
        components = road_gen.cross_section_components_for_row(row)
        if not components:
            components = fallback_cross_section_components(rule)
        spans = component_spans(components)
        if not spans:
            continue
        control_spans = [
            (min(float(left), float(right)), max(float(left), float(right)))
            for component, left, right in spans
            if str(component.get("type", "")) in JUNCTION_APPROACH_CONTROL_COMPONENT_TYPES
            and abs(float(right) - float(left)) > 0.05
        ]
        if not control_spans:
            continue
        node_distance = float(arm.get("node_distance_m", line.project(surface_point)) or 0.0)
        node_distance = max(0.0, min(float(line.length), node_distance))
        sign = float(arm.get("line_direction_sign", 1.0) or 1.0)
        _, stop_line_offset = junction_approach_offsets_for_row(row, rule)
        if include_minor_extra_setback:
            stop_line_offset += junction_arm_extra_setback_m(arm, arms)
        control_offset = max(
            0.0,
            float(stop_line_offset)
            - STOP_LINE_WIDTH_M * 0.5
            + max(float(JUNCTION_STOP_LINE_CONTROL_ZONE_OVERLAP_M), 0.0),
        )
        control_distance = max(0.0, min(float(line.length), node_distance + sign * control_offset))
        start = min(node_distance, control_distance)
        end = max(node_distance, control_distance)
        if end - start <= 0.05:
            continue
        try:
            segment = substring(line, start, end)
        except Exception:
            continue
        if segment is None or segment.is_empty:
            continue
        for left_extent, right_extent in control_spans:
            geom = swept_band_polygon(segment, left_extent, right_extent)
            if geom is None or geom.is_empty:
                continue
            try:
                geom = road_gen.clean_polygonal(geom)
            except Exception:
                pass
            if geom is not None and not geom.is_empty:
                parts.append(geom)
    if not parts:
        return None
    try:
        return road_gen.clean_polygonal(unary_union(parts))
    except Exception:
        return None


def junction_stop_line_control_zones_by_surface(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    surface_geometries: list[dict[str, Any]],
    include_minor_extra_setback: bool = False,
) -> dict[int, list[Any]]:
    zones: dict[int, list[Any]] = defaultdict(list)
    for surface in surface_geometries:
        try:
            surface_idx = int(surface.get("index", -1))
            geom = junction_stop_line_control_zone_geom(
                prepared_roads,
                rules,
                surface,
                include_minor_extra_setback=include_minor_extra_setback,
            )
        except Exception:
            continue
        if surface_idx >= 0 and geom is not None and not geom.is_empty:
            zones[surface_idx].append(geom)
    return dict(zones)


def simple_junction_corner_radius_for_row(row: pd.Series, rule: Any) -> float:
    priority = int(road_gen.road_priority(row))
    base_by_priority = {
        5: 18.0,
        4: 15.0,
        3: 12.0,
        2: 9.0,
        1: 7.0,
    }
    components = road_gen.cross_section_components_for_row(row)
    if not components:
        components = fallback_cross_section_components(rule)
    width_term = max(drivable_width_for_row(row, rule), road_gen.component_total_width(components)) * 0.35
    base = base_by_priority.get(priority, 9.0)
    return max(6.0, min(22.0, max(base, width_term)))


def simple_junction_surface_geometries(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    buckets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not ENABLE_SIMPLE_ROUNDED_JUNCTIONS or prepared_roads.empty:
        return []

    surfaces: list[dict[str, Any]] = []
    for idx, bucket in enumerate(buckets if buckets is not None else junction_point_buckets(prepared_roads)):
        point = bucket["point"]
        member_distances: dict[Any, list[float]] = {}
        for road_idx, distance_hint in bucket["members"]:
            member_distances.setdefault(road_idx, []).append(float(distance_hint))
        try:
            arms = junction_arm_records(prepared_roads, rules, point, bucket["members"])
        except Exception:
            arms = []
        arm_by_road_and_sign = {
            (
                str(arm.get("road_idx")),
                -1 if float(arm.get("line_direction_sign", 0.0) or 0.0) < 0.0 else 1,
            ): arm
            for arm in arms
        }

        parts = []
        radii = []
        approach_boundary_by_arm_id: dict[str, float] = {}
        for road_idx, distance_hints in member_distances.items():
            if road_idx not in prepared_roads.index:
                continue
            row = prepared_roads.loc[road_idx]
            line = row.geometry
            if line is None or line.is_empty or not isinstance(line, LineString):
                continue
            rule = road_gen.get_road_rule(row, rules)
            radius = simple_junction_corner_radius_for_row(row, rule)
            radii.append(radius)
            projected = float(line.project(point))
            if line.distance(point) <= max(road_gen.junction_connection_tolerance(), JUNCTION_BUCKET_CLUSTER_M):
                node_distance = projected
            else:
                node_distance = min(distance_hints, key=lambda value: abs(float(value) - projected))
            node_distance = max(0.0, min(float(line.length), float(node_distance)))
            connection_tolerance = road_gen.junction_connection_tolerance()
            is_internal = connection_tolerance < node_distance < float(line.length) - connection_tolerance
            signs = [-1.0, 1.0] if is_internal else ([1.0] if node_distance <= float(line.length) * 0.5 else [-1.0])

            components = road_gen.cross_section_components_for_row(row)
            if not components:
                components = fallback_cross_section_components(rule)
            spans = asphalt_component_spans_for_components(components, use_planar_surface_types=True)
            if not spans:
                half_width = max(drivable_width_for_row(row, rule), float(rule.road_width)) * 0.5
                spans = [({"type": "main_carriageway"}, -half_width, half_width)]

            for sign in signs:
                arm = arm_by_road_and_sign.get((str(json_safe_value(road_idx)), -1 if sign < 0.0 else 1))
                start = node_distance
                if arm is not None:
                    end = junction_approach_element_stop_distance(line, row, rule, arm, arms)
                    if end is None:
                        continue
                else:
                    stop_edge_offset = junction_approach_element_stop_offset_m(row, rule)
                    end = max(0.0, min(float(line.length), node_distance + sign * max(stop_edge_offset, 1.0)))
                if abs(end - start) <= 0.1:
                    continue
                if arm is not None:
                    approach_boundary_by_arm_id[str(arm.get("arm_id", ""))] = float(end)
                try:
                    segment = substring(line, min(start, end), max(start, end))
                except Exception:
                    continue
                if segment is None or segment.is_empty:
                    continue
                for _, left_offset, right_offset in spans:
                    geom = swept_band_polygon(segment, left_offset, right_offset)
                    if geom is not None and not geom.is_empty:
                        parts.append(geom)

        _, sidewalk_asphalt_fill_parts = simple_junction_outer_sidewalk_connection_geometries(
            prepared_roads,
            rules,
            point,
            bucket["members"],
        )
        parts.extend(sidewalk_asphalt_fill_parts)

        if len(parts) < 2:
            continue
        try:
            base_radius = max(min(radii, default=8.0), 6.0)
            core = point.buffer(max(base_radius * 0.45, 3.0), resolution=12)
            geom = road_gen.clean_polygonal(unary_union([*parts, core]))
            if geom is None or geom.is_empty:
                continue
            corner_radius = max(
                float(JUNCTION_CURB_RETURN_MIN_RADIUS_M),
                min(float(JUNCTION_CURB_RETURN_MAX_RADIUS_M), base_radius * 0.75),
            )
            geom = road_gen.clean_polygonal(
                geom.buffer(corner_radius, join_style=1, resolution=12).buffer(
                    -corner_radius,
                    join_style=1,
                    resolution=12,
                )
            )
        except Exception:
            continue
        if geom is None or geom.is_empty:
            continue
        surfaces.append(
            {
                "index": idx,
                "geometry": geom,
                "members": bucket["members"],
                "point": point,
                "junction_type": "SIMPLE_ROUNDED_ASPHALT",
                "junction_hierarchy": "SIMPLE_AT_GRADE",
                "corner_radius_m": round(float(max(radii, default=0.0)), 3),
                "approach_marking_boundary_by_arm_id": approach_boundary_by_arm_id,
            }
        )
    return surfaces


def simple_junction_clip_profiles_by_road(
    prepared_roads: gpd.GeoDataFrame,
    surface_geometries: list[dict[str, Any]],
) -> dict[str, dict[Any, list[tuple[float, float]]]]:
    raw_ranges: dict[str, dict[Any, list[tuple[float, float]]]] = {
        "drivable": {},
        "roadside": {},
        "divider": {},
        "marking": {},
    }
    surface_union = junction_surface_union(surface_geometries)
    if surface_union is None or surface_union.is_empty:
        return raw_ranges

    marking_union = road_gen.clean_polygonal(
        surface_union.buffer(max(JUNCTION_MARKING_RETREAT_M, 0.1), resolution=4, join_style=1)
    )
    rules = road_gen.load_rules()
    surface_arms_by_index: dict[int, dict[tuple[str, int], dict[str, Any]]] = {}
    for surface in surface_geometries:
        surface_idx = int(surface.get("index", -1))
        surface_point = surface.get("point")
        if surface_idx < 0 or surface_point is None or getattr(surface_point, "is_empty", False):
            continue
        try:
            arms = junction_arm_records(prepared_roads, rules, surface_point, surface.get("members", []))
        except Exception:
            arms = []
        surface_arms_by_index[surface_idx] = {
            (
                str(arm.get("road_idx")),
                -1 if float(arm.get("line_direction_sign", 0.0) or 0.0) < 0.0 else 1,
            ): arm
            for arm in arms
        }
    for surface in surface_geometries:
        surface_idx = int(surface.get("index", -1))
        if surface_idx < 0:
            continue
        arm_by_key = surface_arms_by_index.get(surface_idx, {})
        for arm in arm_by_key.values():
            road_idx = arm.get("road_idx")
            if road_idx not in prepared_roads.index:
                continue
            row = prepared_roads.loc[road_idx]
            line = row.geometry
            if line is None or line.is_empty or not isinstance(line, LineString):
                continue
            rule = road_gen.get_road_rule(row, rules)
            side_sign = -1.0 if float(arm.get("line_direction_sign", 0.0) or 0.0) < 0.0 else 1.0
            node_distance = clamp_junction_chainage(line, float(arm.get("node_distance_m", 0.0) or 0.0))
            if node_distance is None:
                continue
            element_stop_distance = junction_approach_element_stop_distance(
                line,
                row,
                rule,
                arm,
                list(arm_by_key.values()),
            )
            if element_stop_distance is None:
                continue
            start = min(node_distance, element_stop_distance)
            end = max(node_distance, element_stop_distance)
            stop_clearance = max(float(JUNCTION_APPROACH_ELEMENT_STOP_CLEARANCE_M), 0.0)
            if side_sign > 0.0:
                end = min(float(line.length), end + stop_clearance)
            else:
                start = max(0.0, start - stop_clearance)
            if end - start <= 0.05:
                continue
            for profile in ("drivable", "roadside", "divider", "marking"):
                add_adjusted_clip_range(
                    raw_ranges[profile],
                    road_idx,
                    float(line.length),
                    start,
                    end,
                )
    if marking_union is not None and not marking_union.is_empty:
        for road_idx, row in prepared_roads.iterrows():
            line = row.geometry
            if line is None or line.is_empty or not isinstance(line, LineString):
                continue
            for start, end in line_intersection_distance_ranges(line, marking_union):
                add_adjusted_clip_range(
                    raw_ranges["marking"],
                    road_idx,
                    float(line.length),
                    start,
                    end,
                    outward_pad=0.10,
                )
    return {profile: merge_clip_ranges_by_road(ranges) for profile, ranges in raw_ranges.items()}


def left_normal(vector: tuple[float, float]) -> tuple[float, float]:
    return (-float(vector[1]), float(vector[0]))


def normalize_xy(vector: tuple[float, float]) -> tuple[float, float] | None:
    length = math.hypot(float(vector[0]), float(vector[1]))
    if length <= 1e-6:
        return None
    return (float(vector[0]) / length, float(vector[1]) / length)


def line_intersection_point(
    point_a: tuple[float, float],
    direction_a: tuple[float, float],
    point_b: tuple[float, float],
    direction_b: tuple[float, float],
) -> tuple[float, float] | None:
    ax, ay = float(point_a[0]), float(point_a[1])
    bx, by = float(point_b[0]), float(point_b[1])
    dx1, dy1 = float(direction_a[0]), float(direction_a[1])
    dx2, dy2 = float(direction_b[0]), float(direction_b[1])
    determinant = dx1 * dy2 - dy1 * dx2
    if abs(determinant) <= 1e-6:
        return None
    delta_x = bx - ax
    delta_y = by - ay
    t = (delta_x * dy2 - delta_y * dx2) / determinant
    return (ax + dx1 * t, ay + dy1 * t)


def circular_arc_band_polygon(
    start_point: tuple[float, float],
    end_point: tuple[float, float],
    center_point: tuple[float, float],
    half_width: float,
    sample_count: int = 16,
):
    if half_width <= 0.05:
        return None
    start_vector = (
        float(start_point[0]) - float(center_point[0]),
        float(start_point[1]) - float(center_point[1]),
    )
    end_vector = (
        float(end_point[0]) - float(center_point[0]),
        float(end_point[1]) - float(center_point[1]),
    )
    start_radius = math.hypot(start_vector[0], start_vector[1])
    end_radius = math.hypot(end_vector[0], end_vector[1])
    if start_radius <= 0.05 or end_radius <= 0.05:
        return None
    radius = 0.5 * (start_radius + end_radius)
    if radius <= half_width + 0.05:
        return None
    start_angle = math.atan2(start_vector[1], start_vector[0])
    end_angle = math.atan2(end_vector[1], end_vector[0])
    delta = math.atan2(
        start_vector[0] * end_vector[1] - start_vector[1] * end_vector[0],
        start_vector[0] * end_vector[0] + start_vector[1] * end_vector[1],
    )
    if abs(delta) <= math.radians(10.0):
        return None
    steps = max(int(sample_count), int(abs(delta) / math.radians(8.0)) + 2)
    arc_points = [
        (
            float(center_point[0]) + math.cos(start_angle + delta * (idx / steps)) * radius,
            float(center_point[1]) + math.sin(start_angle + delta * (idx / steps)) * radius,
        )
        for idx in range(steps + 1)
    ]
    try:
        return road_gen.clean_polygonal(
            LineString(arc_points).buffer(
                float(half_width),
                cap_style=2,
                join_style=1,
                resolution=12,
            )
        )
    except Exception:
        return None


def add_simple_junction_boundary_markings(
    prepared_roads: gpd.GeoDataFrame,
    rules: dict[str, Any],
    surface_geometries: list[dict[str, Any]],
    crosswalk_meshes: list[trimesh.Trimesh],
    stop_line_meshes: list[trimesh.Trimesh],
    blocked_marking_ranges_by_road: dict[Any, list[tuple[float, float]]] | None = None,
    approach_clip_ranges_by_road: dict[Any, list[tuple[float, float]]] | None = None,
) -> Counter:
    stats: Counter[str] = Counter()
    if not surface_geometries:
        return stats

    for surface in surface_geometries:
        geom = surface.get("geometry")
        surface_idx = int(surface.get("index", -1))
        if geom is None or geom.is_empty:
            continue
        surface_point = surface.get("point")
        approach_boundary_by_arm_id = {
            str(key): float(value)
            for key, value in (surface.get("approach_marking_boundary_by_arm_id") or {}).items()
        }
        surface_arms = (
            junction_arm_records(prepared_roads, rules, surface_point, surface.get("members", []))
            if surface_point is not None and not getattr(surface_point, "is_empty", False)
            else []
        )
        arm_by_key = {
            (
                str(arm.get("road_idx")),
                -1 if float(arm.get("line_direction_sign", 0.0) or 0.0) < 0.0 else 1,
            ): arm
            for arm in surface_arms
        }
        member_road_ids = sorted({road_idx for road_idx, _ in surface.get("members", [])}, key=lambda item: str(item))
        for road_idx in member_road_ids:
            if road_idx not in prepared_roads.index:
                continue
            row = prepared_roads.loc[road_idx].copy()
            row.name = road_idx
            line = row.geometry
            if line is None or line.is_empty or not isinstance(line, LineString):
                continue
            rule = road_gen.get_road_rule(row, rules)
            blocked_marking_ranges = (
                padded_distance_ranges(
                    blocked_marking_ranges_by_road.get(road_idx, []),
                    float(line.length),
                    max(CROSSWALK_BAND_LENGTH_M * 0.5, STOP_LINE_WIDTH_M * 0.5),
                )
                if blocked_marking_ranges_by_road
                else []
            )
            approach_clip_ranges = (
                approach_clip_ranges_by_road.get(road_idx, [])
                if approach_clip_ranges_by_road
                else []
            )
            ranges = line_intersection_distance_ranges(line, geom)
            if not ranges:
                continue
            stop_line_lateral_pieces = junction_stop_line_lateral_pieces_for_row(row, rule)
            crosswalk_left, crosswalk_right = junction_crosswalk_lateral_range_for_row(row, rule)
            crosswalk_lateral_offsets = crosswalk_stripe_layout_for_range(crosswalk_left, crosswalk_right)
            default_z = float(row.get("road_z_mean", row.get("elevation", 0.0)))
            for range_idx, (start, end) in enumerate(merge_distance_ranges(ranges, merge_tolerance=0.25)):
                boundaries = []
                if start > 0.2:
                    boundaries.append((float(start), -1.0, "Before"))
                if end < float(line.length) - 0.2:
                    boundaries.append((float(end), 1.0, "After"))
                for boundary_distance, sign, side_name in boundaries:
                    arm = arm_by_key.get((str(json_safe_value(road_idx)), int(sign)))
                    has_custom_boundary = False
                    if arm is not None:
                        custom_boundary = approach_boundary_by_arm_id.get(str(arm.get("arm_id", "")))
                        if custom_boundary is not None:
                            boundary_distance = max(
                                0.0,
                                min(float(line.length), float(custom_boundary)),
                            )
                            has_custom_boundary = True
                    if has_custom_boundary:
                        stop_distance = float(boundary_distance) + float(sign) * (STOP_LINE_WIDTH_M * 0.5)
                        crosswalk_distance = float(boundary_distance) - float(sign) * (
                            float(SIMPLE_JUNCTION_STOP_LINE_TO_CROSSWALK_GAP_M)
                            + CROSSWALK_BAND_LENGTH_M * 0.5
                        )
                    else:
                        crosswalk_distance = simple_junction_crosswalk_center_distance(boundary_distance, sign)
                        stop_distance = simple_junction_stop_line_center_distance(boundary_distance, sign)
                    min_margin = max(CROSSWALK_BAND_LENGTH_M * 0.5, STOP_LINE_WIDTH_M * 0.5, 0.4)
                    if not (
                        min_margin < stop_distance < float(line.length) - min_margin
                        and min_margin < crosswalk_distance < float(line.length) - min_margin
                    ):
                        continue
                    if transverse_marking_is_inside_merged_junction_clip(
                        boundary_distance,
                        float(line.length),
                        approach_clip_ranges,
                    ):
                        stats["internal_junction_cluster_marking_suppressed"] += 1
                        continue
                    if not transverse_marking_has_approach_surface(
                        boundary_distance,
                        sign,
                        float(line.length),
                        stop_distance,
                        crosswalk_distance,
                        approach_clip_ranges,
                    ):
                        stats["insufficient_approach_surface_marking_suppressed"] += 1
                        continue
                    if road_gen.distance_in_ranges(stop_distance, blocked_marking_ranges) or road_gen.distance_in_ranges(
                        crosswalk_distance,
                        blocked_marking_ranges,
                    ):
                        stats["short_connector_marking_suppressed"] += 1
                        continue
                    stop_point, tangent, normal = road_gen.line_frame_at_distance(line, stop_distance)
                    stop_z = road_gen.elevation_at_distance(row, stop_distance, default_z=default_z)
                    stop_name = f"Stop_Line_{road_idx}_{surface_idx}_{range_idx}_{side_name}"
                    stop_meshes = []
                    for piece_offset, piece_width in stop_line_lateral_pieces:
                        center = (
                            stop_point.x + normal[0] * piece_offset,
                            stop_point.y + normal[1] * piece_offset,
                        )
                        piece_mesh = oriented_rect_mesh(
                            center,
                            tangent,
                            normal,
                            STOP_LINE_WIDTH_M,
                            piece_width,
                            stop_z + rule.lane_marking_z_offset + JUNCTION_STOP_LINE_TOP_Z_OFFSET_M,
                            f"{stop_name}_{round(piece_offset, 2)}",
                            COLORS["stop_line"],
                        )
                        if len(piece_mesh.vertices) > 0:
                            stop_meshes.append(piece_mesh)
                    stop_mesh = road_gen.merge_named_meshes(stop_name, stop_meshes, COLORS["stop_line"])
                    if len(stop_mesh.vertices) > 0:
                        apply_road_feature_metadata(
                            stop_mesh,
                            row,
                            rule,
                            layer_name="Stop_Line",
                            component_type="stop_line",
                            component_idx=surface_idx,
                        )
                        stop_mesh.metadata.update({"junction_marking_type": "stop_line", "junction_index": surface_idx})
                        stop_line_meshes.append(stop_mesh)
                        stats["stop_line_count"] += 1

                    crosswalk_point, crosswalk_tangent, crosswalk_normal = road_gen.line_frame_at_distance(line, crosswalk_distance)
                    crosswalk_z = road_gen.elevation_at_distance(row, crosswalk_distance, default_z=default_z)
                    for stripe_idx, lateral_offset in enumerate(crosswalk_lateral_offsets):
                        stripe_name = f"Crosswalk_{road_idx}_{surface_idx}_{range_idx}_{side_name}_{stripe_idx}"
                        stripe_meshes = []
                        for piece_offset, piece_width in marking_lateral_pieces_around_center_gap(
                            lateral_offset,
                            CROSSWALK_STRIPE_WIDTH_M,
                            gap_width=JUNCTION_CROSSWALK_CENTER_GAP_M,
                        ):
                            center = (
                                crosswalk_point.x + crosswalk_normal[0] * piece_offset,
                                crosswalk_point.y + crosswalk_normal[1] * piece_offset,
                            )
                            piece_mesh = oriented_rect_mesh(
                                center,
                                crosswalk_tangent,
                                crosswalk_normal,
                                CROSSWALK_BAND_LENGTH_M,
                                piece_width,
                                crosswalk_z + rule.lane_marking_z_offset + JUNCTION_CROSSWALK_TOP_Z_OFFSET_M,
                                f"{stripe_name}_{round(piece_offset, 2)}",
                                COLORS["crosswalk"],
                            )
                            if len(piece_mesh.vertices) > 0:
                                stripe_meshes.append(piece_mesh)
                        stripe_mesh = road_gen.merge_named_meshes(stripe_name, stripe_meshes, COLORS["crosswalk"])
                        if len(stripe_mesh.vertices) > 0:
                            apply_road_feature_metadata(
                                stripe_mesh,
                                row,
                                rule,
                                layer_name="Crosswalk",
                                component_type="crosswalk",
                                component_idx=stripe_idx,
                            )
                            stripe_mesh.metadata.update({"junction_marking_type": "crosswalk", "junction_index": surface_idx})
                            crosswalk_meshes.append(stripe_mesh)
                            stats["crosswalk_stripe_count"] += 1
                    stats["candidate_approach_count"] += 1
    return stats






def build_road_surface_meshes(
    roads: gpd.GeoDataFrame,
    profile: RoadGenerationProfile = CIM4_PROFILE,
) -> dict[str, trimesh.Trimesh]:
    """Build road meshes without the legacy junction subsystem attached."""
    profile = road_generation_profile(profile)
    if roads.empty:
        road_generation_log("No road records; skipping road mesh generation.")
        return {}

    road_generation_log(
        f"Preparing {len(roads)} source road records for {profile.name.upper()} surface generation."
    )
    prepared_roads = roads.copy() if roads_are_prepared_for_surfaces(roads) else prepare_roads_for_surfaces(roads)
    if prepared_roads.empty:
        road_generation_log("No valid prepared roads; skipping road mesh generation.")
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
    road_generation_log("Detecting junction buckets from road topology.")
    junction_buckets = junction_point_buckets(prepared_roads)
    road_generation_log(f"Detected {len(junction_buckets)} junction buckets.")
    road_generation_log("Building simple rounded asphalt junction surfaces.")
    junction_surface_geometries = simple_junction_surface_geometries(prepared_roads, rules, junction_buckets)
    road_generation_log(f"Built {len(junction_surface_geometries)} simple junction asphalt surfaces.")
    one_sided_sidewalk_protection = (
        one_sided_sidewalk_protection_geometries_by_surface(
            prepared_roads,
            rules,
            junction_surface_geometries,
        )
        if profile.generate_side_component_connectors
        else {}
    )
    one_sided_sidewalk_subtract = {
        surface_idx: [
            item["geometry"]
            for item in items
            if item.get("geometry") is not None and not item["geometry"].is_empty
        ]
        for surface_idx, items in one_sided_sidewalk_protection.items()
    }
    junction_mask_geom = junction_surface_union(junction_surface_geometries)
    junction_asset_filter_geom = junction_surface_union(junction_surface_geometries, clearance=1.0)
    junction_drivable_core_clip_geom = road_gen.clean_polygonal(junction_mask_geom) if junction_mask_geom is not None and not junction_mask_geom.is_empty else None
    junction_side_component_edge_clip_geom = junction_drivable_core_clip_geom
    junction_clip_profiles = simple_junction_clip_profiles_by_road(prepared_roads, junction_surface_geometries)
    short_gap_surface_meshes, short_gap_clip_profiles = short_junction_gap_surface_meshes_and_clip_ranges(
        prepared_roads,
        rules,
        junction_clip_profiles,
    )
    drivable_clip_ranges = junction_clip_profiles.get("drivable", {})
    roadside_clip_ranges = junction_clip_profiles.get("roadside", {})
    divider_clip_ranges = junction_clip_profiles.get("divider", {})
    marking_clip_ranges = junction_clip_profiles.get("marking", {})
    outer_sidewalk_clip_ranges = roadside_clip_ranges
    drivable_clip_ranges = merge_clip_range_maps(drivable_clip_ranges, short_gap_clip_profiles.get("drivable", {}))
    roadside_clip_ranges = merge_clip_range_maps(roadside_clip_ranges, short_gap_clip_profiles.get("roadside", {}))
    divider_clip_ranges = merge_clip_range_maps(divider_clip_ranges, short_gap_clip_profiles.get("divider", {}))
    marking_clip_ranges = merge_clip_range_maps(marking_clip_ranges, short_gap_clip_profiles.get("marking", {}))
    side_component_conflict_clip_geom = junction_drivable_core_clip_geom
    side_drivable_component_conflict_clip_geom = junction_drivable_core_clip_geom
    approach_extra_setbacks_by_road = junction_approach_extra_setbacks_by_road(
        prepared_roads,
        rules,
        junction_surface_geometries,
    )
    ramp_merge_marking_controls_by_road: dict[Any, list[dict[str, Any]]] = {}
    junction_surface_meshes = build_rounded_junction_surface_meshes(
        prepared_roads,
        junction_surface_geometries,
        subtract_geometries_by_surface=one_sided_sidewalk_subtract,
    )
    junction_surface_meshes.extend(short_gap_surface_meshes)
    if short_gap_surface_meshes:
        road_generation_log(
            f"Converted {len(short_gap_surface_meshes)} short road gaps/connectors to junction-surface material."
        )
    one_sided_sidewalk_meshes = one_sided_sidewalk_protection_meshes(one_sided_sidewalk_protection)
    component_mesh_groups["Sidewalk"].extend(one_sided_sidewalk_meshes)
    if one_sided_sidewalk_meshes:
        road_generation_log(
            f"Preserved {len(one_sided_sidewalk_meshes)} D6 one-sided sidewalk junction approaches."
        )
    sidewalk_curve_meshes = (
        simple_junction_outer_sidewalk_connection_meshes(
            prepared_roads,
            rules,
            junction_surface_geometries,
        )
        if profile.generate_side_component_connectors
        else []
    )
    component_mesh_groups["Sidewalk"].extend(sidewalk_curve_meshes)
    road_generation_log(
        f"Built {len(sidewalk_curve_meshes)} outer sidewalk curve meshes from junction boundary arcs."
    )
    through_component_meshes = (
        simple_junction_through_component_connection_meshes(
            prepared_roads,
            rules,
            junction_surface_geometries,
        )
        if profile.generate_side_component_connectors
        else []
    )
    through_component_counts: Counter[str] = Counter()
    for mesh in through_component_meshes:
        layer_name = str((mesh.metadata or {}).get("layer_name") or "")
        if not layer_name:
            continue
        component_mesh_groups.setdefault(layer_name, []).append(mesh)
        through_component_counts[layer_name] += 1
    if through_component_meshes:
        road_generation_log(
            "Built "
            f"{len(through_component_meshes)} through-road side component connectors "
            f"for T-junction opposite sides: {dict(sorted(through_component_counts.items()))}."
        )
    asset_mesh_groups: dict[str, list[trimesh.Trimesh]] = {}
    total_roads = len(prepared_roads)
    road_generation_log(
        "Sweeping cross-section components, lane markings, curbs, crosswalks, stop lines, and roadside assets."
    )
    for road_counter, (road_idx, row) in enumerate(prepared_roads.iterrows(), start=1):
        if road_counter == 1 or road_counter % 25 == 0 or road_counter == total_roads:
            road_generation_log(f"Processing road {road_counter}/{total_roads}.")
        row = row.copy()
        row.name = road_idx
        rule = road_gen.get_road_rule(row, rules)
        components = road_gen.cross_section_components_for_row(row)
        if not components:
            components = fallback_cross_section_components(rule)
        spans = component_spans(components)
        source_line = row.geometry
        street_light_blocked_ranges = junction_stop_line_asset_exclusion_ranges_for_row(
            row,
            source_line,
            rule,
            approach_extra_setbacks_by_road,
            asset_margin_m=STREET_LIGHT_STOP_LINE_MARGIN_M,
        )
        tree_blocked_ranges = junction_stop_line_asset_exclusion_ranges_for_row(
            row,
            source_line,
            rule,
            approach_extra_setbacks_by_road,
            asset_margin_m=TREE_STOP_LINE_MARGIN_M,
        )
        white_edge_suppression = carriageway_boundary_edge_suppression(spans)
        for line_idx, line in enumerate(iter_lines(row.geometry)):
            line_spans = spans
            segment_cache: dict[str, list[tuple[LineString, float]]] = {}

            def clipped_segments_for(clip_profile: str) -> list[tuple[LineString, float]]:
                if clip_profile not in segment_cache:
                    profile_ranges = {
                        "drivable": drivable_clip_ranges,
                        "roadside": roadside_clip_ranges,
                        "outer_sidewalk": outer_sidewalk_clip_ranges,
                        "divider": divider_clip_ranges,
                        "marking": marking_clip_ranges,
                    }.get(clip_profile, {})
                    clip_ranges = profile_ranges.get(road_idx, [])
                    segment_cache[clip_profile] = (
                        road_gen.line_segments_outside_ranges(line, clip_ranges)
                        if clip_ranges
                        else [(line, 0.0)]
                    )
                return segment_cache[clip_profile]

            # 1. Longitudinal cross-section components: road surfaces,
            # sidewalks, non-motor lanes, green belts, medians, and similar.
            for component_idx, (component, left_offset, right_offset) in enumerate(line_spans):
                component_type = str(component.get("type", ""))
                layer_name = component_layer_name_for_row(component_type, row)
                color = COMPONENT_COLORS.get(layer_name, COLORS["green_belt"])
                if component_type in JUNCTION_ASPHALT_COMPONENT_TYPES:
                    clip_profile = "drivable"
                elif component_type in JUNCTION_DIVIDER_COMPONENT_TYPES:
                    clip_profile = "divider"
                elif is_outermost_sidewalk_span(line_spans, component_idx):
                    clip_profile = "outer_sidewalk"
                else:
                    clip_profile = "roadside"
                for segment_idx, (segment, distance_offset) in enumerate(clipped_segments_for(clip_profile)):
                    if segment is None or segment.is_empty:
                        continue
                    if component_type in JUNCTION_CONTINUOUS_ROADSIDE_COMPONENT_TYPES:
                        component_clip_mask = side_component_conflict_clip_geom
                    elif component_type in RAISED_COMPONENT_TYPES:
                        component_clip_mask = side_component_conflict_clip_geom
                    elif component_type in JUNCTION_SIDE_DRIVABLE_RETRACT_COMPONENT_TYPES:
                        component_clip_mask = side_drivable_component_conflict_clip_geom
                    elif component_type in JUNCTION_EDGE_CLIPPED_COMPONENT_TYPES:
                        component_clip_mask = junction_side_component_edge_clip_geom
                    elif component_type in JUNCTION_DIVIDER_COMPONENT_TYPES:
                        component_clip_mask = junction_drivable_core_clip_geom
                    elif component_type in DRIVABLE_COMPONENT_TYPES:
                        component_clip_mask = junction_drivable_core_clip_geom
                    else:
                        component_clip_mask = None
                    mesh = swept_band_mesh(
                        row,
                        segment,
                        left_offset,
                        right_offset,
                        f"{layer_name}_{road_idx}_{line_idx}_{segment_idx}_{component_idx}",
                        color,
                        z_offset=component_z_offset(component_type, rule),
                        distance_offset=distance_offset,
                        clip_mask=component_clip_mask,
                    )
                    if len(mesh.vertices) > 0:
                        apply_road_feature_metadata(
                            mesh,
                            row,
                            rule,
                            layer_name=layer_name,
                            component_type=component_type,
                            component_idx=component_idx,
                        )
                        component_mesh_groups.setdefault(layer_name, []).append(mesh)

            # 2. Lane markings are generated on the marking profile so they
            # stop earlier than road surfaces near intersections.
            if profile.generate_lane_markings:
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

            # 3. Curbs follow the roadside profile and retreat from junctions
            # together with raised roadside components; divider curbs use the
            # divider profile so medians reach the stop line.
            if profile.generate_curbs:
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
                        clip_mask=side_component_conflict_clip_geom,
                        exclude_boundary_component_types=JUNCTION_DIVIDER_COMPONENT_TYPES,
                    )
                for segment, distance_offset in clipped_segments_for("divider"):
                    if segment is None or segment.is_empty:
                        continue
                    add_component_curbs(
                        row,
                        segment,
                        rule,
                        line_spans,
                        curb_meshes,
                        distance_offset=distance_offset,
                        clip_mask=side_component_conflict_clip_geom,
                        include_boundary_component_types=JUNCTION_DIVIDER_COMPONENT_TYPES,
                    )
            # 4. Roadside assets are generated without legacy junction or
            # stop-line exclusion while the junction subsystem is being rebuilt.
            if GENERATE_ROAD_ASSETS and profile.generate_assets:
                for mesh in road_gen.build_street_light_meshes(
                    row,
                    rule,
                    blocked_distance_ranges=street_light_blocked_ranges,
                ):
                    if mesh_center_inside_polygon(mesh, junction_asset_filter_geom):
                        continue
                    name = f"{mesh.metadata.get('name', 'Street_Light')}_{road_idx}_{line_idx}"
                    mesh.metadata["name"] = name
                    apply_road_feature_metadata(mesh, row, rule, component_type="street_light")
                    mesh.metadata["cim_entity_type"] = "road_asset"
                    asset_mesh_groups.setdefault(road_gen.asset_mesh_group_name(mesh), []).append(mesh)
                if profile.generate_trees:
                    for mesh in road_gen.build_tree_meshes(
                        row,
                        rule,
                        blocked_distance_ranges=tree_blocked_ranges,
                    ):
                        if mesh_center_inside_polygon(mesh, junction_asset_filter_geom):
                            continue
                        name = f"{mesh.metadata.get('name', 'Tree')}_{road_idx}_{line_idx}"
                        mesh.metadata["name"] = name
                        apply_road_feature_metadata(mesh, row, rule, component_type="tree")
                        mesh.metadata["cim_entity_type"] = "road_asset"
                        asset_mesh_groups.setdefault(road_gen.asset_mesh_group_name(mesh), []).append(mesh)

    if profile.generate_junction_markings:
        junction_marking_stats.update(
            add_simple_junction_boundary_markings(
                prepared_roads,
                rules,
                junction_surface_geometries,
                crosswalk_meshes,
                stop_line_meshes,
                short_gap_clip_profiles.get("marking", {}),
                drivable_clip_ranges,
            )
        )
    road_generation_log(
        "Generated "
        f"{junction_marking_stats.get('stop_line_count', 0)} stop lines and "
        f"{junction_marking_stats.get('crosswalk_stripe_count', 0)} crosswalk stripes from simple junction boundaries."
    )

    sidewalk_conflicts = sidewalk_conflict_index(component_mesh_groups, junction_surface_meshes)
    cleaned_sidewalk_meshes, sidewalk_cleanup_stats = trim_sidewalk_meshes_against_indexed_conflicts(
        component_mesh_groups.get("Sidewalk", []),
        sidewalk_conflicts,
    )
    component_mesh_groups["Sidewalk"] = cleaned_sidewalk_meshes
    if sidewalk_cleanup_stats["trimmed_count"] or sidewalk_cleanup_stats["removed_count"]:
        road_generation_log(
            "Removed abrupt sidewalk intrusions into road/junction surfaces: "
            f"{sidewalk_cleanup_stats['trimmed_count']} trimmed, "
            f"{sidewalk_cleanup_stats['removed_count']} removed, "
            f"{sidewalk_cleanup_stats['removed_area_m2']:.2f} m2 affected."
        )

    if GENERATE_JUNCTION_DEBUG_MODELS:
        junction_debug_manifest = write_junction_debug_models(
            prepared_roads,
            junction_surface_geometries,
            {
                **component_mesh_groups,
                "Curb": curb_meshes,
                "Lane_Marking_White": white_marking_meshes,
                "Lane_Marking_Yellow": yellow_marking_meshes,
                "Crosswalk": crosswalk_meshes,
                "Stop_Line": stop_line_meshes,
                "Junction_Surface": junction_surface_meshes,
            },
        )
        road_generation_log(
            f"Exported {junction_debug_manifest['summary']['junction_model_count']} separate junction debug OBJ models."
        )
    else:
        road_generation_log(
            "Skipped separate junction debug OBJ models (set CIM_ROAD_EXPORT_JUNCTION_DEBUG=1 to enable)."
        )

    combined_meshes = {}
    road_generation_log("Merging road mesh parts by output layer and source-road monomer.")
    for group_name, parts in component_mesh_groups.items():
        combined_meshes.update(
            combine_meshes_by_road_monomer(
                group_name,
                parts,
                COMPONENT_COLORS.get(group_name, COLORS["road_surface"]),
            )
        )

    for layer_name, meshes, color in [
        ("Curb", curb_meshes, COLORS["curb"]),
        ("Lane_Marking_White", white_marking_meshes, COLORS["lane_marking"]),
        ("Lane_Marking_Yellow", yellow_marking_meshes, COLORS["center_marking"]),
        ("Crosswalk", crosswalk_meshes, COLORS["crosswalk"]),
        ("Stop_Line", stop_line_meshes, COLORS["stop_line"]),
    ]:
        typed_meshes = combine_meshes_by_road_monomer(layer_name, meshes, color)
        if layer_name in {"Crosswalk", "Stop_Line"}:
            for mesh in typed_meshes.values():
                mesh.metadata.update({f"junction_{key}": int(value) for key, value in junction_marking_stats.items()})
        combined_meshes.update(typed_meshes)

    junction_mesh = combine_mesh_list("Junction_Surface_Shared_All", junction_surface_meshes, COLORS["road_surface_main"])
    if junction_mesh is not None:
        junction_mesh.metadata["road_category"] = "shared"
        combined_meshes[junction_mesh.metadata["name"]] = junction_mesh

    for group_name, parts in sorted(asset_mesh_groups.items()):
        combined_meshes.update(
            combine_meshes_by_road_monomer(
                group_name,
                parts,
                road_gen.asset_group_color(group_name) or COLORS["road_surface"],
            )
        )
    closed_output_road_component_gap_count = 0
    closed_output_road_component_gap_area = 0.0
    closed_output_junction_gap_count = 0
    closed_output_junction_gap_area = 0.0
    for mesh_name, mesh in list(combined_meshes.items()):
        layer_name = str((mesh.metadata or {}).get("layer_name") or "")
        is_junction_surface_mesh = str(mesh_name).startswith("Junction_Surface")
        close_m = 0.0
        if is_junction_surface_mesh:
            close_m = float(OUTPUT_JUNCTION_SURFACE_CLOSE_M)
        elif layer_name in OUTPUT_ROAD_COMPONENT_CLOSE_LAYERS:
            close_m = float(OUTPUT_ROAD_COMPONENT_CLOSE_M)
        elif layer_name in OUTPUT_ROADSIDE_COMPONENT_CLOSE_LAYERS:
            close_m = float(OUTPUT_ROADSIDE_COMPONENT_CLOSE_M)
        if close_m <= 0.0:
            continue
        closed_mesh, closed_count, closed_area = close_output_same_material_gaps(
            mesh,
            close_m,
        )
        if closed_count <= 0 or closed_mesh is None or len(closed_mesh.vertices) == 0:
            continue
        if is_junction_surface_mesh:
            closed_output_junction_gap_count += closed_count
            closed_output_junction_gap_area += closed_area
        elif layer_name in OUTPUT_ROAD_COMPONENT_CLOSE_LAYERS:
            closed_output_road_component_gap_count += closed_count
            closed_output_road_component_gap_area += closed_area
        elif layer_name in OUTPUT_ROADSIDE_COMPONENT_CLOSE_LAYERS:
            closed_output_road_component_gap_count += closed_count
            closed_output_road_component_gap_area += closed_area
        combined_meshes[mesh_name] = closed_mesh
    if closed_output_junction_gap_count:
        road_generation_log(
            "Closed "
            f"{closed_output_junction_gap_count} junction-surface output gaps "
            f"({closed_output_junction_gap_area:.2f} m2 total)."
        )
    if closed_output_road_component_gap_count:
        road_generation_log(
            "Closed "
            f"{closed_output_road_component_gap_count} road-component output gaps "
            f"({closed_output_road_component_gap_area:.2f} m2 total)."
        )
    final_sidewalk_cleanup_stats = trim_output_sidewalk_meshes_against_conflicts(combined_meshes)
    if final_sidewalk_cleanup_stats["trimmed_count"] or final_sidewalk_cleanup_stats["removed_count"]:
        road_generation_log(
            "Final sidewalk output cleanup removed road/junction intrusions: "
            f"{final_sidewalk_cleanup_stats['trimmed_count']} trimmed, "
            f"{final_sidewalk_cleanup_stats['removed_count']} removed, "
            f"{final_sidewalk_cleanup_stats['removed_area_m2']:.2f} m2 affected."
        )
    shared_sidewalk_overlap_stats = trim_shared_sidewalk_overlaps(combined_meshes)
    if shared_sidewalk_overlap_stats["trimmed_count"] or shared_sidewalk_overlap_stats["removed_count"]:
        road_generation_log(
            "Final shared sidewalk overlap cleanup preserved road sidewalks: "
            f"{shared_sidewalk_overlap_stats['trimmed_count']} shared mesh(es) trimmed, "
            f"{shared_sidewalk_overlap_stats['removed_count']} removed, "
            f"{shared_sidewalk_overlap_stats['removed_area_m2']:.2f} m2 affected."
        )
    sidewalk_pair_overlap_stats = trim_sidewalk_pair_overlaps_by_priority(combined_meshes)
    if sidewalk_pair_overlap_stats["trimmed_count"] or sidewalk_pair_overlap_stats["removed_count"]:
        road_generation_log(
            "Final sidewalk pair-overlap cleanup preserved higher-priority sidewalks: "
            f"{sidewalk_pair_overlap_stats['trimmed_count']} trimmed, "
            f"{sidewalk_pair_overlap_stats['removed_count']} removed, "
            f"{sidewalk_pair_overlap_stats['removed_area_m2']:.2f} m2 affected."
        )
    road_generation_log(f"Finished road mesh generation with {len(combined_meshes)} merged output layers.")
    return combined_meshes


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
    """Create the semantic record for one generated road centerline."""
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
    level_record = road_level_record_for_row(row, rule)
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
        "road_level": level_record,
        "road_level_key": road_level_key_for_record(level_record),
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
        "longitudinal_marking_stop_clearance_m": round(longitudinal_marking_stop_clearance_for_row(row, rule), 3),
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


def build_city_road_semantic(
    prepared_roads: gpd.GeoDataFrame,
    origin: tuple[float, float],
    profile: RoadGenerationProfile = CIM4_PROFILE,
) -> dict[str, Any]:
    profile = road_generation_profile(profile)
    records = [road_cross_section_record(row) for _, row in prepared_roads.iterrows()]
    return {
        "project": "cim_road_poc",
        "model": road_output_stem(profile),
        "generation_profile": {
            "name": profile.name,
            "mesh_granularity": profile.mesh_granularity,
            "semantic_level": profile.semantic_level,
            "generate_assets": profile.generate_assets,
            "generate_trees": profile.generate_trees,
            "generate_lane_markings": profile.generate_lane_markings,
            "generate_junction_markings": profile.generate_junction_markings,
            "generate_side_component_connectors": profile.generate_side_component_connectors,
        },
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


def write_city_road_semantic(
    prepared_roads: gpd.GeoDataFrame,
    origin: tuple[float, float],
    profile: RoadGenerationProfile = CIM4_PROFILE,
    path: Path | None = None,
) -> dict[str, Any]:
    profile = road_generation_profile(profile)
    semantic = build_city_road_semantic(prepared_roads, origin, profile)
    output_path = path or road_semantic_path_for_profile(profile)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(semantic, f, ensure_ascii=False, indent=2)
    return semantic


def build_city_road_source_attributes(
    prepared_roads: gpd.GeoDataFrame,
    source_attribute_columns: list[str],
    profile: RoadGenerationProfile = CIM4_PROFILE,
) -> dict[str, Any]:
    profile = road_generation_profile(profile)
    source_columns = [column for column in source_attribute_columns if column in prepared_roads.columns]
    records: list[dict[str, Any]] = []
    for _, row in prepared_roads.iterrows():
        source_attributes = {
            str(column): json_safe_value(row.get(column))
            for column in source_columns
        }
        name = road_gen.safe_str(source_attributes.get("name")) or road_gen.safe_str(row.get("road_name")) or ""
        records.append(
            {
                "name": name,
                "source_road_id": road_gen.safe_str(row.get("road_id")) or "",
                "road_name": road_gen.safe_str(row.get("road_name")) or "",
                "source_attributes": source_attributes,
            }
        )
    records_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        records_by_name[str(record.get("name") or "")].append(record)
    return {
        "project": "cim_road_poc",
        "model": f"{road_output_stem(profile)}_source_attributes",
        "generation_profile": {
            "name": profile.name,
            "mesh_granularity": profile.mesh_granularity,
            "semantic_level": profile.semantic_level,
        },
        "association_key": "name",
        "source_attribute_columns": source_columns,
        "object_count": len(records),
        "unique_name_count": len(records_by_name),
        "objects": records,
        "records_by_name": dict(sorted(records_by_name.items())),
    }


def write_city_road_source_attributes(
    prepared_roads: gpd.GeoDataFrame,
    source_attribute_columns: list[str],
    profile: RoadGenerationProfile = CIM4_PROFILE,
    path: Path | None = None,
) -> dict[str, Any]:
    profile = road_generation_profile(profile)
    attributes = build_city_road_source_attributes(prepared_roads, source_attribute_columns, profile)
    output_path = path or road_source_attributes_path_for_profile(profile)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(attributes, f, ensure_ascii=False, indent=2)
    return attributes


def road_model_classification_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    road_ids = sorted(str(record.get("source_road_id") or "") for record in records)
    return {
        "road_count": len(records),
        "source_road_ids": road_ids,
        "road_class_counts": dict(sorted(Counter(str(record.get("road_class") or "unclassified") for record in records).items())),
        "category_counts": dict(sorted(Counter(str(record.get("category") or "unknown") for record in records).items())),
        "source_section_counts": dict(
            sorted(Counter(str(record.get("source_section_code") or "unknown") for record in records).items())
        ),
        "modeled_section_counts": dict(
            sorted(Counter(str(record.get("modeled_section_code") or "unknown") for record in records).items())
        ),
        "modeled_widths_m": sorted({float(record.get("modeled_width_m", 0.0) or 0.0) for record in records}),
    }


def grouped_road_model_classifications(
    records: list[dict[str, Any]],
    key_fn,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[tuple(str(value or "unknown") for value in key_fn(record))].append(record)
    result = []
    for key, group_records in sorted(groups.items()):
        result.append(
            {
                "classification_key": ":".join(key),
                "classification_values": list(key),
                **road_model_classification_group(group_records),
            }
        )
    return result


def build_city_road_classification(road_semantic: dict[str, Any]) -> dict[str, Any]:
    records = list(road_semantic.get("objects", []))
    source_model = road_semantic.get("model", "cim4_city_roads")
    return {
        "project": road_semantic.get("project", "cim_road_poc"),
        "model": f"{source_model}_classification",
        "source_model": source_model,
        "generation_profile": road_semantic.get("generation_profile", {}),
        "classification_policy": {
            "primary_dimension": "category",
            "secondary_dimension": "road_name",
            "road_name_note": "The source SHP may use section codes or remarks when a formal road-name field is unavailable.",
        },
        "summary": {
            "road_count": len(records),
            "category_count": len({str(record.get("category") or "unknown") for record in records}),
            "road_name_count": len({str(record.get("road_name") or "unknown") for record in records}),
            "category_road_name_group_count": len(
                {
                    (
                        str(record.get("category") or "unknown"),
                        str(record.get("road_name") or "unknown"),
                    )
                    for record in records
                }
            ),
        },
        "by_road_type": grouped_road_model_classifications(
            records,
            lambda record: (record.get("category") or "unknown",),
        ),
        "by_road_name": grouped_road_model_classifications(
            records,
            lambda record: (record.get("road_name") or "unknown",),
        ),
        "by_road_type_and_name": grouped_road_model_classifications(
            records,
            lambda record: (
                record.get("category") or "unknown",
                record.get("road_name") or "unknown",
            ),
        ),
    }


def write_city_road_classification(
    road_semantic: dict[str, Any],
    profile: RoadGenerationProfile = CIM4_PROFILE,
    path: Path | None = None,
) -> dict[str, Any]:
    profile = road_generation_profile(profile)
    classification = build_city_road_classification(road_semantic)
    output_path = path or road_classification_path_for_profile(profile)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(classification, f, ensure_ascii=False, indent=2)
    return classification


ROAD_MESH_ATTRIBUTE_KEYS = [
    "cim_domain",
    "cim_entity_type",
    "cim_monomer",
    "cim_monomer_granularity",
    "layer_name",
    "component_type",
    "component_idx",
    "road_idx",
    "source_road_id",
    "road_name",
    "road_class",
    "road_category",
    "source_section_code",
    "modeled_section_code",
    "road_level_key",
    "road_priority",
    "spatial_layer",
    "elevation_bucket_m",
    "modeled_width_m",
    "lane_count",
    "lane_width_m",
    "length_m",
    "source_part_count",
    "part_count",
    "junction_marking_type",
    "junction_candidate_approach_count",
    "junction_crosswalk_stripe_count",
    "junction_stop_line_count",
]


def json_safe_attribute_value(value: Any) -> Any:
    value = json_safe_value(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return json.dumps([json_safe_attribute_value(item) for item in value], ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps({str(key): json_safe_attribute_value(item) for key, item in value.items()}, ensure_ascii=False)
    return str(value)


def road_mesh_attribute_record(object_name: str, mesh: trimesh.Trimesh) -> dict[str, Any]:
    metadata = dict(mesh.metadata or {})
    layer_name = str(metadata.get("layer_name") or road_output_layer_name(object_name))
    record: dict[str, Any] = {
        "object_name": str(object_name),
        "layer_name": layer_name,
        "mesh_vertex_count": int(len(mesh.vertices)),
        "mesh_face_count": int(len(mesh.faces)),
        "mesh_area_m2": round(float(mesh.area), 3),
    }
    for key in ROAD_MESH_ATTRIBUTE_KEYS:
        if key in metadata and metadata.get(key) is not None:
            record[key] = json_safe_attribute_value(metadata.get(key))
    if "cim_entity_type" not in record:
        record["cim_entity_type"] = "junction_surface" if layer_name == "Junction_Surface" else "road_component"
    if "cim_domain" not in record:
        record["cim_domain"] = "road"
    return record


def build_city_road_mesh_attributes(
    road_meshes: dict[str, trimesh.Trimesh],
    profile: RoadGenerationProfile = CIM4_PROFILE,
) -> dict[str, Any]:
    profile = road_generation_profile(profile)
    records = [
        road_mesh_attribute_record(name, mesh)
        for name, mesh in sorted(road_meshes.items())
        if mesh is not None and len(mesh.vertices) > 0
    ]
    monomer_records = [record for record in records if record.get("cim_monomer")]
    road_keys = {
        str(record.get("road_idx") or "")
        for record in records
        if str(record.get("road_idx") or "")
    }
    layer_counts = Counter(str(record.get("layer_name") or "unknown") for record in records)
    return {
        "project": "cim_road_poc",
        "model": f"{road_output_stem(profile)}_mesh_attributes",
        "generation_profile": {
            "name": profile.name,
            "mesh_granularity": profile.mesh_granularity,
            "semantic_level": profile.semantic_level,
        },
        "policy": "road geometry is exported as source-road monomers per material/component layer; attributes are reattached as Blender/FBX custom properties",
        "object_count": len(records),
        "road_monomer_object_count": len(monomer_records),
        "source_road_count": len(road_keys),
        "layer_object_counts": dict(sorted(layer_counts.items())),
        "objects": records,
        "objects_by_name": {str(record["object_name"]): record for record in records},
    }


def write_city_road_mesh_attributes(
    road_meshes: dict[str, trimesh.Trimesh],
    profile: RoadGenerationProfile = CIM4_PROFILE,
    path: Path | None = None,
) -> dict[str, Any]:
    profile = road_generation_profile(profile)
    attributes = build_city_road_mesh_attributes(road_meshes, profile)
    output_path = path or road_mesh_attributes_path_for_profile(profile)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(attributes, f, ensure_ascii=False, indent=2)
    return attributes


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
    base_type = junction_type_from_arm_geometry(arms)
    if base_type in {"UNKNOWN", "ROUNDABOUT_LIKE", "RAMP_MERGE", "TWO_ARM_CONNECTION"}:
        return base_type
    arm_count = len(arms)
    priorities = [int(arm["road_priority"]) for arm in arms]
    if arm_count == 3 and max(priorities, default=0) - min(priorities, default=0) >= 3:
        return "RAMP_MERGE"
    return base_type


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
        level_record = road_level_record_for_row(row, rule)
        level_key = road_level_key_for_record(level_record)
        category = str(level_record["category"])
        components = road_gen.cross_section_components_for_row(row)
        if not components:
            components = fallback_cross_section_components(rule)
        lane_count_estimate = max(1, int(round(drivable_width / max(float(rule.lane_width), 2.8))))
        crosswalk_offset, stop_line_offset = junction_approach_offsets_for_row(row, rule)
        min_margin = max(CROSSWALK_BAND_LENGTH_M * 0.5, STOP_LINE_WIDTH_M * 0.5, 0.4)
        line_length = float(line.length)
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
                    "road_idx": json_safe_value(road_idx),
                    "source_road_id": road_gen.safe_str(row.get("road_id")) or "",
                    "road_name": road_gen.safe_str(row.get("road_name")) or "",
                    "road_class": road_class,
                    "category": category,
                    "road_level": level_record,
                    "road_level_key": level_key,
                    "source_section_code": source_section,
                    "modeled_section_code": modeled_section,
                    "road_priority": int(level_record["priority"]),
                    "node_distance_m": round(float(node_distance), 3),
                    "line_direction_sign": round(float(sign), 3),
                    "line_length_m": round(float(line_length), 3),
                    "position_ratio": round(float(node_distance / line.length), 4) if line.length > 0 else 0.0,
                    "approach_side": side,
                    "direction_out": (round(float(direction[0]), 6), round(float(direction[1]), 6)),
                    "bearing_out_deg": round(vector_angle_deg(direction), 3),
                    "modeled_width_m": round(float(road_gen.component_total_width(components)), 3),
                    "component_signature_key": road_component_signature_key(components),
                    "drivable_width_m": round(float(drivable_width), 3),
                    "lane_count_estimate": lane_count_estimate,
                    "lane_width_m": round(float(rule.lane_width), 3),
                    "crosswalk_base_offset_m": round(float(crosswalk_offset), 3),
                    "stop_line_base_offset_m": round(float(stop_line_offset), 3),
                    "approach_extra_setback_m": 0.0,
                    "wide_through_required_crosswalk_offset_m": 0.0,
                    "marked_approach_fit": bool(marking_fit),
                    "crosswalk_distance_m": round(float(crosswalk_distance), 3) if marking_fit else None,
                    "stop_line_distance_m": round(float(stop_line_distance), 3) if marking_fit else None,
                }
            )
    arms.sort(key=lambda item: item["bearing_out_deg"])
    for arm in arms:
        try:
            extra_setback = junction_arm_extra_setback_m(arm, arms)
            required_offset = junction_wide_through_required_crosswalk_offset_m(arm, arms)
            crosswalk_distance = float(arm["node_distance_m"]) + float(arm["line_direction_sign"]) * (
                float(arm["crosswalk_base_offset_m"]) + extra_setback
            )
            stop_line_distance = float(arm["node_distance_m"]) + float(arm["line_direction_sign"]) * (
                float(arm["stop_line_base_offset_m"]) + extra_setback
            )
            line_length = float(arm.get("line_length_m", 0.0) or 0.0)
            marking_fit = (
                min_margin < crosswalk_distance < line_length - min_margin
                and min_margin < stop_line_distance < line_length - min_margin
            )
            arm["approach_extra_setback_m"] = round(float(extra_setback), 3)
            arm["wide_through_required_crosswalk_offset_m"] = round(float(required_offset), 3)
            arm["marked_approach_fit"] = bool(marking_fit)
            arm["crosswalk_distance_m"] = round(float(crosswalk_distance), 3) if marking_fit else None
            arm["stop_line_distance_m"] = round(float(stop_line_distance), 3) if marking_fit else None
        except Exception:
            continue
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


def build_junction_correspondence_records(arms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe how adjacent arms correspond across road levels.

    The geometry code uses spatial layer and elevation to decide when raised
    side components can connect. Exposing the pairwise result in semantics
    makes grade-separated junctions auditable instead of leaving the matching
    decision hidden inside mesh generation.
    """
    if len(arms) < 2:
        return []
    correspondences: list[dict[str, Any]] = []
    for idx, arm in enumerate(arms):
        next_arm = arms[(idx + 1) % len(arms)]
        gap = (float(next_arm["bearing_out_deg"]) - float(arm["bearing_out_deg"])) % 360.0
        same_road_level = arm.get("road_level_key") == next_arm.get("road_level_key")
        arm_level = arm.get("road_level", {})
        next_level = next_arm.get("road_level", {})
        same_spatial_level = (
            int(arm_level.get("spatial_layer", 0) or 0) == int(next_level.get("spatial_layer", 0) or 0)
            and abs(
                float(arm_level.get("elevation_bucket_m", 0.0) or 0.0)
                - float(next_level.get("elevation_bucket_m", 0.0) or 0.0)
            )
            <= JUNCTION_LEVEL_ELEVATION_BUCKET_M
        )
        correspondences.append(
            {
                "from_arm_id": arm["arm_id"],
                "to_arm_id": next_arm["arm_id"],
                "angular_gap_deg": round(float(gap), 3),
                "from_road_level_key": arm.get("road_level_key", "unknown"),
                "to_road_level_key": next_arm.get("road_level_key", "unknown"),
                "same_road_level": bool(same_road_level),
                "same_spatial_level": bool(same_spatial_level),
                "priority_delta": int(next_arm.get("road_priority", 0)) - int(arm.get("road_priority", 0)),
                "correspondence_type": "side_component_connectable" if same_spatial_level else "mixed_level_conflict_area",
            }
        )
    return correspondences


def build_city_junction_semantic_records(
    prepared_roads: gpd.GeoDataFrame,
    profile: RoadGenerationProfile = CIM4_PROFILE,
) -> list[dict[str, Any]]:
    """Build semantic junction records from the same buckets used for meshes.

    This mirrors the geometry pipeline: bucket -> arm records -> junction type
    and hierarchy -> design option -> movement records. Keeping semantics tied
    to the mesh buckets makes the QC report traceable back to visible junction
    patches.
    """
    if prepared_roads.empty or not (ENABLE_ROUNDED_JUNCTION_SURFACES or ENABLE_SIMPLE_ROUNDED_JUNCTIONS):
        return []
    profile = road_generation_profile(profile)
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
        correspondences = build_junction_correspondence_records(arms)
        allowed_movements = [movement for movement in movements if movement["allowed_by_default"]]
        type_counts = Counter(movement["movement_type"] for movement in movements)
        connected_road_ids = sorted({arm["source_road_id"] for arm in arms if arm["source_road_id"]})
        road_level_counts = Counter(arm.get("road_level_key", "unknown") for arm in arms)
        connection_level_counts = Counter(
            junction_connection_level_key_for_record(arm.get("road_level", {}))
            for arm in arms
        )
        record = {
            "junction_id": junction_id,
            "junction_type": junction_type,
            "junction_hierarchy": hierarchy,
            "surface_strategy": (
                "simple_rounded_asphalt_with_outer_sidewalk_curves"
                if ENABLE_SIMPLE_ROUNDED_JUNCTIONS
                else selected_design.get("option_id", "clustered_rounded_conflict_area_with_clipped_approaches")
            ),
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
            "road_level_counts": dict(road_level_counts),
            "junction_connection_level_counts": dict(connection_level_counts),
            "section_counts": dict(Counter(arm["modeled_section_code"] or "unknown" for arm in arms)),
            "angular_gaps_deg": angular_gaps_deg([arm["direction_out"] for arm in arms]),
            "max_drivable_width_m": round(max(float(arm["drivable_width_m"]) for arm in arms), 3),
            "max_lane_count_estimate": max(int(arm["lane_count_estimate"]) for arm in arms),
            "arms": arms,
        }
        record.update(
            {
                "source_node_summary": design_profile["source_node_summary"],
                "candidate_design_options": design_profile["candidate_design_options"],
                "selected_design_option": selected_design,
                "design_references": design_profile["design_references"],
                "side_component_connectable_road_levels": [
                    level_key for level_key, count in sorted(road_level_counts.items()) if count >= 2
                ],
                "side_component_connectable_connection_levels": [
                    level_key for level_key, count in sorted(connection_level_counts.items()) if count >= 2
                ],
                "movements": movements,
                "road_level_correspondence": correspondences,
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
        records.append(record)
    return records


def build_city_junction_semantic(
    prepared_roads: gpd.GeoDataFrame,
    origin: tuple[float, float],
    profile: RoadGenerationProfile = CIM4_PROFILE,
) -> dict[str, Any]:
    profile = road_generation_profile(profile)
    records = build_city_junction_semantic_records(prepared_roads, profile)
    for record in records:
        local = record["center_local"]
        record["center_global"] = {
            "x": round(float(local["x"] + origin[0]), 3),
            "y": round(float(local["y"] + origin[1]), 3),
            "z": local["z"],
        }
    return {
        "project": "cim_road_poc",
        "model": f"{profile.name}_city_junctions",
        "generation_profile": {
            "name": profile.name,
            "mesh_granularity": profile.mesh_granularity,
            "semantic_level": profile.semantic_level,
        },
        "generation_state": (
            "simple_rounded_junction_refactor"
            if ENABLE_SIMPLE_ROUNDED_JUNCTIONS
            else "legacy_junction_subsystem_detached_for_refactor"
        ),
        "unit": "meter",
        "coordinate": {
            "model_crs": TARGET_CRS,
            "local_origin": {"x": origin[0], "y": origin[1], "z": 0.0},
        },
        "semantic_level": profile.semantic_level,
        "objects": records,
        "summary": {
            "junction_count": len(records),
            "junction_type_counts": dict(Counter(record["junction_type"] for record in records)),
            "junction_hierarchy_counts": dict(Counter(record["junction_hierarchy"] for record in records)),
            "selected_design_option_counts": dict(
                Counter(record.get("selected_design_option", {}).get("option_id", "not_exported") for record in records)
            ),
            "design_review_required_count": sum(
                1 for record in records if record.get("selected_design_option", {}).get("review_required")
            ),
            "total_arm_count": sum(int(record["arm_count"]) for record in records),
            "total_allowed_movement_count": sum(
                int(record.get("movement_summary", {}).get("allowed_movement_count", 0)) for record in records
            ),
        },
    }


def write_city_junction_semantic(
    prepared_roads: gpd.GeoDataFrame,
    origin: tuple[float, float],
    profile: RoadGenerationProfile = CIM4_PROFILE,
    path: Path | None = None,
) -> dict[str, Any]:
    profile = road_generation_profile(profile)
    semantic = build_city_junction_semantic(prepared_roads, origin, profile)
    output_path = path or junction_semantic_path_for_profile(profile)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
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


def road_output_layer_name(mesh_name: str) -> str:
    name = str(mesh_name)
    if "_RoadType_" in name:
        return name.split("_RoadType_", 1)[0]
    if "_Road_" in name:
        return name.split("_Road_", 1)[0]
    if "_Shared_" in name:
        return name.split("_Shared_", 1)[0]
    if name.endswith("_Shared_All"):
        return name[: -len("_Shared_All")]
    return name[:-4] if name.endswith("_All") else name


def road_meshes_for_layer(
    road_meshes: dict[str, trimesh.Trimesh],
    layer_name: str,
) -> list[trimesh.Trimesh]:
    return [
        mesh
        for name, mesh in road_meshes.items()
        if road_output_layer_name(name) == layer_name
        and mesh is not None
        and len(mesh.vertices) > 0
    ]


def road_mesh_layer_metadata_max(
    meshes: list[trimesh.Trimesh],
    key: str,
    default_key: str | None = None,
) -> int:
    return max(
        [
            int((mesh.metadata or {}).get(key, (mesh.metadata or {}).get(default_key, 0) if default_key else 0) or 0)
            for mesh in meshes
        ]
        or [0]
    )


def road_mesh_layer_name(mesh_name: str, mesh: trimesh.Trimesh) -> str:
    metadata = mesh.metadata or {}
    layer_name = str(metadata.get("layer_name") or "").strip()
    return layer_name or road_output_layer_name(mesh_name)


def sidewalk_footprint_records(road_meshes: dict[str, trimesh.Trimesh]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for object_name, mesh in sorted(road_meshes.items()):
        if road_mesh_layer_name(object_name, mesh) != "Sidewalk":
            continue
        footprint = mesh_xy_footprint(mesh)
        if footprint is None or footprint.is_empty:
            continue
        for part_idx, polygon in enumerate(iter_polygons(footprint)):
            if polygon is None or polygon.is_empty or float(polygon.area) <= 1e-6:
                continue
            records.append(
                {
                    "object_name": str(object_name),
                    "part_index": int(part_idx),
                    "geometry": polygon,
                    "bounds": tuple(float(value) for value in polygon.bounds),
                    "area_m2": float(polygon.area),
                }
            )
    return records


def sidewalk_conflict_union_from_road_meshes(road_meshes: dict[str, trimesh.Trimesh]):
    conflict_meshes = [
        mesh
        for object_name, mesh in road_meshes.items()
        if road_mesh_layer_name(object_name, mesh) in SIDEWALK_CONFLICT_LAYERS
    ]
    return footprint_union_for_meshes(conflict_meshes)


def connected_component_ids_for_polygons(
    records: list[dict[str, Any]],
    tolerance_m: float,
) -> list[int]:
    count = len(records)
    parents = list(range(count))

    def find(idx: int) -> int:
        while parents[idx] != idx:
            parents[idx] = parents[parents[idx]]
            idx = parents[idx]
        return idx

    def union(a: int, b: int) -> None:
        root_a = find(a)
        root_b = find(b)
        if root_a != root_b:
            parents[root_b] = root_a

    tolerance = max(float(tolerance_m), 0.0)
    for idx_a in range(count):
        geom_a = records[idx_a]["geometry"]
        bounds_a = records[idx_a]["bounds"]
        for idx_b in range(idx_a + 1, count):
            if bounds_gap_distance_m(bounds_a, records[idx_b]["bounds"]) > tolerance:
                continue
            try:
                distance = float(geom_a.distance(records[idx_b]["geometry"]))
            except Exception:
                continue
            if distance <= tolerance:
                union(idx_a, idx_b)

    root_to_component: dict[int, int] = {}
    component_ids: list[int] = []
    for idx in range(count):
        root = find(idx)
        if root not in root_to_component:
            root_to_component[root] = len(root_to_component)
        component_ids.append(root_to_component[root])
    return component_ids


def summarize_sidewalk_connectivity(
    records: list[dict[str, Any]],
    *,
    tolerance_m: float = SIDEWALK_CONNECTIVITY_TOLERANCE_M,
    near_gap_max_m: float = SIDEWALK_NEAR_GAP_MAX_M,
) -> dict[str, Any]:
    component_ids = connected_component_ids_for_polygons(records, tolerance_m)
    component_areas: dict[int, float] = defaultdict(float)
    component_part_counts: dict[int, int] = defaultdict(int)
    for record, component_id in zip(records, component_ids):
        component_areas[int(component_id)] += float(record["area_m2"])
        component_part_counts[int(component_id)] += 1

    near_gap_count = 0
    max_near_gap_m = 0.0
    examples: list[dict[str, Any]] = []
    for idx_a in range(len(records)):
        geom_a = records[idx_a]["geometry"]
        bounds_a = records[idx_a]["bounds"]
        for idx_b in range(idx_a + 1, len(records)):
            if component_ids[idx_a] == component_ids[idx_b]:
                continue
            if bounds_gap_distance_m(bounds_a, records[idx_b]["bounds"]) > float(near_gap_max_m):
                continue
            try:
                distance = float(geom_a.distance(records[idx_b]["geometry"]))
            except Exception:
                continue
            if not (float(tolerance_m) < distance <= float(near_gap_max_m)):
                continue
            near_gap_count += 1
            max_near_gap_m = max(max_near_gap_m, distance)
            if len(examples) < 10:
                point_a, point_b = nearest_points(geom_a, records[idx_b]["geometry"])
                examples.append(
                    {
                        "from": records[idx_a]["object_name"],
                        "to": records[idx_b]["object_name"],
                        "gap_m": round(distance, 3),
                        "x": round(float((point_a.x + point_b.x) * 0.5), 3),
                        "y": round(float((point_a.y + point_b.y) * 0.5), 3),
                    }
                )

    component_summaries = [
        {
            "component_id": int(component_id),
            "part_count": int(component_part_counts[component_id]),
            "area_m2": round(float(area), 3),
        }
        for component_id, area in sorted(component_areas.items(), key=lambda item: item[1], reverse=True)
    ]
    return {
        "tolerance_m": round(float(tolerance_m), 3),
        "near_gap_max_m": round(float(near_gap_max_m), 3),
        "sidewalk_part_count": len(records),
        "component_count": len(component_areas),
        "largest_component_area_m2": round(max(component_areas.values() or [0.0]), 3),
        "near_gap_count": int(near_gap_count),
        "max_near_gap_m": round(float(max_near_gap_m), 3),
        "examples": examples,
        "largest_components": component_summaries[:10],
    }


def summarize_sidewalk_overlaps(
    records: list[dict[str, Any]],
    conflict_source,
    *,
    overlap_area_tolerance_m2: float = SIDEWALK_OVERLAP_AREA_TOLERANCE_M2,
) -> dict[str, Any]:
    sidewalk_pair_overlap_count = 0
    sidewalk_pair_overlap_area = 0.0
    sidewalk_examples: list[dict[str, Any]] = []
    tolerance = max(float(overlap_area_tolerance_m2), 0.0)
    sidewalk_geoms = [record["geometry"] for record in records]
    sidewalk_tree = STRtree(sidewalk_geoms) if sidewalk_geoms else None
    for idx_a, geom_a in enumerate(sidewalk_geoms):
        if sidewalk_tree is None:
            break
        try:
            hits = sidewalk_tree.query(geom_a.envelope)
        except Exception:
            hits = []
        for item in hits:
            try:
                idx_b = int(item) if isinstance(item, (int, np.integer)) else sidewalk_geoms.index(item)
            except Exception:
                continue
            if idx_b <= idx_a:
                continue
            try:
                overlap = geom_a.intersection(records[idx_b]["geometry"])
                overlap_area = float(overlap.area)
            except Exception:
                continue
            if overlap_area <= tolerance:
                continue
            sidewalk_pair_overlap_count += 1
            sidewalk_pair_overlap_area += overlap_area
            if len(sidewalk_examples) < 10:
                point = overlap.representative_point()
                sidewalk_examples.append(
                    {
                        "a": records[idx_a]["object_name"],
                        "b": records[idx_b]["object_name"],
                        "overlap_area_m2": round(overlap_area, 4),
                        "x": round(float(point.x), 3),
                        "y": round(float(point.y), 3),
                    }
                )

    road_or_junction_overlap_count = 0
    road_or_junction_overlap_area = 0.0
    conflict_examples: list[dict[str, Any]] = []
    conflict_is_index = isinstance(conflict_source, dict) and "parts" in conflict_source
    if conflict_is_index or (conflict_source is not None and not conflict_source.is_empty):
        for record in records:
            geom = record["geometry"]
            if conflict_is_index:
                local_conflict = indexed_local_conflict_geom(conflict_source, geom, clearance_m=0.0)
            else:
                local_conflict = conflict_source
            if local_conflict is None or local_conflict.is_empty or not local_conflict.intersects(geom):
                continue
            try:
                overlap = geom.intersection(local_conflict)
                overlap_area = float(overlap.area)
            except Exception:
                continue
            if overlap_area <= tolerance:
                continue
            road_or_junction_overlap_count += 1
            road_or_junction_overlap_area += overlap_area
            if len(conflict_examples) < 10:
                point = overlap.representative_point()
                conflict_examples.append(
                    {
                        "object_name": record["object_name"],
                        "overlap_area_m2": round(overlap_area, 4),
                        "x": round(float(point.x), 3),
                        "y": round(float(point.y), 3),
                    }
                )

    return {
        "overlap_area_tolerance_m2": round(float(tolerance), 4),
        "sidewalk_pair_overlap_count": int(sidewalk_pair_overlap_count),
        "sidewalk_pair_overlap_area_m2": round(float(sidewalk_pair_overlap_area), 6),
        "road_or_junction_overlap_count": int(road_or_junction_overlap_count),
        "road_or_junction_overlap_area_m2": round(float(road_or_junction_overlap_area), 6),
        "sidewalk_pair_examples": sidewalk_examples,
        "road_or_junction_examples": conflict_examples,
    }


def build_city_sidewalk_topology_qc(
    road_meshes: dict[str, trimesh.Trimesh],
    profile: RoadGenerationProfile = CIM4_PROFILE,
    *,
    connectivity_tolerance_m: float = SIDEWALK_CONNECTIVITY_TOLERANCE_M,
    near_gap_max_m: float = SIDEWALK_NEAR_GAP_MAX_M,
    overlap_area_tolerance_m2: float = SIDEWALK_OVERLAP_AREA_TOLERANCE_M2,
) -> dict[str, Any]:
    profile = road_generation_profile(profile)
    records = sidewalk_footprint_records(road_meshes)
    conflict_index = sidewalk_conflict_index_from_road_meshes(road_meshes)
    total_area = sum(float(record["area_m2"]) for record in records)
    connectivity = summarize_sidewalk_connectivity(
        records,
        tolerance_m=connectivity_tolerance_m,
        near_gap_max_m=near_gap_max_m,
    )
    overlap = summarize_sidewalk_overlaps(
        records,
        conflict_index,
        overlap_area_tolerance_m2=overlap_area_tolerance_m2,
    )
    issue_count = (
        int(connectivity["near_gap_count"])
        + int(overlap["sidewalk_pair_overlap_count"])
        + int(overlap["road_or_junction_overlap_count"])
    )
    return {
        "project": "cim_road_poc",
        "model": f"{road_output_stem(profile)}_sidewalk_qc",
        "generation_profile": {
            "name": profile.name,
            "mesh_granularity": profile.mesh_granularity,
            "semantic_level": profile.semantic_level,
        },
        "summary": {
            "sidewalk_object_count": sum(
                1 for name, mesh in road_meshes.items() if road_mesh_layer_name(name, mesh) == "Sidewalk"
            ),
            "sidewalk_part_count": len(records),
            "sidewalk_area_m2": round(float(total_area), 3),
            "issue_count": int(issue_count),
            "has_issues": bool(issue_count),
        },
        "connectivity": connectivity,
        "overlap": overlap,
    }


def write_city_sidewalk_topology_qc(
    road_meshes: dict[str, trimesh.Trimesh],
    profile: RoadGenerationProfile = CIM4_PROFILE,
    path: Path | None = None,
) -> dict[str, Any]:
    profile = road_generation_profile(profile)
    report = build_city_sidewalk_topology_qc(road_meshes, profile)
    output_path = path or road_sidewalk_qc_path_for_profile(profile)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def build_city_road_model_score(prepared_roads: gpd.GeoDataFrame, road_meshes: dict[str, trimesh.Trimesh]) -> dict[str, Any]:
    """Score whether the generated road model matches section rules and layers.

    This report checks geometry width, symmetry fallback behavior, required
    cross-section components, semantic completeness, material/layer separation,
    and whether roads with junctions received transition/clearance treatment.
    """
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
    if (ENABLE_ROUNDED_JUNCTION_SURFACES or ENABLE_SIMPLE_ROUNDED_JUNCTIONS) and roads_with_junction_clearance:
        expected_layers.add("Junction_Surface")
    if roads_with_junction_clearance:
        if GENERATE_JUNCTION_CROSSWALKS or ENABLE_SIMPLE_ROUNDED_JUNCTIONS:
            expected_layers.add("Crosswalk")
        if GENERATE_JUNCTION_STOP_LINES or ENABLE_SIMPLE_ROUNDED_JUNCTIONS:
            expected_layers.add("Stop_Line")
    present_layers = {
        road_output_layer_name(name)
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


def count_valid_junction_approaches(
    prepared_roads: gpd.GeoDataFrame,
    approach_extra_setbacks_by_road: dict[Any, list[tuple[float, float, float]]] | None = None,
) -> int:
    rules = road_gen.load_rules()
    count = 0
    for road_idx, row in prepared_roads.iterrows():
        row = row.copy()
        row.name = road_idx
        line = row.geometry
        if line is None or line.is_empty or not isinstance(line, LineString):
            continue
        rule = road_gen.get_road_rule(row, rules)
        crosswalk_offset, stop_line_offset = junction_approach_offsets_for_row(row, rule)
        min_margin = max(CROSSWALK_BAND_LENGTH_M * 0.5, STOP_LINE_WIDTH_M * 0.5, 0.4)
        for junction_distance in road_gen.row_junction_distances(row):
            clamped_distance = clamp_junction_chainage(line, junction_distance)
            if clamped_distance is None:
                continue
            for side_sign in (-1.0, 1.0):
                extra_setback = lookup_junction_approach_extra_setback(
                    approach_extra_setbacks_by_road,
                    road_idx,
                    clamped_distance,
                    side_sign,
                )
                crosswalk_distance = float(clamped_distance) + side_sign * (crosswalk_offset + extra_setback)
                stop_line_distance = float(clamped_distance) + side_sign * (stop_line_offset + extra_setback)
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


def mesh_list_area_m2(meshes: list[trimesh.Trimesh]) -> float:
    return sum(mesh_area_m2(mesh) for mesh in meshes)


def build_city_junction_score(
    prepared_roads: gpd.GeoDataFrame,
    road_meshes: dict[str, trimesh.Trimesh],
    junction_semantic: dict[str, Any] | None = None,
    road_profile: RoadGenerationProfile | None = None,
) -> dict[str, Any]:
    """Score visible junction geometry, markings, assets, and semantics."""
    records = [road_cross_section_record(row) for _, row in prepared_roads.iterrows()]
    junction_buckets = junction_point_buckets(prepared_roads)
    junction_count = len(junction_buckets)
    rules = road_gen.load_rules()
    junction_surface_geometries = (
        simple_junction_surface_geometries(prepared_roads, rules, junction_buckets)
        if ENABLE_SIMPLE_ROUNDED_JUNCTIONS
        else build_rounded_junction_surface_geometries(prepared_roads, rules, junction_buckets)
    )
    approach_extra_setbacks_by_road = junction_approach_extra_setbacks_by_road(
        prepared_roads,
        rules,
        junction_surface_geometries,
    )
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
    source_expected_approaches = count_valid_junction_approaches(prepared_roads, approach_extra_setbacks_by_road)

    present_layers = {
        road_output_layer_name(name)
        for name, mesh in road_meshes.items()
        if mesh is not None and len(mesh.vertices) > 0
    }
    junction_meshes = road_meshes_for_layer(road_meshes, "Junction_Surface")
    crosswalk_meshes = road_meshes_for_layer(road_meshes, "Crosswalk")
    stop_line_meshes = road_meshes_for_layer(road_meshes, "Stop_Line")
    junction_surface_count = sum(int((mesh.metadata or {}).get("part_count", 0) or 0) for mesh in junction_meshes)
    crosswalk_stripe_count = road_mesh_layer_metadata_max(
        crosswalk_meshes,
        "junction_crosswalk_stripe_count",
        "part_count",
    )
    stop_line_count = road_mesh_layer_metadata_max(
        stop_line_meshes,
        "junction_stop_line_count",
        "part_count",
    )
    mesh_expected_approaches = max(
        road_mesh_layer_metadata_max(crosswalk_meshes, "junction_candidate_approach_count"),
        road_mesh_layer_metadata_max(stop_line_meshes, "junction_candidate_approach_count"),
    )
    expected_approaches = mesh_expected_approaches or source_expected_approaches

    junction_surface_enabled = ENABLE_ROUNDED_JUNCTION_SURFACES or ENABLE_SIMPLE_ROUNDED_JUNCTIONS
    surface_ratio = score_ratio(junction_surface_count, junction_count) if junction_surface_enabled else 1.0
    clearance_ratio = score_ratio(roads_with_clearance, roads_with_junctions)
    crossing_ratio = (
        1.0
        if not (GENERATE_JUNCTION_CROSSWALKS or ENABLE_SIMPLE_ROUNDED_JUNCTIONS) or expected_approaches == 0
        else min(1.0, crosswalk_stripe_count / max(1, expected_approaches * 3))
    )
    stop_ratio = 1.0 if not (GENERATE_JUNCTION_STOP_LINES or ENABLE_SIMPLE_ROUNDED_JUNCTIONS) else score_ratio(stop_line_count, expected_approaches)
    expected_layers = {"Lane_Marking_White", "Lane_Marking_Yellow"}
    if junction_surface_enabled:
        expected_layers.add("Junction_Surface")
    if expected_approaches and (GENERATE_JUNCTION_CROSSWALKS or ENABLE_SIMPLE_ROUNDED_JUNCTIONS):
        expected_layers.add("Crosswalk")
    if expected_approaches and (GENERATE_JUNCTION_STOP_LINES or ENABLE_SIMPLE_ROUNDED_JUNCTIONS):
        expected_layers.add("Stop_Line")
    layer_ratio = score_ratio(sum(1 for layer in expected_layers if layer in present_layers), len(expected_layers))
    light_present = any(layer.startswith("Street_Light") for layer in present_layers)
    tree_present = any(layer.startswith("Tree") for layer in present_layers)
    tree_assets_enabled = bool(road_profile.generate_trees) if road_profile is not None else True
    asset_checks: list[bool] = []
    if GENERATE_ROAD_ASSETS:
        asset_checks.append(light_present)
        if tree_assets_enabled:
            asset_checks.append(tree_present)
    asset_ratio = 1.0 if not asset_checks else sum(1.0 for present in asset_checks if present) / len(asset_checks)
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
            "junction_surface_area_m2": round(mesh_list_area_m2(junction_meshes), 3),
            "crosswalk_stripe_count": crosswalk_stripe_count,
            "crosswalk_area_m2": round(mesh_list_area_m2(crosswalk_meshes), 3),
            "stop_line_count": stop_line_count,
            "stop_line_area_m2": round(mesh_list_area_m2(stop_line_meshes), 3),
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
    road_profile: RoadGenerationProfile | None = None,
) -> dict[str, Any]:
    report = build_city_junction_score(prepared_roads, road_meshes, junction_semantic, road_profile)
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
    """Check that lane markings, crosswalks, and stop lines stay in bounds.

    The mesh generator already clips markings, but this independent pass
    recomputes lateral envelopes from the same cross-section spans. It catches
    cases where a white/yellow lane mark or a transverse junction mark would
    extend beyond its intended drivable component.
    """
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
            junction_clearance = longitudinal_marking_stop_clearance_for_row(row, rule)
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
            junction_clearance = longitudinal_marking_stop_clearance_for_row(row, rule)
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
        crosswalk_left, crosswalk_right = junction_crosswalk_lateral_range_for_row(row, rule)
        crosswalk_lateral_offsets = crosswalk_stripe_layout_for_range(crosswalk_left, crosswalk_right)
        if crosswalk_lateral_offsets:
            crosswalk_extent_left = min(offset - CROSSWALK_STRIPE_WIDTH_M / 2.0 for offset in crosswalk_lateral_offsets)
            crosswalk_extent_right = max(offset + CROSSWALK_STRIPE_WIDTH_M / 2.0 for offset in crosswalk_lateral_offsets)
        else:
            crosswalk_extent_left = crosswalk_extent_right = 0.0
        stop_line_lateral_pieces = junction_stop_line_lateral_pieces_for_row(row, rule)
        stop_extent = max(
            [abs(float(offset)) + float(width) / 2.0 for offset, width in stop_line_lateral_pieces],
            default=0.0,
        )
        for junction_idx, _ in enumerate(road_gen.row_junction_distances(row)):
            for side_name in ("before", "after"):
                checks["crosswalk_approach"] += 1
                crosswalk_overhang = max(
                    0.0,
                    float(crosswalk_left) - crosswalk_extent_left,
                    crosswalk_extent_right - float(crosswalk_right),
                )
                max_overhang = max(max_overhang, crosswalk_overhang)
                record_marking_issue(
                    issues,
                    "crosswalk_outside_sidewalk_connection_width",
                    road_idx,
                    crosswalk_overhang,
                    {
                        "junction_index": junction_idx,
                        "side": side_name,
                        "road_width_m": round(float(road_width), 3),
                        "crosswalk_left_m": round(float(crosswalk_left), 3),
                        "crosswalk_right_m": round(float(crosswalk_right), 3),
                        "stripe_count": len(crosswalk_lateral_offsets),
                    },
                )

                checks["stop_line_approach"] += 1
                road_half_width = road_width / 2.0
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


# ---------------------------------------------------------------------------
# Other city modules
# ---------------------------------------------------------------------------


def build_building_meshes(buildings: gpd.GeoDataFrame) -> dict[str, trimesh.Trimesh]:
    """Build non-road city building meshes."""
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
    values = " ".join(str(row.get(column, "")) for column in ("railway", "type", "name", "line_name", "raw_name")).lower()
    if any(token in values for token in ("高铁", "高速铁路", "high_speed", "high-speed", "highspeed")):
        return False
    if railway == "subway" or tunnel in {"yes", "true"} or layer < 0:
        return True
    if line_type in {"subway", "light_rail"} or any(token in line_type for token in ["地铁", "轨道", "轻轨"]):
        return True
    return any(token in name for token in ["地铁", "轨道"])


def railway_tunnel_category(row) -> str:
    values = " ".join(
        str(row.get(column, ""))
        for column in ("railway", "tunnel", "layer", "type", "name", "line_name", "raw_name")
    ).lower()
    if any(token in values for token in ("高铁", "高速铁路", "high_speed", "high-speed", "highspeed")):
        return "high_speed_rail"
    if any(token in values for token in ("地铁", "subway", "u-bahn", "metro")):
        return "subway"
    if any(token in values for token in ("轻轨", "轨道", "light_rail")):
        return "urban_rail"
    if any(token in values for token in ("铁路", "rail")):
        return "railway"
    return "subway"


def railway_tunnel_base_depth_m(row) -> float:
    category = railway_tunnel_category(row)
    if category == "high_speed_rail":
        return HIGH_SPEED_RAIL_TUNNEL_DEPTH_M
    if category == "urban_rail":
        return URBAN_RAIL_TUNNEL_DEPTH_M
    if category == "railway":
        return RAILWAY_TUNNEL_DEPTH_M
    return SUBWAY_TUNNEL_DEPTH_M


def railway_geometries_intersect(a, b) -> bool:
    if a is None or b is None or a.is_empty or b.is_empty:
        return False
    try:
        if a.intersects(b):
            return True
        return float(a.distance(b)) <= SUBWAY_TUNNEL_INTERSECTION_TOLERANCE_M
    except (TypeError, ValueError):
        return False


def railway_tunnels_need_vertical_separation(a, b) -> bool:
    if a is None or b is None or a.is_empty or b.is_empty:
        return False
    try:
        if a.touches(b):
            intersection = a.intersection(b)
            if not intersection.is_empty and float(intersection.length) <= 0.05:
                return False
        if a.intersects(b):
            return True
        min_centerline_distance = 2.0 * SUBWAY_TUNNEL_OUTER_RADIUS_M + SUBWAY_TUNNEL_OVERLAP_CLEARANCE_M
        return float(a.distance(b)) < min_centerline_distance
    except (TypeError, ValueError):
        return False


def railway_lines_are_same_corridor(a, b) -> bool:
    if a is None or b is None or a.is_empty or b.is_empty:
        return False
    try:
        length_a = max(float(a.length), 0.001)
        length_b = max(float(b.length), 0.001)
        near_length_a = float(a.intersection(b.buffer(SUBWAY_CORRIDOR_MERGE_DISTANCE_M)).length)
        near_length_b = float(b.intersection(a.buffer(SUBWAY_CORRIDOR_MERGE_DISTANCE_M)).length)
        near_ratio = min(near_length_a / length_a, near_length_b / length_b)
        if near_ratio < SUBWAY_CORRIDOR_NEAR_OVERLAP_RATIO:
            return False
        if a.intersects(b):
            intersection = a.intersection(b)
            if not intersection.is_empty and float(intersection.length) <= 0.05:
                return False
        return float(a.distance(b)) < SUBWAY_CORRIDOR_MERGE_DISTANCE_M
    except (TypeError, ValueError):
        return False


def railway_line_direction(geom) -> tuple[float, float]:
    longest_line = None
    for line in iter_lines(geom):
        if longest_line is None or line.length > longest_line.length:
            longest_line = line
    if longest_line is None:
        return (1.0, 0.0)
    coords = list(longest_line.coords)
    if len(coords) < 2:
        return (1.0, 0.0)
    ax, ay = coords[0][0], coords[0][1]
    bx, by = coords[-1][0], coords[-1][1]
    dx = float(bx - ax)
    dy = float(by - ay)
    length = math.hypot(dx, dy)
    if length <= 0.001:
        return (1.0, 0.0)
    return (dx / length, dy / length)


def railway_lines_are_parallel(a, b) -> bool:
    adx, ady = railway_line_direction(a)
    bdx, bdy = railway_line_direction(b)
    return abs(adx * bdx + ady * bdy) >= 0.85


def subway_parallel_shift_records(railways: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    records = []
    for idx, row in railways.iterrows():
        if not subway_like(row):
            continue
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        direction = railway_line_direction(geom)
        normal = (-direction[1], direction[0])
        centroid = geom.centroid
        records.append(
            {
                "idx": idx,
                "geometry": geom,
                "line_name": subway_line_name(row, subway_source_id(row, idx)),
                "direction": direction,
                "normal": normal,
                "centroid": (float(centroid.x), float(centroid.y)),
            }
        )
    return records


def subway_parallel_shift_components(records: list[dict[str, Any]]) -> list[list[int]]:
    adjacency: dict[int, set[int]] = {i: set() for i in range(len(records))}
    for left_idx, left in enumerate(records):
        for right_idx in range(left_idx + 1, len(records)):
            right = records[right_idx]
            if not railway_lines_are_parallel(left["geometry"], right["geometry"]):
                continue
            distance = float(left["geometry"].distance(right["geometry"]))
            if distance >= SUBWAY_PARALLEL_SHIFT_TRIGGER_M and not left["geometry"].intersects(right["geometry"]):
                continue
            if not railway_lines_are_same_corridor(left["geometry"], right["geometry"]) and distance >= SUBWAY_PARALLEL_SHIFT_TRIGGER_M:
                continue
            adjacency[left_idx].add(right_idx)
            adjacency[right_idx].add(left_idx)

    components: list[list[int]] = []
    seen: set[int] = set()
    for start_idx in range(len(records)):
        if start_idx in seen:
            continue
        stack = [start_idx]
        component: list[int] = []
        seen.add(start_idx)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in adjacency[current]:
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                stack.append(neighbor)
        components.append(component)
    return components


def assign_subway_lateral_translations(
    railways: gpd.GeoDataFrame,
    spacing_m: float = SUBWAY_PARALLEL_LATERAL_SPACING_M,
) -> dict[Any, tuple[float, float]]:
    records = subway_parallel_shift_records(railways)
    translations: dict[Any, tuple[float, float]] = {record["idx"]: (0.0, 0.0) for record in records}
    for component in subway_parallel_shift_components(records):
        if len(component) <= 1:
            continue
        base_normal = records[component[0]]["normal"]
        if len(component) == 2:
            first = records[component[0]]
            second = records[component[1]]
            try:
                first_point, second_point = nearest_points(first["geometry"], second["geometry"])
                dx = float(second_point.x - first_point.x)
                dy = float(second_point.y - first_point.y)
                if math.hypot(dx, dy) <= 0.001:
                    dx = float(second["centroid"][0] - first["centroid"][0])
                    dy = float(second["centroid"][1] - first["centroid"][1])
                if math.hypot(dx, dy) > 0.001:
                    base_normal = (dx, dy)
            except (TypeError, ValueError):
                pass
        normal_length = math.hypot(base_normal[0], base_normal[1])
        if normal_length <= 0.001:
            continue
        nx, ny = base_normal[0] / normal_length, base_normal[1] / normal_length
        sorted_component = sorted(
            component,
            key=lambda item_idx: records[item_idx]["centroid"][0] * nx + records[item_idx]["centroid"][1] * ny,
        )
        count = len(sorted_component)
        for rank, item_idx in enumerate(sorted_component):
            delta = (rank - (count - 1) * 0.5) * spacing_m
            translations[records[item_idx]["idx"]] = (nx * delta, ny * delta)
    return translations


def apply_subway_lateral_translations(
    railways: gpd.GeoDataFrame,
    translations: dict[Any, tuple[float, float]],
) -> gpd.GeoDataFrame:
    shifted = railways.copy()
    shifted["_subway_lateral_translation_x_m"] = 0.0
    shifted["_subway_lateral_translation_y_m"] = 0.0
    for idx, (dx, dy) in translations.items():
        if idx not in shifted.index:
            continue
        shifted.at[idx, shifted.geometry.name] = shapely_translate(shifted.at[idx, shifted.geometry.name], xoff=dx, yoff=dy)
        shifted.at[idx, "_subway_lateral_translation_x_m"] = round(float(dx), 3)
        shifted.at[idx, "_subway_lateral_translation_y_m"] = round(float(dy), 3)
    return shifted


def apply_subway_pair_axis_lateral_translation(
    railways: gpd.GeoDataFrame,
    spacing_m: float = SUBWAY_PAIR_AXIS_LATERAL_SPACING_M,
) -> gpd.GeoDataFrame | None:
    """Create two separated axes using curve-following offsets from each source track."""
    if len(railways) != 2:
        return None
    indices = list(railways.index)
    left_geom = railways.loc[indices[0]].geometry
    right_geom = railways.loc[indices[1]].geometry
    if left_geom is None or right_geom is None or left_geom.is_empty or right_geom.is_empty:
        return None
    if not railway_lines_are_parallel(left_geom, right_geom):
        return None
    if float(left_geom.distance(right_geom)) >= SUBWAY_PARALLEL_SHIFT_TRIGGER_M:
        return None

    left_line = longest_subway_line(left_geom)
    right_line = longest_subway_line(right_geom)
    if left_line is None or right_line is None:
        return None
    if railway_line_direction(left_line)[0] * railway_line_direction(right_line)[0] + railway_line_direction(
        left_line
    )[1] * railway_line_direction(right_line)[1] < 0.0:
        right_line = LineString(list(right_line.coords)[::-1])

    left_nearest, right_nearest = nearest_points(left_line, right_line)
    left_chainage = float(left_line.project(left_nearest))
    sample_delta = min(5.0, max(float(left_line.length) * 0.001, 0.25))
    before = left_line.interpolate(max(0.0, left_chainage - sample_delta))
    after = left_line.interpolate(min(float(left_line.length), left_chainage + sample_delta))
    tangent_x = float(after.x - before.x)
    tangent_y = float(after.y - before.y)
    toward_right_x = float(right_nearest.x - left_nearest.x)
    toward_right_y = float(right_nearest.y - left_nearest.y)
    cross = tangent_x * toward_right_y - tangent_y * toward_right_x
    if abs(cross) <= 1e-9:
        return None

    half_spacing = float(spacing_m) * 0.5
    left_offset = -half_spacing if cross > 0.0 else half_spacing
    right_offset = half_spacing if cross > 0.0 else -half_spacing
    left_shifted = longest_subway_line(shapely.offset_curve(left_line, left_offset, join_style="round"))
    right_shifted = longest_subway_line(shapely.offset_curve(right_line, right_offset, join_style="round"))
    if left_shifted is None or right_shifted is None:
        return None

    shifted = railways.copy()
    shifted["_subway_lateral_translation_x_m"] = shifted.get("_subway_lateral_translation_x_m", 0.0).astype(float)
    shifted["_subway_lateral_translation_y_m"] = shifted.get("_subway_lateral_translation_y_m", 0.0).astype(float)
    if "_subway_same_line_track_spacing_m" in shifted.columns:
        shifted["_subway_same_line_track_spacing_m"] = shifted["_subway_same_line_track_spacing_m"].astype(float)
    else:
        shifted["_subway_same_line_track_spacing_m"] = float(SUBWAY_SAME_LINE_TRACK_SPACING_M)
    for idx, source_line, shifted_line in (
        (indices[0], left_line, left_shifted),
        (indices[1], right_line, right_shifted),
    ):
        shifted.at[idx, shifted.geometry.name] = shifted_line
        shifted.at[idx, "_subway_lateral_translation_x_m"] = round(
            float(shifted_line.centroid.x - source_line.centroid.x),
            3,
        )
        shifted.at[idx, "_subway_lateral_translation_y_m"] = round(
            float(shifted_line.centroid.y - source_line.centroid.y),
            3,
        )
        shifted.at[idx, "_subway_same_line_track_spacing_m"] = round(float(spacing_m), 3)
    return shifted


def longest_subway_line(geometry) -> LineString | None:
    lines = [line for line in iter_lines(geometry) if line is not None and not line.is_empty]
    if not lines:
        return None
    return max(lines, key=lambda line: float(line.length))


def apply_subway_same_line_overlap_lateral_translations(
    corridors: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Translate only same-line corridor groups whose generated tunnel axes are too close."""
    if corridors.empty:
        return corridors
    shifted_corridors = corridors.copy()
    shifted_corridors["_subway_lateral_translation_x_m"] = shifted_corridors.get(
        "_subway_lateral_translation_x_m",
        0.0,
    ).astype(float)
    shifted_corridors["_subway_lateral_translation_y_m"] = shifted_corridors.get(
        "_subway_lateral_translation_y_m",
        0.0,
    ).astype(float)
    if "_subway_same_line_track_spacing_m" in shifted_corridors.columns:
        shifted_corridors["_subway_same_line_track_spacing_m"] = shifted_corridors[
            "_subway_same_line_track_spacing_m"
        ].astype(float)
    else:
        shifted_corridors["_subway_same_line_track_spacing_m"] = float(SUBWAY_SAME_LINE_TRACK_SPACING_M)
    for _line_name, line_corridors in shifted_corridors.groupby("_subway_line_name", sort=False):
        if len(line_corridors) <= 1:
            continue
        spacing_m = subway_pair_axis_lateral_spacing_for_line(_line_name)
        shifted = apply_subway_pair_axis_lateral_translation(line_corridors, spacing_m=spacing_m)
        if shifted is None:
            translations = assign_subway_lateral_translations(line_corridors, spacing_m=spacing_m)
            shifted = apply_subway_lateral_translations(line_corridors, translations)
            shifted["_subway_same_line_track_spacing_m"] = round(float(spacing_m), 3)
        for idx in shifted.index:
            shifted_corridors.at[idx, shifted_corridors.geometry.name] = shifted.at[idx, shifted.geometry.name]
            shifted_corridors.at[idx, "_subway_lateral_translation_x_m"] = shifted.at[
                idx,
                "_subway_lateral_translation_x_m",
            ]
            shifted_corridors.at[idx, "_subway_lateral_translation_y_m"] = shifted.at[
                idx,
                "_subway_lateral_translation_y_m",
            ]
            shifted_corridors.at[idx, "_subway_same_line_track_spacing_m"] = shifted.at[
                idx,
                "_subway_same_line_track_spacing_m",
            ]
    return shifted_corridors


def subway_line_allows_lateral_translation(line_name: str) -> bool:
    text = str(line_name or "").strip()
    return any(token in text for token in SUBWAY_LATERAL_TRANSLATION_LINE_TOKENS)


def subway_pair_axis_lateral_spacing_for_line(line_name: str) -> float:
    text = str(line_name or "").strip()
    if "11号线" in text or "十一号线" in text:
        return SUBWAY_LINE_11_PAIR_AXIS_LATERAL_SPACING_M
    return SUBWAY_PAIR_AXIS_LATERAL_SPACING_M


def subway_tunnel_side_for_translation(row) -> str:
    dx = safe_float(row.get("_subway_lateral_translation_x_m"), 0.0)
    dy = safe_float(row.get("_subway_lateral_translation_y_m"), 0.0)
    if math.hypot(dx, dy) <= 1e-6:
        return ""
    direction = railway_line_direction(row.geometry)
    left_normal = (-direction[1], direction[0])
    lateral = dx * left_normal[0] + dy * left_normal[1]
    if lateral > 1e-6:
        return "left"
    if lateral < -1e-6:
        return "right"
    return ""


def line_midpoint_axis_between_pair(a, b) -> LineString | None:
    return line_average_axis([a, b])


def line_average_axis(lines: list[LineString]) -> LineString | None:
    valid_lines = [line for line in lines if line is not None and not line.is_empty and float(line.length) > 1.0]
    if not valid_lines:
        return None
    try:
        reference = max(valid_lines, key=lambda line: float(line.length))
        reference_direction = railway_line_direction(reference)
        length = float(reference.length)
        if length <= 1.0:
            return None
        sample_count = max(8, min(240, int(length / 25.0)))
        coords = []
        for sample_idx in range(sample_count + 1):
            ratio = sample_idx / sample_count
            samples = []
            for line in valid_lines:
                line_direction = railway_line_direction(line)
                line_ratio = ratio
                if reference_direction[0] * line_direction[0] + reference_direction[1] * line_direction[1] < 0.0:
                    line_ratio = 1.0 - ratio
                point = line.interpolate(float(line.length) * line_ratio)
                samples.append((float(point.x), float(point.y)))
            coords.append(
                (
                    sum(point[0] for point in samples) / len(samples),
                    sum(point[1] for point in samples) / len(samples),
                )
            )
        return LineString(coords)
    except (TypeError, ValueError):
        return None


def same_line_axis_components(lines: list[LineString]) -> list[list[int]]:
    adjacency: dict[int, set[int]] = {idx: set() for idx in range(len(lines))}
    for left_idx, left in enumerate(lines):
        for right_idx in range(left_idx + 1, len(lines)):
            right = lines[right_idx]
            if not railway_lines_are_parallel(left, right):
                continue
            if float(left.distance(right)) >= SUBWAY_PARALLEL_SHIFT_TRIGGER_M:
                continue
            try:
                left_near_length = float(left.intersection(right.buffer(SUBWAY_PARALLEL_SHIFT_TRIGGER_M)).length)
                right_near_length = float(right.intersection(left.buffer(SUBWAY_PARALLEL_SHIFT_TRIGGER_M)).length)
                overlap_ratio = min(left_near_length / max(float(left.length), 0.001), right_near_length / max(float(right.length), 0.001))
            except (TypeError, ValueError):
                overlap_ratio = 0.0
            if overlap_ratio < 0.45:
                continue
            adjacency[left_idx].add(right_idx)
            adjacency[right_idx].add(left_idx)

    components: list[list[int]] = []
    seen: set[int] = set()
    for start_idx in range(len(lines)):
        if start_idx in seen:
            continue
        stack = [start_idx]
        component: list[int] = []
        seen.add(start_idx)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in adjacency[current]:
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                stack.append(neighbor)
        components.append(component)
    return components


def merge_same_line_axis(group: gpd.GeoDataFrame):
    lines = []
    for geom in group.geometry:
        lines.extend(list(iter_lines(geom)))
    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]

    merged_axes = []
    for component in same_line_axis_components(lines):
        component_lines = [lines[idx] for idx in component]
        if len(component_lines) == 1:
            merged_axes.append(component_lines[0])
            continue
        average_axis = line_average_axis(component_lines)
        merged_axes.append(average_axis if average_axis is not None else component_lines[0])

    merged = unary_union([line for line in merged_axes if line is not None and not line.is_empty])
    try:
        return linemerge(merged)
    except ValueError:
        return merged


def same_line_interval_axis_rows(line_name: str, group: gpd.GeoDataFrame, geometry_name: str) -> list[pd.Series]:
    line_records = []
    for idx, row in group.iterrows():
        for line in iter_lines(row.geometry):
            if line is None or line.is_empty:
                continue
            line_records.append(
                {
                    "source_index": idx,
                    "source_id": subway_source_id(row, idx),
                    "row": row,
                    "line": line,
                    "length": float(line.length),
                }
            )
    if not line_records:
        return []

    lines = [record["line"] for record in line_records]
    raw_intervals = []
    for component in same_line_axis_components(lines):
        component_records = [line_records[idx] for idx in component]
        representative_record = max(component_records, key=lambda record: record["length"])
        component_lines = [record["line"] for record in component_records]
        if len(component_lines) == 1:
            interval_axis = component_lines[0]
        else:
            average_axis = line_average_axis(component_lines)
            interval_axis = average_axis if average_axis is not None else representative_record["line"]
        raw_intervals.append(
            {
                "axis": interval_axis,
                "records": component_records,
                "representative_record": representative_record,
                "length": float(interval_axis.length),
            }
        )

    accepted_intervals = []
    for interval in sorted(raw_intervals, key=lambda item: item["length"], reverse=True):
        duplicate = False
        for accepted in accepted_intervals:
            try:
                near_length = float(interval["axis"].intersection(accepted["axis"].buffer(SUBWAY_PARALLEL_SHIFT_TRIGGER_M)).length)
                near_ratio = near_length / max(float(interval["axis"].length), 0.001)
            except (TypeError, ValueError):
                near_ratio = 0.0
            if near_ratio >= 0.8:
                duplicate = True
                break
        if not duplicate:
            accepted_intervals.append(interval)

    accepted_intervals.sort(key=lambda item: min(record["source_index"] for record in item["records"]))
    output_rows: list[pd.Series] = []
    for interval_idx, interval in enumerate(accepted_intervals, start=1):
        interval_axis = interval["axis"]
        component_records = interval["records"]
        representative_record = interval["representative_record"]
        representative = representative_record["row"].copy()
        source_ids = [str(record["source_id"]) for record in component_records]
        source_indices = [str(record["source_index"]) for record in component_records]
        interval_source_id = "_".join(source_ids)
        tunnel_row = representative.copy()
        tunnel_row[geometry_name] = interval_axis
        tunnel_row["OBJECTID"] = f"{interval_source_id}_I{interval_idx:03d}"
        tunnel_row["name"] = f"{line_name}_区间{interval_idx:03d}"
        tunnel_row["_subway_line_name"] = line_name
        tunnel_row["_subway_interval_index"] = interval_idx
        tunnel_row["_subway_tunnel_side"] = ""
        tunnel_row["_subway_corridor_source_count"] = int(len(component_records))
        tunnel_row["_subway_corridor_source_indices"] = ",".join(source_indices)
        tunnel_row["_subway_lateral_translation_x_m"] = 0.0
        tunnel_row["_subway_lateral_translation_y_m"] = 0.0
        tunnel_row["_subway_same_line_representative_policy"] = "source_track_line_is_single_tunnel_centerline"
        output_rows.append(tunnel_row)
    return output_rows


def subway_endpoint_connected_components(
    group: gpd.GeoDataFrame,
    tolerance_m: float = SUBWAY_SOURCE_PART_ENDPOINT_JOIN_TOLERANCE_M,
) -> list[list[Any]]:
    """Group source parts only when nearby endpoints continue in the same direction."""
    indices = list(group.index)
    endpoints: dict[Any, list[dict[str, tuple[float, float]]]] = {}
    for idx, row in group.iterrows():
        row_endpoints: list[dict[str, tuple[float, float]]] = []
        for line in iter_lines(row.geometry):
            coords = list(line.coords)
            if len(coords) >= 2:
                row_endpoints.extend(
                    [
                        {
                            "point": (float(coords[0][0]), float(coords[0][1])),
                            "inward": (
                                float(coords[1][0] - coords[0][0]),
                                float(coords[1][1] - coords[0][1]),
                            ),
                        },
                        {
                            "point": (float(coords[-1][0]), float(coords[-1][1])),
                            "inward": (
                                float(coords[-2][0] - coords[-1][0]),
                                float(coords[-2][1] - coords[-1][1]),
                            ),
                        },
                    ]
                )
        endpoints[idx] = row_endpoints

    adjacency: dict[Any, set[Any]] = {idx: set() for idx in indices}
    for left_pos, left_idx in enumerate(indices):
        for right_idx in indices[left_pos + 1 :]:
            connected = any(
                subway_source_endpoints_continue(left, right, tolerance_m)
                for left in endpoints[left_idx]
                for right in endpoints[right_idx]
            )
            if connected:
                adjacency[left_idx].add(right_idx)
                adjacency[right_idx].add(left_idx)

    components: list[list[Any]] = []
    seen: set[Any] = set()
    for start_idx in indices:
        if start_idx in seen:
            continue
        stack = [start_idx]
        component: list[Any] = []
        seen.add(start_idx)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(component)
    return components


def subway_source_endpoints_continue(
    left: dict[str, tuple[float, float]],
    right: dict[str, tuple[float, float]],
    tolerance_m: float = SUBWAY_SOURCE_PART_ENDPOINT_JOIN_TOLERANCE_M,
) -> bool:
    left_point = left["point"]
    right_point = right["point"]
    if math.hypot(left_point[0] - right_point[0], left_point[1] - right_point[1]) > tolerance_m:
        return False
    left_vector = left["inward"]
    right_vector = right["inward"]
    left_length = math.hypot(left_vector[0], left_vector[1])
    right_length = math.hypot(right_vector[0], right_vector[1])
    if left_length <= 0.001 or right_length <= 0.001:
        return False
    tangent_dot = (
        left_vector[0] * right_vector[0] + left_vector[1] * right_vector[1]
    ) / (left_length * right_length)
    return tangent_dot <= SUBWAY_SOURCE_PART_ENDPOINT_TANGENT_DOT_MAX


def explode_subway_source_parts(candidates: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    rows: list[pd.Series] = []
    geometry_name = candidates.geometry.name
    for source_index, row in candidates.iterrows():
        parts = list(iter_lines(row.geometry))
        for part_index, part in enumerate(parts, start=1):
            part_row = row.copy()
            part_row[geometry_name] = part
            part_row["_subway_source_feature_index"] = source_index
            part_row["_subway_source_part_index"] = part_index
            rows.append(part_row)
    exploded = gpd.GeoDataFrame(rows, geometry=geometry_name, crs=candidates.crs)
    exploded.index = pd.RangeIndex(start=0, stop=len(exploded), step=1)
    return exploded


def merge_subway_endpoint_chain(group: gpd.GeoDataFrame):
    lines = [line for geometry in group.geometry for line in iter_lines(geometry)]
    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]

    endpoint_records: list[list[dict[str, tuple[float, float]]]] = []
    for line in lines:
        coords = list(line.coords)
        endpoint_records.append(
            [
                {
                    "point": (float(coords[0][0]), float(coords[0][1])),
                    "inward": (
                        float(coords[1][0] - coords[0][0]),
                        float(coords[1][1] - coords[0][1]),
                    ),
                },
                {
                    "point": (float(coords[-1][0]), float(coords[-1][1])),
                    "inward": (
                        float(coords[-2][0] - coords[-1][0]),
                        float(coords[-2][1] - coords[-1][1]),
                    ),
                },
            ]
        )

    candidate_connections = []
    for left_index in range(len(lines)):
        for right_index in range(left_index + 1, len(lines)):
            for left_endpoint_index, left_endpoint in enumerate(endpoint_records[left_index]):
                for right_endpoint_index, right_endpoint in enumerate(endpoint_records[right_index]):
                    if not subway_source_endpoints_continue(left_endpoint, right_endpoint):
                        continue
                    distance = math.hypot(
                        left_endpoint["point"][0] - right_endpoint["point"][0],
                        left_endpoint["point"][1] - right_endpoint["point"][1],
                    )
                    candidate_connections.append(
                        (
                            distance,
                            left_index,
                            right_index,
                            left_endpoint_index,
                            right_endpoint_index,
                        )
                    )

    parents = list(range(len(lines)))

    def find(item: int) -> int:
        while parents[item] != item:
            parents[item] = parents[parents[item]]
            item = parents[item]
        return item

    selected_connections = []
    for _, left_index, right_index, left_endpoint_index, right_endpoint_index in sorted(candidate_connections):
        left_root = find(left_index)
        right_root = find(right_index)
        if left_root == right_root:
            continue
        parents[right_root] = left_root
        selected_connections.append(
            (left_index, right_index, left_endpoint_index, right_endpoint_index)
        )

    snapped_coords = [list(line.coords) for line in lines]
    for left_index, right_index, left_endpoint_index, right_endpoint_index in selected_connections:
        left_coord_index = 0 if left_endpoint_index == 0 else -1
        right_coord_index = 0 if right_endpoint_index == 0 else -1
        left_point = snapped_coords[left_index][left_coord_index]
        right_point = snapped_coords[right_index][right_coord_index]
        midpoint = (
            (float(left_point[0]) + float(right_point[0])) * 0.5,
            (float(left_point[1]) + float(right_point[1])) * 0.5,
        )
        snapped_coords[left_index][left_coord_index] = midpoint
        snapped_coords[right_index][right_coord_index] = midpoint

    merged = unary_union([LineString(coords) for coords in snapped_coords])
    try:
        return linemerge(merged)
    except ValueError:
        return merged


def subway_tunnel_generation_corridors(railways: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    candidates = railways[[subway_like(row) for _, row in railways.iterrows()]].copy()
    if candidates.empty:
        return candidates

    candidates = explode_subway_source_parts(candidates)
    candidates["_subway_lateral_translation_x_m"] = 0.0
    candidates["_subway_lateral_translation_y_m"] = 0.0

    candidates["_subway_group_line_name"] = [
        subway_line_name(row, subway_source_id(row, idx))
        for idx, row in candidates.iterrows()
    ]
    corridor_rows: list[pd.Series] = []
    for line_name, line_group in candidates.groupby("_subway_group_line_name", sort=False):
        for component in subway_endpoint_connected_components(line_group):
            component_group = line_group.loc[component]
            representative_idx = max(
                component,
                key=lambda idx: float(component_group.loc[idx].geometry.length),
            )
            corridor = component_group.loc[representative_idx].copy()
            merged_geometry = merge_subway_endpoint_chain(component_group)
            source_ids = [
                subway_source_id(component_group.loc[idx], idx)
                for idx in component
            ]
            corridor[candidates.geometry.name] = merged_geometry
            corridor["OBJECTID"] = "_".join(dict.fromkeys(str(source_id) for source_id in source_ids))
            corridor["_subway_line_name"] = str(line_name)
            corridor["_subway_interval_index"] = int(len(corridor_rows) + 1)
            corridor["_subway_tunnel_side"] = ""
            corridor["_subway_corridor_source_count"] = int(len(component))
            corridor["_subway_corridor_source_indices"] = ",".join(str(idx) for idx in component)
            corridor["_subway_lateral_translation_x_m"] = safe_float(
                component_group.loc[representative_idx].get("_subway_lateral_translation_x_m"),
                0.0,
            )
            corridor["_subway_lateral_translation_y_m"] = safe_float(
                component_group.loc[representative_idx].get("_subway_lateral_translation_y_m"),
                0.0,
            )
            has_lateral_translation = (
                abs(float(corridor["_subway_lateral_translation_x_m"])) > 1e-6
                or abs(float(corridor["_subway_lateral_translation_y_m"])) > 1e-6
            )
            corridor["_subway_same_line_representative_policy"] = (
                "endpoint_connected_source_chain_with_same_line_overlap_lateral_translation"
                if has_lateral_translation
                else "endpoint_connected_source_chain_preserved_without_plan_translation"
            )
            corridor_rows.append(corridor)

    corridors = gpd.GeoDataFrame(corridor_rows, geometry=candidates.geometry.name, crs=candidates.crs)
    corridors.index = pd.RangeIndex(start=0, stop=len(corridors), step=1)
    corridors = apply_subway_same_line_overlap_lateral_translations(corridors)
    return corridors


def subway_corridor_source_indices(railways: gpd.GeoDataFrame) -> list[Any]:
    candidates: list[dict[str, Any]] = []
    for idx, row in railways.iterrows():
        if not subway_like(row):
            continue
        candidates.append(
            {
                "idx": idx,
                "geometry": row.geometry,
                "length": float(row.geometry.length) if row.geometry is not None and not row.geometry.is_empty else 0.0,
                "line_name": subway_line_name(row, subway_source_id(row, idx)),
            }
        )

    accepted: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: item["length"], reverse=True):
        duplicate = False
        for existing in accepted:
            if railway_lines_are_same_corridor(candidate["geometry"], existing["geometry"]):
                duplicate = True
                break
        if not duplicate:
            accepted.append(candidate)
    return [item["idx"] for item in accepted]


def filter_subway_tunnel_corridors(railways: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    indices = subway_corridor_source_indices(railways)
    return railways.loc[indices].copy()


def assign_railway_tunnel_depths(railways: gpd.GeoDataFrame) -> dict[Any, float]:
    records_by_line: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for idx, row in railways.iterrows():
        if not subway_like(row):
            continue
        source_id = subway_source_id(row, idx)
        line_name = subway_depth_group_name(row, source_id)
        category = railway_tunnel_category(row)
        records_by_line[line_name].append(
            {
                "idx": idx,
                "geometry": row.geometry,
                "source_id": source_id,
                "line_name": line_name,
                "category": category,
                "depth_m": railway_tunnel_base_depth_m(row),
            }
        )

    line_records: list[dict[str, Any]] = []
    for line_name, records in records_by_line.items():
        geometries = [record["geometry"] for record in records if record["geometry"] is not None and not record["geometry"].is_empty]
        line_records.append(
            {
                "line_name": line_name,
                "records": records,
                "geometry": unary_union(geometries) if geometries else None,
                "category": records[0]["category"],
                "depth_m": min(float(record["depth_m"]) for record in records),
            }
        )

    assigned: list[dict[str, Any]] = []
    for record in line_records:
        depth_m = float(record["depth_m"])
        while True:
            conflicting_depths = [
                float(other["depth_m"])
                for other in assigned
                if record["line_name"] != other["line_name"]
                and record["category"] in {"subway", "urban_rail"}
                and other["category"] in {"subway", "urban_rail"}
                and railway_tunnels_need_vertical_separation(record["geometry"], other["geometry"])
                and abs(depth_m - float(other["depth_m"])) < SUBWAY_TUNNEL_VERTICAL_CLEARANCE_M
            ]
            if not conflicting_depths:
                break
            depth_m = min(depth_m, min(conflicting_depths)) - SUBWAY_TUNNEL_VERTICAL_CLEARANCE_M
        record["depth_m"] = depth_m
        assigned.append(record)

    depths: dict[Any, float] = {}
    for line_record in line_records:
        for record in line_record["records"]:
            depths[record["idx"]] = float(line_record["depth_m"])
    return depths


def subway_depth_group_name(row, fallback: Any) -> str:
    explicit_line_name = row.get("_subway_line_name")
    if explicit_line_name is not None and not pd.isna(explicit_line_name) and str(explicit_line_name).strip():
        return str(explicit_line_name).strip()
    return subway_line_name(row, fallback)


def subway_source_id(row, fallback: Any) -> str:
    for column in ("OBJECTID", "objectid", "osm_id", "Id", "id"):
        value = row.get(column)
        if value is not None and not pd.isna(value):
            return str(value)
    return str(fallback)


def subway_line_name(row, fallback: Any) -> str:
    for column in ("name", "line_name", "raw_name"):
        value = row.get(column)
        text = str(value).strip() if value is not None and not pd.isna(value) else ""
        if text:
            return text
    return f"Subway_{fallback}"


def subway_line_matches_filter(row, idx: Any, line_filter: str | None) -> bool:
    token = str(line_filter or "").strip().casefold()
    if not token:
        return True
    source_id = subway_source_id(row, idx)
    values = [
        row.get("_subway_line_name"),
        subway_line_name(row, source_id),
        row.get("name"),
        row.get("line_name"),
        row.get("raw_name"),
    ]
    return any(token in str(value).strip().casefold() for value in values if value is not None and not pd.isna(value))


def subway_tunnel_object_name(row, source_id: str, line_name: str) -> str:
    return f"Subway_Tunnel_{object_name_token(line_name, 'Line')}_{object_name_token(source_id, 'Source')}"


def subway_reference_component_names() -> list[str]:
    return [str(record["component"]) for record in SUBWAY_REFERENCE_COMPONENTS_41]


def normalize_subway_professional_systems(
    enabled_systems: Iterable[str] | None = None,
) -> frozenset[str]:
    if enabled_systems is None:
        return SUBWAY_PROFESSIONAL_SYSTEMS
    tokens = {
        token.strip().lower()
        for value in enabled_systems
        for token in str(value).split(",")
        if token.strip()
    }
    if not tokens or "all" in tokens:
        return SUBWAY_PROFESSIONAL_SYSTEMS
    unknown = tokens - SUBWAY_PROFESSIONAL_SYSTEMS
    if unknown:
        raise ValueError(
            "Unknown subway professional systems: "
            f"{sorted(unknown)}. Expected any of: {sorted(SUBWAY_PROFESSIONAL_SYSTEMS)}"
        )
    return frozenset(tokens)


def subway_components_for_professional_systems(
    enabled_systems: Iterable[str] | None = None,
) -> frozenset[str]:
    systems = normalize_subway_professional_systems(enabled_systems)
    return frozenset(
        component
        for system in systems
        for component in SUBWAY_PROFESSIONAL_SYSTEM_COMPONENTS[system]
    )


def subway_reference_component_dimension_ratios(component: str) -> tuple[float, float, float] | None:
    record = SUBWAY_REFERENCE_COMPONENT_BY_NAME.get(component)
    if record is None:
        return None
    dims = record["reference_dimensions_m"]
    return tuple(
        round(float(dims[idx]) / max(float(SUBWAY_REFERENCE_BENCHMARK_DIMENSIONS_M[idx]), 0.001), 6)
        for idx in range(3)
    )


def subway_tunnel_component_object_names(
    row,
    source_id: str,
    line_name: str,
    enabled_systems: Iterable[str] | None = None,
) -> list[str]:
    base_name = subway_tunnel_object_name(row, source_id, line_name)
    enabled_components = subway_components_for_professional_systems(enabled_systems)
    return [
        f"{base_name}_{component}"
        for component in subway_reference_component_names()
        if component in enabled_components
    ]


def segment_frame(a, b) -> tuple[np.ndarray, np.ndarray, np.ndarray, float] | None:
    p0 = np.array([float(a[0]), float(a[1]), 0.0], dtype=float)
    p1 = np.array([float(b[0]), float(b[1]), 0.0], dtype=float)
    vector = p1 - p0
    length = float(np.linalg.norm(vector))
    if length <= 0.05:
        return None
    tangent = vector / length
    normal = np.array([-tangent[1], tangent[0], 0.0], dtype=float)
    up = np.array([0.0, 0.0, 1.0], dtype=float)
    return tangent, normal, up, length


def subway_template_section_offsets(expand_m: float = 0.0) -> list[tuple[float, float]]:
    radius = max(SUBWAY_SINGLE_TUNNEL_SECTION_RADIUS_M + float(expand_m), 0.001)
    offsets: list[tuple[float, float]] = []
    for section_idx in range(SUBWAY_SINGLE_TUNNEL_SECTION_SEGMENTS):
        angle = 2.0 * math.pi * section_idx / SUBWAY_SINGLE_TUNNEL_SECTION_SEGMENTS
        lateral = math.cos(angle) * radius
        vertical = math.sin(angle) * radius
        offsets.append((lateral, vertical))
    return offsets


def smooth_subway_centerline_coords(
    coords: Iterable[tuple[float, ...]],
    iterations: int = 2,
) -> list[tuple[float, float, float]]:
    """Round source-polyline corners while preserving both line endpoints."""
    points = [
        (
            float(coord[0]),
            float(coord[1]),
            float(coord[2]) if len(coord) > 2 else 0.0,
        )
        for coord in coords
    ]
    if len(points) < 3:
        return points
    for _ in range(max(int(iterations), 0)):
        smoothed = [points[0]]
        for left, right in zip(points, points[1:]):
            smoothed.append(tuple(0.75 * left[i] + 0.25 * right[i] for i in range(3)))
            smoothed.append(tuple(0.25 * left[i] + 0.75 * right[i] for i in range(3)))
        smoothed.append(points[-1])
        points = smoothed
    return points


def resample_subway_centerline_coords(
    coords: Iterable[tuple[float, ...]],
    max_segment_m: float = SUBWAY_TUNNEL_SWEEP_MAX_SEGMENT_M,
) -> list[tuple[float, float, float]]:
    """Limit loft spacing so curved tunnel sections cannot span long chords."""
    points = [np.array(coord[:3], dtype=float) for coord in coords]
    if len(points) < 2:
        return [tuple(float(value) for value in point) for point in points]
    spacing = max(float(max_segment_m), 0.10)
    sampled: list[np.ndarray] = [points[0]]
    for start, end in zip(points, points[1:]):
        delta = end - start
        horizontal_length = float(np.linalg.norm(delta[:2]))
        divisions = max(1, int(math.ceil(horizontal_length / spacing)))
        sampled.extend(start + delta * (step / divisions) for step in range(1, divisions + 1))
    return [tuple(float(value) for value in point) for point in sampled]


def safe_subway_lining_centerline_coords(
    coords: Iterable[tuple[float, ...]],
    line_name: str,
    source_id: str,
    part_index: int,
    minimum_radius_m: float = SUBWAY_TUNNEL_MIN_BEND_RADIUS_M,
    initial_iterations: int = 2,
    max_iterations: int = 6,
) -> list[tuple[float, float, float]]:
    """Return a smoothed/resampled centerline whose local bend radius is safe when possible."""
    source_coords = list(coords)
    if len(source_coords) < 3:
        return resample_subway_centerline_coords(
            smooth_subway_centerline_coords(source_coords, iterations=initial_iterations)
        )

    best_curve: list[tuple[float, float, float]] | None = None
    best_radius = -math.inf
    for iterations in range(max(int(initial_iterations), 0), max(int(max_iterations), int(initial_iterations)) + 1):
        curve = resample_subway_centerline_coords(
            smooth_subway_centerline_coords(source_coords, iterations=iterations)
        )
        radius = subway_centerline_min_bend_radius_m(curve)
        comparable_radius = radius if math.isfinite(radius) else math.inf
        if comparable_radius > best_radius:
            best_curve = curve
            best_radius = comparable_radius
        if comparable_radius >= float(minimum_radius_m):
            if iterations > int(initial_iterations):
                print(
                    "[INFO] Increased subway centerline smoothing to satisfy lining bend radius: "
                    f"line={line_name!r}, source_id={source_id!r}, part={part_index}, "
                    f"iterations={iterations}, minimum_radius_m={comparable_radius:.3f}",
                    flush=True,
                )
            return curve
    return best_curve if best_curve is not None else []


def subway_centerline_min_bend_radius_m(coords: Iterable[tuple[float, ...]]) -> float:
    """Estimate the smallest horizontal circumradius along a sampled curve."""
    points = [np.array(coord[:2], dtype=float) for coord in coords]
    minimum = math.inf
    for left, center, right in zip(points, points[1:], points[2:]):
        a = float(np.linalg.norm(center - left))
        b = float(np.linalg.norm(right - center))
        c = float(np.linalg.norm(right - left))
        incoming = center - left
        chord = right - left
        doubled_area = abs(float(incoming[0] * chord[1] - incoming[1] * chord[0]))
        if min(a, b, c) <= 1e-8 or doubled_area <= 1e-8:
            continue
        radius = a * b * c / (2.0 * doubled_area)
        minimum = min(minimum, radius)
    return minimum


def subway_sweep_frames(
    coords: list[tuple[float, float, float]],
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Return center/normal/up frames shared by every sweep cross-section."""
    centers = [np.array(coord, dtype=float) for coord in coords]
    frames: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for idx, center in enumerate(centers):
        if idx == 0:
            direction = centers[1] - center
        elif idx == len(centers) - 1:
            direction = center - centers[idx - 1]
        else:
            incoming = center - centers[idx - 1]
            outgoing = centers[idx + 1] - center
            incoming[2] = 0.0
            outgoing[2] = 0.0
            incoming_length = float(np.linalg.norm(incoming))
            outgoing_length = float(np.linalg.norm(outgoing))
            if incoming_length > 1e-9:
                incoming /= incoming_length
            if outgoing_length > 1e-9:
                outgoing /= outgoing_length
            direction = incoming + outgoing
            if float(np.linalg.norm(direction)) <= 1e-9:
                direction = outgoing if outgoing_length > 1e-9 else incoming
        direction[2] = 0.0
        length = float(np.linalg.norm(direction))
        if length <= 1e-9:
            direction = np.array([1.0, 0.0, 0.0], dtype=float)
        else:
            direction /= length
        normal = np.array([-direction[1], direction[0], 0.0], dtype=float)
        frames.append((center, normal, np.array([0.0, 0.0, 1.0], dtype=float)))
    return frames


def subway_centerline_interval_samples(
    coords: Iterable[tuple[float, ...]],
    interval_m: float,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray, float]]:
    """Sample a curved centerline by mileage with its local tangent and normal."""
    points = [np.array(coord[:3], dtype=float) for coord in coords]
    if len(points) < 2:
        return []
    segment_lengths = [
        float(np.linalg.norm((right - left)[:2]))
        for left, right in zip(points, points[1:])
    ]
    total_length = sum(segment_lengths)
    if total_length <= 0.05:
        return []
    spacing = max(float(interval_m), 0.05)
    sample_count = max(1, int(total_length // spacing))
    samples: list[tuple[np.ndarray, np.ndarray, np.ndarray, float]] = []
    segment_idx = 0
    segment_start_m = 0.0
    for sample_idx in range(sample_count):
        distance_m = min(total_length, (sample_idx + 0.5) * spacing)
        while (
            segment_idx < len(segment_lengths) - 1
            and distance_m > segment_start_m + segment_lengths[segment_idx]
        ):
            segment_start_m += segment_lengths[segment_idx]
            segment_idx += 1
        segment_length = max(segment_lengths[segment_idx], 1e-9)
        ratio = min(max((distance_m - segment_start_m) / segment_length, 0.0), 1.0)
        left = points[segment_idx]
        right = points[segment_idx + 1]
        center = left + (right - left) * ratio
        tangent = right - left
        tangent[2] = 0.0
        tangent_length = float(np.linalg.norm(tangent))
        if tangent_length <= 1e-9:
            continue
        tangent /= tangent_length
        normal = np.array([-tangent[1], tangent[0], 0.0], dtype=float)
        samples.append((center, tangent, normal, distance_m))
    return samples


def subway_template_section_sweep(
    name: str,
    coords: Iterable[tuple[float, ...]],
    color,
    expand_m: float = 0.0,
    thickness_m: float = 0.0,
) -> trimesh.Trimesh | None:
    """Build one continuous tunnel sweep across an entire curved centerline."""
    sweep_coords = [tuple(float(value) for value in coord[:3]) for coord in coords]
    if len(sweep_coords) < 2:
        return None
    frames = subway_sweep_frames(sweep_coords)
    inner_section = subway_template_section_offsets(expand_m)
    thickness = max(float(thickness_m), 0.0)
    outer_section = subway_template_section_offsets(expand_m + thickness)
    section_count = len(inner_section)
    ring_count = len(frames)
    vertices: list[np.ndarray] = []
    for center, normal, up in frames:
        vertices.extend(center + normal * lateral + up * vertical for lateral, vertical in inner_section)
    if thickness > 0.0:
        for center, normal, up in frames:
            vertices.extend(center + normal * lateral + up * vertical for lateral, vertical in outer_section)

    faces: list[list[int]] = []
    outer_base = ring_count * section_count
    for ring_idx in range(ring_count - 1):
        inner_ring = ring_idx * section_count
        inner_next_ring = (ring_idx + 1) * section_count
        outer_ring = outer_base + inner_ring
        outer_next_ring = outer_base + inner_next_ring
        for section_idx in range(section_count):
            next_idx = (section_idx + 1) % section_count
            faces.append([inner_ring + section_idx, inner_next_ring + next_idx, inner_ring + next_idx])
            faces.append([inner_ring + section_idx, inner_next_ring + section_idx, inner_next_ring + next_idx])
            if thickness > 0.0:
                faces.append([outer_ring + section_idx, outer_ring + next_idx, outer_next_ring + next_idx])
                faces.append([outer_ring + section_idx, outer_next_ring + next_idx, outer_next_ring + section_idx])

    if thickness > 0.0:
        first_inner = 0
        first_outer = outer_base
        last_inner = (ring_count - 1) * section_count
        last_outer = outer_base + last_inner
        for section_idx in range(section_count):
            next_idx = (section_idx + 1) % section_count
            faces.append([first_inner + section_idx, first_inner + next_idx, first_outer + next_idx])
            faces.append([first_inner + section_idx, first_outer + next_idx, first_outer + section_idx])
            faces.append([last_inner + section_idx, last_outer + section_idx, last_outer + next_idx])
            faces.append([last_inner + section_idx, last_outer + next_idx, last_inner + next_idx])

    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=False)
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def subway_closed_section_sweep(
    name: str,
    coords: Iterable[tuple[float, ...]],
    section_offsets: Iterable[tuple[float, float]],
    color,
    lateral_offset_m: float = 0.0,
) -> trimesh.Trimesh | None:
    """Sweep one closed 2D section continuously along a curved centerline."""
    sweep_coords = [tuple(float(value) for value in coord[:3]) for coord in coords]
    section = [(float(lateral), float(vertical)) for lateral, vertical in section_offsets]
    if len(sweep_coords) < 2 or len(section) < 3:
        return None
    frames = subway_sweep_frames(sweep_coords)
    vertices: list[np.ndarray] = []
    lateral_offset = float(lateral_offset_m)
    for center, normal, up in frames:
        section_center = center + normal * lateral_offset
        vertices.extend(
            section_center + normal * lateral + up * vertical
            for lateral, vertical in section
        )

    section_count = len(section)
    ring_count = len(frames)
    faces: list[list[int]] = []
    for ring_idx in range(ring_count - 1):
        ring = ring_idx * section_count
        next_ring = (ring_idx + 1) * section_count
        for section_idx in range(section_count):
            next_idx = (section_idx + 1) % section_count
            faces.append([ring + section_idx, ring + next_idx, next_ring + next_idx])
            faces.append([ring + section_idx, next_ring + next_idx, next_ring + section_idx])

    start_center_idx = len(vertices)
    vertices.append(np.mean(vertices[:section_count], axis=0))
    end_center_idx = len(vertices)
    vertices.append(np.mean(vertices[-section_count - 1 : -1], axis=0))
    last_ring = (ring_count - 1) * section_count
    for section_idx in range(section_count):
        next_idx = (section_idx + 1) % section_count
        faces.append([start_center_idx, next_idx, section_idx])
        faces.append([end_center_idx, last_ring + section_idx, last_ring + next_idx])

    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=False)
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def subway_rectangle_section_offsets(width_m: float, height_m: float) -> list[tuple[float, float]]:
    half_width = max(float(width_m) * 0.5, 0.001)
    half_height = max(float(height_m) * 0.5, 0.001)
    return [
        (-half_width, half_height),
        (-half_width, -half_height),
        (half_width, -half_height),
        (half_width, half_height),
    ]


def subway_circle_section_offsets(radius_m: float, sections: int = 12) -> list[tuple[float, float]]:
    radius = max(float(radius_m), 0.001)
    count = max(int(sections), 6)
    return [
        (
            math.cos(2.0 * math.pi * idx / count) * radius,
            math.sin(2.0 * math.pi * idx / count) * radius,
        )
        for idx in range(count)
    ]


def subway_template_section_surface_between(
    name: str,
    start,
    end,
    color,
    expand_m: float = 0.0,
    thickness_m: float = 0.0,
) -> trimesh.Trimesh | None:
    return subway_template_section_sweep(
        name,
        [start, end],
        color,
        expand_m=expand_m,
        thickness_m=thickness_m,
    )


def subway_template_radius_at_angle(angle_deg: float) -> float:
    return SUBWAY_SINGLE_TUNNEL_SECTION_RADIUS_M


def subway_tube_surface_between(
    name: str,
    start,
    end,
    radius: float,
    color,
    sections: int = 28,
) -> trimesh.Trimesh | None:
    frame = segment_frame(start, end)
    if frame is None:
        return None
    _, normal, up, _ = frame
    p0 = np.array([float(start[0]), float(start[1]), float(start[2])], dtype=float)
    p1 = np.array([float(end[0]), float(end[1]), float(end[2])], dtype=float)
    vertices = []
    for center in (p0, p1):
        for section_idx in range(sections):
            angle = 2.0 * math.pi * section_idx / sections
            vertices.append(center + math.cos(angle) * radius * normal + math.sin(angle) * radius * up)
    faces = []
    for section_idx in range(sections):
        next_idx = (section_idx + 1) % sections
        faces.append([section_idx, next_idx, sections + next_idx])
        faces.append([section_idx, sections + next_idx, sections + section_idx])
    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces), process=False)
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def cylinder_surface_between(
    name: str,
    start,
    end,
    radius: float,
    color,
    sections: int = 12,
) -> trimesh.Trimesh | None:
    return subway_tube_surface_between(name, start, end, radius, color, sections=sections)


def oriented_box_between(
    name: str,
    start,
    end,
    length_m: float,
    width_m: float,
    height_m: float,
    lateral_offset_m: float,
    z_center_m: float,
    color,
) -> trimesh.Trimesh | None:
    frame = segment_frame(start, end)
    if frame is None:
        return None
    tangent, normal, up, _ = frame
    p0 = np.array([float(start[0]), float(start[1]), z_center_m], dtype=float)
    p1 = np.array([float(end[0]), float(end[1]), z_center_m], dtype=float)
    center = (p0 + p1) * 0.5 + normal * lateral_offset_m
    transform = np.eye(4)
    transform[:3, 0] = tangent
    transform[:3, 1] = normal
    transform[:3, 2] = up
    transform[:3, 3] = center
    mesh = trimesh.creation.box(extents=(length_m, width_m, height_m), transform=transform)
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def subway_track_bed_between(
    name: str,
    start,
    end,
    width_m: float,
    top_offset_m: float,
    tunnel_radius_m: float,
    invert_clearance_m: float,
    side_thickness_m: float,
    color,
    bottom_segments: int = 8,
) -> trimesh.Trimesh | None:
    """Sweep a track-bed section whose underside follows the tunnel invert.

    A rectangular slab placed at rail level leaves a visible air gap above the
    circular tunnel floor.  The real track-bed/filling concrete reaches the
    invert while its upper surface follows the track longitudinal profile.
    """
    section = subway_track_bed_section_offsets(
        width_m,
        top_offset_m,
        tunnel_radius_m,
        invert_clearance_m,
        side_thickness_m,
        bottom_segments,
    )
    return subway_closed_section_sweep(name, [start, end], section, color)


def subway_track_bed_section_offsets(
    width_m: float,
    top_offset_m: float,
    tunnel_radius_m: float,
    invert_clearance_m: float,
    side_thickness_m: float,
    bottom_segments: int = 8,
) -> list[tuple[float, float]]:
    """Return the full-width formation/invert filling section."""
    requested_half_width = max(float(width_m) * 0.5, 0.001)
    radius = max(float(tunnel_radius_m), requested_half_width + 0.001)
    clearance = max(float(invert_clearance_m), 0.0)
    inner_radius = max(radius - clearance, requested_half_width + 0.001)
    top_offset = float(top_offset_m)
    side_thickness = max(float(side_thickness_m), 0.01)

    # Extend the flat formation surface to the circular tunnel inner wall.
    wall_half_width = math.sqrt(max(inner_radius * inner_radius - top_offset * top_offset, 0.0))
    top_half_width = max(requested_half_width, wall_half_width)
    top_half_width = min(top_half_width, wall_half_width)

    # Start the curved invert below the top/wall intersection so the shoulder
    # retains a visible structural thickness instead of ending as a zero-thick
    # sliver.  The short outer wedge then closes against the tunnel wall.
    shoulder_bottom_offset = top_offset - side_thickness
    bottom_half_width = math.sqrt(
        max(inner_radius * inner_radius - shoulder_bottom_offset * shoulder_bottom_offset, 0.0)
    )
    bottom_half_width = min(bottom_half_width, top_half_width)

    section: list[tuple[float, float]] = [
        (-top_half_width, top_offset),
        (-bottom_half_width, shoulder_bottom_offset),
    ]
    segment_count = max(int(bottom_segments), 2)
    for idx in range(1, segment_count):
        lateral = -bottom_half_width + 2.0 * bottom_half_width * idx / segment_count
        bottom_offset = -math.sqrt(max(inner_radius * inner_radius - lateral * lateral, 0.0))
        section.append((lateral, min(bottom_offset, top_offset - 0.01)))
    section.extend(
        [
            (bottom_half_width, shoulder_bottom_offset),
            (top_half_width, top_offset),
        ]
    )
    return section


def subway_tunnel_wall_point(
    center_xy,
    z_center_m: float,
    normal: np.ndarray,
    up: np.ndarray,
    angle_deg: float,
    radius_m: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    angle = math.radians(angle_deg)
    radial = normal * math.cos(angle) + up * math.sin(angle)
    template_radius = subway_template_radius_at_angle(angle_deg)
    radius_delta = 0.0 if radius_m is None else float(radius_m) - SUBWAY_TUNNEL_RADIUS_M
    radius = max(template_radius + radius_delta, 0.001)
    center = np.array([float(center_xy[0]), float(center_xy[1]), z_center_m], dtype=float)
    return center + radial * radius, radial


def subway_component_metadata(
    object_name: str,
    component: str,
    profile: SubwayGenerationProfile,
    source_id: str,
    line_name: str,
    source_index: Any,
    tunnel_depth_m: float,
    tunnel_category: str,
    part_count: int,
    mileage_length_m: float,
    lateral_translation_x_m: float = 0.0,
    lateral_translation_y_m: float = 0.0,
) -> dict[str, Any]:
    reference_record = SUBWAY_REFERENCE_COMPONENT_BY_NAME.get(component)
    rule_spec = SUBWAY_RULE_COMPONENT_SPECS.get(component, {})
    tunnel_side = ""
    if abs(float(lateral_translation_x_m)) > 1e-6 or abs(float(lateral_translation_y_m)) > 1e-6:
        tunnel_side = "left" if float(lateral_translation_x_m) >= 0.0 else "right"
    metadata = {
        "name": object_name,
        "layer_name": f"Subway_{component}",
        "component": component,
        "professional_system": SUBWAY_COMPONENT_PROFESSIONAL_SYSTEM.get(component, "unclassified"),
        "cim_domain": "subway",
        "cim_entity_type": "subway_interval_tunnel_component",
        "cim_generation_level": profile.name,
        "cim_monomer": True,
        "source_subway_id": source_id,
        "line_name": line_name,
        "railway_tunnel_category": tunnel_category,
        "source_index": str(source_index),
        "part_count": part_count,
        "tunnel_depth_m": tunnel_depth_m,
        "tunnel_side": tunnel_side,
        "lateral_translation_x_m": lateral_translation_x_m,
        "lateral_translation_y_m": lateral_translation_y_m,
        "inner_clear_radius_m": SUBWAY_TUNNEL_RADIUS_M,
        "lining_thickness_m": SUBWAY_TUNNEL_LINING_THICKNESS_M,
        "outer_radius_m": SUBWAY_TUNNEL_OUTER_RADIUS_M,
        "section_profile": SUBWAY_SINGLE_TUNNEL_SECTION_PROFILE_NAME,
        "section_source_mesh": SUBWAY_TEMPLATE_SECTION_SOURCE_MESH,
        "section_half": SUBWAY_SINGLE_TUNNEL_SECTION_SIDE,
        "section_radius_m": SUBWAY_SINGLE_TUNNEL_SECTION_RADIUS_M,
        "section_circle_segment_count": SUBWAY_SINGLE_TUNNEL_SECTION_SEGMENTS,
        "section_source_arc_vertex_count": len(SUBWAY_SINGLE_TUNNEL_SECTION_SOURCE_ARC_XZ_M),
        "section_hull_vertex_count": SUBWAY_SINGLE_TUNNEL_SECTION_SEGMENTS,
        "section_hull_area_m2": SUBWAY_SINGLE_TUNNEL_SECTION_AREA_M2,
        "construction_method": "subway01_circular_single_tunnel_rule_sweep",
        "geometry_source": "procedural_parameterized_rule",
        "geometry_lod": "cim_simplified_semantic_geometry",
        "mileage_start_m": 0.0,
        "mileage_end_m": round(float(mileage_length_m), 3),
    }
    metadata.update(rule_spec)
    if reference_record is not None:
        metadata.update(
            {
                "reference_mesh": reference_record["reference_mesh"],
                "reference_system": reference_record["system"],
                "reference_dimensions_m": list(reference_record["reference_dimensions_m"]),
                "reference_benchmark_dimensions_m": list(SUBWAY_REFERENCE_BENCHMARK_DIMENSIONS_M),
                "reference_dimension_ratios": list(subway_reference_component_dimension_ratios(component) or ()),
            }
        )
    return metadata


def add_mesh(meshes: list[trimesh.Trimesh] | None, mesh: trimesh.Trimesh | None) -> None:
    if meshes is not None and mesh is not None and len(mesh.vertices) > 0:
        meshes.append(mesh)


def marker_sphere(name: str, center, radius: float, color, subdivisions: int = 1) -> trimesh.Trimesh:
    mesh = trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)
    mesh.apply_translation(np.array(center, dtype=float))
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def build_subway_tunnel_meshes(
    railways: gpd.GeoDataFrame,
    profile: SubwayGenerationProfile = SUBWAY_CIM4_PROFILE,
    depth_by_index: dict[Any, float] | None = None,
    enabled_systems: Iterable[str] | None = None,
) -> dict[str, trimesh.Trimesh]:
    profile = subway_generation_profile(profile)
    professional_systems = subway_professional_systems_for_profile(profile, enabled_systems)
    enabled_components = subway_components_for_professional_systems(professional_systems)
    depth_by_index = depth_by_index or assign_railway_tunnel_depths(railways)
    tunnel_meshes: dict[str, trimesh.Trimesh] = {}
    track_bed_rule = {
        "lateral_offset_m": 0.0,
        "top_offset_m": -1.86,
        "invert_clearance_m": 0.08,
        "side_thickness_m": 0.22,
        "width_m": 2.80,
    }
    platform_rule = {"lateral_offset_m": 1.95, "vertical_offset_m": -1.15, "width_m": 0.90, "height_m": 0.32}
    contact_rule = {"lateral_offset_m": 0.0, "vertical_offset_m": 2.20, "width_m": 0.20, "height_m": 0.72}
    cable_rule = {"lateral_offset_m": -2.35, "vertical_offset_m": 0.0, "width_m": 0.74, "height_m": 1.40}
    pipe_rule = {"lateral_offset_m": -2.45, "vertical_offset_m": 0.75, "width_m": 0.54, "height_m": 0.12}
    lighting_rule = {"lateral_offset_m": 2.42, "vertical_offset_m": 0.88, "width_m": 0.42, "height_m": 0.22}
    sign_rule = {"lateral_offset_m": 2.42, "vertical_offset_m": 0.25, "width_m": 0.55, "height_m": 0.34}
    water_rule = {"lateral_offset_m": -2.32, "vertical_offset_m": -0.82, "width_m": 0.72, "height_m": 0.30}
    rail_surface_rule = {"width_m": 2.56, "height_m": 0.08}
    sleeper_rule = {"length_m": 0.24, "width_m": 2.40, "height_m": 0.16}
    isolation_rule = {"length_m": 0.20, "width_m": 0.24, "height_m": 0.04}
    fastener_rule = {"length_m": 0.16, "width_m": 0.20, "height_m": 0.08}
    for idx, row in railways.iterrows():
        if not subway_like(row):
            continue
        source_id = subway_source_id(row, idx)
        line_name = subway_line_name(row, source_id)
        tunnel_name = subway_tunnel_object_name(row, source_id, line_name)
        tunnel_depth_m = float(depth_by_index.get(idx, railway_tunnel_base_depth_m(row)))
        tunnel_category = railway_tunnel_category(row)
        lateral_translation_x_m = safe_float(row.get("_subway_lateral_translation_x_m"), 0.0)
        lateral_translation_y_m = safe_float(row.get("_subway_lateral_translation_y_m"), 0.0)
        component_meshes: dict[str, list[trimesh.Trimesh] | None] = {
            component: [] if component in enabled_components else None
            for component in subway_reference_component_names()
        }
        part_count = 0
        mileage_length_m = 0.0
        for line_idx, line in enumerate(iter_lines(row.geometry)):
            coords = list(line.coords)
            lining_centerline = safe_subway_lining_centerline_coords(
                [(coord[0], coord[1], tunnel_depth_m) for coord in coords],
                line_name=line_name,
                source_id=source_id,
                part_index=line_idx,
            )
            minimum_bend_radius_m = subway_centerline_min_bend_radius_m(lining_centerline)
            if minimum_bend_radius_m < SUBWAY_TUNNEL_MIN_BEND_RADIUS_M:
                print(
                    "[WARN] Subway tunnel centerline bend radius is smaller than the safe lining radius: "
                    f"line={line_name!r}, source_id={source_id!r}, part={line_idx}, "
                    f"minimum_radius_m={minimum_bend_radius_m:.3f}, "
                    f"recommended_minimum_m={SUBWAY_TUNNEL_MIN_BEND_RADIUS_M:.3f}",
                    flush=True,
                )
            lining = subway_template_section_sweep(
                f"{tunnel_name}_Lining_Line_{line_idx}",
                lining_centerline,
                COLORS["subway_tunnel"],
                thickness_m=SUBWAY_TUNNEL_LINING_THICKNESS_M,
            )
            add_mesh(component_meshes["Ref04_Concrete_Segment"], lining)

            track_bed_width = track_bed_rule["width_m"]
            track_bed_top_z = tunnel_depth_m + track_bed_rule["top_offset_m"]
            track_bed_section = subway_track_bed_section_offsets(
                track_bed_width,
                track_bed_rule["top_offset_m"],
                SUBWAY_TUNNEL_RADIUS_M,
                track_bed_rule["invert_clearance_m"],
                track_bed_rule["side_thickness_m"],
            )
            track_bed = subway_closed_section_sweep(
                f"{tunnel_name}_Track_Bed_Line_{line_idx}",
                lining_centerline,
                track_bed_section,
                [150, 150, 145, 255],
            )
            add_mesh(component_meshes["Ref02_Aggregate_Base"], track_bed)

            track_surface_height = rail_surface_rule["height_m"]
            track_surface_z = track_bed_top_z + track_surface_height * 0.5
            track_surface_centerline = [
                (coord[0], coord[1], track_surface_z)
                for coord in lining_centerline
            ]
            track_surface = subway_closed_section_sweep(
                f"{tunnel_name}_Rail_Bed_Surface_Line_{line_idx}",
                track_surface_centerline,
                subway_rectangle_section_offsets(
                    rail_surface_rule["width_m"],
                    track_surface_height,
                ),
                [132, 132, 128, 255],
                lateral_offset_m=track_bed_rule["lateral_offset_m"],
            )
            add_mesh(component_meshes["Ref37_Rail_Bed_Surface"], track_surface)

            track_surface_top_z = track_surface_z + track_surface_height * 0.5
            sleeper_height = sleeper_rule["height_m"]
            sleeper_z = track_surface_top_z + sleeper_height * 0.5
            sleeper_top_z = sleeper_z + sleeper_height * 0.5
            isolation_z = sleeper_top_z + isolation_rule["height_m"] * 0.5
            fastener_z = sleeper_top_z + isolation_rule["height_m"] + fastener_rule["height_m"] * 0.5
            rail_z = sleeper_top_z + isolation_rule["height_m"] + fastener_rule["height_m"] + 0.055
            rail_offset = SUBWAY_TRACK_GAUGE_M * 0.5
            rail_centerline = [(coord[0], coord[1], rail_z) for coord in lining_centerline]
            for rail_side, lateral_offset in (("L", -rail_offset), ("R", rail_offset)):
                rail_lateral = track_bed_rule["lateral_offset_m"] + lateral_offset
                rail = subway_closed_section_sweep(
                    f"{tunnel_name}_Rail_{rail_side}_Line_{line_idx}",
                    rail_centerline,
                    subway_circle_section_offsets(0.055, sections=10),
                    [55, 55, 55, 255],
                    lateral_offset_m=rail_lateral,
                )
                add_mesh(component_meshes["Ref38_Rail_Aluminum_Part"], rail)
                rail_top_centerline = [
                    (coord[0], coord[1], rail_z + 0.055)
                    for coord in lining_centerline
                ]
                rail_top = subway_closed_section_sweep(
                    f"{tunnel_name}_Rail_Top_{rail_side}_Line_{line_idx}",
                    rail_top_centerline,
                    subway_circle_section_offsets(0.026, sections=10),
                    [210, 214, 216, 255],
                    lateral_offset_m=rail_lateral,
                )
                add_mesh(component_meshes["Ref40_Rail_Chrome_Part"], rail_top)

            sleeper_samples = subway_centerline_interval_samples(
                lining_centerline,
                SUBWAY_SLEEPER_INTERVAL_M,
            )
            isolation_half_length = isolation_rule["length_m"] * 0.5
            fastener_half_length = fastener_rule["length_m"] * 0.5
            for sleeper_idx, (center, tangent, normal, _distance_m) in enumerate(sleeper_samples):
                for rail_side, lateral_offset in (("L", -rail_offset), ("R", rail_offset)):
                    rail_lateral = track_bed_rule["lateral_offset_m"] + lateral_offset
                    isolation_pad = oriented_box_between(
                        f"{tunnel_name}_Isolation_Pad_{rail_side}_{line_idx}_{sleeper_idx}",
                        center - tangent * isolation_half_length,
                        center + tangent * isolation_half_length,
                        isolation_rule["length_m"],
                        isolation_rule["width_m"],
                        isolation_rule["height_m"],
                        rail_lateral,
                        isolation_z,
                        [28, 28, 26, 255],
                    )
                    add_mesh(component_meshes["Ref03_Rubber_Isolation"], isolation_pad)
                    fastener = oriented_box_between(
                        f"{tunnel_name}_Rail_Fastener_{rail_side}_{line_idx}_{sleeper_idx}",
                        center - tangent * fastener_half_length,
                        center + tangent * fastener_half_length,
                        fastener_rule["length_m"],
                        fastener_rule["width_m"],
                        fastener_rule["height_m"],
                        rail_lateral,
                        fastener_z,
                        [72, 72, 68, 255],
                    )
                    add_mesh(component_meshes["Ref41_Rail_Fastener"], fastener)

            platform_width = min(platform_rule["width_m"], SUBWAY_REFERENCE_BENCHMARK_DIMENSIONS_M[0] - 1.00)
            platform_height = min(max(platform_rule["height_m"], 0.32), 1.20)
            platform_z = tunnel_depth_m + platform_rule["vertical_offset_m"]
            platform_top_z = platform_z + platform_height * 0.5
            platform_centerline = [(coord[0], coord[1], platform_z) for coord in lining_centerline]
            platform = subway_closed_section_sweep(
                f"{tunnel_name}_Evacuation_Platform_Line_{line_idx}",
                platform_centerline,
                subway_rectangle_section_offsets(platform_width, platform_height),
                [132, 132, 128, 255],
                lateral_offset_m=platform_rule["lateral_offset_m"],
            )
            add_mesh(component_meshes["Ref08_Platform_Main"], platform)

            platform_panel = subway_closed_section_sweep(
                f"{tunnel_name}_Platform_Panel_Line_{line_idx}",
                [(coord[0], coord[1], platform_top_z - 0.035) for coord in lining_centerline],
                subway_rectangle_section_offsets(max(platform_width - 0.12, 0.30), 0.07),
                [154, 154, 150, 255],
                lateral_offset_m=platform_rule["lateral_offset_m"],
            )
            add_mesh(component_meshes["Ref12_Platform_Concrete_Panel"], platform_panel)

            platform_frame_lateral = platform_rule["lateral_offset_m"] - platform_width * 0.28
            platform_frame = subway_closed_section_sweep(
                f"{tunnel_name}_Platform_Frame_Line_{line_idx}",
                [(coord[0], coord[1], platform_top_z - 0.22) for coord in lining_centerline],
                subway_rectangle_section_offsets(0.16, 0.16),
                [72, 72, 68, 255],
                lateral_offset_m=platform_frame_lateral,
            )
            add_mesh(component_meshes["Ref11_Platform_Steel_Frame"], platform_frame)

            platform_edge_lateral = platform_rule["lateral_offset_m"] + platform_width * 0.5 - 0.05
            platform_edge = subway_closed_section_sweep(
                f"{tunnel_name}_Platform_Edge_Line_{line_idx}",
                [(coord[0], coord[1], platform_top_z + 0.03) for coord in lining_centerline],
                subway_rectangle_section_offsets(0.10, 0.06),
                [210, 190, 86, 255],
                lateral_offset_m=platform_edge_lateral,
            )
            add_mesh(component_meshes["Ref10_Platform_Edge_Strip"], platform_edge)

            guardrail_lateral = platform_rule["lateral_offset_m"] - platform_width * 0.5 + 0.12
            for rail_level, z_offset in enumerate((0.45, 0.82)):
                guardrail = subway_closed_section_sweep(
                    f"{tunnel_name}_Guardrail_Rail_Line_{line_idx}_{rail_level}",
                    [(coord[0], coord[1], platform_top_z + z_offset) for coord in lining_centerline],
                    subway_circle_section_offsets(0.035, sections=8),
                    [128, 128, 122, 255],
                    lateral_offset_m=guardrail_lateral,
                )
                add_mesh(component_meshes["Ref01_Guardrail"], guardrail)

            pipe_z = tunnel_depth_m + pipe_rule["vertical_offset_m"]
            pipe_radius = min(max(pipe_rule["height_m"] * 0.45, 0.055), 0.12)
            for component, pipe_side, lateral_offset, z_offset in (
                ("Ref20_Leakage_Cable_A", "A", pipe_rule["lateral_offset_m"], -0.25),
                ("Ref21_Leakage_Cable_B", "B", pipe_rule["lateral_offset_m"], 0.0),
                ("Ref22_Leakage_Cable_C", "C", pipe_rule["lateral_offset_m"], 0.25),
            ):
                if component_meshes[component] is None:
                    continue
                pipe = subway_closed_section_sweep(
                    f"{tunnel_name}_Leakage_Cable_{pipe_side}_Line_{line_idx}",
                    [(coord[0], coord[1], pipe_z + z_offset) for coord in lining_centerline],
                    subway_circle_section_offsets(min(pipe_radius, 0.075), sections=8),
                    [110, 110, 105, 255],
                    lateral_offset_m=lateral_offset,
                )
                add_mesh(component_meshes[component], pipe)

            contact_z = tunnel_depth_m + contact_rule["vertical_offset_m"]
            contact_lateral = contact_rule["lateral_offset_m"]
            if component_meshes["Ref14_Contact_Rail"] is not None:
                contact = subway_closed_section_sweep(
                    f"{tunnel_name}_Contact_Wire_Line_{line_idx}",
                    [(coord[0], coord[1], contact_z) for coord in lining_centerline],
                    subway_circle_section_offsets(0.045, sections=8),
                    [50, 50, 48, 255],
                    lateral_offset_m=contact_lateral,
                )
                add_mesh(component_meshes["Ref14_Contact_Rail"], contact)

            lighting_z = tunnel_depth_m + lighting_rule["vertical_offset_m"]
            lighting_lateral = lighting_rule["lateral_offset_m"]
            if component_meshes["Ref27_Lighting_Cable"] is not None:
                lighting_cable = subway_closed_section_sweep(
                    f"{tunnel_name}_Lighting_Cable_Line_{line_idx}",
                    [(coord[0], coord[1], lighting_z + 0.28) for coord in lining_centerline],
                    subway_circle_section_offsets(0.018, sections=6),
                    [44, 44, 42, 255],
                    lateral_offset_m=lighting_lateral,
                )
                add_mesh(component_meshes["Ref27_Lighting_Cable"], lighting_cable)

            for part_idx, (a, b) in enumerate(zip(coords, coords[1:])):
                frame = segment_frame(a, b)
                if frame is None:
                    continue
                tangent, normal, up, segment_length = frame
                mileage_length_m += segment_length

                sleeper_start = np.array([float(a[0]), float(a[1]), 0.0], dtype=float)
                post_count = max(1, int(segment_length // SUBWAY_CABLE_BRACKET_INTERVAL_M))
                for post_idx in range(post_count):
                    distance = min(segment_length, (post_idx + 0.5) * SUBWAY_CABLE_BRACKET_INTERVAL_M)
                    center_xy = sleeper_start + tangent * distance
                    post = oriented_box_between(
                        f"{tunnel_name}_Guardrail_Post_{part_idx}_{post_idx}",
                        center_xy,
                        center_xy + tangent * 0.12,
                        0.12,
                        0.08,
                        0.82,
                        guardrail_lateral,
                        platform_top_z + 0.41,
                        [128, 128, 122, 255],
                    )
                    add_mesh(component_meshes["Ref01_Guardrail"], post)
                    platform_support = oriented_box_between(
                        f"{tunnel_name}_Platform_Support_{part_idx}_{post_idx}",
                        center_xy,
                        center_xy + tangent * 0.18,
                        0.18,
                        0.16,
                        0.72,
                        platform_frame_lateral,
                        platform_z - platform_height * 0.5 - 0.34,
                        [92, 92, 88, 255],
                    )
                    add_mesh(component_meshes["Ref09_Platform_Support"], platform_support)
                    platform_bracket = oriented_box_between(
                        f"{tunnel_name}_Platform_Bracket_{part_idx}_{post_idx}",
                        center_xy,
                        center_xy + tangent * 0.20,
                        0.20,
                        0.58,
                        0.10,
                        platform_rule["lateral_offset_m"] - platform_width * 0.32,
                        platform_top_z - 0.34,
                        [112, 112, 106, 255],
                    )
                    add_mesh(component_meshes["Ref13_Platform_Bracket"], platform_bracket)

                bracket_count = max(1, int(segment_length // SUBWAY_CABLE_BRACKET_INTERVAL_M))
                cable_bracket_rules = tuple(rule for rule in (
                    ("Ref17_High_Voltage_Cable_Bracket", "HV", cable_rule["lateral_offset_m"], -0.45, [120, 58, 190, 255], 0.72),
                    ("Ref18_Comm_Cable_Bracket_A", "COMM_A", cable_rule["lateral_offset_m"], 0.05, [168, 110, 138, 255], 0.62),
                    ("Ref19_Comm_Cable_Bracket_B", "COMM_B", cable_rule["lateral_offset_m"], 0.55, [132, 82, 168, 255], 0.52),
                ) if component_meshes[rule[0]] is not None)
                water_base_z = water_rule["vertical_offset_m"]
                water_bracket_rules = tuple(rule for rule in (
                    ("Ref29_Water_System_Bracket_A", "WATER_A", water_rule["lateral_offset_m"], water_base_z - 0.38, [72, 116, 170, 255], 0.74),
                    ("Ref30_Water_System_Bracket_B", "WATER_B", water_rule["lateral_offset_m"], water_base_z - 0.13, [82, 126, 180, 255], 0.66),
                    ("Ref31_Water_System_Bracket_C", "WATER_C", water_rule["lateral_offset_m"], water_base_z + 0.12, [92, 136, 190, 255], 0.58),
                    ("Ref32_Water_System_Bracket_D", "WATER_D", water_rule["lateral_offset_m"], water_base_z + 0.37, [102, 146, 200, 255], 0.50),
                    ("Ref33_Fire_Water_Bracket_A", "FIRE_A", -water_rule["lateral_offset_m"], water_base_z - 0.18, [182, 64, 54, 255], 0.78),
                    ("Ref34_Fire_Water_Bracket_B", "FIRE_B", -water_rule["lateral_offset_m"], water_base_z + 0.07, [192, 74, 64, 255], 0.70),
                    ("Ref35_Fire_Water_Bracket_C", "FIRE_C", -water_rule["lateral_offset_m"], water_base_z + 0.32, [202, 84, 74, 255], 0.62),
                    ("Ref36_Fire_Water_Bracket_D", "FIRE_D", -water_rule["lateral_offset_m"], water_base_z + 0.57, [212, 94, 84, 255], 0.54),
                ) if component_meshes[rule[0]] is not None)
                for bracket_idx in range(bracket_count):
                    distance = min(segment_length, (bracket_idx + 0.5) * SUBWAY_CABLE_BRACKET_INTERVAL_M)
                    center_xy = sleeper_start + tangent * distance
                    for component, bracket_name, lateral_offset, z_offset, color, shelf_width in (
                        *cable_bracket_rules,
                        *water_bracket_rules,
                    ):
                        bracket = oriented_box_between(
                            f"{tunnel_name}_{bracket_name}_{part_idx}_{bracket_idx}",
                            center_xy,
                            center_xy + tangent * 0.18,
                            0.18,
                            shelf_width,
                            0.08,
                            lateral_offset,
                            tunnel_depth_m + z_offset,
                            color,
                        )
                        add_mesh(component_meshes[component], bracket)
                        support = oriented_box_between(
                            f"{tunnel_name}_{bracket_name}_Support_{part_idx}_{bracket_idx}",
                            center_xy,
                            center_xy + tangent * 0.18,
                            0.18,
                            0.10,
                            0.32,
                            lateral_offset - math.copysign(shelf_width * 0.45, lateral_offset or 1.0),
                            tunnel_depth_m + z_offset + 0.12,
                            color,
                        )
                        add_mesh(component_meshes[component], support)

                hanger_count = max(1, int(segment_length // SUBWAY_CONTACT_HANGER_INTERVAL_M))
                for hanger_idx in range(hanger_count if component_meshes["Ref15_Contact_Hanger"] is not None else 0):
                    distance = min(segment_length, (hanger_idx + 0.5) * SUBWAY_CONTACT_HANGER_INTERVAL_M)
                    center_xy = sleeper_start + tangent * distance
                    hanger = oriented_box_between(
                        f"{tunnel_name}_Contact_Hanger_{part_idx}_{hanger_idx}",
                        center_xy,
                        center_xy + tangent * 0.16,
                        0.16,
                        0.08,
                        0.72,
                        contact_lateral,
                        contact_z + 0.18,
                        [76, 76, 72, 255],
                    )
                    add_mesh(component_meshes["Ref15_Contact_Hanger"], hanger)
                    clamp = oriented_box_between(
                        f"{tunnel_name}_Contact_Clamp_{part_idx}_{hanger_idx}",
                        center_xy,
                        center_xy + tangent * 0.12,
                        0.12,
                        0.16,
                        0.10,
                        contact_lateral,
                        contact_z,
                        [54, 54, 52, 255],
                    )
                    add_mesh(component_meshes["Ref16_Contact_Clamp"], clamp)

                lighting_count = max(1, int(segment_length // SUBWAY_LIGHTING_INTERVAL_M))
                for light_idx in range(lighting_count if component_meshes["Ref26_Lighting_Fixture"] is not None else 0):
                    distance = min(segment_length, (light_idx + 0.5) * SUBWAY_LIGHTING_INTERVAL_M)
                    center_xy = sleeper_start + tangent * distance
                    light = oriented_box_between(
                        f"{tunnel_name}_Lighting_{part_idx}_{light_idx}",
                        center_xy,
                        center_xy + tangent * 0.42,
                        0.38,
                        0.18,
                        min(max(lighting_rule["height_m"] * 0.35, 0.10), 0.18),
                        lighting_lateral,
                        lighting_z,
                        [235, 238, 210, 255],
                    )
                    add_mesh(component_meshes["Ref26_Lighting_Fixture"], light)
                    light_bracket = oriented_box_between(
                        f"{tunnel_name}_Lighting_Bracket_{part_idx}_{light_idx}",
                        center_xy,
                        center_xy + tangent * 0.18,
                        0.18,
                        0.42,
                        0.08,
                        lighting_lateral,
                        lighting_z - 0.10,
                        [96, 96, 90, 255],
                    )
                    add_mesh(component_meshes["Ref28_Lighting_Bracket"], light_bracket)

                sign_count = max(1, int(segment_length // SUBWAY_EVACUATION_SIGN_INTERVAL_M))
                sign_z = tunnel_depth_m + sign_rule["vertical_offset_m"]
                sign_lateral = sign_rule["lateral_offset_m"]
                for sign_idx in range(sign_count):
                    distance = min(segment_length, (sign_idx + 0.5) * SUBWAY_EVACUATION_SIGN_INTERVAL_M)
                    center_xy = sleeper_start + tangent * distance
                    sign_frame = oriented_box_between(
                        f"{tunnel_name}_Evacuation_Sign_Frame_{part_idx}_{sign_idx}",
                        center_xy,
                        center_xy + tangent * 0.55,
                        0.55,
                        0.10,
                        0.34,
                        sign_lateral,
                        sign_z,
                        [28, 88, 46, 255],
                    )
                    add_mesh(component_meshes["Ref24_Evacuation_Sign_Frame"], sign_frame)
                    sign_panel = oriented_box_between(
                        f"{tunnel_name}_Evacuation_Sign_Panel_{part_idx}_{sign_idx}",
                        center_xy,
                        center_xy + tangent * 0.48,
                        0.48,
                        0.07,
                        0.26,
                        sign_lateral,
                        sign_z,
                        [60, 170, 90, 255],
                    )
                    add_mesh(component_meshes["Ref23_Evacuation_Sign_Panel"], sign_panel)
                    sign_lamp = oriented_box_between(
                        f"{tunnel_name}_Evacuation_Sign_Lamp_{part_idx}_{sign_idx}",
                        center_xy,
                        center_xy + tangent * 0.36,
                        0.36,
                        0.08,
                        0.08,
                        sign_lateral,
                        sign_z + 0.20,
                        [96, 236, 126, 255],
                    )
                    add_mesh(component_meshes["Ref25_Evacuation_Sign_Lamp"], sign_lamp)
                part_count += 1
        for component, meshes in component_meshes.items():
            if meshes is None:
                continue
            object_name = f"{tunnel_name}_{component}"
            color = COLORS["subway_tunnel"] if component == "Ref04_Concrete_Segment" else None
            combined = combine_mesh_list(object_name, meshes, color)
            if combined is None:
                continue
            combined.metadata.update(
                subway_component_metadata(
                    object_name,
                    component,
                    profile,
                    source_id,
                    line_name,
                    idx,
                    tunnel_depth_m,
                    tunnel_category,
                    part_count,
                    mileage_length_m,
                    lateral_translation_x_m,
                    lateral_translation_y_m,
                )
            )
            tunnel_meshes[object_name] = combined
    return tunnel_meshes


def subway_tunnel_semantic_record(
    row,
    idx: Any,
    origin: tuple[float, float],
    depth_by_index: dict[Any, float] | None = None,
    enabled_systems: Iterable[str] | None = None,
) -> dict[str, Any]:
    source_id = subway_source_id(row, idx)
    line_name = subway_line_name(row, source_id)
    object_name = subway_tunnel_object_name(row, source_id, line_name)
    professional_systems = normalize_subway_professional_systems(enabled_systems)
    component_object_names = subway_tunnel_component_object_names(
        row,
        source_id,
        line_name,
        professional_systems,
    )
    tunnel_depth_m = float((depth_by_index or {}).get(idx, railway_tunnel_base_depth_m(row)))
    tunnel_category = railway_tunnel_category(row)
    tunnel_side = str(row.get("_subway_tunnel_side") or "")
    lateral_translation_x_m = safe_float(row.get("_subway_lateral_translation_x_m"), 0.0)
    lateral_translation_y_m = safe_float(row.get("_subway_lateral_translation_y_m"), 0.0)
    same_line_track_spacing_m = safe_float(row.get("_subway_same_line_track_spacing_m"), SUBWAY_SAME_LINE_TRACK_SPACING_M)
    length_m = 0.0
    segment_count = 0
    for line in iter_lines(row.geometry):
        length_m += float(line.length)
        coords = list(line.coords)
        segment_count += max(0, len(coords) - 1)
    horizontal_bounds = (
        [round(float(value), 3) for value in row.geometry.bounds]
        if row.geometry is not None and not row.geometry.is_empty
        else None
    )
    return {
        "object_name": object_name,
        "component_object_names": component_object_names,
        "source_subway_id": source_id,
        "source_index": str(idx),
        "line_name": line_name,
        "interval_name": line_name,
        "component": "Subway_Tunnel",
        "professional_systems": sorted(professional_systems),
        "cim_domain": "subway",
        "cim_entity_type": "subway_interval_tunnel",
        "railway_tunnel_category": tunnel_category,
        "tunnel_side": tunnel_side,
        "construction_method": "subway01_circular_single_tunnel_rule_sweep",
        "geometry_basis": "railway_centerline",
        "section_profile": SUBWAY_SINGLE_TUNNEL_SECTION_PROFILE_NAME,
        "section_source_mesh": SUBWAY_TEMPLATE_SECTION_SOURCE_MESH,
        "section_half": SUBWAY_SINGLE_TUNNEL_SECTION_SIDE,
        "section_radius_m": SUBWAY_SINGLE_TUNNEL_SECTION_RADIUS_M,
        "section_circle_segment_count": SUBWAY_SINGLE_TUNNEL_SECTION_SEGMENTS,
        "section_source_arc_vertex_count": len(SUBWAY_SINGLE_TUNNEL_SECTION_SOURCE_ARC_XZ_M),
        "section_hull_vertex_count": SUBWAY_SINGLE_TUNNEL_SECTION_SEGMENTS,
        "section_hull_area_m2": SUBWAY_SINGLE_TUNNEL_SECTION_AREA_M2,
        "length_m": round(length_m, 3),
        "source_segment_count": segment_count,
        "tunnel_depth_m": round(tunnel_depth_m, 3),
        "tunnel_top_z_m": round(tunnel_depth_m + SUBWAY_TUNNEL_OUTER_RADIUS_M, 3),
        "tunnel_bottom_z_m": round(tunnel_depth_m - SUBWAY_TUNNEL_OUTER_RADIUS_M, 3),
        "horizontal_bounds_xy_m": horizontal_bounds,
        "absolute_z_datum": "model_local_z_meter",
        "road_surface_base_z_m": ROAD_SURFACE_BASE_Z_M,
        "lateral_translation_x_m": round(lateral_translation_x_m, 3),
        "lateral_translation_y_m": round(lateral_translation_y_m, 3),
        "same_line_track_spacing_m": round(same_line_track_spacing_m, 3),
        "inner_clear_radius_m": SUBWAY_TUNNEL_RADIUS_M,
        "lining_thickness_m": SUBWAY_TUNNEL_LINING_THICKNESS_M,
        "outer_radius_m": SUBWAY_TUNNEL_OUTER_RADIUS_M,
        "coordinate": {
            "model_crs": TARGET_CRS,
            "local_origin": {"x": origin[0], "y": origin[1], "z": 0.0},
            "absolute_z_datum": "model_local_z_meter",
            "road_surface_base_z_m": ROAD_SURFACE_BASE_Z_M,
        },
    }


def build_subway_tunnel_semantic(
    railways: gpd.GeoDataFrame,
    origin: tuple[float, float],
    profile: SubwayGenerationProfile = SUBWAY_CIM4_PROFILE,
    depth_by_index: dict[Any, float] | None = None,
    enabled_systems: Iterable[str] | None = None,
) -> dict[str, Any]:
    profile = subway_generation_profile(profile)
    professional_systems = subway_professional_systems_for_profile(profile, enabled_systems)
    depth_by_index = depth_by_index or assign_railway_tunnel_depths(railways)
    records = [
        subway_tunnel_semantic_record(row, idx, origin, depth_by_index, professional_systems)
        for idx, row in railways.iterrows()
        if subway_like(row)
    ]
    return {
        "project": "cim_road_poc",
        "model": subway_output_stem(profile, professional_systems),
        "generation_profile": {
            "name": profile.name,
            "mesh_granularity": profile.mesh_granularity,
            "semantic_level": profile.semantic_level,
            "generate_station_trim": profile.generate_station_trim,
            "generate_track_bed": profile.generate_track_bed,
            "professional_systems": sorted(professional_systems),
        },
        "unit": "meter",
        "coordinate": {
            "model_crs": TARGET_CRS,
            "local_origin": {"x": origin[0], "y": origin[1], "z": 0.0},
            "absolute_z_datum": "model_local_z_meter",
            "road_surface_base_z_m": ROAD_SURFACE_BASE_Z_M,
        },
        "objects": records,
        "objects_by_name": {str(record["object_name"]): record for record in records},
    }


def write_subway_tunnel_semantic(
    railways: gpd.GeoDataFrame,
    origin: tuple[float, float],
    profile: SubwayGenerationProfile = SUBWAY_CIM4_PROFILE,
    path: Path | None = None,
    depth_by_index: dict[Any, float] | None = None,
    enabled_systems: Iterable[str] | None = None,
) -> dict[str, Any]:
    profile = subway_generation_profile(profile)
    semantic = build_subway_tunnel_semantic(
        railways,
        origin,
        profile,
        depth_by_index,
        enabled_systems,
    )
    output_path = path or subway_tunnel_semantic_path_for_profile(profile, enabled_systems)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(semantic, f, ensure_ascii=False, indent=2)
    return semantic


def subway_tunnel_mesh_attribute_record(object_name: str, mesh: trimesh.Trimesh) -> dict[str, Any]:
    metadata = dict(mesh.metadata or {})
    record: dict[str, Any] = {
        "object_name": str(object_name),
        "layer_name": str(metadata.get("layer_name") or "Subway_Tunnel"),
        "component": str(metadata.get("component") or ""),
        "professional_system": str(metadata.get("professional_system") or "unclassified"),
        "mesh_vertex_count": int(len(mesh.vertices)),
        "mesh_face_count": int(len(mesh.faces)),
        "mesh_area_m2": round(float(mesh.area), 3),
        "cim_domain": str(metadata.get("cim_domain") or "subway"),
        "cim_entity_type": str(metadata.get("cim_entity_type") or "subway_interval_tunnel"),
    }
    for key in (
        "cim_generation_level",
        "cim_monomer",
        "source_subway_id",
        "line_name",
        "tunnel_side",
        "railway_tunnel_category",
        "source_index",
        "part_count",
        "tunnel_depth_m",
        "lateral_translation_x_m",
        "lateral_translation_y_m",
        "inner_clear_radius_m",
        "lining_thickness_m",
        "outer_radius_m",
        "construction_method",
        "section_profile",
        "section_source_mesh",
        "section_half",
        "section_radius_m",
        "section_circle_segment_count",
        "section_source_arc_vertex_count",
        "section_hull_vertex_count",
        "section_hull_area_m2",
        "reference_mesh",
        "reference_system",
        "reference_dimensions_m",
        "reference_benchmark_dimensions_m",
        "reference_dimension_ratios",
        "component_name_zh",
        "rule_geometry_type",
        "placement_mode",
        "placement_interval_m",
        "installation_side",
        "installation_height_relative_m",
        "geometry_source",
        "geometry_lod",
        "mileage_start_m",
        "mileage_end_m",
    ):
        if key in metadata and metadata.get(key) is not None:
            record[key] = json_safe_attribute_value(metadata.get(key))
    return record


def build_subway_tunnel_mesh_attributes(
    tunnel_meshes: dict[str, trimesh.Trimesh],
    profile: SubwayGenerationProfile = SUBWAY_CIM4_PROFILE,
) -> dict[str, Any]:
    profile = subway_generation_profile(profile)
    records = [
        subway_tunnel_mesh_attribute_record(name, mesh)
        for name, mesh in sorted(tunnel_meshes.items())
        if mesh is not None and len(mesh.vertices) > 0
    ]
    layer_counts = Counter(str(record.get("layer_name") or "unknown") for record in records)
    professional_systems = sorted(
        {
            str(record.get("professional_system"))
            for record in records
            if record.get("professional_system")
        }
    )
    generated_component_classes = {
        str(record.get("component"))
        for record in records
        if record.get("component")
    }
    return {
        "project": "cim_road_poc",
        "model": f"{subway_output_stem(profile, professional_systems)}_mesh_attributes",
        "generation_profile": {
            "name": profile.name,
            "mesh_granularity": profile.mesh_granularity,
            "semantic_level": profile.semantic_level,
            "professional_systems": professional_systems,
        },
        "policy": (
            "selected subway professional systems are generated from railway centerlines as simplified "
            "parameterized rule geometry; subway01.blend is retained only as a dimension reference"
        ),
        "object_count": len(records),
        "rule_component_class_count": len(generated_component_classes),
        "geometry_source": "procedural_parameterized_rule",
        "source_line_count": len({str(record.get("source_subway_id") or "") for record in records}),
        "layer_object_counts": dict(sorted(layer_counts.items())),
        "objects": records,
        "objects_by_name": {str(record["object_name"]): record for record in records},
    }


def write_subway_tunnel_mesh_attributes(
    tunnel_meshes: dict[str, trimesh.Trimesh],
    profile: SubwayGenerationProfile = SUBWAY_CIM4_PROFILE,
    path: Path | None = None,
) -> dict[str, Any]:
    profile = subway_generation_profile(profile)
    attributes = build_subway_tunnel_mesh_attributes(tunnel_meshes, profile)
    output_path = path or subway_tunnel_mesh_attributes_path_for_profile(profile)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(attributes, f, ensure_ascii=False, indent=2)
    return attributes


def build_subway_tunnel_source_attributes(
    railways: gpd.GeoDataFrame,
    source_attribute_columns: list[str],
    profile: SubwayGenerationProfile = SUBWAY_CIM4_PROFILE,
    depth_by_index: dict[Any, float] | None = None,
    enabled_systems: Iterable[str] | None = None,
) -> dict[str, Any]:
    profile = subway_generation_profile(profile)
    professional_systems = subway_professional_systems_for_profile(profile, enabled_systems)
    depth_by_index = depth_by_index or assign_railway_tunnel_depths(railways)
    source_columns = [column for column in source_attribute_columns if column in railways.columns]
    records: list[dict[str, Any]] = []
    for idx, row in railways.iterrows():
        if not subway_like(row):
            continue
        source_id = subway_source_id(row, idx)
        line_name = subway_line_name(row, source_id)
        object_name = subway_tunnel_object_name(row, source_id, line_name)
        component_object_names = subway_tunnel_component_object_names(
            row,
            source_id,
            line_name,
            professional_systems,
        )
        tunnel_depth_m = float(depth_by_index.get(idx, railway_tunnel_base_depth_m(row)))
        lateral_translation_x_m = safe_float(row.get("_subway_lateral_translation_x_m"), 0.0)
        lateral_translation_y_m = safe_float(row.get("_subway_lateral_translation_y_m"), 0.0)
        source_attributes = {str(column): json_safe_value(row.get(column)) for column in source_columns}
        records.append(
            {
                "object_name": object_name,
                "component_object_names": component_object_names,
                "source_subway_id": source_id,
                "line_name": line_name,
                "professional_systems": sorted(professional_systems),
                "railway_tunnel_category": railway_tunnel_category(row),
                "tunnel_depth_m": round(tunnel_depth_m, 3),
                "lateral_translation_x_m": round(lateral_translation_x_m, 3),
                "lateral_translation_y_m": round(lateral_translation_y_m, 3),
                "source_attributes": source_attributes,
            }
        )
    records_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        records_by_name[str(record.get("line_name") or "")].append(record)
    return {
        "project": "cim_road_poc",
        "model": f"{subway_output_stem(profile)}_source_attributes",
        "generation_profile": {
            "name": profile.name,
            "mesh_granularity": profile.mesh_granularity,
            "semantic_level": profile.semantic_level,
            "professional_systems": sorted(professional_systems),
        },
        "association_key": "line_name",
        "source_attribute_columns": source_columns,
        "object_count": len(records),
        "unique_name_count": len(records_by_name),
        "objects": records,
        "records_by_name": dict(sorted(records_by_name.items())),
    }


def write_subway_tunnel_source_attributes(
    railways: gpd.GeoDataFrame,
    source_attribute_columns: list[str],
    profile: SubwayGenerationProfile = SUBWAY_CIM4_PROFILE,
    path: Path | None = None,
    depth_by_index: dict[Any, float] | None = None,
    enabled_systems: Iterable[str] | None = None,
) -> dict[str, Any]:
    profile = subway_generation_profile(profile)
    attributes = build_subway_tunnel_source_attributes(
        railways,
        source_attribute_columns,
        profile,
        depth_by_index,
        enabled_systems,
    )
    output_path = path or subway_tunnel_source_attributes_path_for_profile(profile, enabled_systems)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(attributes, f, ensure_ascii=False, indent=2)
    return attributes


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


def road_generation_log(message: str) -> None:
    print(f"[roads] {message}", flush=True)


def generate_roads_only(
    level: str | RoadGenerationProfile | None = None,
    *,
    generate_trees: bool | None = None,
) -> dict[str, Any]:
    profile = road_generation_profile_with_tree_switch(level, generate_trees=generate_trees)
    road_obj_path = road_obj_path_for_profile(profile)
    road_semantic_path = road_semantic_path_for_profile(profile)
    road_classification_path = road_classification_path_for_profile(profile)
    road_mesh_attributes_path = road_mesh_attributes_path_for_profile(profile)
    road_source_attributes_path = road_source_attributes_path_for_profile(profile)
    junction_semantic_path = junction_semantic_path_for_profile(profile)
    print(f"Road generation level: {profile.name}", flush=True)
    print(f"Road tree generation: {'enabled' if profile.generate_trees else 'disabled'}", flush=True)
    print(f"[1/5] Loading road layer from {road_gen.RAW_ROADS}...", flush=True)
    roads = load_layer(road_gen.RAW_ROADS)
    source_attribute_columns = [str(column) for column in roads.columns if str(column) != str(roads.geometry.name)]
    print(f"[2/5] Localizing {len(roads)} road records...", flush=True)
    origin = compute_road_model_origin(roads)
    roads = localize(roads, origin)

    print("[3/5] Preparing road topology and junction distances...", flush=True)
    prepared_roads_for_qc = prepare_roads_for_surfaces(roads)

    print("[4/5] Building road, junction, marking, and roadside meshes...", flush=True)
    road_meshes = build_road_surface_meshes(prepared_roads_for_qc, profile)

    print("[5/5] Exporting road OBJ and semantics...", flush=True)
    MODULE_OBJ_DIR.mkdir(parents=True, exist_ok=True)
    road_obj_path.parent.mkdir(parents=True, exist_ok=True)
    if road_meshes:
        scene_from_meshes(road_meshes).export(road_obj_path)
    elif road_obj_path.exists():
        road_obj_path.unlink()

    road_semantic = write_city_road_semantic(prepared_roads_for_qc, origin, profile, road_semantic_path)
    road_classification = write_city_road_classification(road_semantic, profile, road_classification_path)
    road_mesh_attributes = write_city_road_mesh_attributes(road_meshes, profile, road_mesh_attributes_path)
    road_source_attributes = write_city_road_source_attributes(
        prepared_roads_for_qc,
        source_attribute_columns,
        profile,
        road_source_attributes_path,
    )
    junction_semantic = write_city_junction_semantic(prepared_roads_for_qc, origin, profile, junction_semantic_path)
    road_score = None
    junction_score = None
    marking_qc = None
    sidewalk_qc = None
    if RUN_GENERATION_QC:
        road_score = write_city_road_model_score(prepared_roads_for_qc, road_meshes)
        junction_score = write_city_junction_score(
            prepared_roads_for_qc,
            road_meshes,
            junction_semantic,
            profile,
        )
        marking_qc = write_city_marking_alignment_qc(prepared_roads_for_qc)
    if RUN_SIDEWALK_TOPOLOGY_QC:
        sidewalk_qc = write_city_sidewalk_topology_qc(road_meshes, profile)

    print("CIM road OBJ generated:")
    print(f"- roads OBJ: {road_obj_path if road_meshes else 'skipped (no geometry)'}")
    print(f"- road mesh layers: {len(road_meshes)}")
    print(f"- road semantic objects: {len(road_semantic['objects'])} -> {road_semantic_path}")
    print(f"- road source attribute records: {len(road_source_attributes['objects'])} -> {road_source_attributes_path}")
    print(f"- road classification groups: {road_classification['summary']['category_road_name_group_count']} -> {road_classification_path}")
    print(
        "- road mesh monomers: "
        f"{road_mesh_attributes['road_monomer_object_count']} / {road_mesh_attributes['object_count']} "
        f"-> {road_mesh_attributes_path}"
    )
    print(f"- junction semantic objects: {len(junction_semantic['objects'])} -> {junction_semantic_path}")
    if GENERATE_JUNCTION_DEBUG_MODELS:
        print(f"- separate junction debug models: {CITY_JUNCTION_DEBUG_OBJ_DIR}")
        print(f"- junction debug manifest: {CITY_JUNCTION_DEBUG_MANIFEST_PATH}")
    else:
        print("- separate junction debug models: skipped (set CIM_ROAD_EXPORT_JUNCTION_DEBUG=1 to enable)")
    if RUN_GENERATION_QC:
        print(f"- road model score: {road_score['score']} ({road_score['grade']}) -> {CITY_ROAD_SCORE_PATH}")
        print(f"- junction score: {junction_score['score']} ({junction_score['grade']}) -> {CITY_JUNCTION_SCORE_PATH}")
        print(f"- marking alignment qc: {marking_qc['score']} ({marking_qc['grade']}) -> {CITY_MARKING_QC_PATH}")
    if RUN_SIDEWALK_TOPOLOGY_QC:
        sidewalk_qc_path = road_sidewalk_qc_path_for_profile(profile)
        print(
            "- sidewalk connectivity/overlap qc: "
            f"{sidewalk_qc['summary']['issue_count']} issue(s) -> {sidewalk_qc_path}"
        )
    else:
        print("- sidewalk qc: skipped (set CIM_ROAD_RUN_SIDEWALK_QC=1 to enable)")
    if not RUN_GENERATION_QC:
        print("- qc reports: skipped (set CIM_ROAD_RUN_QC=1 to enable)")
    return {
        "road_obj_path": road_obj_path if road_meshes else None,
        "road_meshes": road_meshes,
        "road_semantic": road_semantic,
        "road_source_attributes": road_source_attributes,
        "road_classification": road_classification,
        "road_mesh_attributes": road_mesh_attributes,
        "junction_semantic": junction_semantic,
        "road_score": road_score,
        "junction_score": junction_score,
        "marking_qc": marking_qc,
        "sidewalk_qc": sidewalk_qc,
    }


def generate_subway_tunnels_only(
    level: str | SubwayGenerationProfile | None = None,
    line_filter: str | None = None,
    enabled_systems: Iterable[str] | None = None,
) -> dict[str, Any]:
    profile = subway_generation_profile(level)
    professional_systems = subway_professional_systems_for_profile(profile, enabled_systems)
    tunnel_obj_path = subway_tunnel_obj_path_for_profile(profile, professional_systems)
    tunnel_semantic_path = subway_tunnel_semantic_path_for_profile(profile, professional_systems)
    tunnel_mesh_attributes_path = subway_tunnel_mesh_attributes_path_for_profile(profile, professional_systems)
    tunnel_source_attributes_path = subway_tunnel_source_attributes_path_for_profile(profile, professional_systems)

    print(f"Subway tunnel generation level: {profile.name}", flush=True)
    print(f"Subway professional systems: {', '.join(sorted(professional_systems))}", flush=True)
    print(f"[1/4] Loading railway layer from {RAIL_LINES_PATH}...", flush=True)
    railways = load_layer(RAIL_LINES_PATH)
    roads = load_layer(road_gen.RAW_ROADS)
    (railways,) = align_layers_to_road_coordinates(roads, railways)
    source_attribute_columns = [str(column) for column in railways.columns if str(column) != str(railways.geometry.name)]

    print(f"[2/4] Localizing {len(railways)} railway records...", flush=True)
    origin = compute_road_model_origin(roads)
    railways = localize(railways, origin)
    tunnel_corridors = subway_tunnel_generation_corridors(railways)
    if line_filter:
        selected_indices = [
            idx
            for idx, row in tunnel_corridors.iterrows()
            if subway_line_matches_filter(row, idx, line_filter)
        ]
        tunnel_corridors = tunnel_corridors.loc[selected_indices].copy()
        print(
            f"[2/4] Applied subway line filter {line_filter!r}: {len(tunnel_corridors)} corridors selected.",
            flush=True,
        )
    print(
        f"[2/4] Selected {len(tunnel_corridors)} subway tunnel corridors from {len(railways)} railway records.",
        flush=True,
    )

    print("[3/4] Building subway interval tunnel meshes...", flush=True)
    tunnel_depths = assign_railway_tunnel_depths(tunnel_corridors)
    tunnel_meshes = build_subway_tunnel_meshes(
        tunnel_corridors,
        profile,
        tunnel_depths,
        professional_systems,
    )

    print("[4/4] Exporting subway tunnel OBJ and semantics...", flush=True)
    tunnel_obj_path.parent.mkdir(parents=True, exist_ok=True)
    if tunnel_meshes:
        scene_from_meshes(tunnel_meshes).export(tunnel_obj_path)
    elif tunnel_obj_path.exists():
        tunnel_obj_path.unlink()

    tunnel_semantic = write_subway_tunnel_semantic(
        tunnel_corridors,
        origin,
        profile,
        tunnel_semantic_path,
        tunnel_depths,
        professional_systems,
    )
    tunnel_mesh_attributes = write_subway_tunnel_mesh_attributes(tunnel_meshes, profile, tunnel_mesh_attributes_path)
    tunnel_source_attributes = write_subway_tunnel_source_attributes(
        tunnel_corridors,
        source_attribute_columns,
        profile,
        tunnel_source_attributes_path,
        tunnel_depths,
        professional_systems,
    )

    print("CIM subway tunnel OBJ generated:")
    print(f"- subway tunnels OBJ: {tunnel_obj_path if tunnel_meshes else 'skipped (no geometry)'}")
    print(f"- subway tunnel mesh objects: {len(tunnel_meshes)}")
    print(f"- subway tunnel semantic objects: {len(tunnel_semantic['objects'])} -> {tunnel_semantic_path}")
    print(
        "- subway tunnel mesh attributes: "
        f"{tunnel_mesh_attributes['object_count']} -> {tunnel_mesh_attributes_path}"
    )
    print(
        "- subway tunnel source attributes: "
        f"{tunnel_source_attributes['object_count']} -> {tunnel_source_attributes_path}"
    )
    return {
        "subway_tunnel_obj_path": tunnel_obj_path if tunnel_meshes else None,
        "subway_tunnel_meshes": tunnel_meshes,
        "subway_tunnel_semantic": tunnel_semantic,
        "subway_tunnel_mesh_attributes": tunnel_mesh_attributes,
        "subway_tunnel_source_attributes": tunnel_source_attributes,
    }


def build_sewer_only_utility_layer_sets(
    water_lines: gpd.GeoDataFrame,
    sewer_lines: gpd.GeoDataFrame,
    gas_lines: gpd.GeoDataFrame,
    sewer_points: gpd.GeoDataFrame,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select the reduced utility data set used for underground pipe output."""

    return (
        [{"pipe_type": "Sewer", "layer": sewer_lines}],
        [{"pipe_type": "Sewer", "layer": sewer_points}],
    )


def generate_utility_pipes_only(level: str = "cim4") -> dict[str, Any]:
    detail_level = str(level or "cim4").strip().lower()
    utility_obj_path = utility_obj_path_for_level(detail_level)
    utility_semantic_path = utility_semantic_path_for_level(detail_level)
    utility_mesh_attributes_path = utility_mesh_attributes_path_for_level(detail_level)
    utility_qc_path = utility_qc_path_for_level(detail_level)

    print(f"Utility pipe generation level: {detail_level}", flush=True)
    print(f"[1/4] Loading road and utility layers from {RAW_DIR}...", flush=True)
    roads = load_layer(road_gen.RAW_ROADS)
    sewer_lines = load_layer(SEWER_LINES_PATH)
    sewer_points = load_layer(SEWER_NODE_POINTS_PATH)
    railways = load_layer(RAIL_LINES_PATH)

    print("[2/4] Localizing utility layers...", flush=True)
    sewer_lines, sewer_points, railways = align_layers_to_road_coordinates(
        roads,
        sewer_lines,
        sewer_points,
        railways,
    )
    origin = compute_road_model_origin(roads)
    roads = localize(roads, origin)
    sewer_lines = localize(sewer_lines, origin)
    sewer_points = localize(sewer_points, origin)
    railways = localize(railways, origin)

    empty_utility_layer = gpd.GeoDataFrame(geometry=[], crs=sewer_lines.crs)
    utility_layers, utility_node_layers = build_sewer_only_utility_layer_sets(
        empty_utility_layer,
        sewer_lines,
        empty_utility_layer,
        sewer_points,
    )

    print("[3/4] Building utility pipes and MEP wells...", flush=True)
    utility_meshes, utility_records = build_utility_pipe_meshes(
        utility_layers,
        roads,
        utility_node_layers,
        detail_level,
    )
    tunnel_corridors = subway_tunnel_generation_corridors(railways)
    tunnel_depths = assign_railway_tunnel_depths(tunnel_corridors)
    subway_records = [
        subway_tunnel_semantic_record(row, idx, origin, tunnel_depths)
        for idx, row in tunnel_corridors.iterrows()
        if subway_like(row)
    ]
    subway_clearance = validate_utility_subway_vertical_clearance(
        utility_records,
        subway_records,
    )
    if not subway_clearance["vertical_order_ok"]:
        print(
            "[ERROR] Utility/subway vertical-order violations detected: "
            f"{subway_clearance['violation_count']} pair(s).",
            flush=True,
        )

    print("[4/4] Exporting utility OBJ and semantics...", flush=True)
    utility_obj_path.parent.mkdir(parents=True, exist_ok=True)
    if utility_meshes:
        scene_from_meshes(utility_meshes).export(utility_obj_path)
    elif utility_obj_path.exists():
        utility_obj_path.unlink()

    utility_semantic = write_city_utility_pipe_semantic(
        utility_records,
        origin,
        detail_level,
        utility_semantic_path,
        subway_clearance,
    )
    utility_mesh_attributes = write_city_utility_pipe_mesh_attributes(
        utility_meshes,
        utility_records,
        detail_level,
        utility_mesh_attributes_path,
    )
    utility_qc = None
    if RUN_GENERATION_QC:
        utility_qc = write_city_utility_pipe_qc(utility_records, detail_level, utility_qc_path)

    well_count = sum(1 for record in utility_records if record.get("object_type") == "MEP_Well")
    ring_replaced_count = sum(
        1 for record in utility_records if record.get("quality_flags", {}).get("source_ring_replaced_by_well")
    )
    print("CIM utility pipe OBJ generated:")
    print(f"- utility OBJ: {utility_obj_path if utility_meshes else 'skipped (no geometry)'}")
    print(f"- utility mesh objects: {len(utility_meshes)}")
    print(f"- utility semantic objects: {len(utility_semantic['objects'])} -> {utility_semantic_path}")
    print(f"- utility MEP wells: {well_count} ({ring_replaced_count} from source rings)")
    print(f"- utility mesh attributes: {utility_mesh_attributes['object_count']} -> {utility_mesh_attributes_path}")
    if RUN_GENERATION_QC:
        print(f"- utility pipe qc: {utility_qc['score']} ({utility_qc['grade']}) -> {utility_qc_path}")
    else:
        print("- qc reports: skipped (set CIM_ROAD_RUN_QC=1 to enable)")
    return {
        "utility_obj_path": utility_obj_path if utility_meshes else None,
        "utility_meshes": utility_meshes,
        "utility_semantic": utility_semantic,
        "utility_mesh_attributes": utility_mesh_attributes,
        "utility_qc": utility_qc,
    }


def main() -> None:
    print(f"[1/6] Loading raw layers from {RAW_DIR}...", flush=True)
    roads = load_layer(road_gen.RAW_ROADS)
    buildings = load_layer(BUILDINGS_PATH)
    bus_stops = load_layer(BUS_STOPS_PATH)
    railways = load_layer(RAIL_LINES_PATH)
    railway_stations = load_layer(RAIL_STATIONS_PATH)
    water_lines = load_layer(WATER_LINES_PATH)
    sewer_lines = load_layer(SEWER_LINES_PATH)
    gas_lines = load_layer(GAS_LINES_PATH)
    sewer_points = load_layer(SEWER_NODE_POINTS_PATH)
    transport = combine_layers(bus_stops, railway_stations)

    print("[2/6] Localizing layers...", flush=True)
    railways, water_lines, sewer_lines, gas_lines, sewer_points = align_layers_to_road_coordinates(
        roads,
        railways,
        water_lines,
        sewer_lines,
        gas_lines,
        sewer_points,
    )
    origin = compute_road_model_origin(roads)
    roads = localize(roads, origin)
    buildings = localize(buildings, origin)
    transport = localize(transport, origin)
    railways = localize(railways, origin)
    water_lines = localize(water_lines, origin)
    sewer_lines = localize(sewer_lines, origin)
    gas_lines = localize(gas_lines, origin)
    sewer_points = localize(sewer_points, origin)

    print("[3/6] Building transit meshes...", flush=True)
    subway_station_meshes, bus_stop_meshes = build_transit_node_meshes(transport)
    utility_layers, utility_node_layers = build_sewer_only_utility_layer_sets(
        water_lines,
        sewer_lines,
        gas_lines,
        sewer_points,
    )
    print("[4/6] Building road cross-section meshes...", flush=True)
    prepared_roads_for_qc = prepare_roads_for_surfaces(roads)
    road_meshes = build_road_surface_meshes(prepared_roads_for_qc)
    print("[5/6] Building optional rail and utility meshes...", flush=True)
    utility_meshes, utility_records = (
        build_utility_pipe_meshes(utility_layers, roads, utility_node_layers) if GENERATE_UTILITY_PIPES else ({}, [])
    )
    tunnel_corridors = subway_tunnel_generation_corridors(railways) if GENERATE_SUBWAY_TUNNELS else railways.iloc[0:0].copy()
    tunnel_depths = assign_railway_tunnel_depths(tunnel_corridors) if GENERATE_SUBWAY_TUNNELS else {}
    subway_records = [
        subway_tunnel_semantic_record(row, idx, origin, tunnel_depths)
        for idx, row in tunnel_corridors.iterrows()
        if subway_like(row)
    ]
    subway_clearance = validate_utility_subway_vertical_clearance(
        utility_records,
        subway_records,
    )
    if not subway_clearance["vertical_order_ok"]:
        print(
            "[ERROR] Utility/subway vertical-order violations detected: "
            f"{subway_clearance['violation_count']} pair(s).",
            flush=True,
        )
    modules = {
        "roads": road_meshes,
        "buildings": build_building_meshes(buildings),
        "subway_tunnels": (
            build_subway_tunnel_meshes(tunnel_corridors, depth_by_index=tunnel_depths)
            if GENERATE_SUBWAY_TUNNELS
            else {}
        ),
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
    road_classification = write_city_road_classification(road_semantic)
    road_mesh_attributes = write_city_road_mesh_attributes(road_meshes)
    junction_semantic = write_city_junction_semantic(prepared_roads_for_qc, origin)
    utility_semantic = write_city_utility_pipe_semantic(
        utility_records,
        origin,
        subway_clearance=subway_clearance,
    )
    road_score = None
    junction_score = None
    marking_qc = None
    utility_qc = None
    if RUN_GENERATION_QC:
        road_score = write_city_road_model_score(prepared_roads_for_qc, road_meshes)
        junction_score = write_city_junction_score(
            prepared_roads_for_qc,
            road_meshes,
            junction_semantic,
            profile,
        )
        marking_qc = write_city_marking_alignment_qc(prepared_roads_for_qc)
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
    print(f"- road classification groups: {road_classification['summary']['category_road_name_group_count']} -> {CITY_ROAD_CLASSIFICATION_PATH}")
    print(
        "- road mesh monomers: "
        f"{road_mesh_attributes['road_monomer_object_count']} / {road_mesh_attributes['object_count']} "
        f"-> {CITY_ROAD_MESH_ATTRIBUTES_PATH}"
    )
    print(f"- junction semantic objects: {len(junction_semantic['objects'])} -> {CITY_JUNCTION_SEMANTIC_PATH}")
    if GENERATE_JUNCTION_DEBUG_MODELS:
        print(f"- separate junction debug models: {CITY_JUNCTION_DEBUG_OBJ_DIR}")
        print(f"- junction debug manifest: {CITY_JUNCTION_DEBUG_MANIFEST_PATH}")
    else:
        print("- separate junction debug models: skipped (set CIM_ROAD_EXPORT_JUNCTION_DEBUG=1 to enable)")
    print(f"- utility semantic objects: {len(utility_semantic['objects'])} -> {CITY_UTILITY_SEMANTIC_PATH}")
    if RUN_GENERATION_QC:
        print(f"- road model score: {road_score['score']} ({road_score['grade']}) -> {CITY_ROAD_SCORE_PATH}")
        print(f"- junction score: {junction_score['score']} ({junction_score['grade']}) -> {CITY_JUNCTION_SCORE_PATH}")
        print(f"- marking alignment qc: {marking_qc['score']} ({marking_qc['grade']}) -> {CITY_MARKING_QC_PATH}")
        print(f"- utility pipe qc: {utility_qc['score']} ({utility_qc['grade']}) -> {CITY_UTILITY_QC_PATH}")
    else:
        print("- qc reports: skipped (set CIM_ROAD_RUN_QC=1 to enable)")


if __name__ == "__main__":
    main()
