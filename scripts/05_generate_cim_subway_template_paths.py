#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Write subway tunnel centerline paths for Blender template instancing."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

from shapely.geometry import LineString
from shapely.ops import linemerge, unary_union

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from city.pipeline import (  # noqa: E402
    RAIL_LINES_PATH,
    SUBWAY_CIM4_PROFILE,
    TARGET_CRS,
    assign_railway_tunnel_depths,
    compute_origin,
    iter_lines,
    line_average_axis,
    load_layer,
    localize,
    same_line_axis_components,
    subway_source_id,
    subway_tunnel_generation_corridors,
)


DEFAULT_PATH = ROOT / "output" / "semantic" / "cim4" / "subway_tunnel_template_paths.json"


def normalize_filter_text(text: str) -> str:
    return str(text).strip().lower().replace(" ", "")


def record_matches_line_filter(record: dict, line_filters: list[str]) -> bool:
    if not line_filters:
        return True
    haystack = normalize_filter_text(
        " ".join(
            str(record.get(key) or "")
            for key in ("source_subway_id", "line_name", "base_line_name")
        )
    )
    return any(normalize_filter_text(line_filter) in haystack for line_filter in line_filters)


def line_record_to_geometry(line_record: dict) -> LineString | None:
    coords = line_record.get("coords_xy") or []
    if len(coords) < 2:
        return None
    try:
        line = LineString([(float(x), float(y)) for x, y in coords])
    except (TypeError, ValueError):
        return None
    return line if not line.is_empty and line.length > 0.001 else None


def serialize_line(line: LineString) -> dict:
    return {
        "coords_xy": [[round(float(x), 6), round(float(y), 6)] for x, y in line.coords],
        "length_m": round(float(line.length), 3),
    }


def unique_tokens(values: list[object]) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple, set)):
            candidates = value
        else:
            candidates = [value]
        for candidate in candidates:
            token = str(candidate or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return tokens


def record_source_indices(record: dict) -> list[str]:
    return unique_tokens(record.get("merged_source_indices") or [record.get("source_index")])


def record_source_ids(record: dict) -> list[str]:
    return unique_tokens(record.get("merged_source_ids") or [record.get("source_subway_id")])


def record_axis_line(record: dict) -> LineString | None:
    lines = []
    for line_record in record.get("lines", []):
        line = line_record_to_geometry(line_record)
        if line is not None:
            lines.append(line)
    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]
    average_axis = line_average_axis(lines)
    if average_axis is not None:
        return average_axis
    return max(lines, key=lambda item: float(item.length))


def records_touch_merged_line(record: dict, merged_line: LineString, tolerance_m: float = 1.0) -> bool:
    for line_record in record.get("lines", []):
        line = line_record_to_geometry(line_record)
        if line is None:
            continue
        try:
            near_length = float(line.intersection(merged_line.buffer(tolerance_m)).length)
            near_ratio = near_length / max(float(line.length), 0.001)
        except (TypeError, ValueError):
            near_ratio = 0.0
        if near_ratio >= 0.8:
            return True
    return False


def merge_connected_template_path_records(records: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, float], list[dict]] = defaultdict(list)
    for record in records:
        grouped[
            (
                str(record.get("base_line_name") or record.get("line_name") or ""),
                str(record.get("tunnel_side") or ""),
                float(record.get("tunnel_depth_m") or 0.0),
            )
        ].append(record)

    merged_records: list[dict] = []
    for (_line_name, _side, _depth), group in grouped.items():
        source_lines: list[LineString] = []
        for record in group:
            for line_record in record.get("lines", []):
                line = line_record_to_geometry(line_record)
                if line is not None:
                    source_lines.append(line)
        if not source_lines:
            continue

        try:
            merged_geom = linemerge(unary_union(source_lines))
        except (TypeError, ValueError):
            merged_geom = unary_union(source_lines)
        merged_lines = sorted(list(iter_lines(merged_geom)), key=lambda item: float(item.length), reverse=True)
        if not merged_lines:
            merged_lines = sorted(source_lines, key=lambda item: float(item.length), reverse=True)

        for interval_idx, merged_line in enumerate(merged_lines, start=1):
            supporting = [record for record in group if records_touch_merged_line(record, merged_line)]
            if not supporting:
                supporting = group
            representative = max(supporting, key=lambda record: sum(line_record.get("length_m", 0.0) for line_record in record.get("lines", [])))
            source_indices = [str(record.get("source_index") or "") for record in supporting]
            source_ids = [str(record.get("source_subway_id") or "") for record in supporting]
            merged_record = dict(representative)
            merged_record["source_index"] = "_".join(source_indices)
            merged_record["source_subway_id"] = "_".join(source_ids)
            merged_record["line_name"] = str(representative.get("base_line_name") or representative.get("line_name") or merged_record["source_subway_id"])
            merged_record["base_line_name"] = str(representative.get("base_line_name") or merged_record["line_name"])
            merged_record["interval_index"] = interval_idx
            merged_record["merged_source_count"] = len(supporting)
            merged_record["merged_source_ids"] = source_ids
            merged_record["merged_source_indices"] = source_indices
            merged_record["lines"] = [serialize_line(merged_line)]
            merged_records.append(merged_record)

    return sorted(
        merged_records,
        key=lambda record: (
            str(record.get("base_line_name") or ""),
            int(record.get("interval_index") or 0),
            str(record.get("source_subway_id") or ""),
        ),
    )


def collapse_parallel_template_path_records(records: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, float], list[dict]] = defaultdict(list)
    for record in records:
        grouped[
            (
                str(record.get("base_line_name") or record.get("line_name") or ""),
                str(record.get("tunnel_side") or ""),
                float(record.get("tunnel_depth_m") or 0.0),
            )
        ].append(record)

    collapsed_records: list[dict] = []
    for (_line_name, _side, _depth), group in grouped.items():
        axis_records: list[tuple[LineString, dict]] = []
        for record in group:
            axis = record_axis_line(record)
            if axis is not None:
                axis_records.append((axis, record))
        if not axis_records:
            continue

        axes = [axis for axis, _record in axis_records]
        components = same_line_axis_components(axes)
        component_records: list[dict] = []
        for component in components:
            records_in_component = [axis_records[idx][1] for idx in component]
            axes_in_component = [axis_records[idx][0] for idx in component]
            if len(records_in_component) == 1:
                component_records.append(dict(records_in_component[0]))
                continue

            representative = max(
                records_in_component,
                key=lambda record: sum(line_record.get("length_m", 0.0) for line_record in record.get("lines", [])),
            )
            center_axis = line_average_axis(axes_in_component)
            if center_axis is None:
                center_axis = max(axes_in_component, key=lambda item: float(item.length))
            source_indices = unique_tokens(
                token
                for record in records_in_component
                for token in record_source_indices(record)
            )
            source_ids = unique_tokens(
                token
                for record in records_in_component
                for token in record_source_ids(record)
            )
            collapsed_record = dict(representative)
            collapsed_record["source_index"] = "_".join(source_indices)
            collapsed_record["source_subway_id"] = "_".join(source_ids)
            collapsed_record["merged_source_count"] = sum(int(record.get("merged_source_count") or 1) for record in records_in_component)
            collapsed_record["merged_source_ids"] = source_ids
            collapsed_record["merged_source_indices"] = source_indices
            collapsed_record["line_name"] = str(
                representative.get("base_line_name")
                or representative.get("line_name")
                or collapsed_record["source_subway_id"]
            )
            collapsed_record["base_line_name"] = str(representative.get("base_line_name") or collapsed_record["line_name"])
            collapsed_record["collapsed_parallel_count"] = len(records_in_component)
            collapsed_record["collapsed_parallel_source_ids"] = source_ids
            collapsed_record["collapsed_parallel_source_indices"] = source_indices
            collapsed_record["parallel_collapse_policy"] = "two_tunnel_template_center_axis"
            collapsed_record["lines"] = [serialize_line(center_axis)]
            component_records.append(collapsed_record)

        component_records.sort(
            key=lambda record: (
                min(int(token) for token in record_source_indices(record) if str(token).isdigit())
                if any(str(token).isdigit() for token in record_source_indices(record))
                else 0,
                str(record.get("source_subway_id") or ""),
            )
        )
        for interval_idx, record in enumerate(component_records, start=1):
            record["interval_index"] = interval_idx
            collapsed_records.append(record)

    return sorted(
        collapsed_records,
        key=lambda record: (
            str(record.get("base_line_name") or ""),
            int(record.get("interval_index") or 0),
            str(record.get("source_subway_id") or ""),
        ),
    )


def build_template_paths(
    line_filters: list[str] | None = None,
    merge_connected: bool = True,
    collapse_parallel: bool = True,
) -> dict:
    line_filters = [item for item in (line_filters or []) if str(item).strip()]
    railways = load_layer(RAIL_LINES_PATH)
    origin = compute_origin(railways)
    railways = localize(railways, origin)
    corridors = subway_tunnel_generation_corridors(railways)
    depths = assign_railway_tunnel_depths(corridors)

    records = []
    for idx, row in corridors.iterrows():
        source_id = subway_source_id(row, idx)
        line_records = []
        for line in iter_lines(row.geometry):
            coords = [[round(float(x), 6), round(float(y), 6)] for x, y in line.coords]
            if len(coords) < 2:
                continue
            line_records.append({"coords_xy": coords, "length_m": round(float(line.length), 3)})
        if not line_records:
            continue
        record = {
            "source_index": str(idx),
            "source_subway_id": source_id,
            "line_name": str(row.get("name") or source_id),
            "base_line_name": str(row.get("_subway_line_name") or row.get("name") or source_id),
            "tunnel_side": str(row.get("_subway_tunnel_side") or ""),
            "interval_index": int(float(row.get("_subway_interval_index") or 0)),
            "tunnel_depth_m": round(float(depths.get(idx, 0.0)), 3),
            "lateral_translation_x_m": round(float(row.get("_subway_lateral_translation_x_m") or 0.0), 3),
            "lateral_translation_y_m": round(float(row.get("_subway_lateral_translation_y_m") or 0.0), 3),
            "lines": line_records,
        }
        if record_matches_line_filter(record, line_filters):
            records.append(record)

    if merge_connected:
        records = merge_connected_template_path_records(records)
    if collapse_parallel:
        records = collapse_parallel_template_path_records(records)

    return {
        "project": "cim_road_poc",
        "model": "cim4_subway_tunnel_template_paths",
        "generation_profile": SUBWAY_CIM4_PROFILE.name,
        "source": str(RAIL_LINES_PATH),
        "coordinate": {
            "model_crs": TARGET_CRS,
                "local_origin": {"x": round(float(origin[0]), 6), "y": round(float(origin[1]), 6), "z": 0.0},
        },
        "line_filters": line_filters,
        "merge_connected": merge_connected,
        "collapse_parallel": collapse_parallel,
        "parallel_collapse_policy": "two_tunnel_template_center_axis" if collapse_parallel else "disabled",
        "path_count": len(records),
        "paths": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_PATH)
    parser.add_argument(
        "--line",
        action="append",
        default=[],
        help="Only include subway records whose line name/source id contains this text. Repeat for multiple lines.",
    )
    parser.add_argument(
        "--no-merge-connected",
        action="store_true",
        help="Keep source railway records split instead of merging connected same-name tunnel segments.",
    )
    parser.add_argument(
        "--no-collapse-parallel",
        action="store_true",
        help="Keep parallel same-name source tunnel axes instead of collapsing them to the center axis for the twin-tunnel template.",
    )
    args = parser.parse_args(argv)

    data = build_template_paths(
        args.line,
        merge_connected=not args.no_merge_connected,
        collapse_parallel=not args.no_collapse_parallel,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    filter_text = f" line_filters={data['line_filters']}" if data.get("line_filters") else ""
    print(f"Subway template paths: {data['path_count']} -> {args.output}{filter_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
