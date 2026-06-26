#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Utility pipe mesh generation, semantics, and QC."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import json
import math
import re

import geopandas as gpd
import numpy as np
import pandas as pd
import trimesh
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPoint, Point

from road import generator as road_gen
from city.mesh_utils import combine_mesh_list, cylinder_between, iter_lines, json_safe_value, offset_segment


ROOT = Path(__file__).resolve().parents[2]
SOURCE_PROJECTED_CRS = "EPSG:4547"
TARGET_CRS = road_gen.TARGET_CRS
CITY_UTILITY_SEMANTIC_PATH = ROOT / "output" / "semantic" / "cim_city_utility_pipes_semantic.json"
CITY_UTILITY_QC_PATH = ROOT / "output" / "qc_report" / "cim_city_utility_pipe_qc.json"
UTILITY_WELL_COLOR = [96, 104, 112, 255]
UTILITY_WELL_CHAMBER_DIAMETER_M = 1.0
UTILITY_WELL_NECK_DIAMETER_M = 0.8
UTILITY_WELL_COVER_DIAMETER_M = 0.7
UTILITY_WELL_COVER_THICKNESS_M = 0.08
UTILITY_WELL_DEFAULT_RADIUS_M = UTILITY_WELL_CHAMBER_DIAMETER_M * 0.5
UTILITY_WELL_MIN_RADIUS_M = UTILITY_WELL_DEFAULT_RADIUS_M
UTILITY_WELL_DEFAULT_DEPTH_M = 3.0
UTILITY_WELL_MESH_SECTIONS = 24
UTILITY_SUBWAY_HORIZONTAL_PROXIMITY_M = 1.0
UTILITY_WELL_PIPE_CONNECTION_SEARCH_M = 10.0
UTILITY_CROSS_TYPE_WELL_SEPARATION_M = UTILITY_WELL_CHAMBER_DIAMETER_M * 1.1
ABSOLUTE_Z_DATUM = "model_local_z_meter"
ROAD_SURFACE_BASE_Z_M = 3.0

UTILITY_PIPE_STANDARD_REFERENCES = {
    "GB 50289-2016": "城市工程管线综合规划规范，用于管线综合、覆土和交叉避让控制。",
    "GB 50013-2018": "室外给水设计标准，用于给水管网设计语义和管径合理性控制。",
    "GB 50014-2021": "室外排水设计标准，用于污水管网设计语义、最小管径和重力管线控制。",
    "GB 50268-2008": "给水排水管道工程施工及验收规范，用于施工验收和回填语义控制。",
}

UTILITY_PIPE_SPECS: dict[str, dict[str, Any]] = {
    "Water": {
        "label_zh": "供水",
        "default_dn_mm": 300,
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
        "default_dn_mm": 400,
        "min_dn_mm": 300,
        "cover_depth_m": 1.8,
        "min_cover_depth_m": 0.7,
        "synthetic_lateral_offset_m": 0.0,
        "material_class": "magenta_coated_gravity_pipe",
        "flow_model": "gravity",
        "color": [210, 24, 224, 255],
        "mesh_sections": 12,
        "standards": ["GB 50289-2016", "GB 50014-2021", "GB 50268-2008"],
    },
    "Gas": {
        "label_zh": "燃气",
        "default_dn_mm": 200,
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
        "default_dn_mm": 200,
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
        "default_dn_mm": 110,
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
    "Id",
    "ORIG_FID",
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


def utility_output_level(level: str | None = None) -> str:
    key = str(level or "cim4").strip().lower()
    if key not in {"cim3", "cim4"}:
        raise ValueError(f"Unknown utility generation level: {level!r}. Expected 'cim3' or 'cim4'.")
    return key


def utility_obj_path_for_level(level: str | None = None) -> Path:
    return ROOT / "output" / "obj" / "modules" / utility_output_level(level) / "city_utility_pipes.obj"


def utility_semantic_path_for_level(level: str | None = None) -> Path:
    return ROOT / "output" / "semantic" / utility_output_level(level) / "city_utility_pipes_semantic.json"


def utility_mesh_attributes_path_for_level(level: str | None = None) -> Path:
    return ROOT / "output" / "semantic" / utility_output_level(level) / "city_utility_pipes_mesh_attributes.json"


def utility_qc_path_for_level(level: str | None = None) -> Path:
    return ROOT / "output" / "qc_report" / utility_output_level(level) / "city_utility_pipe_qc.json"


def is_source_ring(line: LineString) -> bool:
    if line is None or line.is_empty or len(line.coords) < 8:
        return False
    if not line.is_ring:
        return False
    minx, miny, maxx, maxy = line.bounds
    width = float(maxx - minx)
    height = float(maxy - miny)
    if min(width, height) <= 0.2:
        return False
    ratio = max(width, height) / max(min(width, height), 1e-6)
    return ratio <= 1.35


def source_ring_radius_m(line: LineString) -> float:
    minx, miny, maxx, maxy = line.bounds
    bbox_radius = ((float(maxx - minx) + float(maxy - miny)) / 4.0)
    length_radius = float(line.length) / (2.0 * math.pi) if line.length > 0 else 0.0
    radius = bbox_radius if bbox_radius > 0 else length_radius
    if length_radius > 0 and bbox_radius > 0:
        radius = (bbox_radius + length_radius) / 2.0
    return max(radius, UTILITY_WELL_MIN_RADIUS_M)


def utility_well_depth_m(pipe_name: str | None = None) -> float:
    if pipe_name in UTILITY_PIPE_SPECS:
        spec = UTILITY_PIPE_SPECS[str(pipe_name)]
        pipe_radius = pipe_radius_m(int(spec["default_dn_mm"]), str(pipe_name))
        return max(float(spec["cover_depth_m"]) + pipe_radius * 2.0 + 0.6, UTILITY_WELL_DEFAULT_DEPTH_M)
    return UTILITY_WELL_DEFAULT_DEPTH_M


def extend_pipe_line_to_well_connections(
    coords: list[tuple[float, float]] | list[tuple[float, float, float]],
    well_centers: list[tuple[float, float]],
    search_radius_m: float = UTILITY_WELL_PIPE_CONNECTION_SEARCH_M,
) -> tuple[list[tuple[float, float]], int, list[dict[str, Any]]]:
    """Extend/snap pipe endpoints into nearby standardized utility wells.

    Source CAD/GIS linework often stops at the edge of a circular manhole
    symbol.  For the 3D model we want the pipe to visually penetrate the
    standardized well chamber instead of leaving a gap outside the well.
    """

    adjusted = [(float(coord[0]), float(coord[1])) for coord in coords]
    if len(adjusted) < 2 or not well_centers:
        return adjusted, 0, []

    normalized_wells = [(float(x), float(y)) for x, y in well_centers]
    connection_records: list[dict[str, Any]] = []

    for endpoint_index, endpoint_name in ((0, "start"), (-1, "end")):
        endpoint = np.array(adjusted[endpoint_index], dtype=float)
        nearest_center: tuple[float, float] | None = None
        nearest_distance = float("inf")
        for center in normalized_wells:
            distance = float(np.linalg.norm(endpoint - np.array(center, dtype=float)))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_center = center
        if nearest_center is None:
            continue
        if nearest_distance <= 1e-6 or nearest_distance > float(search_radius_m):
            continue
        adjusted[endpoint_index] = nearest_center
        connection_records.append(
            {
                "endpoint": endpoint_name,
                "well_center_xy": [round(nearest_center[0], 3), round(nearest_center[1], 3)],
                "extension_length_m": round(nearest_distance, 3),
            }
        )

    return adjusted, len(connection_records), connection_records


def collect_pipe_intersection_well_centers(
    layer: gpd.GeoDataFrame,
    coordinate_precision: int = 3,
    minimum_touch_count: int = 2,
    bend_deflection_degrees: float = 30.0,
    endpoint_snap_tolerance_m: float = 0.75,
) -> list[tuple[float, float]]:
    """Derive utility well centers from pipe network vertices.

    Some source utility layers, notably water supply, do not draw manholes as
    closed rings.  In those layers the network nodes are encoded directly as
    shared pipe vertices/endpoints.  A rounded XY vertex touched by two or more
    non-ring pipe features is treated as a derived well/valve-chamber center.
    """

    def rounded_xy(point: Point | tuple[float, float]) -> tuple[float, float]:
        if isinstance(point, Point):
            return (
                round(float(point.x), int(coordinate_precision)),
                round(float(point.y), int(coordinate_precision)),
            )
        return (
            round(float(point[0]), int(coordinate_precision)),
            round(float(point[1]), int(coordinate_precision)),
        )

    def add_intersection_geometry(geometry: Any, target: set[tuple[float, float]]) -> None:
        if geometry.is_empty:
            return
        if isinstance(geometry, Point):
            target.add(rounded_xy(geometry))
            return
        if isinstance(geometry, MultiPoint):
            for point in geometry.geoms:
                target.add(rounded_xy(point))
            return
        if isinstance(geometry, LineString):
            coords = list(geometry.coords)
            if coords:
                target.add(rounded_xy(coords[0]))
                target.add(rounded_xy(coords[-1]))
            return
        if isinstance(geometry, MultiLineString | GeometryCollection):
            for part in geometry.geoms:
                add_intersection_geometry(part, target)

    def is_significant_bend(
        prev_coord: tuple[float, float],
        vertex_coord: tuple[float, float],
        next_coord: tuple[float, float],
    ) -> bool:
        prev_vec = np.array(
            [float(prev_coord[0]) - float(vertex_coord[0]), float(prev_coord[1]) - float(vertex_coord[1])],
            dtype=float,
        )
        next_vec = np.array(
            [float(next_coord[0]) - float(vertex_coord[0]), float(next_coord[1]) - float(vertex_coord[1])],
            dtype=float,
        )
        prev_len = float(np.linalg.norm(prev_vec))
        next_len = float(np.linalg.norm(next_vec))
        if prev_len <= 1e-9 or next_len <= 1e-9:
            return False
        cosine = float(np.dot(prev_vec, next_vec) / (prev_len * next_len))
        angle_degrees = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
        deflection_degrees = abs(180.0 - angle_degrees)
        return deflection_degrees >= float(bend_deflection_degrees)

    touches: dict[tuple[float, float], int] = defaultdict(int)
    pipe_lines: list[LineString] = []
    bend_vertices: set[tuple[float, float]] = set()
    for _, row in layer.iterrows():
        feature_vertices: set[tuple[float, float]] = set()
        for line in iter_lines(row.geometry):
            if is_source_ring(line):
                continue
            pipe_lines.append(line)
            line_coords = [(float(coord[0]), float(coord[1])) for coord in line.coords]
            for coord in line_coords:
                feature_vertices.add(rounded_xy(coord))
            for prev_coord, vertex_coord, next_coord in zip(
                line_coords,
                line_coords[1:],
                line_coords[2:],
            ):
                if is_significant_bend(prev_coord, vertex_coord, next_coord):
                    bend_vertices.add(rounded_xy(vertex_coord))
        for vertex in feature_vertices:
            touches[vertex] += 1

    geometric_intersections: set[tuple[float, float]] = set()
    for left_index, left in enumerate(pipe_lines):
        for right in pipe_lines[left_index + 1 :]:
            left_bounds = left.bounds
            right_bounds = right.bounds
            if (
                left_bounds[2] < right_bounds[0]
                or right_bounds[2] < left_bounds[0]
                or left_bounds[3] < right_bounds[1]
                or right_bounds[3] < left_bounds[1]
            ):
                continue
            add_intersection_geometry(left.intersection(right), geometric_intersections)

    nearby_endpoint_intersections: set[tuple[float, float]] = set()

    def add_nearby_endpoint_intersections(source: LineString, target: LineString) -> None:
        if source.is_empty or target.is_empty or source.length <= 1e-9 or target.length <= 1e-9:
            return
        target_length = float(target.length)
        for endpoint in (Point(source.coords[0]), Point(source.coords[-1])):
            projected_distance = float(target.project(endpoint))
            if projected_distance <= 1e-6 or projected_distance >= target_length - 1e-6:
                continue
            projected_point = target.interpolate(projected_distance)
            if float(endpoint.distance(projected_point)) <= float(endpoint_snap_tolerance_m):
                nearby_endpoint_intersections.add(rounded_xy(projected_point))

    if endpoint_snap_tolerance_m > 0.0:
        for left_index, left in enumerate(pipe_lines):
            for right in pipe_lines[left_index + 1 :]:
                left_bounds = left.bounds
                right_bounds = right.bounds
                padding = float(endpoint_snap_tolerance_m)
                if (
                    left_bounds[2] + padding < right_bounds[0]
                    or right_bounds[2] + padding < left_bounds[0]
                    or left_bounds[3] + padding < right_bounds[1]
                    or right_bounds[3] + padding < left_bounds[1]
                ):
                    continue
                add_nearby_endpoint_intersections(left, right)
                add_nearby_endpoint_intersections(right, left)

    for point_xy in geometric_intersections:
        touches[point_xy] = max(touches[point_xy], int(minimum_touch_count))
    for point_xy in nearby_endpoint_intersections:
        touches[point_xy] = max(touches[point_xy], int(minimum_touch_count))
    for point_xy in bend_vertices:
        touches[point_xy] = max(touches[point_xy], int(minimum_touch_count))

    return sorted(
        [vertex for vertex, count in touches.items() if count >= int(minimum_touch_count)],
        key=lambda item: (item[0], item[1]),
    )


def filter_cross_type_derived_well_centers(
    derived_by_type: dict[str, list[tuple[float, float]]],
    existing_by_type: dict[str, list[tuple[float, float]]] | None = None,
    separation_m: float = UTILITY_CROSS_TYPE_WELL_SEPARATION_M,
) -> dict[str, list[tuple[float, float]]]:
    """Remove derived wells that would overlap a different utility system."""

    existing_by_type = existing_by_type or {}
    all_points: list[tuple[str, tuple[float, float], str]] = []
    for pipe_type, centers in existing_by_type.items():
        for center in centers:
            all_points.append((pipe_type, (float(center[0]), float(center[1])), "existing"))
    for pipe_type, centers in derived_by_type.items():
        for center in centers:
            all_points.append((pipe_type, (float(center[0]), float(center[1])), "derived"))

    conflicting_derived: set[tuple[str, tuple[float, float]]] = set()
    for index, (left_type, left_xy, left_kind) in enumerate(all_points):
        for right_type, right_xy, right_kind in all_points[index + 1 :]:
            if left_type == right_type:
                continue
            distance = math.hypot(left_xy[0] - right_xy[0], left_xy[1] - right_xy[1])
            if distance > float(separation_m):
                continue
            if left_kind == "derived":
                conflicting_derived.add((left_type, left_xy))
            if right_kind == "derived":
                conflicting_derived.add((right_type, right_xy))

    return {
        pipe_type: [
            center
            for center in centers
            if (pipe_type, (float(center[0]), float(center[1]))) not in conflicting_derived
        ]
        for pipe_type, centers in derived_by_type.items()
    }


def _cylinder_between_z(
    radius_m: float,
    z_bottom: float,
    z_top: float,
    center_xy: tuple[float, float],
    sections: int,
) -> trimesh.Trimesh:
    return trimesh.creation.cylinder(
        radius=float(radius_m),
        sections=int(sections),
        segment=np.array(
            [
                [float(center_xy[0]), float(center_xy[1]), float(z_bottom)],
                [float(center_xy[0]), float(center_xy[1]), float(z_top)],
            ]
        ),
    )


def _truncated_cone_between_z(
    bottom_radius_m: float,
    top_radius_m: float,
    z_bottom: float,
    z_top: float,
    center_xy: tuple[float, float],
    sections: int,
) -> trimesh.Trimesh:
    angles = np.linspace(0.0, math.tau, int(sections), endpoint=False)
    bottom = np.column_stack(
        (
            center_xy[0] + np.cos(angles) * float(bottom_radius_m),
            center_xy[1] + np.sin(angles) * float(bottom_radius_m),
            np.full(len(angles), float(z_bottom)),
        )
    )
    top = np.column_stack(
        (
            center_xy[0] + np.cos(angles) * float(top_radius_m),
            center_xy[1] + np.sin(angles) * float(top_radius_m),
            np.full(len(angles), float(z_top)),
        )
    )
    vertices = np.vstack(
        (
            bottom,
            top,
            np.array(
                [
                    [center_xy[0], center_xy[1], z_bottom],
                    [center_xy[0], center_xy[1], z_top],
                ],
                dtype=float,
            ),
        )
    )
    bottom_center_idx = int(sections) * 2
    top_center_idx = bottom_center_idx + 1
    faces: list[list[int]] = []
    for idx in range(int(sections)):
        nxt = (idx + 1) % int(sections)
        faces.extend(
            (
                [idx, nxt, int(sections) + nxt],
                [idx, int(sections) + nxt, int(sections) + idx],
                [bottom_center_idx, nxt, idx],
                [top_center_idx, int(sections) + idx, int(sections) + nxt],
            )
        )
    return trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces), process=False)


def _well_cover_detail_meshes(
    center_xy: tuple[float, float],
    cover_top_z: float,
) -> list[trimesh.Trimesh]:
    details: list[trimesh.Trimesh] = []
    cover_radius = UTILITY_WELL_COVER_DIAMETER_M * 0.5
    stripe_length = UTILITY_WELL_COVER_DIAMETER_M * 0.70
    stripe_width = 0.025
    stripe_height = 0.012
    for axis in ("x", "y"):
        for offset in (-0.18, -0.09, 0.0, 0.09, 0.18):
            extents = (
                stripe_length if axis == "x" else stripe_width,
                stripe_width if axis == "x" else stripe_length,
                stripe_height,
            )
            stripe = trimesh.creation.box(extents=extents)
            translation = (
                center_xy[0] + (0.0 if axis == "x" else offset),
                center_xy[1] + (offset if axis == "x" else 0.0),
                cover_top_z - stripe_height * 0.5,
            )
            stripe.apply_translation(translation)
            stripe.visual.face_colors = [48, 50, 52, 255]
            details.append(stripe)
    center_badge = _cylinder_between_z(
        cover_radius * 0.42,
        cover_top_z - 0.016,
        cover_top_z,
        center_xy,
        20,
    )
    center_badge.visual.face_colors = [52, 54, 56, 255]
    details.append(center_badge)
    for x_offset in (-0.16, 0.16):
        lift_marker = _cylinder_between_z(
            0.035,
            cover_top_z - 0.018,
            cover_top_z,
            (center_xy[0] + x_offset, center_xy[1]),
            12,
        )
        lift_marker.visual.face_colors = [28, 30, 32, 255]
        details.append(lift_marker)
    return details


def build_utility_well_mesh(
    name: str,
    center_xy: tuple[float, float],
    radius_m: float,
    depth_m: float,
    detail_level: str,
) -> trimesh.Trimesh:
    z_top = -0.05
    z_bottom = -abs(float(depth_m))
    chamber_radius = min(
        max(float(radius_m), UTILITY_WELL_MIN_RADIUS_M),
        UTILITY_WELL_CHAMBER_DIAMETER_M * 0.5,
    )
    cover_radius = UTILITY_WELL_COVER_DIAMETER_M * 0.5
    sections = UTILITY_WELL_MESH_SECTIONS if detail_level == "cim4" else 16
    cover_bottom_z = z_top - UTILITY_WELL_COVER_THICKNESS_M
    base_flange_diameter_m = UTILITY_WELL_CHAMBER_DIAMETER_M + 0.24
    top_shoulder_diameter_m = UTILITY_WELL_CHAMBER_DIAMETER_M + 0.10
    if detail_level == "cim3":
        body = _cylinder_between_z(chamber_radius, z_bottom, cover_bottom_z, center_xy, sections)
        cover = _cylinder_between_z(cover_radius, cover_bottom_z, z_top, center_xy, sections)
        body.visual.face_colors = UTILITY_WELL_COLOR
        cover.visual.face_colors = [58, 62, 66, 255]
        mesh = trimesh.util.concatenate([body, cover])
    else:
        base_flange_height = 0.14
        base_reveal_height = 0.07
        top_shoulder_height = 0.16
        neck_height = 0.28
        cover_recess_height = 0.035
        body_bottom_z = z_bottom + base_flange_height
        top_shoulder_bottom_z = cover_bottom_z - neck_height - top_shoulder_height
        neck_bottom_z = cover_bottom_z - neck_height
        base_foot = _cylinder_between_z(
            base_flange_diameter_m * 0.5,
            z_bottom,
            z_bottom + base_reveal_height,
            center_xy,
            sections,
        )
        base_flange = _cylinder_between_z(
            top_shoulder_diameter_m * 0.5,
            z_bottom + base_reveal_height,
            body_bottom_z,
            center_xy,
            sections,
        )
        chamber = _cylinder_between_z(chamber_radius, body_bottom_z, top_shoulder_bottom_z, center_xy, sections)
        top_shoulder = _cylinder_between_z(
            top_shoulder_diameter_m * 0.5,
            top_shoulder_bottom_z,
            neck_bottom_z,
            center_xy,
            sections,
        )
        neck = _cylinder_between_z(
            UTILITY_WELL_NECK_DIAMETER_M * 0.5,
            neck_bottom_z,
            cover_bottom_z,
            center_xy,
            sections,
        )
        cover_recess = _cylinder_between_z(
            cover_radius + 0.05,
            cover_bottom_z,
            z_top - cover_recess_height,
            center_xy,
            sections,
        )
        cover = _cylinder_between_z(cover_radius, z_top - cover_recess_height, z_top, center_xy, sections)
        base_foot.visual.face_colors = [118, 122, 126, 255]
        base_flange.visual.face_colors = [132, 136, 140, 255]
        chamber.visual.face_colors = UTILITY_WELL_COLOR
        top_shoulder.visual.face_colors = [132, 136, 140, 255]
        neck.visual.face_colors = [128, 132, 136, 255]
        cover_recess.visual.face_colors = [112, 116, 120, 255]
        cover.visual.face_colors = [62, 66, 70, 255]
        mesh = trimesh.util.concatenate(
            [base_foot, base_flange, chamber, top_shoulder, neck, cover_recess, cover]
        )
    mesh.metadata["name"] = name
    mesh.metadata["object_type"] = "MEP_Well"
    mesh.metadata["well_style"] = (
        "straight_chamber_with_raised_rims_and_flat_cover"
        if detail_level == "cim4"
        else "simplified_precast_concrete_with_flat_cover"
    )
    mesh.metadata["well_chamber_diameter_m"] = UTILITY_WELL_CHAMBER_DIAMETER_M
    mesh.metadata["well_cover_diameter_m"] = UTILITY_WELL_COVER_DIAMETER_M
    mesh.metadata["well_base_flange_diameter_m"] = base_flange_diameter_m
    mesh.metadata["well_top_shoulder_diameter_m"] = top_shoulder_diameter_m
    mesh.metadata["well_has_tapered_cone"] = detail_level != "cim4"
    mesh.metadata["well_top_cover_style"] = "plain_recessed_disc" if detail_level == "cim4" else "flat_cover"
    return mesh


def build_utility_well_semantic_record(
    source_feature_index: Any,
    source_kind: str,
    center_xy: tuple[float, float],
    radius_m: float,
    depth_m: float,
    connected_pipe_type: str | None,
    source_attributes: dict[str, Any] | None = None,
    source_rule: str = "source_node",
    source_ring_radius_m: float | None = None,
) -> dict[str, Any]:
    source_id = json_safe_value(source_feature_index)
    if isinstance(source_id, float) and source_id.is_integer():
        source_id = int(source_id)
    return {
        "object_id": f"Utility_MEP_Well_{source_id}",
        "object_type": "MEP_Well",
        "pipe_type": "MEP_Well",
        "pipe_type_zh": "机电井",
        "source_kind": source_kind,
        "source_feature_index": source_id,
        "source_attributes": source_attributes or {},
        "source_rule": source_rule,
        "connected_pipe_type": connected_pipe_type,
        "diameter_source": "source_ring_radius" if source_rule == "closed_ring_to_well" else "node_default_radius",
        "dn_mm": None,
        "radius_m": round(float(radius_m), 3),
        "well_radius_m": round(float(radius_m), 3),
        "well_diameter_m": round(float(radius_m) * 2.0, 3),
        "well_type": "straight_precast_concrete_chamber",
        "well_chamber_diameter_m": UTILITY_WELL_CHAMBER_DIAMETER_M,
        "well_neck_diameter_m": UTILITY_WELL_NECK_DIAMETER_M,
        "well_cover_diameter_m": UTILITY_WELL_COVER_DIAMETER_M,
        "well_cover_thickness_m": UTILITY_WELL_COVER_THICKNESS_M,
        "well_base_flange_diameter_m": round(UTILITY_WELL_CHAMBER_DIAMETER_M + 0.24, 3),
        "well_top_shoulder_diameter_m": round(UTILITY_WELL_CHAMBER_DIAMETER_M + 0.10, 3),
        "well_has_tapered_cone": False,
        "well_top_cover_style": "plain_recessed_disc",
        "source_ring_radius_m": round(float(source_ring_radius_m), 3)
        if source_ring_radius_m is not None
        else None,
        "source_ring_diameter_m": round(float(source_ring_radius_m) * 2.0, 3)
        if source_ring_radius_m is not None
        else None,
        "well_depth_m": round(abs(float(depth_m)), 3),
        "center_xy_m": [round(float(center_xy[0]), 3), round(float(center_xy[1]), 3)],
        "top_z_m": -0.05,
        "bottom_z_m": round(-abs(float(depth_m)), 3),
        "absolute_z_datum": ABSOLUTE_Z_DATUM,
        "road_surface_base_z_m": ROAD_SURFACE_BASE_Z_M,
        "length_m": 0.0,
        "geometry_segment_count": 1,
        "quality_flags": {
            "source_ring_replaced_by_well": source_rule == "closed_ring_to_well",
            "ring_geometry_suppressed": source_rule == "closed_ring_to_well",
            "has_source_traceability": source_kind != "unknown",
            "well_radius_from_source_ring": source_rule == "closed_ring_to_well",
        },
    }


def build_pipe_semantic_record(
    pipe_name: str,
    source_feature_index: Any,
    source_kind: str,
    dn_mm: int,
    diameter_source: str,
    length_m: float,
    segment_count: int,
    source_attributes: dict[str, Any] | None = None,
    horizontal_bounds_xy_m: tuple[float, float, float, float] | list[float] | None = None,
    well_connection_extension_count: int = 0,
    well_connection_extensions: list[dict[str, Any]] | None = None,
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
        "horizontal_bounds_xy_m": [
            round(float(value), 3) for value in horizontal_bounds_xy_m
        ]
        if horizontal_bounds_xy_m is not None
        else None,
        "absolute_z_datum": ABSOLUTE_Z_DATUM,
        "road_surface_base_z_m": ROAD_SURFACE_BASE_Z_M,
        "min_cover_depth_m": round(float(spec["min_cover_depth_m"]), 3),
        "min_dn_mm": int(spec["min_dn_mm"]),
        "material_class": spec["material_class"],
        "flow_model": spec["flow_model"],
        "standard_references": spec["standards"],
        "length_m": round(float(length_m), 3),
        "geometry_segment_count": int(segment_count),
        "well_connection_extension_count": int(well_connection_extension_count),
        "well_connection_extensions": well_connection_extensions or [],
        "quality_flags": {
            "diameter_assigned": True,
            "diameter_meets_minimum": int(max(dn_mm, int(spec["min_dn_mm"]))) >= int(spec["min_dn_mm"]),
            "cover_depth_meets_minimum": cover_depth_m >= float(spec["min_cover_depth_m"]),
            "has_source_traceability": source_kind != "unknown",
            "extends_to_mep_well": int(well_connection_extension_count) > 0,
        },
    }


def _merge_xy_bounds(
    current: tuple[float, float, float, float] | None,
    bounds: tuple[float, float, float, float] | list[float],
) -> tuple[float, float, float, float]:
    candidate = tuple(float(value) for value in bounds)
    if current is None:
        return candidate
    return (
        min(current[0], candidate[0]),
        min(current[1], candidate[1]),
        max(current[2], candidate[2]),
        max(current[3], candidate[3]),
    )


def _bounds_are_near(
    left: list[float] | tuple[float, ...],
    right: list[float] | tuple[float, ...],
    proximity_m: float,
) -> bool:
    padding = max(float(proximity_m), 0.0)
    return not (
        float(left[2]) + padding < float(right[0])
        or float(right[2]) + padding < float(left[0])
        or float(left[3]) + padding < float(right[1])
        or float(right[3]) + padding < float(left[1])
    )


def validate_utility_subway_vertical_clearance(
    utility_records: list[dict[str, Any]],
    subway_records: list[dict[str, Any]],
    horizontal_proximity_m: float = UTILITY_SUBWAY_HORIZONTAL_PROXIMITY_M,
) -> dict[str, Any]:
    checked_pair_count = 0
    violation_count = 0
    minimum_clearance: float | None = None
    violations: list[dict[str, Any]] = []
    for utility in utility_records:
        if utility.get("object_type") == "MEP_Well" or utility.get("pipe_type") == "MEP_Well":
            continue
        utility_bounds = utility.get("horizontal_bounds_xy_m")
        if not utility_bounds or utility.get("bottom_z_m") is None:
            continue
        quality_flags = utility.setdefault("quality_flags", {})
        nearby_pair_count = 0
        utility_ok = True
        utility_minimum: float | None = None
        for subway in subway_records:
            subway_bounds = subway.get("horizontal_bounds_xy_m")
            if not subway_bounds or not _bounds_are_near(utility_bounds, subway_bounds, horizontal_proximity_m):
                continue
            nearby_pair_count += 1
            checked_pair_count += 1
            tunnel_top_z_m = subway.get("tunnel_top_z_m")
            if tunnel_top_z_m is None:
                tunnel_top_z_m = float(subway["tunnel_depth_m"]) + float(subway["outer_radius_m"])
            clearance_m = float(utility["bottom_z_m"]) - float(tunnel_top_z_m)
            utility_minimum = clearance_m if utility_minimum is None else min(utility_minimum, clearance_m)
            minimum_clearance = clearance_m if minimum_clearance is None else min(minimum_clearance, clearance_m)
            if clearance_m <= 0.0:
                utility_ok = False
                violation_count += 1
                violations.append(
                    {
                        "utility_object_id": utility.get("object_id"),
                        "subway_object_name": subway.get("object_name"),
                        "utility_bottom_z_m": round(float(utility["bottom_z_m"]), 3),
                        "subway_top_z_m": round(float(tunnel_top_z_m), 3),
                        "vertical_clearance_m": round(clearance_m, 3),
                    }
                )
        quality_flags["subway_clearance_checked"] = nearby_pair_count > 0
        quality_flags["nearby_subway_pair_count"] = nearby_pair_count
        quality_flags["above_nearby_subway"] = utility_ok
        quality_flags["minimum_subway_vertical_clearance_m"] = (
            round(utility_minimum, 3) if utility_minimum is not None else None
        )
    return {
        "absolute_z_datum": ABSOLUTE_Z_DATUM,
        "road_surface_base_z_m": ROAD_SURFACE_BASE_Z_M,
        "horizontal_proximity_m": float(horizontal_proximity_m),
        "checked_pair_count": checked_pair_count,
        "violation_count": violation_count,
        "vertical_order_ok": violation_count == 0,
        "minimum_vertical_clearance_m": round(minimum_clearance, 3)
        if minimum_clearance is not None
        else None,
        "violations": violations,
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
                            LineString([start_xy, end_xy]).bounds,
                        )
                    )
    return meshes, records


def build_utility_pipe_meshes(
    utility_layers: list[dict[str, Any]],
    roads: gpd.GeoDataFrame,
    utility_node_layers: list[dict[str, Any]] | None = None,
    detail_level: str = "cim4",
) -> tuple[dict[str, trimesh.Trimesh], list[dict[str, Any]]]:
    detail_level = utility_output_level(detail_level)
    has_real_utilities = any(not item["layer"].empty for item in utility_layers)
    if not has_real_utilities:
        return build_synthetic_utility_pipe_meshes(roads)

    meshes = {}
    records: list[dict[str, Any]] = []
    well_meshes: list[trimesh.Trimesh] = []
    well_centers_by_pipe_type: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for item in utility_layers:
        pipe_name = item["pipe_type"]
        for _, row in item["layer"].iterrows():
            for line in iter_lines(row.geometry):
                if is_source_ring(line):
                    point = line.centroid
                    well_centers_by_pipe_type[pipe_name].append((float(point.x), float(point.y)))
    derived_well_centers_by_pipe_type: dict[str, list[tuple[float, float]]] = {}
    for item in utility_layers:
        pipe_name = item["pipe_type"]
        derived_centers = collect_pipe_intersection_well_centers(item["layer"])
        existing_centers = {
            (round(float(x), 3), round(float(y), 3))
            for x, y in well_centers_by_pipe_type.get(pipe_name, [])
        }
        derived_centers = [
            center
            for center in derived_centers
            if (round(float(center[0]), 3), round(float(center[1]), 3)) not in existing_centers
        ]
        if derived_centers:
            derived_well_centers_by_pipe_type[pipe_name] = derived_centers
            well_centers_by_pipe_type[pipe_name].extend(derived_centers)
    for item in utility_node_layers or []:
        pipe_name = item.get("pipe_type")
        layer = item.get("layer")
        if pipe_name is None or layer is None or layer.empty:
            continue
        for _, row in layer.iterrows():
            geom = row.geometry
            if isinstance(geom, Point) and not geom.is_empty:
                well_centers_by_pipe_type[str(pipe_name)].append((float(geom.x), float(geom.y)))

    existing_well_centers_by_pipe_type: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for pipe_name, centers in well_centers_by_pipe_type.items():
        derived_keys = {
            (round(float(x), 3), round(float(y), 3))
            for x, y in derived_well_centers_by_pipe_type.get(pipe_name, [])
        }
        for center in centers:
            key = (round(float(center[0]), 3), round(float(center[1]), 3))
            if key not in derived_keys:
                existing_well_centers_by_pipe_type[pipe_name].append(center)

    derived_well_centers_by_pipe_type = filter_cross_type_derived_well_centers(
        derived_well_centers_by_pipe_type,
        existing_well_centers_by_pipe_type,
    )
    well_centers_by_pipe_type = defaultdict(list, existing_well_centers_by_pipe_type)
    for pipe_name, centers in derived_well_centers_by_pipe_type.items():
        well_centers_by_pipe_type[pipe_name].extend(centers)

    for pipe_name, centers in derived_well_centers_by_pipe_type.items():
        for node_idx, center_xy in enumerate(centers):
            well_radius = UTILITY_WELL_DEFAULT_RADIUS_M
            well_depth = utility_well_depth_m(pipe_name)
            well_name = f"Utility_MEP_Well_{pipe_name}_Intersection_{node_idx}"
            well_meshes.append(
                build_utility_well_mesh(
                    well_name,
                    center_xy,
                    well_radius,
                    well_depth,
                    detail_level,
                )
            )
            records.append(
                build_utility_well_semantic_record(
                    f"{pipe_name}_Intersection_{node_idx}",
                    "derived_pipe_intersection",
                    center_xy,
                    well_radius,
                    well_depth,
                    pipe_name,
                    {"derivation": "shared_pipe_vertex", "touch_count_minimum": 2},
                    "pipe_intersection_to_well",
                )
            )

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
            feature_bounds: tuple[float, float, float, float] | None = None
            feature_well_extension_count = 0
            feature_well_extensions: list[dict[str, Any]] = []
            for line in iter_lines(row.geometry):
                if is_source_ring(line):
                    point = line.centroid
                    source_radius = source_ring_radius_m(line)
                    well_radius = UTILITY_WELL_DEFAULT_RADIUS_M
                    well_depth = utility_well_depth_m(pipe_name)
                    well_name = f"Utility_MEP_Well_{pipe_name}_{line_idx}"
                    well_meshes.append(
                        build_utility_well_mesh(
                            well_name,
                            (float(point.x), float(point.y)),
                            well_radius,
                            well_depth,
                            detail_level,
                        )
                    )
                    records.append(
                        build_utility_well_semantic_record(
                            f"{pipe_name}_{line_idx}",
                            "source_shp_ring",
                            (float(point.x), float(point.y)),
                            well_radius,
                            well_depth,
                            pipe_name,
                            collect_pipe_source_attributes(row),
                            "closed_ring_to_well",
                            source_radius,
                        )
                    )
                    continue
                coords, extension_count, extension_records = extend_pipe_line_to_well_connections(
                    list(line.coords),
                    well_centers_by_pipe_type.get(pipe_name, []),
                )
                line_for_mesh = LineString(coords)
                feature_length += float(line_for_mesh.length)
                feature_bounds = _merge_xy_bounds(feature_bounds, line_for_mesh.bounds)
                feature_well_extension_count += extension_count
                feature_well_extensions.extend(extension_records)
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
                        feature_bounds,
                        feature_well_extension_count,
                        feature_well_extensions,
                    )
                )
        combined = combine_mesh_list(f"Utility_{pipe_name}_All", pipe_meshes, color)
        if combined is not None:
            combined.metadata["pipe_type"] = pipe_name
            combined.metadata["feature_count"] = sum(1 for record in records if record["pipe_type"] == pipe_name)
            combined.metadata["standardized_pipe_model"] = True
            meshes[combined.metadata["name"]] = combined
    for item in utility_node_layers or []:
        pipe_name = item.get("pipe_type")
        layer = item.get("layer")
        if layer is None or layer.empty:
            continue
        for node_idx, row in layer.iterrows():
            geom = row.geometry
            point = geom if isinstance(geom, Point) else None
            if point is None or point.is_empty:
                continue
            well_radius = UTILITY_WELL_DEFAULT_RADIUS_M
            well_depth = utility_well_depth_m(pipe_name)
            well_name = f"Utility_MEP_Well_{pipe_name or 'Node'}_{node_idx}"
            well_meshes.append(
                build_utility_well_mesh(
                    well_name,
                    (float(point.x), float(point.y)),
                    well_radius,
                    well_depth,
                    detail_level,
                )
            )
            records.append(
                build_utility_well_semantic_record(
                    f"{pipe_name or 'Node'}_{node_idx}",
                    "source_shp_point",
                    (float(point.x), float(point.y)),
                    well_radius,
                    well_depth,
                    pipe_name,
                    collect_pipe_source_attributes(row),
                    "source_point_to_well",
                )
            )
    combined_wells = combine_mesh_list("Utility_MEP_Well_All", well_meshes, UTILITY_WELL_COLOR)
    if combined_wells is not None:
        combined_wells.metadata["object_type"] = "MEP_Well"
        combined_wells.metadata["feature_count"] = len(well_meshes)
        combined_wells.metadata["ring_replacement_model"] = True
        meshes[combined_wells.metadata["name"]] = combined_wells
    return meshes, records


def build_city_utility_pipe_semantic(
    utility_records: list[dict[str, Any]],
    origin: tuple[float, float],
    detail_level: str = "cim4",
    subway_clearance: dict[str, Any] | None = None,
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
        "absolute_z_datum": ABSOLUTE_Z_DATUM,
        "road_surface_base_z_m": ROAD_SURFACE_BASE_Z_M,
        "origin_xy": [round(float(origin[0]), 3), round(float(origin[1]), 3)],
        "generation_level": utility_output_level(detail_level),
        "semantic_level": "source_pipe_feature_with_standardized_3d_profile_and_mep_well_nodes",
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
        "node_modeling_rules": {
            "closed_source_ring": "do_not_generate_ring_pipe; replace with MEP well at ring centroid",
            "well_radius": f"standardized chamber radius {UTILITY_WELL_DEFAULT_RADIUS_M:.3f} m",
            "source_ring_size": "preserve source ring radius/diameter in semantics only",
            "source_point": "generate default-radius MEP well when a utility point layer is supplied",
        },
        "utility_subway_vertical_clearance": subway_clearance
        or {
            "absolute_z_datum": ABSOLUTE_Z_DATUM,
            "road_surface_base_z_m": ROAD_SURFACE_BASE_Z_M,
            "checked_pair_count": 0,
            "violation_count": 0,
            "vertical_order_ok": None,
            "status": "not_evaluated_no_subway_context",
        },
        "object_count": len(utility_records),
        "type_counts": dict(type_counts),
        "length_m_by_type": {pipe_type: round(length, 3) for pipe_type, length in sorted(length_by_type.items())},
        "objects": utility_records,
    }


def write_city_utility_pipe_semantic(
    utility_records: list[dict[str, Any]],
    origin: tuple[float, float],
    detail_level: str = "cim4",
    path: Path | None = None,
    subway_clearance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    semantic = build_city_utility_pipe_semantic(
        utility_records,
        origin,
        detail_level,
        subway_clearance,
    )
    output_path = path or utility_semantic_path_for_level(detail_level)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(semantic, f, ensure_ascii=False, indent=2)
    return semantic


def build_city_utility_pipe_mesh_attributes(
    meshes: dict[str, trimesh.Trimesh],
    utility_records: list[dict[str, Any]],
    detail_level: str = "cim4",
) -> dict[str, Any]:
    object_type_by_prefix = {
        "Utility_MEP_Well": "MEP_Well",
        "Utility_Water": "Water",
        "Utility_Sewer": "Sewer",
        "Utility_Gas": "Gas",
        "Utility_Power": "Power",
        "Utility_Telecom": "Telecom",
    }
    records_by_type = Counter(record.get("pipe_type") for record in utility_records)
    objects = []
    objects_by_name = {}
    for name, mesh in sorted(meshes.items()):
        pipe_type = str(mesh.metadata.get("pipe_type") or "")
        object_type = str(mesh.metadata.get("object_type") or "")
        if not pipe_type and not object_type:
            for prefix, value in object_type_by_prefix.items():
                if name.startswith(prefix):
                    object_type = value
                    pipe_type = value if value != "MEP_Well" else "MEP_Well"
                    break
        record = {
            "object_name": name,
            "layer_name": "Utility_MEP_Well" if object_type == "MEP_Well" else f"Utility_{pipe_type}",
            "module": "utility_pipes",
            "generation_level": utility_output_level(detail_level),
            "object_type": object_type or "Utility_Pipe",
            "pipe_type": pipe_type or None,
            "feature_count": int(mesh.metadata.get("feature_count") or records_by_type.get(pipe_type, 0) or 0),
            "vertex_count": int(len(mesh.vertices)),
            "face_count": int(len(mesh.faces)),
        }
        objects.append(record)
        objects_by_name[name] = record
    return {
        "schema_version": "cim_city_utility_pipes_mesh_attributes_v1",
        "generation_level": utility_output_level(detail_level),
        "object_count": len(objects),
        "objects": objects,
        "objects_by_name": objects_by_name,
    }


def write_city_utility_pipe_mesh_attributes(
    meshes: dict[str, trimesh.Trimesh],
    utility_records: list[dict[str, Any]],
    detail_level: str = "cim4",
    path: Path | None = None,
) -> dict[str, Any]:
    attributes = build_city_utility_pipe_mesh_attributes(meshes, utility_records, detail_level)
    output_path = path or utility_mesh_attributes_path_for_level(detail_level)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(attributes, f, ensure_ascii=False, indent=2)
    return attributes


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
    well_count = type_counts.get("MEP_Well", 0)
    ring_replaced_count = sum(
        1 for record in utility_records if record.get("quality_flags", {}).get("source_ring_replaced_by_well")
    )
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
    if ring_replaced_count:
        findings.append(
            f"Replaced {ring_replaced_count} closed source rings with MEP wells; source ring radii are used as well radii."
        )

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
        "mep_well_count": int(well_count),
        "source_ring_replaced_by_well_count": int(ring_replaced_count),
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


def write_city_utility_pipe_qc(
    utility_records: list[dict[str, Any]],
    detail_level: str = "cim4",
    path: Path | None = None,
) -> dict[str, Any]:
    report = build_city_utility_pipe_qc(utility_records)
    output_path = path or utility_qc_path_for_level(detail_level)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report
