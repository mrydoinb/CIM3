#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Analyze a subway template export at cross-section precision."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Iterable

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLEND = ROOT / "output" / "blend" / "modules" / "cim4" / "subway_tunnels_template.blend"
DEFAULT_REPORT = ROOT / "output" / "qc_report" / "cim4_subway_template_section_analysis.json"
DEFAULT_LINE_FILTER = "深圳地铁10号线"


def mesh_points(obj: bpy.types.Object, matrix, *, sample_limit: int | None = None) -> list[Vector]:
    vertices = obj.data.vertices
    if sample_limit is not None and len(vertices) > sample_limit:
        step = max(1, len(vertices) // sample_limit)
        return [matrix @ vertices[idx].co for idx in range(0, len(vertices), step)]
    return [matrix @ vertex.co for vertex in vertices]


def bounds(points: list[Vector]) -> dict[str, list[float]]:
    return {
        "min": [round(min(getattr(point, axis) for point in points), 6) for axis in "xyz"],
        "max": [round(max(getattr(point, axis) for point in points), 6) for axis in "xyz"],
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


def section_metrics(points: list[Vector], hull: list[tuple[float, float]]) -> dict:
    xs = [float(point.x) for point in points]
    zs = [float(point.z) for point in points]
    hull_over = [convex_outside_distance(hull, (point.x, point.z)) for point in points]
    radii = [math.hypot(point.x, point.z) for point in points]
    return {
        "section_bounds_xz_m": [round(min(xs), 6), round(max(xs), 6), round(min(zs), 6), round(max(zs), 6)],
        "max_radius_m": round(max(radii), 6),
        "max_hull_over_m": round(max(hull_over, default=0.0), 6),
        "outside_vertex_count": sum(1 for value in hull_over if value > 1e-4),
        "vertex_count": len(points),
    }


def load_blend(blend_path: Path) -> None:
    if Path(bpy.data.filepath) != blend_path:
        bpy.ops.wm.open_mainfile(filepath=str(blend_path))


def group_key(obj: bpy.types.Object) -> tuple[str, str, str, int, int]:
    return (
        str(obj.get("base_line_name") or obj.get("line_name") or ""),
        str(obj.get("source_subway_id") or ""),
        str(obj.get("tunnel_side") or ""),
        int(obj.get("template_line_index", -1)),
        int(obj.get("template_chunk_index", -1)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blend", type=Path, default=DEFAULT_BLEND)
    parser.add_argument("--line", default=os.environ.get("CIM_SUBWAY_SECTION_LINE") or DEFAULT_LINE_FILTER)
    parser.add_argument("--report", type=Path, default=Path(os.environ.get("CIM_SUBWAY_SECTION_REPORT") or DEFAULT_REPORT))
    parser.add_argument("--sample-limit", type=int, default=40000)
    args, _unknown = parser.parse_known_args()

    blend_path = args.blend.expanduser().resolve()
    if not blend_path.exists():
        raise FileNotFoundError(f"Blend file not found: {blend_path}")

    load_blend(blend_path)

    instances = [obj for obj in bpy.data.objects if obj.name.startswith("SubwayTemplate_")]
    if args.line:
        instances = [obj for obj in instances if str(obj.get("line_name") or "").find(args.line) >= 0 or str(obj.get("base_line_name") or "").find(args.line) >= 0]
    if not instances:
        raise RuntimeError(f"No template instances matched line filter: {args.line}")

    grouped: dict[tuple[str, str, str, int, int], list[bpy.types.Object]] = {}
    for obj in instances:
        grouped.setdefault(group_key(obj), []).append(obj)

    representative_chunks = {}
    for key, group in grouped.items():
        shell = next((obj for obj in group if obj.get("template_mesh") == "Mesh.004"), None)
        if shell is None:
            continue
        representative_chunks[key] = shell

    if not representative_chunks:
        raise RuntimeError("No shell instances found for the selected line.")

    shell_sample_key = sorted(representative_chunks.keys())[
        0
    ]
    shell = representative_chunks[shell_sample_key]
    shell_local_points = mesh_points(shell, shell.matrix_world.inverted() @ shell.matrix_world, sample_limit=args.sample_limit)
    shell_world_points = mesh_points(shell, shell.matrix_world, sample_limit=args.sample_limit)
    shell_hull = convex_hull((point.x, point.z) for point in shell_local_points)
    shell_local_bounds = bounds(shell_local_points)
    shell_world_bounds = bounds(shell_world_points)

    sample_group = grouped[shell_sample_key]
    reference_inverse = shell.matrix_world.inverted()
    component_records: list[dict] = []
    for obj in sorted(sample_group, key=lambda item: str(item.get("template_mesh") or "")):
        local_points = mesh_points(obj, reference_inverse @ obj.matrix_world, sample_limit=args.sample_limit)
        metrics = section_metrics(local_points, shell_hull)
        record = {
            "mesh": str(obj.get("template_mesh") or obj.name),
            "object_name": obj.name,
            "lateral_translation_x_m": round(float(obj.get("lateral_translation_x_m") or 0.0), 3),
            "lateral_translation_y_m": round(float(obj.get("lateral_translation_y_m") or 0.0), 3),
            "section": metrics,
            "sampled_vertex_count": metrics["vertex_count"],
        }
        component_records.append(record)

    sorted_violations = sorted(
        (item for item in component_records if item["section"]["max_hull_over_m"] > 1e-4 and item["mesh"] != "Mesh.004"),
        key=lambda item: item["section"]["max_hull_over_m"],
        reverse=True,
    )

    report = {
        "blend": str(blend_path),
        "line_filter": args.line,
        "total_template_instances": len(instances),
        "unique_tunnel_chunks": len(grouped),
        "representative_chunk": {
            "base_line_name": str(shell.get("base_line_name") or shell.get("line_name") or ""),
            "source_subway_id": str(shell.get("source_subway_id") or ""),
            "tunnel_side": str(shell.get("tunnel_side") or ""),
            "template_line_index": int(shell.get("template_line_index", -1)),
            "template_chunk_index": int(shell.get("template_chunk_index", -1)),
            "lateral_translation_x_m": round(float(shell.get("lateral_translation_x_m") or 0.0), 3),
            "lateral_translation_y_m": round(float(shell.get("lateral_translation_y_m") or 0.0), 3),
        },
        "shell": {
            "object_name": shell.name,
            "local_bounds_xyz_m": shell_local_bounds,
            "world_bounds_xyz_m": shell_world_bounds,
            "local_hull_vertex_count": len(shell_hull),
            "local_hull_area_m2": round(abs(polygon_area(shell_hull)), 6),
            "local_hull_points_xz_m": [[round(x, 6), round(z, 6)] for x, z in shell_hull],
        },
        "components": component_records,
        "top_violations": sorted_violations[:12],
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
