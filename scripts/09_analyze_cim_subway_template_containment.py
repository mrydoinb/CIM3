#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Analyze which subway template components fall outside the tunnel section."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Iterable

import bpy
from mathutils import Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = Path.home() / "Desktop" / "chk" / "\u5730\u94c1" / "subway01.blend"
REPORT_PATH = ROOT / "output" / "qc_report" / "cim4_subway_template_containment_analysis.json"
TARGET_RADIUS_M = 3.0045


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
        "scale": [round(float(scale.x), 6), round(float(scale.y), 6), round(float(scale.z), 6)],
    }


def mesh_points(obj: bpy.types.Object, matrix: Matrix, *, sample_limit: int | None = None) -> list[Vector]:
    vertices = obj.data.vertices
    if sample_limit is not None and len(vertices) > sample_limit:
        step = max(1, len(vertices) // sample_limit)
        return [matrix @ vertices[idx].co for idx in range(0, len(vertices), step)]
    return [matrix @ vertex.co for vertex in vertices]


def bound_box_points(obj: bpy.types.Object, matrix: Matrix) -> list[Vector]:
    return [matrix @ Vector(corner) for corner in obj.bound_box]


def section_point(point: Vector, reference_center: Vector, lateral_scale: float, vertical_scale: float) -> tuple[float, float]:
    return (
        float((point.x - reference_center.x) * lateral_scale),
        float((point.z - reference_center.z) * vertical_scale),
    )


def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def convex_hull(points: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set((round(x, 6), round(z, 6)) for x, z in points))
    if len(unique) <= 1:
        return unique
    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


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
        points = bound_box_points(obj, matrix)
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
    ordered = hull if polygon_area(hull) > 0 else list(reversed(hull))
    max_outside = 0.0
    for start, end in zip(ordered, ordered[1:] + ordered[:1]):
        edge = (end[0] - start[0], end[1] - start[1])
        edge_len = max(math.hypot(edge[0], edge[1]), 1e-9)
        signed = (edge[0] * (point[1] - start[1]) - edge[1] * (point[0] - start[0])) / edge_len
        if signed < 0.0:
            max_outside = max(max_outside, -signed)
    return max_outside


def section_metrics(
    obj: bpy.types.Object,
    matrix: Matrix,
    reference_center: Vector,
    lateral_scale: float,
    vertical_scale: float,
    hull: list[tuple[float, float]],
) -> dict:
    points = [
        section_point(point, reference_center, lateral_scale, vertical_scale)
        for point in mesh_points(obj, matrix, sample_limit=10000)
    ]
    if not points:
        points = [
            section_point(point, reference_center, lateral_scale, vertical_scale)
            for point in bound_box_points(obj, matrix)
        ]
    xs = [point[0] for point in points]
    zs = [point[1] for point in points]
    radius_values = [math.hypot(x, z) for x, z in points]
    hull_over_values = [convex_outside_distance(hull, point) for point in points]
    radius_max = max(radius_values, default=0.0)
    hull_over_max = max(hull_over_values, default=0.0)
    return {
        "section_bounds_m": [
            round(min(xs), 4),
            round(max(xs), 4),
            round(min(zs), 4),
            round(max(zs), 4),
        ],
        "max_radius_m": round(radius_max, 4),
        "circle_over_m": round(max(0.0, radius_max - TARGET_RADIUS_M), 4),
        "hull_over_m": round(hull_over_max, 4),
        "outside_vertex_count": sum(1 for value in hull_over_values if value > 1e-4),
        "vertex_count": len(points),
    }


def main() -> None:
    template_path = Path(os.environ.get("CIM_SUBWAY_TEMPLATE_BLEND") or DEFAULT_TEMPLATE)
    bpy.ops.wm.open_mainfile(filepath=str(template_path))

    template_objects = [bpy.data.objects[f"Mesh.{idx:03d}"] for idx in range(1, 42) if f"Mesh.{idx:03d}" in bpy.data.objects]
    if len(template_objects) != 41:
        raise RuntimeError(f"Expected 41 template meshes, found {len(template_objects)}")

    reference = bpy.data.objects["Mesh.004"]
    reference_min, reference_max = local_bounds(reference)
    reference_center = bounds_center(reference_min, reference_max)
    reference_half_width = max(float((reference_max - reference_min).x) * 0.5, 0.001)
    reference_half_height = max(float((reference_max - reference_min).z) * 0.5, 0.001)
    lateral_scale = TARGET_RADIUS_M / reference_half_width
    vertical_scale = TARGET_RADIUS_M / reference_half_height

    reference_inverse = reference.matrix_world.inverted()
    relative_matrices = {obj.name: reference_inverse @ obj.matrix_world.copy() for obj in template_objects}
    source_hull = convex_hull(
        (point.x, point.z)
        for point in mesh_points(reference, Matrix.Identity(4), sample_limit=40000)
    )
    component_fits: dict[str, dict] = {}
    corrected_relative_matrices: dict[str, Matrix] = {}
    for obj in template_objects:
        source_min, source_max = transformed_bounds(obj, relative_matrices[obj.name])
        if obj.name == reference.name:
            correction = Matrix.Identity(4)
            fit = {"corrected": False, "axes": [], "scale": [1.0, 1.0, 1.0]}
            corrected_matrix = relative_matrices[obj.name]
        else:
            correction, fit = fit_matrix(source_min, source_max, reference_min, reference_max)
            corrected_matrix = correction @ relative_matrices[obj.name]
            section_correction, section_fit = fit_matrix_to_section_hull(obj, corrected_matrix, source_hull)
            if section_fit["corrected"]:
                corrected_matrix = section_correction @ corrected_matrix
                fit["corrected"] = True
                fit["axes"] = list(dict.fromkeys([*fit["axes"], "section_x"]))
                fit["section_hull"] = section_fit
        component_fits[obj.name] = fit
        corrected_relative_matrices[obj.name] = corrected_matrix

    shell_points = [
        section_point(point, reference_center, lateral_scale, vertical_scale)
        for point in mesh_points(reference, Matrix.Identity(4), sample_limit=40000)
    ]
    if not shell_points:
        shell_points.extend(
            section_point(point, reference_center, lateral_scale, vertical_scale)
            for point in bound_box_points(reference, Matrix.Identity(4))
        )
    hull = convex_hull(shell_points)

    source_outside = []
    corrected_outside = []
    per_mesh = {}
    for obj in template_objects:
        source_metrics = section_metrics(
            obj,
            relative_matrices[obj.name],
            reference_center,
            lateral_scale,
            vertical_scale,
            hull,
        )
        corrected_metrics = section_metrics(
            obj,
            corrected_relative_matrices[obj.name],
            reference_center,
            lateral_scale,
            vertical_scale,
            hull,
        )
        item = {
            "fit": component_fits[obj.name],
            "source": source_metrics,
            "corrected": corrected_metrics,
        }
        per_mesh[obj.name] = item
        if obj.name != reference.name and source_metrics["hull_over_m"] > 1e-4:
            source_outside.append(
                {
                    "mesh": obj.name,
                    "hull_over_m": source_metrics["hull_over_m"],
                    "circle_over_m": source_metrics["circle_over_m"],
                    "section_bounds_m": source_metrics["section_bounds_m"],
                }
            )
        if obj.name != reference.name and corrected_metrics["hull_over_m"] > 1e-4:
            corrected_outside.append(
                {
                    "mesh": obj.name,
                    "hull_over_m": corrected_metrics["hull_over_m"],
                    "circle_over_m": corrected_metrics["circle_over_m"],
                    "section_bounds_m": corrected_metrics["section_bounds_m"],
                    "fit": component_fits[obj.name],
                }
            )

    source_outside.sort(key=lambda item: item["hull_over_m"], reverse=True)
    corrected_outside.sort(key=lambda item: item["hull_over_m"], reverse=True)
    report = {
        "template": str(template_path),
        "reference_mesh": reference.name,
        "target_radius_m": TARGET_RADIUS_M,
        "reference_center": [round(float(reference_center.x), 6), round(float(reference_center.y), 6), round(float(reference_center.z), 6)],
        "reference_half_width_m": round(reference_half_width, 6),
        "reference_half_height_m": round(reference_half_height, 6),
        "lateral_scale": round(lateral_scale, 9),
        "vertical_scale": round(vertical_scale, 9),
        "hull_vertex_count": len(hull),
        "reference_vertex_count": len(reference.data.vertices),
        "source_outside_count": len(source_outside),
        "corrected_outside_count": len(corrected_outside),
        "source_outside": source_outside,
        "corrected_outside": corrected_outside,
        "per_mesh": per_mesh,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
