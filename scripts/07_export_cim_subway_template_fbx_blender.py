#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Blender-side template-instanced subway tunnel exporter."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
from typing import Iterable

import bpy
from mathutils import Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = Path.home() / "Desktop" / "chk" / "\u5730\u94c1" / "subway01.blend"
PATH_JSON = ROOT / "output" / "semantic" / "cim4" / "subway_tunnel_template_paths.json"
OUTPUT_BLEND = ROOT / "output" / "blend" / "modules" / "cim4" / "subway_tunnels_template.blend"
OUTPUT_FBX = ROOT / "output" / "fbx" / "modules" / "cim4" / "subway_tunnels_template.fbx"


def token(text: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(text), flags=re.UNICODE).strip("_")
    return cleaned or fallback


def mesh_bounds_center(obj: bpy.types.Object) -> Vector:
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return Vector(((min(p.x for p in points) + max(p.x for p in points)) * 0.5,
                   (min(p.y for p in points) + max(p.y for p in points)) * 0.5,
                   (min(p.z for p in points) + max(p.z for p in points)) * 0.5))


def local_bounds(obj: bpy.types.Object) -> tuple[Vector, Vector]:
    points = [Vector(corner) for corner in obj.bound_box]
    return (
        Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points))),
        Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points))),
    )


def transformed_bounds(obj: bpy.types.Object, matrix: Matrix) -> tuple[Vector, Vector]:
    points = [matrix @ Vector(corner) for corner in obj.bound_box]
    return (
        Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points))),
        Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points))),
    )


def bounds_center(minimum: Vector, maximum: Vector) -> Vector:
    return (minimum + maximum) * 0.5


def bounds_size(minimum: Vector, maximum: Vector) -> Vector:
    return maximum - minimum


def mesh_points(obj: bpy.types.Object, matrix: Matrix, *, sample_limit: int | None = None) -> list[Vector]:
    vertices = obj.data.vertices
    if sample_limit is not None and len(vertices) > sample_limit:
        step = max(1, len(vertices) // sample_limit)
        return [matrix @ vertices[idx].co for idx in range(0, len(vertices), step)]
    return [matrix @ vertex.co for vertex in vertices]


def axis_value(vector: Vector, axis: str) -> float:
    return float(getattr(vector, axis))


def set_axis_value(vector: Vector, axis: str, value: float) -> None:
    setattr(vector, axis, float(value))


def fit_axis_to_window(
    source_min: float,
    source_max: float,
    target_min: float,
    target_max: float,
    *,
    margin: float,
) -> tuple[float, float, bool]:
    source_center = (source_min + source_max) * 0.5
    source_size = max(source_max - source_min, 1e-6)
    target_center = (target_min + target_max) * 0.5
    target_size = max((target_max - target_min) * margin, 1e-6)
    constrained_min = target_center - target_size * 0.5
    constrained_max = target_center + target_size * 0.5

    scale = 1.0
    if source_size > target_size:
        scale = target_size / source_size
    half_size = source_size * scale * 0.5
    fitted_center = source_center
    if fitted_center - half_size < constrained_min:
        fitted_center = constrained_min + half_size
    if fitted_center + half_size > constrained_max:
        fitted_center = constrained_max - half_size

    changed = (
        abs(scale - 1.0) > 1e-6
        or abs(fitted_center - source_center) > 1e-6
        or source_min < target_min - 1e-6
        or source_max > target_max + 1e-6
    )
    return scale, fitted_center, changed


def fit_matrix(
    source_min: Vector,
    source_max: Vector,
    reference_min: Vector,
    reference_max: Vector,
    *,
    axial_threshold: float = 1.25,
    section_margin: float = 0.98,
    axial_margin: float = 0.95,
) -> tuple[Matrix, dict]:
    source_center = bounds_center(source_min, source_max)
    source_size = bounds_size(source_min, source_max)
    reference_size = bounds_size(reference_min, reference_max)
    target_center = source_center.copy()
    scale = Vector((1.0, 1.0, 1.0))
    corrected_axes: list[str] = []

    for axis in ("x", "z"):
        axis_scale, axis_center, changed = fit_axis_to_window(
            axis_value(source_min, axis),
            axis_value(source_max, axis),
            axis_value(reference_min, axis),
            axis_value(reference_max, axis),
            margin=section_margin,
        )
        if changed:
            set_axis_value(scale, axis, axis_scale)
            set_axis_value(target_center, axis, axis_center)
            corrected_axes.append(axis)

    if source_size.y > reference_size.y * axial_threshold:
        axis_scale, axis_center, _changed = fit_axis_to_window(
            source_min.y,
            source_max.y,
            reference_min.y,
            reference_max.y,
            margin=axial_margin,
        )
        scale.y = axis_scale
        target_center.y = axis_center
        corrected_axes.append("y")

    matrix = (
        Matrix.Translation(target_center)
        @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
        @ Matrix.Translation(-source_center)
    )
    return matrix, {
        "corrected": bool(corrected_axes),
        "axes": corrected_axes,
        "source_size": [round(float(source_size.x), 3), round(float(source_size.y), 3), round(float(source_size.z), 3)],
        "reference_size": [
            round(float(reference_size.x), 3),
            round(float(reference_size.y), 3),
            round(float(reference_size.z), 3),
        ],
        "scale": [round(float(scale.x), 6), round(float(scale.y), 6), round(float(scale.z), 6)],
    }


def cross_2d(origin: tuple[float, float], left: tuple[float, float], right: tuple[float, float]) -> float:
    return (left[0] - origin[0]) * (right[1] - origin[1]) - (left[1] - origin[1]) * (right[0] - origin[0])


def convex_hull(points: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set((round(x, 6), round(z, 6)) for x, z in points))
    if len(unique) <= 1:
        return unique

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross_2d(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross_2d(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def polygon_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    return 0.5 * sum(
        points[idx][0] * points[(idx + 1) % len(points)][1]
        - points[(idx + 1) % len(points)][0] * points[idx][1]
        for idx in range(len(points))
    )


def convex_outside_distance(hull: list[tuple[float, float]], point: tuple[float, float]) -> float:
    if len(hull) < 3:
        return 0.0
    ordered = hull if polygon_area(hull) > 0.0 else list(reversed(hull))
    max_outside = 0.0
    for start, end in zip(ordered, ordered[1:] + ordered[:1]):
        edge = (end[0] - start[0], end[1] - start[1])
        edge_len = max(math.hypot(edge[0], edge[1]), 1e-9)
        signed = (edge[0] * (point[1] - start[1]) - edge[1] * (point[0] - start[0])) / edge_len
        if signed < 0.0:
            max_outside = max(max_outside, -signed)
    return max_outside


def hull_x_interval_at_z(hull: list[tuple[float, float]], z_value: float) -> tuple[float, float] | None:
    intersections: list[float] = []
    for start, end in zip(hull, hull[1:] + hull[:1]):
        x0, z0 = start
        x1, z1 = end
        if abs(z1 - z0) <= 1e-9:
            if abs(z_value - z0) <= 1e-6:
                intersections.extend([x0, x1])
            continue
        if min(z0, z1) - 1e-6 <= z_value <= max(z0, z1) + 1e-6:
            factor = (z_value - z0) / (z1 - z0)
            intersections.append(x0 + (x1 - x0) * factor)
    if len(intersections) < 2:
        return None
    return min(intersections), max(intersections)


def reduced_z_samples(values: list[float], hull: list[tuple[float, float]]) -> list[float]:
    if not values:
        return []
    ordered = sorted(values)
    samples = {ordered[0], ordered[-1]}
    if len(ordered) <= 64:
        samples.update(ordered)
    else:
        for idx in range(17):
            samples.add(ordered[round((len(ordered) - 1) * idx / 16)])
    for _x, z_value in hull:
        if ordered[0] - 1e-6 <= z_value <= ordered[-1] + 1e-6:
            samples.add(z_value)
    return sorted(samples)


def common_hull_x_window(
    hull: list[tuple[float, float]],
    z_values: list[float],
) -> tuple[float, float] | None:
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []
    for z_value in reduced_z_samples(z_values, hull):
        interval = hull_x_interval_at_z(hull, z_value)
        if interval is None:
            continue
        lower_bounds.append(interval[0])
        upper_bounds.append(interval[1])
    if not lower_bounds or not upper_bounds:
        return None
    lower = max(lower_bounds)
    upper = min(upper_bounds)
    if lower >= upper:
        return None
    return lower, upper


def fit_matrix_to_section_hull(
    obj: bpy.types.Object,
    matrix: Matrix,
    hull: list[tuple[float, float]],
    *,
    margin: float = 0.98,
) -> tuple[Matrix, dict]:
    points = mesh_points(obj, matrix, sample_limit=10000)
    if not points:
        points = [matrix @ Vector(corner) for corner in obj.bound_box]
    x_values = [float(point.x) for point in points]
    z_values = [float(point.z) for point in points]
    if not x_values or not z_values:
        return Matrix.Identity(4), {"corrected": False, "axis": None}
    max_hull_over = max(convex_outside_distance(hull, (point.x, point.z)) for point in points)
    if max_hull_over <= 1e-4:
        return Matrix.Identity(4), {
            "corrected": False,
            "axis": None,
            "max_hull_over": round(float(max_hull_over), 6),
        }

    window = common_hull_x_window(hull, z_values)
    if window is None:
        return Matrix.Identity(4), {"corrected": False, "axis": None}

    source_min = min(x_values)
    source_max = max(x_values)
    axis_scale, axis_center, changed = fit_axis_to_window(
        source_min,
        source_max,
        window[0],
        window[1],
        margin=margin,
    )
    if not changed:
        return Matrix.Identity(4), {
            "corrected": False,
            "axis": None,
            "x_window": [round(window[0], 3), round(window[1], 3)],
        }

    source_center = (source_min + source_max) * 0.5
    correction = (
        Matrix.Translation(Vector((axis_center, 0.0, 0.0)))
        @ Matrix.Diagonal((axis_scale, 1.0, 1.0, 1.0))
        @ Matrix.Translation(Vector((-source_center, 0.0, 0.0)))
    )
    return correction, {
        "corrected": True,
        "axis": "x",
        "source_x": [round(source_min, 3), round(source_max, 3)],
        "x_window": [round(window[0], 3), round(window[1], 3)],
        "scale": round(float(axis_scale), 6),
    }


def polyline_length(points: list[Vector]) -> float:
    return sum((b - a).length for a, b in zip(points, points[1:]))


def interpolate_polyline(points: list[Vector], distance: float) -> Vector:
    if distance <= 0.0:
        return points[0].copy()
    remaining = float(distance)
    for a, b in zip(points, points[1:]):
        segment = b - a
        length = segment.length
        if length <= 1e-6:
            continue
        if remaining <= length:
            return a + segment * (remaining / length)
        remaining -= length
    return points[-1].copy()


def tangent_at_polyline_distance(points: list[Vector], distance: float, sample_m: float) -> Vector:
    total = polyline_length(points)
    if total <= 1e-6:
        return Vector((0.0, 1.0, 0.0))
    half_window = max(float(sample_m) * 0.5, 0.5)
    before = interpolate_polyline(points, max(0.0, float(distance) - half_window))
    after = interpolate_polyline(points, min(total, float(distance) + half_window))
    tangent = after - before
    tangent.z = 0.0
    if tangent.length <= 1e-6:
        for a, b in zip(points, points[1:]):
            tangent = b - a
            tangent.z = 0.0
            if tangent.length > 1e-6:
                break
    if tangent.length <= 1e-6:
        return Vector((0.0, 1.0, 0.0))
    return tangent.normalized()


def chunk_polyline(
    points: list[Vector],
    chunk_length: float,
    overlap_m: float = 0.0,
) -> list[tuple[Vector, Vector, float]]:
    total = polyline_length(points)
    if total <= 1e-6:
        return []
    if chunk_length <= 0.0 or total <= chunk_length:
        center_distance = total * 0.5
        center = interpolate_polyline(points, center_distance)
        tangent = tangent_at_polyline_distance(points, center_distance, max(8.0, min(total * 0.5, 45.0)))
        return [(center - tangent * (total * 0.5), center + tangent * (total * 0.5), total)]
    chunks = []
    distance = 0.0
    overlap_half = max(float(overlap_m), 0.0) * 0.5
    tangent_sample_m = max(8.0, min(float(chunk_length) * 0.5, 45.0))
    while distance < total - 1e-6:
        next_distance = min(total, distance + chunk_length)
        start_distance = max(0.0, distance - (overlap_half if distance > 0.0 else 0.0))
        end_distance = min(total, next_distance + (overlap_half if next_distance < total else 0.0))
        center_distance = (start_distance + end_distance) * 0.5
        length = end_distance - start_distance
        center = interpolate_polyline(points, center_distance)
        tangent = tangent_at_polyline_distance(points, center_distance, tangent_sample_m)
        chunks.append((center - tangent * (length * 0.5), center + tangent * (length * 0.5), length))
        distance = next_distance
    return chunks


def placement_matrix(start: Vector, end: Vector, depth_m: float) -> Matrix | None:
    direction = end - start
    direction.z = 0.0
    if direction.length <= 1e-6:
        return None
    tangent = direction.normalized()
    normal = Vector((-tangent.y, tangent.x, 0.0))
    up = Vector((0.0, 0.0, 1.0))
    center = (start + end) * 0.5
    center.z = depth_m
    return Matrix((
        (normal.x, tangent.x, up.x, center.x),
        (normal.y, tangent.y, up.y, center.y),
        (normal.z, tangent.z, up.z, center.z),
        (0.0, 0.0, 0.0, 1.0),
    ))


def load_paths() -> dict:
    with PATH_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    template_path = Path(os.environ.get("CIM_SUBWAY_TEMPLATE_BLEND") or DEFAULT_TEMPLATE)
    chunk_length_env = str(os.environ.get("CIM_SUBWAY_TEMPLATE_CHUNK_LENGTH_M") or "").strip().lower()
    chunk_length_override = None if chunk_length_env in {"", "auto", "template"} else float(chunk_length_env)
    chunk_overlap_env = str(os.environ.get("CIM_SUBWAY_TEMPLATE_CHUNK_OVERLAP_M") or "2.0").strip().lower()
    chunk_overlap_m = 0.0 if chunk_overlap_env in {"", "none", "off"} else max(float(chunk_overlap_env), 0.0)
    exact_geometry = str(os.environ.get("CIM_SUBWAY_TEMPLATE_EXACT_GEOMETRY") or "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    export_fbx = str(os.environ.get("CIM_SUBWAY_TEMPLATE_EXPORT_FBX") or "0").strip().lower() in {"1", "true", "yes", "on"}

    bpy.ops.wm.open_mainfile(filepath=str(template_path))
    path_data = load_paths()

    template_objects = [bpy.data.objects[f"Mesh.{idx:03d}"] for idx in range(1, 42) if f"Mesh.{idx:03d}" in bpy.data.objects]
    if len(template_objects) != 41:
        raise RuntimeError(f"Expected 41 template meshes, found {len(template_objects)}")

    reference = bpy.data.objects["Mesh.004"]
    reference_min, reference_max = local_bounds(reference)
    reference_local_center = bounds_center(reference_min, reference_max)
    reference_axis_length = max(float((reference_max - reference_min).y), 0.001)
    reference_half_width = max(float((reference_max - reference_min).x) * 0.5, 0.001)
    reference_half_height = max(float((reference_max - reference_min).z) * 0.5, 0.001)
    target_radius = reference_half_height
    lateral_scale = 1.0
    vertical_scale = 1.0
    if exact_geometry:
        chunk_length = reference_axis_length
        chunk_overlap_m = 0.0
        chunk_length_mode = "exact_source_template"
        section_mode = "exact_source_mesh_no_scale"
    else:
        chunk_length = reference_axis_length if chunk_length_override is None else chunk_length_override
        section_mode = "preserve_source_template"
    if exact_geometry:
        pass
    elif chunk_length_override is None:
        chunk_length_mode = "template_axis_length"
    elif chunk_length_override <= 0.0:
        chunk_length_mode = "single_extended_template"
    else:
        chunk_length_mode = "override"

    reference_inverse = reference.matrix_world.inverted()
    relative_matrices = {obj.name: reference_inverse @ obj.matrix_world.copy() for obj in template_objects}
    reference_anchor_min, reference_anchor_max = reference_min, reference_max
    reference_anchor_size = bounds_size(reference_anchor_min, reference_anchor_max)
    component_fits: dict[str, dict] = {}
    corrected_relative_matrices: dict[str, Matrix] = {}
    for obj in template_objects:
        source_min, source_max = transformed_bounds(obj, relative_matrices[obj.name])
        if obj.name == reference.name:
            correction = Matrix.Identity(4)
            source_size = bounds_size(source_min, source_max)
            fit = {
                "corrected": False,
                "axes": [],
                "source_size": [
                    round(float(source_size.x), 3),
                    round(float(source_size.y), 3),
                    round(float(source_size.z), 3),
                ],
                "reference_size": [
                    round(float(reference_anchor_size.x), 3),
                    round(float(reference_anchor_size.y), 3),
                    round(float(reference_anchor_size.z), 3),
                ],
                "scale": [1.0, 1.0, 1.0],
            }
        else:
            source_size = bounds_size(source_min, source_max)
            fit = {
                "corrected": False,
                "axes": [],
                "source_size": [
                    round(float(source_size.x), 3),
                    round(float(source_size.y), 3),
                    round(float(source_size.z), 3),
                ],
                "reference_size": [
                    round(float(reference_anchor_size.x), 3),
                    round(float(reference_anchor_size.y), 3),
                    round(float(reference_anchor_size.z), 3),
                ],
                "scale": [1.0, 1.0, 1.0],
            }
        component_fits[obj.name] = fit
        corrected_relative_matrices[obj.name] = relative_matrices[obj.name]

    template_anchor_center = reference_local_center.copy()

    output_collection = bpy.data.collections.new("CIM_Subway_Template_Instances")
    bpy.context.scene.collection.children.link(output_collection)

    generated_objects: list[bpy.types.Object] = []
    chunk_total = 0
    for path in path_data.get("paths", []):
        line_name = token(path.get("line_name") or "Subway")
        source_id = token(path.get("source_subway_id") or path.get("source_index") or "Source")
        depth_m = float(path.get("tunnel_depth_m") or 0.0)
        for line_idx, line in enumerate(path.get("lines", [])):
            points = [Vector((float(x), float(y), 0.0)) for x, y in line.get("coords_xy", [])]
            if len(points) < 2:
                continue
            for chunk_idx, (start, end, length_m) in enumerate(chunk_polyline(points, chunk_length, chunk_overlap_m)):
                matrix = placement_matrix(start, end, depth_m)
                if matrix is None:
                    continue
                longitudinal_scale = 1.0 if exact_geometry else max(length_m / reference_axis_length, 0.001)
                scale = Matrix.Diagonal((lateral_scale, longitudinal_scale, vertical_scale, 1.0))
                base_matrix = matrix @ scale @ Matrix.Translation(-template_anchor_center)
                chunk_total += 1
                for template_obj in template_objects:
                    instance = template_obj.copy()
                    instance.data = template_obj.data
                    instance.animation_data_clear()
                    instance.name = f"SubwayTemplate_{line_name}_{source_id}_G{line_idx:03d}_C{chunk_idx:04d}_{template_obj.name}"
                    instance.matrix_world = base_matrix @ corrected_relative_matrices[template_obj.name]
                    instance["template_source_blend"] = str(template_path)
                    instance["template_mesh"] = template_obj.name
                    instance["source_subway_id"] = source_id
                    instance["line_name"] = line_name
                    instance["base_line_name"] = str(path.get("base_line_name") or line_name)
                    instance["tunnel_side"] = str(path.get("tunnel_side") or "")
                    instance["lateral_translation_x_m"] = round(float(path.get("lateral_translation_x_m") or 0.0), 3)
                    instance["lateral_translation_y_m"] = round(float(path.get("lateral_translation_y_m") or 0.0), 3)
                    instance["template_chunk_index"] = chunk_idx
                    instance["template_line_index"] = line_idx
                    instance["template_chunk_length_m"] = round(float(length_m), 3)
                    instance["template_chunk_length_mode"] = chunk_length_mode
                    instance["template_chunk_overlap_m"] = round(float(chunk_overlap_m), 3)
                    instance["template_reference_axis_length_m"] = round(reference_axis_length, 3)
                    instance["template_reference_half_width_m"] = round(reference_half_width, 3)
                    instance["template_reference_half_height_m"] = round(reference_half_height, 3)
                    instance["target_radius_m"] = round(target_radius, 4)
                    instance["template_lateral_scale"] = round(lateral_scale, 6)
                    instance["template_longitudinal_scale"] = round(longitudinal_scale, 6)
                    instance["template_vertical_scale"] = round(vertical_scale, 6)
                    instance["template_anchor_x_m"] = round(float(template_anchor_center.x), 3)
                    instance["template_section_mode"] = section_mode
                    instance["template_exact_geometry"] = bool(exact_geometry)
                    instance["template_placement_policy"] = "path_chunk_rigid_placement"
                    instance["template_small_component_policy"] = "source_mesh_identity_no_rule_rebuild"
                    if component_fits[template_obj.name]["corrected"]:
                        instance["template_fit_corrected"] = True
                        instance["template_fit_axes"] = ",".join(component_fits[template_obj.name]["axes"])
                    output_collection.objects.link(instance)
                    generated_objects.append(instance)

    for template_obj in template_objects:
        template_obj.hide_set(True)
        template_obj.hide_viewport = True
        template_obj.hide_render = True
        template_obj["template_role"] = "hidden_source_mesh_identity"

    for obj in bpy.data.objects:
        obj.select_set(False)
    for obj in generated_objects:
        obj.select_set(True)

    OUTPUT_BLEND.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FBX.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT_BLEND))

    if export_fbx:
        bpy.ops.export_scene.fbx(
            filepath=str(OUTPUT_FBX),
            use_selection=True,
            object_types={"MESH"},
            add_leaf_bones=False,
            bake_anim=False,
            apply_unit_scale=True,
            use_space_transform=True,
        )

    print(
        json.dumps(
            {
                "template": str(template_path),
                "paths": len(path_data.get("paths", [])),
                "chunks": chunk_total,
                "template_meshes": len(template_objects),
                "generated_objects": len(generated_objects),
                "chunk_length_m": chunk_length,
                "chunk_length_mode": chunk_length_mode,
                "chunk_overlap_m": chunk_overlap_m,
                "target_radius_m": target_radius,
                "reference_half_width_m": reference_half_width,
                "reference_half_height_m": reference_half_height,
                "lateral_scale": lateral_scale,
                "longitudinal_scale_mode": "identity" if exact_geometry else "fit_chunk_length",
                "vertical_scale": vertical_scale,
                "exact_geometry": exact_geometry,
                "placement_policy": "path_chunk_rigid_placement",
                "small_component_policy": "source_mesh_identity_no_rule_rebuild",
                "anchor_x_m": float(template_anchor_center.x),
                "section_mode": section_mode,
                "fit_corrected_templates": {
                    name: fit for name, fit in component_fits.items() if fit["corrected"]
                },
                "blend": str(OUTPUT_BLEND),
                "fbx": str(OUTPUT_FBX) if export_fbx else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
