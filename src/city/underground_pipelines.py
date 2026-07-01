#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Underground pipeline BIM mesh generation from Excel-derived Shapefiles."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import json
import math
import re

import geopandas as gpd
import numpy as np
import pandas as pd
import trimesh
from shapely.affinity import translate as shapely_translate
from shapely.geometry import LineString, Point

from city.mesh_utils import json_safe_value, scene_from_meshes


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SHP_DIR = ROOT / "output" / "shp" / "underground_pipelines" / "shp"
SOURCE_CRS = "EPSG:4547"

ROUND_PIPE_COLORS = {
    "Sewer": [255, 0, 255, 255],
    "Water": [255, 0, 0, 255],
    "Storm": [0, 255, 255, 255],
}
BOX_CULVERT_COLOR = [0, 255, 255, 255]
WELL_CHAMBER_COLOR = [255, 255, 255, 255]
WELL_COVER_WHITE_COLOR = [255, 255, 255, 255]
WELL_COVER_MEDIUM_GRAY_COLOR = [132, 132, 132, 255]
WELL_COVER_DARK_GRAY_COLOR = [61, 61, 61, 255]
WELL_COVER_COLORS = {
    "white": WELL_COVER_WHITE_COLOR,
    "medium_gray": WELL_COVER_MEDIUM_GRAY_COLOR,
    "dark_gray": WELL_COVER_DARK_GRAY_COLOR,
}

DATASET_LAYERS: dict[str, list[dict[str, Any]]] = {
    "ws": [
        {"layer": "ws_pipes", "kind": "round_pipe", "system": "Sewer", "label": "污水圆管"},
        {"layer": "ws_wells", "kind": "well", "system": "Sewer", "label": "污水井"},
    ],
    "sys02": [
        {"layer": "sys02_sw_pipes", "kind": "round_pipe", "system": "Sewer", "label": "污水圆管"},
        {"layer": "sys02_sw_wells", "kind": "well", "system": "Sewer", "label": "污水井"},
        {"layer": "sys02_gs_pipes", "kind": "round_pipe", "system": "Water", "label": "给水圆管"},
        {"layer": "sys02_ys_pipes", "kind": "round_pipe", "system": "Storm", "label": "雨水圆管"},
        {"layer": "sys02_ys_box", "kind": "box_culvert", "system": "Storm", "label": "雨水方涵"},
        {"layer": "sys02_ys_wells", "kind": "well", "system": "Storm", "label": "雨水井"},
    ],
}


def underground_output_level(level: str | None = None) -> str:
    value = str(level or "cim4").strip().lower()
    if value not in {"cim3", "cim4"}:
        raise ValueError(f"Unknown underground generation level: {level!r}")
    return value


def underground_obj_path(dataset: str, level: str | None = None) -> Path:
    return ROOT / "output" / "obj" / "modules" / underground_output_level(level) / f"city_underground_pipelines_{dataset}.obj"


def underground_fbx_path(dataset: str, level: str | None = None) -> Path:
    return ROOT / "output" / "fbx" / "modules" / underground_output_level(level) / f"city_underground_pipelines_{dataset}.fbx"


def underground_semantic_path(dataset: str, level: str | None = None) -> Path:
    return (
        ROOT
        / "output"
        / "semantic"
        / underground_output_level(level)
        / f"city_underground_pipelines_{dataset}_semantic.json"
    )


def underground_mesh_attributes_path(dataset: str, level: str | None = None) -> Path:
    return (
        ROOT
        / "output"
        / "semantic"
        / underground_output_level(level)
        / f"city_underground_pipelines_{dataset}_mesh_attributes.json"
    )


def underground_qc_path(dataset: str, level: str | None = None) -> Path:
    return (
        ROOT
        / "output"
        / "qc_report"
        / underground_output_level(level)
        / f"city_underground_pipelines_{dataset}_qc.json"
    )


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def as_float(value: Any, default: float | None = None) -> float | None:
    if _is_missing(value):
        return default
    try:
        return float(str(value).strip())
    except ValueError:
        return default


def parse_mm(value: Any, default: float | None = None) -> float | None:
    if _is_missing(value):
        return default
    numbers = re.findall(r"\d+(?:\.\d+)?", str(value))
    if not numbers:
        return default
    return float(numbers[0])


def parse_box_spec_mm(spec: Any) -> tuple[float, float]:
    text = str(spec or "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*[xX×＊*]\s*(\d+(?:\.\d+)?)", text)
    if not match:
        raise ValueError(f"Cannot parse box culvert spec: {spec!r}")
    return float(match.group(1)), float(match.group(2))


def round_pipe_profile_m(
    start_invert_z: float,
    end_invert_z: float,
    diameter_mm: float,
    wall_mm: float,
) -> dict[str, float]:
    inner_radius = max(float(diameter_mm) / 1000.0 / 2.0, 0.05)
    wall = max(float(wall_mm) / 1000.0, 0.0)
    outer_radius = inner_radius + wall
    return {
        "inner_radius_m": inner_radius,
        "wall_m": wall,
        "outer_radius_m": outer_radius,
        "start_center_z_m": float(start_invert_z) + inner_radius,
        "end_center_z_m": float(end_invert_z) + inner_radius,
        "start_outer_bottom_z_m": float(start_invert_z) - wall,
        "end_outer_bottom_z_m": float(end_invert_z) - wall,
        "start_outer_top_z_m": float(start_invert_z) + inner_radius * 2.0 + wall,
        "end_outer_top_z_m": float(end_invert_z) + inner_radius * 2.0 + wall,
    }


def box_culvert_profile_m(
    start_invert_z: float,
    end_invert_z: float,
    spec: Any,
    wall_mm: float,
) -> dict[str, float]:
    inner_width_mm, inner_height_mm = parse_box_spec_mm(spec)
    inner_width = inner_width_mm / 1000.0
    inner_height = inner_height_mm / 1000.0
    wall = max(float(wall_mm) / 1000.0, 0.0)
    return {
        "inner_width_m": inner_width,
        "inner_height_m": inner_height,
        "wall_m": wall,
        "outer_width_m": inner_width + wall * 2.0,
        "outer_height_m": inner_height + wall * 2.0,
        "start_center_z_m": float(start_invert_z) + inner_height / 2.0,
        "end_center_z_m": float(end_invert_z) + inner_height / 2.0,
        "start_outer_bottom_z_m": float(start_invert_z) - wall,
        "end_outer_bottom_z_m": float(end_invert_z) - wall,
        "start_outer_top_z_m": float(start_invert_z) + inner_height + wall,
        "end_outer_top_z_m": float(end_invert_z) + inner_height + wall,
    }


def well_profile_m(
    design_z: Any,
    coord_z: Any,
    depth_m: Any,
    node_spec: Any,
    length_mm: Any,
    width_mm: Any,
    system: str | None = None,
    source_layer: str | None = None,
    sys_type: Any = None,
) -> dict[str, Any]:
    top_z = as_float(design_z, None)
    if top_z is None:
        top_z = as_float(coord_z, 0.0) or 0.0
    depth = as_float(depth_m, None)
    has_explicit_depth = depth is not None and depth > 0.0
    if not has_explicit_depth:
        depth = 3.8 if system == "Storm" and source_layer == "sys02_ys_wells" else 3.0
    diameter = parse_mm(node_spec, None)
    has_explicit_diameter = diameter is not None and diameter > 0.0
    if not has_explicit_diameter:
        length = as_float(length_mm, 0.0) or 0.0
        width = as_float(width_mm, 0.0) or 0.0
        diameter = max(length, width)
    cover_radius = max((diameter or 800.0) / 1000.0 / 2.0, 0.35)
    chamber_radius = max(cover_radius * 1.8, 0.64)
    cover_thickness = 0.05
    cover_material_key = "white"
    if source_layer == "ws_wells":
        cover_radius = 0.40
        chamber_radius = 0.85
        cover_thickness = 0.04
        cover_material_key = "medium_gray"
    elif (
        system == "Storm"
        and source_layer == "sys02_ys_wells"
        and not has_explicit_depth
        and not has_explicit_diameter
        and not _is_missing(sys_type)
    ):
        cover_radius = 0.35
        chamber_radius = 0.64
        cover_thickness = 0.05
        cover_material_key = "white"
    elif system == "Storm" and source_layer == "sys02_ys_wells" and not has_explicit_depth:
        cover_radius = 0.35
        chamber_radius = 0.59
        cover_thickness = 0.10
        cover_material_key = "dark_gray"
    elif not has_explicit_diameter:
        cover_radius = 0.40
        chamber_radius = 0.85
    chamber_wall = min(0.12, max(chamber_radius - 0.05, 0.02))
    chamber_inner_radius = max(chamber_radius - chamber_wall, 0.05)
    return {
        "top_z_m": float(top_z),
        "bottom_z_m": float(top_z) - float(depth),
        "depth_m": float(depth),
        "cover_radius_m": float(cover_radius),
        "chamber_radius_m": float(chamber_radius),
        "chamber_wall_m": float(chamber_wall),
        "chamber_inner_radius_m": float(chamber_inner_radius),
        "cover_thickness_m": float(cover_thickness),
        "cover_material_key": cover_material_key,
    }


def _load_layer(path: Path) -> gpd.GeoDataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing underground Shapefile: {path}")
    layer = gpd.read_file(path)
    if layer.crs is None:
        layer = layer.set_crs(SOURCE_CRS)
    elif layer.crs.to_epsg() != 4547:
        layer = layer.to_crs(SOURCE_CRS)
    return layer[layer.geometry.notna() & ~layer.geometry.is_empty].copy()


def load_dataset_layers(dataset: str, shp_dir: Path = DEFAULT_SHP_DIR) -> dict[str, gpd.GeoDataFrame]:
    if dataset not in DATASET_LAYERS:
        raise ValueError(f"Unknown underground dataset: {dataset!r}")
    layers = {}
    for spec in DATASET_LAYERS[dataset]:
        layers[spec["layer"]] = _load_layer(shp_dir / f"{spec['layer']}.shp")
    return layers


def compute_dataset_origin(layers: dict[str, gpd.GeoDataFrame]) -> tuple[float, float]:
    bounds = [layer.total_bounds for layer in layers.values() if not layer.empty]
    if not bounds:
        return (0.0, 0.0)
    stacked = np.asarray(bounds, dtype=float)
    return (
        float((stacked[:, 0].min() + stacked[:, 2].max()) / 2.0),
        float((stacked[:, 1].min() + stacked[:, 3].max()) / 2.0),
    )


def localize_layer(layer: gpd.GeoDataFrame, origin: tuple[float, float]) -> gpd.GeoDataFrame:
    result = layer.copy()
    result["geometry"] = result.geometry.apply(
        lambda geom: shapely_translate(geom, xoff=-origin[0], yoff=-origin[1])
        if geom is not None and not geom.is_empty
        else None
    )
    return result[result.geometry.notna()].copy()


def _row_text(row: pd.Series, field: str) -> str | None:
    value = row.get(field)
    if _is_missing(value):
        return None
    return str(value)


def _record_common(row: pd.Series, layer_spec: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "src_file",
        "src_sheet",
        "elem_id",
        "sys_type",
        "node_cat",
        "node_type",
        "node_spec",
        "z_type",
        "diam_mm",
        "wall_mm",
        "pipe_len",
        "slope",
        "material",
        "spec",
        "joint",
        "laying",
        "ditch",
        "chan_type",
        "class_cd",
        "code",
    )
    return {
        "source_layer": layer_spec["layer"],
        "component_kind": layer_spec["kind"],
        "system": layer_spec["system"],
        "system_label": layer_spec["label"],
        **{field: json_safe_value(row.get(field)) for field in fields if field in row.index},
    }


def _line_endpoints(line: LineString) -> tuple[tuple[float, float], tuple[float, float]]:
    coords = list(line.coords)
    return (float(coords[0][0]), float(coords[0][1])), (float(coords[-1][0]), float(coords[-1][1]))


def _object_layer_name(layer_spec: dict[str, Any]) -> str:
    kind = layer_spec["kind"]
    system = layer_spec["system"]
    if kind == "well":
        return "Underground_Well"
    if kind == "box_culvert":
        return "Underground_Box_Culvert"
    return f"Underground_{system}_Pipe"


def _segment_frame(
    start: np.ndarray | tuple[float, float, float],
    end: np.ndarray | tuple[float, float, float],
) -> tuple[np.ndarray, np.ndarray, float, np.ndarray, np.ndarray, np.ndarray] | None:
    p0 = np.asarray(start, dtype=float)
    p1 = np.asarray(end, dtype=float)
    direction = p1 - p0
    length = float(np.linalg.norm(direction))
    if length <= 0.05:
        return None
    x_axis = direction / length
    up = np.array([0.0, 0.0, 1.0], dtype=float)
    if abs(float(np.dot(x_axis, up))) > 0.95:
        up = np.array([0.0, 1.0, 0.0], dtype=float)
    y_axis = np.cross(up, x_axis)
    y_axis = y_axis / max(float(np.linalg.norm(y_axis)), 1e-9)
    z_axis = np.cross(x_axis, y_axis)
    z_axis = z_axis / max(float(np.linalg.norm(z_axis)), 1e-9)
    return p0, p1, length, x_axis, y_axis, z_axis


def _mesh_from_hollow_rings(
    name: str,
    start: np.ndarray | tuple[float, float, float],
    end: np.ndarray | tuple[float, float, float],
    outer_ring_yz: list[tuple[float, float]],
    inner_ring_yz: list[tuple[float, float]],
    color: list[int],
) -> trimesh.Trimesh | None:
    frame = _segment_frame(start, end)
    if frame is None:
        return None
    p0, p1, _, _, y_axis, z_axis = frame
    if len(outer_ring_yz) != len(inner_ring_yz):
        raise ValueError("Outer and inner hollow rings must use the same vertex count")
    section_count = len(outer_ring_yz)
    if section_count < 3:
        raise ValueError("Hollow ring needs at least three vertices")

    vertices: list[np.ndarray] = []

    def add_ring(center: np.ndarray, ring_yz: list[tuple[float, float]]) -> list[int]:
        indices = []
        for y, z in ring_yz:
            indices.append(len(vertices))
            vertices.append(center + y_axis * float(y) + z_axis * float(z))
        return indices

    outer_start = add_ring(p0, outer_ring_yz)
    outer_end = add_ring(p1, outer_ring_yz)
    inner_start = add_ring(p0, inner_ring_yz)
    inner_end = add_ring(p1, inner_ring_yz)
    faces: list[list[int]] = []
    for index in range(section_count):
        next_index = (index + 1) % section_count
        faces.extend(
            [
                [outer_start[index], outer_end[index], outer_end[next_index]],
                [outer_start[index], outer_end[next_index], outer_start[next_index]],
                [inner_start[index], inner_end[next_index], inner_end[index]],
                [inner_start[index], inner_start[next_index], inner_end[next_index]],
                [outer_start[index], inner_start[next_index], inner_start[index]],
                [outer_start[index], outer_start[next_index], inner_start[next_index]],
                [outer_end[index], inner_end[index], inner_end[next_index]],
                [outer_end[index], inner_end[next_index], outer_end[next_index]],
            ]
        )

    mesh = trimesh.Trimesh(vertices=np.asarray(vertices, dtype=float), faces=np.asarray(faces, dtype=int), process=False)
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def _circle_ring(radius: float, sections: int) -> list[tuple[float, float]]:
    return [
        (math.cos(2.0 * math.pi * index / sections) * float(radius), math.sin(2.0 * math.pi * index / sections) * float(radius))
        for index in range(sections)
    ]


def _make_hollow_round_tube(
    name: str,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    outer_radius_m: float,
    inner_radius_m: float,
    color: list[int],
    sections: int,
) -> trimesh.Trimesh | None:
    inner_radius = max(float(inner_radius_m), 0.01)
    outer_radius = max(float(outer_radius_m), inner_radius + 0.01)
    return _mesh_from_hollow_rings(
        name,
        start,
        end,
        _circle_ring(outer_radius, sections),
        _circle_ring(inner_radius, sections),
        color,
    )


def _make_hollow_oriented_box(
    name: str,
    start: np.ndarray,
    end: np.ndarray,
    outer_width_m: float,
    outer_height_m: float,
    inner_width_m: float,
    inner_height_m: float,
    color: list[int],
) -> trimesh.Trimesh | None:
    outer_half_width = float(outer_width_m) / 2.0
    outer_half_height = float(outer_height_m) / 2.0
    inner_half_width = min(float(inner_width_m) / 2.0, outer_half_width - 0.01)
    inner_half_height = min(float(inner_height_m) / 2.0, outer_half_height - 0.01)
    outer_ring = [
        (-outer_half_width, -outer_half_height),
        (outer_half_width, -outer_half_height),
        (outer_half_width, outer_half_height),
        (-outer_half_width, outer_half_height),
    ]
    inner_ring = [
        (-inner_half_width, -inner_half_height),
        (inner_half_width, -inner_half_height),
        (inner_half_width, inner_half_height),
        (-inner_half_width, inner_half_height),
    ]
    return _mesh_from_hollow_rings(name, start, end, outer_ring, inner_ring, color)


def _make_well_meshes(name: str, center_xy: tuple[float, float], profile: dict[str, Any]) -> list[trimesh.Trimesh]:
    chamber_radius = profile["chamber_radius_m"]
    chamber_inner_radius = profile["chamber_inner_radius_m"]
    cover_radius = profile["cover_radius_m"]
    cover_thickness = profile["cover_thickness_m"]
    cover_color = WELL_COVER_COLORS.get(str(profile.get("cover_material_key")), WELL_COVER_WHITE_COLOR)
    top_z = profile["top_z_m"]
    bottom_z = profile["bottom_z_m"]
    chamber = _make_hollow_round_tube(
        f"{name}_Chamber",
        (center_xy[0], center_xy[1], bottom_z),
        (center_xy[0], center_xy[1], top_z),
        chamber_radius,
        chamber_inner_radius,
        WELL_CHAMBER_COLOR,
        sections=64,
    )
    cover = trimesh.creation.cylinder(
        radius=cover_radius,
        sections=64,
        segment=np.asarray(
            [[center_xy[0], center_xy[1], top_z], [center_xy[0], center_xy[1], top_z + cover_thickness]]
        ),
    )
    cover.metadata["name"] = f"{name}_Cover"
    cover.visual.face_colors = cover_color
    return [mesh for mesh in [chamber, cover] if mesh is not None]


def _build_round_pipe_feature(
    row: pd.Series,
    line: LineString,
    layer_spec: dict[str, Any],
    feature_index: int,
    sections: int,
) -> tuple[trimesh.Trimesh | None, dict[str, Any]]:
    diameter = as_float(row.get("diam_mm"), None)
    if diameter is None or diameter <= 0.0:
        diameter = parse_mm(row.get("spec"), 500.0) or 500.0
    wall = as_float(row.get("wall_mm"), None)
    if wall is None or wall <= 0.0:
        wall = max(diameter * 0.1, 30.0)
    start_z = as_float(row.get("start_z"), as_float(row.get("sz"), 0.0)) or 0.0
    end_z = as_float(row.get("end_z"), as_float(row.get("ez"), start_z)) or start_z
    profile = round_pipe_profile_m(start_z, end_z, diameter, wall)
    start_xy, end_xy = _line_endpoints(line)
    start = (start_xy[0], start_xy[1], profile["start_center_z_m"])
    end = (end_xy[0], end_xy[1], profile["end_center_z_m"])
    name = f"UG_{layer_spec['layer']}_{feature_index:04d}"
    color = ROUND_PIPE_COLORS[layer_spec["system"]]
    mesh = _make_hollow_round_tube(
        name,
        start,
        end,
        profile["outer_radius_m"],
        profile["inner_radius_m"],
        color,
        sections=sections,
    )
    if mesh is not None:
        mesh.metadata["name"] = name
        mesh.metadata["layer_name"] = _object_layer_name(layer_spec)
    source_length = as_float(row.get("pipe_len"), None)
    mesh_axis_length = float(line.length)

    record = {
        **_record_common(row, layer_spec),
        "object_id": name,
        "geometry_type": "round_pipe",
        "diameter_mm": round(float(diameter), 3),
        "wall_mm": round(float(wall), 3),
        "start_local_xyz_m": [round(start[0], 3), round(start[1], 3), round(start[2], 3)],
        "end_local_xyz_m": [round(end[0], 3), round(end[1], 3), round(end[2], 3)],
        "profile": {key: round(value, 4) for key, value in profile.items()},
        "length_m": round(float(source_length if source_length is not None else mesh_axis_length), 3),
        "source_length_m": round(float(source_length), 3) if source_length is not None else None,
        "mesh_axis_length_m": round(mesh_axis_length, 3),
    }
    return mesh, record


def _build_box_feature(
    row: pd.Series,
    line: LineString,
    layer_spec: dict[str, Any],
    feature_index: int,
) -> tuple[trimesh.Trimesh | None, dict[str, Any]]:
    wall = as_float(row.get("wall_mm"), 390.0) or 390.0
    spec = row.get("spec") or "1000x1000"
    start_z = as_float(row.get("start_z"), as_float(row.get("sz"), 0.0)) or 0.0
    end_z = as_float(row.get("end_z"), as_float(row.get("ez"), start_z)) or start_z
    profile = box_culvert_profile_m(start_z, end_z, spec, wall)
    start_xy, end_xy = _line_endpoints(line)
    start = np.asarray([start_xy[0], start_xy[1], profile["start_center_z_m"]], dtype=float)
    end = np.asarray([end_xy[0], end_xy[1], profile["end_center_z_m"]], dtype=float)
    name = f"UG_{layer_spec['layer']}_{feature_index:04d}"
    mesh = _make_hollow_oriented_box(
        name,
        start,
        end,
        profile["outer_width_m"],
        profile["outer_height_m"],
        profile["inner_width_m"],
        profile["inner_height_m"],
        BOX_CULVERT_COLOR,
    )
    if mesh is not None:
        mesh.metadata["layer_name"] = _object_layer_name(layer_spec)
    source_length = as_float(row.get("pipe_len"), None)
    mesh_axis_length = float(line.length)

    record = {
        **_record_common(row, layer_spec),
        "object_id": name,
        "geometry_type": "box_culvert",
        "spec": json_safe_value(spec),
        "wall_mm": round(float(wall), 3),
        "start_local_xyz_m": [round(float(start[0]), 3), round(float(start[1]), 3), round(float(start[2]), 3)],
        "end_local_xyz_m": [round(float(end[0]), 3), round(float(end[1]), 3), round(float(end[2]), 3)],
        "profile": {key: round(value, 4) for key, value in profile.items()},
        "length_m": round(float(source_length if source_length is not None else mesh_axis_length), 3),
        "source_length_m": round(float(source_length), 3) if source_length is not None else None,
        "mesh_axis_length_m": round(mesh_axis_length, 3),
    }
    return mesh, record


def _apply_mesh_metadata(
    mesh: trimesh.Trimesh,
    layer_spec: dict[str, Any],
    feature_index: int,
    component_role: str,
    layer_name: str | None = None,
) -> None:
    mesh.metadata["layer_name"] = layer_name or _object_layer_name(layer_spec)
    mesh.metadata["source_layer"] = layer_spec["layer"]
    mesh.metadata["component_kind"] = layer_spec["kind"]
    mesh.metadata["system"] = layer_spec["system"]
    mesh.metadata["feature_index"] = int(feature_index)
    mesh.metadata["component_role"] = component_role
    mesh.metadata["feature_count"] = 1


def _well_component_layer_name(component_role: str, profile: dict[str, Any]) -> str:
    if component_role == "chamber":
        return "Underground_Well_Chamber"
    cover_material_key = str(profile.get("cover_material_key") or "white")
    if cover_material_key == "medium_gray":
        return "Underground_Well_Cover_Medium_Gray"
    if cover_material_key == "dark_gray":
        return "Underground_Well_Cover_Dark_Gray"
    return "Underground_Well_Cover_White"


def _profile_for_record(profile: dict[str, Any]) -> dict[str, Any]:
    return {key: round(value, 4) if isinstance(value, (int, float)) else value for key, value in profile.items()}


def _build_well_feature(
    row: pd.Series,
    point: Point,
    layer_spec: dict[str, Any],
    feature_index: int,
) -> tuple[list[trimesh.Trimesh], dict[str, Any]]:
    profile = well_profile_m(
        row.get("design_z"),
        row.get("coord_z"),
        row.get("depth"),
        row.get("node_spec"),
        row.get("len_mm"),
        row.get("wid_mm"),
        system=layer_spec["system"],
        source_layer=layer_spec["layer"],
        sys_type=row.get("sys_type"),
    )
    name = f"UG_{layer_spec['layer']}_{feature_index:04d}"
    meshes = _make_well_meshes(name, (float(point.x), float(point.y)), profile)
    for mesh in meshes:
        component_role = str(mesh.metadata.get("name", "")).rsplit("_", 1)[-1].lower()
        _apply_mesh_metadata(
            mesh,
            layer_spec,
            feature_index,
            component_role,
            layer_name=_well_component_layer_name(component_role, profile),
        )
    record = {
        **_record_common(row, layer_spec),
        "object_id": name,
        "geometry_type": "well",
        "mesh_object_ids": [str(mesh.metadata.get("name")) for mesh in meshes],
        "center_local_xyz_m": [round(float(point.x), 3), round(float(point.y), 3), round(profile["top_z_m"], 3)],
        "profile": _profile_for_record(profile),
        "used_default_depth": _is_missing(row.get("depth")) or (as_float(row.get("depth"), 0.0) or 0.0) <= 0.0,
    }
    return meshes, record


def build_underground_pipeline_meshes(
    dataset: str,
    layers: dict[str, gpd.GeoDataFrame],
    detail_level: str = "cim4",
) -> tuple[dict[str, trimesh.Trimesh], list[dict[str, Any]]]:
    level = underground_output_level(detail_level)
    sections = 16 if level == "cim3" else 64
    meshes: dict[str, trimesh.Trimesh] = {}
    records: list[dict[str, Any]] = []
    for layer_spec in DATASET_LAYERS[dataset]:
        layer = layers[layer_spec["layer"]]
        for feature_index, row in layer.iterrows():
            geom = row.geometry
            mesh: trimesh.Trimesh | None
            record: dict[str, Any]
            if layer_spec["kind"] == "well":
                if not isinstance(geom, Point):
                    continue
                well_meshes, record = _build_well_feature(row, geom, layer_spec, int(feature_index))
                records.append(record)
                for well_mesh in well_meshes:
                    mesh_name = str(well_mesh.metadata.get("name"))
                    meshes[mesh_name] = well_mesh
                continue
            else:
                if not isinstance(geom, LineString):
                    continue
                if layer_spec["kind"] == "box_culvert":
                    mesh, record = _build_box_feature(row, geom, layer_spec, int(feature_index))
                else:
                    mesh, record = _build_round_pipe_feature(row, geom, layer_spec, int(feature_index), sections)
            records.append(record)
            if mesh is not None:
                _apply_mesh_metadata(mesh, layer_spec, int(feature_index), layer_spec["kind"])
                meshes[str(mesh.metadata.get("name"))] = mesh
    return meshes, records


def build_underground_semantic(
    dataset: str,
    records: list[dict[str, Any]],
    origin: tuple[float, float],
    detail_level: str,
) -> dict[str, Any]:
    system_counts = Counter(record.get("system") for record in records)
    kind_counts = Counter(record.get("geometry_type") for record in records)
    length_by_system: dict[str, float] = {}
    for record in records:
        system = str(record.get("system") or "Unknown")
        length_by_system[system] = length_by_system.get(system, 0.0) + float(record.get("length_m") or 0.0)
    return {
        "schema_version": "cim_city_underground_pipelines_semantic_v1",
        "dataset": dataset,
        "source_crs": SOURCE_CRS,
        "model_coordinate_mode": "local_xy_with_absolute_z",
        "origin_xy": [round(float(origin[0]), 3), round(float(origin[1]), 3)],
        "generation_level": underground_output_level(detail_level),
        "modeling_rules": {
            "round_pipe_z": "source start_z/end_z are treated as invert elevations; center_z = invert_z + diameter/2",
            "round_pipe_radius": "outer_radius = diameter/2 + wall_thickness",
            "box_culvert_z": "source start_z/end_z are treated as invert elevations; center_z = invert_z + inner_height/2",
            "well_z": "top_z = design_z when available, else source coordinate z; bottom_z = top_z - depth",
        },
        "object_count": len(records),
        "system_counts": dict(system_counts),
        "geometry_type_counts": dict(kind_counts),
        "length_m_by_system": {key: round(value, 3) for key, value in sorted(length_by_system.items())},
        "objects": records,
    }


def write_json(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def build_underground_mesh_attributes(
    dataset: str,
    meshes: dict[str, trimesh.Trimesh],
    detail_level: str,
) -> dict[str, Any]:
    objects = []
    objects_by_name = {}
    for name, mesh in sorted(meshes.items()):
        record = {
            "object_name": name,
            "layer_name": str(mesh.metadata.get("layer_name") or ""),
            "module": "underground_pipelines",
            "dataset": dataset,
            "generation_level": underground_output_level(detail_level),
            "source_layer": str(mesh.metadata.get("source_layer") or ""),
            "component_kind": str(mesh.metadata.get("component_kind") or ""),
            "component_role": str(mesh.metadata.get("component_role") or ""),
            "feature_index": int(mesh.metadata.get("feature_index") or 0),
            "system": str(mesh.metadata.get("system") or ""),
            "feature_count": int(mesh.metadata.get("feature_count") or 0),
            "vertex_count": int(len(mesh.vertices)),
            "face_count": int(len(mesh.faces)),
        }
        objects.append(record)
        objects_by_name[name] = record
    return {
        "schema_version": "cim_city_underground_pipelines_mesh_attributes_v1",
        "dataset": dataset,
        "object_count": len(objects),
        "objects": objects,
        "objects_by_name": objects_by_name,
    }


def build_underground_qc(
    dataset: str,
    records: list[dict[str, Any]],
    meshes: dict[str, trimesh.Trimesh],
) -> dict[str, Any]:
    default_depth_count = sum(1 for record in records if record.get("used_default_depth"))
    mesh_feature_count = sum(int(mesh.metadata.get("feature_count") or 0) for mesh in meshes.values())
    missing_mesh_count = max(0, len(records) - mesh_feature_count)
    z_values = []
    for record in records:
        profile = record.get("profile") or {}
        for key in ("start_outer_bottom_z_m", "end_outer_bottom_z_m", "bottom_z_m", "start_outer_top_z_m", "end_outer_top_z_m", "top_z_m"):
            value = profile.get(key)
            if isinstance(value, (int, float)):
                z_values.append(float(value))
    return {
        "dataset": dataset,
        "record_count": len(records),
        "mesh_object_count": len(meshes),
        "mesh_feature_count": mesh_feature_count,
        "missing_mesh_count": missing_mesh_count,
        "default_depth_count": default_depth_count,
        "geometry_type_counts": dict(Counter(record.get("geometry_type") for record in records)),
        "system_counts": dict(Counter(record.get("system") for record in records)),
        "z_range_m": [round(min(z_values), 3), round(max(z_values), 3)] if z_values else None,
        "status": "generated" if meshes and not missing_mesh_count else "generated_with_warnings",
    }


def generate_underground_dataset(
    dataset: str,
    detail_level: str = "cim4",
    shp_dir: Path = DEFAULT_SHP_DIR,
) -> dict[str, Any]:
    layers = load_dataset_layers(dataset, shp_dir)
    origin = compute_dataset_origin(layers)
    localized_layers = {name: localize_layer(layer, origin) for name, layer in layers.items()}
    meshes, records = build_underground_pipeline_meshes(dataset, localized_layers, detail_level)

    obj_path = underground_obj_path(dataset, detail_level)
    obj_path.parent.mkdir(parents=True, exist_ok=True)
    if meshes:
        scene_from_meshes(meshes).export(obj_path)
    elif obj_path.exists():
        obj_path.unlink()

    semantic = write_json(
        underground_semantic_path(dataset, detail_level),
        build_underground_semantic(dataset, records, origin, detail_level),
    )
    mesh_attributes = write_json(
        underground_mesh_attributes_path(dataset, detail_level),
        build_underground_mesh_attributes(dataset, meshes, detail_level),
    )
    qc = write_json(
        underground_qc_path(dataset, detail_level),
        build_underground_qc(dataset, records, meshes),
    )
    return {
        "dataset": dataset,
        "obj_path": obj_path if meshes else None,
        "semantic_path": underground_semantic_path(dataset, detail_level),
        "mesh_attributes_path": underground_mesh_attributes_path(dataset, detail_level),
        "qc_path": underground_qc_path(dataset, detail_level),
        "meshes": meshes,
        "records": records,
        "semantic": semantic,
        "mesh_attributes": mesh_attributes,
        "qc": qc,
    }


def generate_underground_pipelines(
    datasets: list[str] | None = None,
    detail_level: str = "cim4",
    shp_dir: Path = DEFAULT_SHP_DIR,
) -> dict[str, Any]:
    selected = datasets or sorted(DATASET_LAYERS)
    results = [generate_underground_dataset(dataset, detail_level, shp_dir) for dataset in selected]
    return {
        "generation_level": underground_output_level(detail_level),
        "datasets": {result["dataset"]: result for result in results},
    }
