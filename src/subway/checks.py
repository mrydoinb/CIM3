#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Checks for generated CIM subway tunnel corridors."""

from __future__ import annotations

from pathlib import Path
import json
import math
from typing import Any

import geopandas as gpd

from city.pipeline import (
    ROOT,
    RAIL_LINES_PATH,
    SUBWAY_TUNNEL_LINING_THICKNESS_M,
    SUBWAY_TUNNEL_OVERLAP_CLEARANCE_M,
    SUBWAY_TUNNEL_OUTER_RADIUS_M,
    SUBWAY_TUNNEL_RADIUS_M,
    SUBWAY_SAME_LINE_TRACK_SPACING_M,
    apply_subway_lateral_translations,
    assign_subway_lateral_translations,
    assign_railway_tunnel_depths,
    compute_origin,
    load_layer,
    localize,
    subway_like,
    subway_line_name,
    subway_source_id,
    railway_tunnels_need_vertical_separation,
    subway_tunnel_generation_corridors,
)


SUBWAY_TUNNEL_SEPARATION_CHECK_PATH = (
    ROOT / "output" / "qc_report" / "cim4_subway_tunnel_separation_check.json"
)


def tunnel_record(row, idx: Any, depth_m: float) -> dict[str, Any]:
    source_id = subway_source_id(row, idx)
    line_name = subway_line_name(row, source_id)
    return {
        "source_index": str(idx),
        "source_subway_id": source_id,
        "line_name": line_name,
        "base_line_name": str(row.get("_subway_line_name") or line_name),
        "tunnel_side": str(row.get("_subway_tunnel_side") or ""),
        "depth_m": round(float(depth_m), 3),
        "length_m": round(float(row.geometry.length), 3) if row.geometry is not None and not row.geometry.is_empty else 0.0,
    }


def build_subway_tunnel_separation_check(railways: gpd.GeoDataFrame) -> dict[str, Any]:
    subway_candidates = railways[[subway_like(row) for _, row in railways.iterrows()]].copy()
    tunnel_corridors = subway_tunnel_generation_corridors(railways)
    depths = assign_railway_tunnel_depths(tunnel_corridors)
    records = [
        {
            **tunnel_record(row, idx, depths.get(idx, 0.0)),
            "lateral_translation_x_m": round(float(row.get("_subway_lateral_translation_x_m", 0.0)), 3),
            "lateral_translation_y_m": round(float(row.get("_subway_lateral_translation_y_m", 0.0)), 3),
            "geometry": row.geometry,
        }
        for idx, row in tunnel_corridors.iterrows()
    ]

    min_clear_center_distance = 2.0 * SUBWAY_TUNNEL_OUTER_RADIUS_M + SUBWAY_TUNNEL_OVERLAP_CLEARANCE_M
    issues: list[dict[str, Any]] = []
    same_line_issues: list[dict[str, Any]] = []
    min_3d_clearance_m: float | None = None

    for line_name, group in subway_candidates.groupby(
        subway_candidates.apply(lambda row: subway_line_name(row, subway_source_id(row, row.name)), axis=1)
    ):
        shifted_group = apply_subway_lateral_translations(
            group.copy(),
            assign_subway_lateral_translations(group.copy(), spacing_m=SUBWAY_SAME_LINE_TRACK_SPACING_M),
        )
        group_records = [
            {
                "idx": idx,
                "source_id": subway_source_id(row, idx),
                "geometry": row.geometry,
                "translation_x_m": round(float(row.get("_subway_lateral_translation_x_m", 0.0)), 3),
                "translation_y_m": round(float(row.get("_subway_lateral_translation_y_m", 0.0)), 3),
            }
            for idx, row in shifted_group.iterrows()
        ]
        for left_pos, left in enumerate(group_records):
            for right in group_records[left_pos + 1:]:
                if not railway_tunnels_need_vertical_separation(left["geometry"], right["geometry"]):
                    continue
                horizontal_distance = float(left["geometry"].distance(right["geometry"]))
                clearance = horizontal_distance - min_clear_center_distance
                if clearance >= 0:
                    continue
                same_line_issues.append(
                    {
                        "line_name": line_name,
                        "left": {
                            "source_index": str(left["idx"]),
                            "source_subway_id": left["source_id"],
                            "translation_x_m": left["translation_x_m"],
                            "translation_y_m": left["translation_y_m"],
                        },
                        "right": {
                            "source_index": str(right["idx"]),
                            "source_subway_id": right["source_id"],
                            "translation_x_m": right["translation_x_m"],
                            "translation_y_m": right["translation_y_m"],
                        },
                        "horizontal_distance_m": round(horizontal_distance, 3),
                        "required_center_distance_m": round(min_clear_center_distance, 3),
                        "clearance_m": round(clearance, 3),
                    }
                )

    for left_pos, left in enumerate(records):
        for right in records[left_pos + 1:]:
            if left["line_name"] == right["line_name"]:
                continue
            if (
                left.get("base_line_name") == right.get("base_line_name")
                and left.get("tunnel_side")
                and left.get("tunnel_side") == right.get("tunnel_side")
            ):
                continue
            if not railway_tunnels_need_vertical_separation(left["geometry"], right["geometry"]):
                continue
            horizontal_distance = float(left["geometry"].distance(right["geometry"]))
            vertical_distance = abs(float(left["depth_m"]) - float(right["depth_m"]))
            center_distance_3d = math.hypot(horizontal_distance, vertical_distance)
            clearance = center_distance_3d - min_clear_center_distance
            min_3d_clearance_m = clearance if min_3d_clearance_m is None else min(min_3d_clearance_m, clearance)
            if clearance >= 0:
                continue
            issues.append(
                {
                    "left": {key: left[key] for key in ("source_index", "source_subway_id", "line_name", "depth_m")},
                    "right": {key: right[key] for key in ("source_index", "source_subway_id", "line_name", "depth_m")},
                    "horizontal_distance_m": round(horizontal_distance, 3),
                    "vertical_distance_m": round(vertical_distance, 3),
                    "center_distance_3d_m": round(center_distance_3d, 3),
                    "required_center_distance_m": round(min_clear_center_distance, 3),
                    "clearance_m": round(clearance, 3),
                }
            )

    return {
        "project": "cim_road_poc",
        "model": "cim4_subway_tunnels",
        "check": "subway_tunnel_3d_separation",
        "status": "pass" if not issues else "fail",
        "policy": "subway tunnel candidates keep their lateral shifts and side labels when parallel tracks are detected",
        "parameters": {
            "inner_clear_radius_m": SUBWAY_TUNNEL_RADIUS_M,
            "lining_thickness_m": SUBWAY_TUNNEL_LINING_THICKNESS_M,
            "outer_radius_m": SUBWAY_TUNNEL_OUTER_RADIUS_M,
            "minimum_center_distance_m": round(min_clear_center_distance, 3),
        },
        "summary": {
            "source_railway_record_count": int(len(railways)),
            "subway_candidate_record_count": int(len(subway_candidates)),
            "generated_corridor_count": int(len(tunnel_corridors)),
            "merged_or_ignored_candidate_count": 0,
            "laterally_shifted_candidate_count": int(
                sum(
                    1
                    for _, row in tunnel_corridors.iterrows()
                    if abs(float(row.get("_subway_lateral_translation_x_m", 0.0))) > 0.001
                    or abs(float(row.get("_subway_lateral_translation_y_m", 0.0))) > 0.001
                )
            ),
            "issue_count": len(issues),
            "same_line_source_overlap_count": len(same_line_issues),
            "minimum_3d_clearance_m": round(float(min_3d_clearance_m), 3) if min_3d_clearance_m is not None else None,
        },
        "corridors": [
            {
                key: record[key]
                for key in (
                    "source_index",
                    "source_subway_id",
                    "line_name",
                    "depth_m",
                    "length_m",
                    "lateral_translation_x_m",
                    "lateral_translation_y_m",
                )
            }
            for record in records
        ],
        "issues": issues,
        "same_line_issues": same_line_issues,
    }


def write_subway_tunnel_separation_check(path: Path | None = None) -> dict[str, Any]:
    railways = load_layer(RAIL_LINES_PATH)
    railways = localize(railways, compute_origin(railways))
    report = build_subway_tunnel_separation_check(railways)
    output_path = path or SUBWAY_TUNNEL_SEPARATION_CHECK_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report
