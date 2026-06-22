#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Blender-side structural check for the template-instanced subway blend."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
BLEND_PATH = ROOT / "output" / "blend" / "modules" / "cim4" / "subway_tunnels_template.blend"
REPORT_PATH = ROOT / "output" / "qc_report" / "cim4_subway_template_blend_check.json"


def mesh_points(obj: bpy.types.Object, matrix, *, sample_limit: int | None = None) -> list[Vector]:
    vertices = obj.data.vertices
    if sample_limit is not None and len(vertices) > sample_limit:
        step = max(1, len(vertices) // sample_limit)
        return [matrix @ vertices[idx].co for idx in range(0, len(vertices), step)]
    return [matrix @ vertex.co for vertex in vertices]


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


def main() -> None:
    if Path(bpy.data.filepath) != BLEND_PATH:
        bpy.ops.wm.open_mainfile(filepath=str(BLEND_PATH))

    instances = [obj for obj in bpy.data.objects if obj.name.startswith("SubwayTemplate_")]
    by_chunk: dict[tuple[str, int, int], dict[str, bpy.types.Object]] = {}
    left_right_names: list[str] = []
    for obj in instances:
        key = (
            str(obj.get("source_subway_id") or ""),
            int(obj.get("template_line_index", -1)),
            int(obj.get("template_chunk_index", -1)),
        )
        by_chunk.setdefault(key, {})[str(obj.get("template_mesh") or "")] = obj
        if any(token in obj.name for token in ("左线", "右线", "宸︾嚎", "鍙崇嚎")):
            left_right_names.append(obj.name)

    mesh_counts: dict[str, int] = {}
    for obj in instances:
        mesh_name = str(obj.get("template_mesh") or "")
        mesh_counts[mesh_name] = mesh_counts.get(mesh_name, 0) + 1

    sample_shell = next((obj for obj in instances if obj.get("template_mesh") == "Mesh.004"), None)
    sample_shell_dims = None
    sample_scales = {}
    section_mode = None
    if sample_shell is not None:
        points = [sample_shell.matrix_world @ Vector(corner) for corner in sample_shell.bound_box]
        sample_shell_dims = [
            round(max(getattr(point, axis) for point in points) - min(getattr(point, axis) for point in points), 6)
            for axis in "xyz"
        ]
        section_mode = sample_shell.get("template_section_mode")
        sample_scales = {
            "template_lateral_scale": sample_shell.get("template_lateral_scale"),
            "template_vertical_scale": sample_shell.get("template_vertical_scale"),
            "template_chunk_length_mode": sample_shell.get("template_chunk_length_mode"),
            "template_chunk_overlap_m": sample_shell.get("template_chunk_overlap_m"),
            "template_anchor_x_m": sample_shell.get("template_anchor_x_m"),
            "template_track_anchor_x_m": sample_shell.get("template_track_anchor_x_m"),
            "template_section_mode": section_mode,
        }

    section_violations = []
    checked_components = 0
    max_section_over = 0.0
    section_hull_violations = []
    checked_hull_component_types = 0
    max_section_hull_over = 0.0
    hull_checked_chunk = None
    for key, meshes in by_chunk.items():
        reference = meshes.get("Mesh.004")
        if reference is None:
            continue
        reference_points = [Vector(corner) for corner in reference.bound_box]
        ref_min = {axis: min(getattr(point, axis) for point in reference_points) for axis in "xz"}
        ref_max = {axis: max(getattr(point, axis) for point in reference_points) for axis in "xz"}
        inverse = reference.matrix_world.inverted()
        for mesh_name, obj in meshes.items():
            if mesh_name == "Mesh.004":
                continue
            points = [inverse @ (obj.matrix_world @ Vector(corner)) for corner in obj.bound_box]
            over_by_axis = {}
            for axis in "xz":
                lower = min(getattr(point, axis) for point in points)
                upper = max(getattr(point, axis) for point in points)
                over_by_axis[axis] = max(0.0, ref_min[axis] - lower, upper - ref_max[axis])
            checked_components += 1
            max_over = max(over_by_axis.values())
            max_section_over = max(max_section_over, max_over)
            if max_over > 1e-4:
                section_violations.append(
                    {
                        "chunk": list(key),
                        "mesh": mesh_name,
                        "over_x": round(over_by_axis["x"], 6),
                        "over_z": round(over_by_axis["z"], 6),
                        "max_over": round(max_over, 6),
                    }
                )

        if hull_checked_chunk is None:
            hull_checked_chunk = list(key)
            shell_points = [(point.x, point.z) for point in mesh_points(reference, reference.matrix_world.inverted() @ reference.matrix_world, sample_limit=40000)]
            hull = convex_hull(shell_points)
            inverse = reference.matrix_world.inverted()
            for mesh_name, obj in sorted(meshes.items()):
                if mesh_name == "Mesh.004":
                    continue
                local_points = mesh_points(obj, inverse @ obj.matrix_world, sample_limit=10000)
                if not local_points:
                    local_points = [inverse @ (obj.matrix_world @ Vector(corner)) for corner in obj.bound_box]
                checked_hull_component_types += 1
                max_over = max(
                    (convex_outside_distance(hull, (point.x, point.z)) for point in local_points),
                    default=0.0,
                )
                max_section_hull_over = max(max_section_hull_over, max_over)
                if max_over > 1e-4:
                    section_hull_violations.append(
                        {
                            "chunk": list(key),
                            "mesh": mesh_name,
                            "max_hull_over": round(max_over, 6),
                        }
                    )

    section_constraints_enforced = section_mode != "preserve_source_template"
    section_failure = section_constraints_enforced and (section_violations or section_hull_violations)
    report = {
        "status": "pass" if not left_right_names and not section_failure else "fail",
        "blend": str(BLEND_PATH),
        "object_count": len(bpy.data.objects),
        "mesh_data_count": len(bpy.data.meshes),
        "template_instance_objects": len(instances),
        "unique_template_chunks": len(by_chunk),
        "template_mesh_count_keys": len(mesh_counts),
        "per_template_min": min(mesh_counts.values()) if mesh_counts else 0,
        "per_template_max": max(mesh_counts.values()) if mesh_counts else 0,
        "left_right_name_count": len(left_right_names),
        "checked_non_shell_components": checked_components,
        "section_violation_count": len(section_violations),
        "max_section_over": round(max_section_over, 6),
        "checked_hull_component_types": checked_hull_component_types,
        "section_constraints_enforced": section_constraints_enforced,
        "section_hull_violation_mesh_count": len(section_hull_violations),
        "max_section_hull_over": round(max_section_hull_over, 6),
        "hull_checked_chunk": hull_checked_chunk,
        "sample_instance": instances[0].name if instances else None,
        "sample_shell_world_dims_xyz_m": sample_shell_dims,
        "sample_scales": sample_scales,
        "section_violations": section_violations[:20],
        "section_hull_violations": section_hull_violations[:20],
        "left_right_names": left_right_names[:20],
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
