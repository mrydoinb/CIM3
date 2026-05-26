#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
import json

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

from city import pipeline as city_pipeline


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "output" / "qc_report" / "cim_city_junction_stack_check.json"
CORE_INSET_M = 0.75
CONNECTIVITY_TOLERANCE_M = 0.20
DRIVABLE_LAYERS = {
    "Road_Surface_Main",
    "Road_Surface_Service",
    "Road_Surface_Branch",
    "Non_Motor_Lane",
    "Parking_Lane",
}


def load_city_module():
    return city_pipeline


def clean_polygonal(city: Any, geom):
    if geom is None or geom.is_empty:
        return None
    try:
        geom = geom.buffer(0)
    except Exception:
        pass
    if geom.is_empty:
        return None
    return city.road_gen.clean_polygonal(geom)


def band_polygon(city: Any, line, left_offset: float, right_offset: float):
    if right_offset <= left_offset or line is None or line.is_empty or line.length <= 0.05:
        return None
    distances = city.road_gen.sample_line_for_sweep(line)
    if len(distances) < 2:
        return None

    left_points = []
    right_points = []
    for distance in distances:
        point, _, normal = city.road_gen.line_frame_at_distance(line, distance)
        nx, ny = normal
        left_points.append((point.x + nx * left_offset, point.y + ny * left_offset))
        right_points.append((point.x + nx * right_offset, point.y + ny * right_offset))

    return clean_polygonal(city, Polygon(left_points + list(reversed(right_points))))


def mesh_polygon(city: Any, mesh):
    if mesh is None or len(mesh.vertices) == 0:
        return None

    triangles = []
    for face in mesh.faces:
        coords = [(float(mesh.vertices[idx][0]), float(mesh.vertices[idx][1])) for idx in face]
        poly = Polygon(coords)
        if not poly.is_empty and poly.area > 1e-8:
            triangles.append(poly)
    if not triangles:
        return None
    return clean_polygonal(city, unary_union(triangles))


def new_layer_stats() -> dict[str, Any]:
    return {
        "part_count": 0,
        "overlap_count": 0,
        "deep_count": 0,
        "total_area": 0.0,
        "overlap_area": 0.0,
        "deep_area": 0.0,
        "max_overlap_area": 0.0,
        "max_deep_area": 0.0,
        "examples": [],
    }


def record_overlap(
    stats: dict[str, dict[str, Any]],
    layer: str,
    geom,
    label: str,
    junction_union,
    junction_mask,
    core_union,
    core_mask,
) -> None:
    if geom is None or geom.is_empty:
        return
    area = float(geom.area)
    if area <= 1e-8:
        return

    item = stats[layer]
    item["part_count"] += 1
    item["total_area"] += area

    overlap_area = 0.0
    deep_area = 0.0
    if junction_mask.intersects(geom):
        overlap_area = float(geom.intersection(junction_union).area)
    if core_mask is not None and core_mask.intersects(geom):
        deep_area = float(geom.intersection(core_union).area)

    if overlap_area > 0.001:
        item["overlap_count"] += 1
        item["overlap_area"] += overlap_area
        item["max_overlap_area"] = max(item["max_overlap_area"], overlap_area)
        if len(item["examples"]) < 5:
            item["examples"].append(
                {
                    "label": label,
                    "overlap_area_m2": round(overlap_area, 4),
                    "deep_area_m2": round(deep_area, 4),
                }
            )

    if deep_area > 0.001:
        item["deep_count"] += 1
        item["deep_area"] += deep_area
        item["max_deep_area"] = max(item["max_deep_area"], deep_area)


def record_component_layers(
    city: Any,
    prepared_roads,
    rules: dict[str, Any],
    clip_profiles: dict[str, dict[Any, list[tuple[float, float]]]],
    stats: dict[str, dict[str, Any]],
    drivable_geoms_by_road: dict[Any, list[Any]],
    component_geoms_by_key: dict[tuple[Any, str, str, str], list[Any]],
    drivable_core_clip_geom,
    junction_union,
    junction_mask,
    core_union,
    core_mask,
) -> None:
    def remember_component_geom(road_idx: Any, layer: str, component_type: str, component_key: Any, geom) -> None:
        if geom is not None and not geom.is_empty:
            component_geoms_by_key[(road_idx, layer, component_type, str(component_key))].append(geom)

    for road_idx, row in prepared_roads.iterrows():
        row = row.copy()
        row.name = road_idx
        rule = city.road_gen.get_road_rule(row, rules)
        components = city.road_gen.cross_section_components_for_row(row) or city.fallback_cross_section_components(rule)
        spans = city.component_spans(components)

        for line_idx, line in enumerate(city.iter_lines(row.geometry)):
            segment_cache: dict[str, list[tuple[Any, float]]] = {}

            def clipped_segments_for(profile: str) -> list[tuple[Any, float]]:
                if profile not in segment_cache:
                    profile_ranges = clip_profiles.get(profile, {})
                    clip_ranges = profile_ranges.get(road_idx, [])
                    segment_cache[profile] = (
                        city.road_gen.line_segments_outside_ranges(line, clip_ranges)
                        if clip_ranges
                        else [(line, 0.0)]
                    )
                return segment_cache[profile]

            for component_idx, (component, left, right) in enumerate(spans):
                component_type = str(component.get("type", ""))
                layer = city.component_layer_name_for_row(component_type, row)
                profile = "drivable" if component_type in city.DRIVABLE_COMPONENT_TYPES else "roadside"
                for segment_idx, (segment, _) in enumerate(clipped_segments_for(profile)):
                    geom = band_polygon(city, segment, left, right)
                    if component_type in city.DRIVABLE_COMPONENT_TYPES and geom is not None and not geom.is_empty:
                        local_clip = city.local_segment_clip_mask(
                            segment,
                            left,
                            right,
                            drivable_core_clip_geom,
                        )
                        if local_clip is not None and not local_clip.is_empty:
                            geom = clean_polygonal(city, geom.difference(local_clip))
                        if geom is not None and not geom.is_empty:
                            drivable_geoms_by_road[road_idx].append(geom)
                    remember_component_geom(road_idx, layer, component_type, component_idx, geom)
                    record_overlap(
                        stats,
                        layer,
                        geom,
                        f"road={road_idx},line={line_idx},seg={segment_idx},component={component_idx}:{component_type}",
                        junction_union,
                        junction_mask,
                        core_union,
                        core_mask,
                    )

            for segment_idx, (segment, _) in enumerate(clipped_segments_for("roadside")):
                curb_width = max(0.18, min(float(rule.curb_width), 0.45))
                for idx in range(len(spans) - 1):
                    left_component, _, boundary = spans[idx]
                    right_component, next_left, _ = spans[idx + 1]
                    if abs(boundary - next_left) > 0.01:
                        continue
                    left_type = str(left_component.get("type", ""))
                    right_type = str(right_component.get("type", ""))
                    if not (
                        left_type in city.DRIVABLE_COMPONENT_TYPES
                        and right_type in city.RAISED_COMPONENT_TYPES
                    ) and not (
                        right_type in city.DRIVABLE_COMPONENT_TYPES
                        and left_type in city.RAISED_COMPONENT_TYPES
                    ):
                        continue
                    geom = band_polygon(city, segment, boundary - curb_width / 2.0, boundary + curb_width / 2.0)
                    remember_component_geom(road_idx, "Curb", "curb", f"curb_{idx}", geom)
                    record_overlap(
                        stats,
                        "Curb",
                        geom,
                        f"road={road_idx},line={line_idx},seg={segment_idx},curb={idx}",
                        junction_union,
                        junction_mask,
                        core_union,
                        core_mask,
                    )


def record_marking_layers(
    city: Any,
    prepared_roads,
    rules: dict[str, Any],
    clip_profiles: dict[str, dict[Any, list[tuple[float, float]]]],
    stats: dict[str, dict[str, Any]],
    junction_union,
    junction_mask,
    core_union,
    core_mask,
) -> None:
    white_markings = []
    yellow_markings = []
    crosswalks = []
    stop_lines = []
    marking_filter = clean_polygonal(
        city,
        junction_union.buffer(city.JUNCTION_MARKING_SURFACE_CLEARANCE_M, resolution=6, join_style=1),
    )

    for road_idx, row in prepared_roads.iterrows():
        row = row.copy()
        row.name = road_idx
        rule = city.road_gen.get_road_rule(row, rules)
        components = city.road_gen.cross_section_components_for_row(row) or city.fallback_cross_section_components(rule)
        spans = city.component_spans(components)
        suppression = city.carriageway_boundary_edge_suppression(spans)
        clip_ranges = clip_profiles.get("marking", {}).get(road_idx, [])

        for line in city.iter_lines(row.geometry):
            clipped_segments = (
                city.road_gen.line_segments_outside_ranges(line, clip_ranges)
                if clip_ranges
                else [(line, 0.0)]
            )
            for segment, distance_offset in clipped_segments:
                for component_idx, (component, left, right) in enumerate(spans):
                    city.add_component_lane_markings(
                        row,
                        segment,
                        rule,
                        component,
                        left,
                        right,
                        white_markings,
                        suppress_left_edge="left" in suppression.get(component_idx, set()),
                        suppress_right_edge="right" in suppression.get(component_idx, set()),
                        distance_offset=distance_offset,
                    )
                city.add_center_markings_for_adjacent_carriageways(
                    row,
                    segment,
                    rule,
                    spans,
                    yellow_markings,
                    distance_offset=distance_offset,
                )
            city.add_junction_crosswalks_and_stop_lines(row, line, rule, crosswalks, stop_lines)

    crosswalks, _ = city.filter_meshes_outside_polygon(crosswalks, marking_filter)
    stop_lines, _ = city.filter_meshes_outside_polygon(stop_lines, marking_filter)
    crosswalks, _ = city.filter_meshes_without_polygon_overlap(crosswalks, junction_union)
    stop_lines, _ = city.filter_meshes_without_polygon_overlap(stop_lines, junction_union)

    for idx, mesh in enumerate(white_markings):
        record_overlap(stats, "Lane_Marking_White", mesh_polygon(city, mesh), f"white_marking={idx}", junction_union, junction_mask, core_union, core_mask)
    for idx, mesh in enumerate(yellow_markings):
        record_overlap(stats, "Lane_Marking_Yellow", mesh_polygon(city, mesh), f"yellow_marking={idx}", junction_union, junction_mask, core_union, core_mask)
    for idx, mesh in enumerate(crosswalks):
        record_overlap(stats, "Crosswalk", mesh_polygon(city, mesh), f"crosswalk={idx}", junction_union, junction_mask, core_union, core_mask)
    for idx, mesh in enumerate(stop_lines):
        record_overlap(stats, "Stop_Line", mesh_polygon(city, mesh), f"stop_line={idx}", junction_union, junction_mask, core_union, core_mask)


def record_assets(city: Any, prepared_roads, rules: dict[str, Any], junction_union, junction_mask, core_mask) -> dict[str, Any]:
    asset_stats = defaultdict(
        lambda: {
            "count": 0,
            "filtered_inside_junction_count": 0,
            "inside_junction_count": 0,
            "inside_core_count": 0,
            "examples": [],
        }
    )
    asset_filter = clean_polygonal(city, junction_union.buffer(1.0, resolution=6, join_style=1))

    def check_asset(layer: str, road_idx: Any, mesh) -> None:
        if mesh is None or len(mesh.vertices) == 0:
            return
        bounds = mesh.bounds
        point = Point(
            (float(bounds[0][0]) + float(bounds[1][0])) / 2.0,
            (float(bounds[0][1]) + float(bounds[1][1])) / 2.0,
        )
        item = asset_stats[layer]
        if asset_filter is not None and asset_filter.intersects(point):
            item["filtered_inside_junction_count"] += 1
            return
        item["count"] += 1
        inside = bool(junction_mask.intersects(point))
        deep = bool(core_mask is not None and core_mask.intersects(point))
        if inside:
            item["inside_junction_count"] += 1
        if deep:
            item["inside_core_count"] += 1
        if inside and len(item["examples"]) < 5:
            item["examples"].append(
                {
                    "road": str(road_idx),
                    "x": round(float(point.x), 3),
                    "y": round(float(point.y), 3),
                    "inside_core": deep,
                }
            )

    for road_idx, row in prepared_roads.iterrows():
        row = row.copy()
        row.name = road_idx
        rule = city.road_gen.get_road_rule(row, rules)
        for mesh in city.road_gen.build_street_light_meshes(row, rule):
            check_asset("Street_Light", road_idx, mesh)
        for mesh in city.road_gen.build_tree_meshes(row, rule):
            check_asset("Tree", road_idx, mesh)

    return dict(asset_stats)


def summarize_junction_element_connectivity(
    city: Any,
    prepared_roads,
    rules: dict[str, Any],
    junction_surfaces: list[dict[str, Any]],
    component_geoms_by_key: dict[tuple[Any, str, str, str], list[Any]],
    roadside_clip_ranges: dict[Any, list[tuple[float, float]]] | None = None,
) -> dict[str, Any]:
    connector_mesh_groups = city.build_junction_side_component_connector_meshes(
        prepared_roads,
        rules,
        junction_surfaces,
        roadside_clip_ranges,
    )
    connector_parts: dict[tuple[int, str, str], list[Any]] = defaultdict(list)
    for meshes in connector_mesh_groups.values():
        for mesh in meshes:
            geom = mesh_polygon(city, mesh)
            if geom is None or geom.is_empty:
                continue
            metadata = mesh.metadata or {}
            try:
                junction_index = int(metadata.get("junction_index", -1))
            except (TypeError, ValueError):
                junction_index = -1
            component_type = str(metadata.get("component_type", ""))
            connection_key = str(metadata.get("junction_connection_level_key", ""))
            connector_parts[(junction_index, component_type, connection_key)].append(geom)

    connector_cache: dict[tuple[int, str, str], Any] = {}

    def connector_geom(key: tuple[int, str, str]):
        if key not in connector_cache:
            geoms = [geom for geom in connector_parts.get(key, []) if geom is not None and not geom.is_empty]
            connector_cache[key] = clean_polygonal(city, unary_union(geoms)) if geoms else None
        return connector_cache[key]

    component_cache: dict[tuple[Any, str, str, str], Any] = {}

    def component_geom(key: tuple[Any, str, str, str]):
        if key not in component_cache:
            geoms = [geom for geom in component_geoms_by_key.get(key, []) if geom is not None and not geom.is_empty]
            component_cache[key] = clean_polygonal(city, unary_union(geoms)) if geoms else None
        return component_cache[key]

    def empty_category() -> dict[str, Any]:
        return {
            "checked_count": 0,
            "disconnected_count": 0,
            "missing_connector_count": 0,
            "missing_component_count": 0,
            "max_gap_m": 0.0,
            "examples": [],
        }

    result = {
        "tolerance_m": CONNECTIVITY_TOLERANCE_M,
        "drivable": empty_category(),
        "side_components": empty_category(),
        "by_layer": defaultdict(empty_category),
    }

    def record_check(
        category: str,
        layer: str,
        surface: dict[str, Any],
        road_idx: Any,
        component_type: str,
        component_key: str,
        gap: float | None,
        missing_connector: bool = False,
        missing_component: bool = False,
    ) -> None:
        buckets = [result[category], result["by_layer"][layer]]
        failed = missing_connector or missing_component or gap is None or gap > CONNECTIVITY_TOLERANCE_M
        for bucket in buckets:
            bucket["checked_count"] += 1
            if missing_connector:
                bucket["missing_connector_count"] += 1
            if missing_component:
                bucket["missing_component_count"] += 1
            if not failed:
                continue
            bucket["disconnected_count"] += 1
            if gap is not None:
                bucket["max_gap_m"] = max(float(bucket["max_gap_m"]), gap)
            if len(bucket["examples"]) < 10:
                point = surface.get("point")
                bucket["examples"].append(
                    {
                        "junction_index": int(surface.get("index", -1)),
                        "x": round(float(point.x), 3) if isinstance(point, Point) else None,
                        "y": round(float(point.y), 3) if isinstance(point, Point) else None,
                        "road": str(road_idx),
                        "layer": layer,
                        "component_type": component_type,
                        "component_key": component_key,
                        "gap_m": None if gap is None else round(float(gap), 3),
                        "missing_connector": bool(missing_connector),
                        "missing_component": bool(missing_component),
                    }
                )

    for surface in junction_surfaces:
        surface_geom = surface.get("geometry")
        if surface_geom is None or surface_geom.is_empty:
            continue
        surface_idx = int(surface.get("index", -1))
        surface_point = surface.get("point")
        members = surface.get("members", [])
        arms = city.junction_arm_records(prepared_roads, rules, surface_point, members)
        connection_level_counts = defaultdict(int)
        for arm in arms:
            connection_level_counts[city.junction_connection_level_key_for_record(arm.get("road_level", {}))] += 1
        connectable_levels = {key for key, count in connection_level_counts.items() if count >= 2}
        member_distances: dict[Any, list[float]] = defaultdict(list)
        for road_idx, distance_hint in members:
            member_distances[road_idx].append(float(distance_hint))

        side_records: list[dict[str, Any]] = []
        type_level_roads: dict[tuple[str, str], set[Any]] = defaultdict(set)
        for road_idx in sorted(member_distances, key=str):
            if road_idx not in prepared_roads.index:
                continue
            row = prepared_roads.loc[road_idx].copy()
            row.name = road_idx
            rule = city.road_gen.get_road_rule(row, rules)
            components = city.road_gen.cross_section_components_for_row(row) or city.fallback_cross_section_components(rule)
            spans = city.component_spans(components)
            level_record = city.road_level_record_for_row(row, rule)
            connection_level = city.junction_connection_level_key_for_record(level_record)

            for component_idx, (component, _, _) in enumerate(spans):
                component_type = str(component.get("type", ""))
                if component_type not in city.DRIVABLE_COMPONENT_TYPES:
                    continue
                layer = city.component_layer_name_for_row(component_type, row)
                key = (road_idx, layer, component_type, str(component_idx))
                geom = component_geom(key)
                gap = float(surface_geom.distance(geom)) if geom is not None and not geom.is_empty else None
                if component_type in {"non_motor_lane", "parking_lane"} and geom is not None and not geom.is_empty:
                    connector = connector_geom((surface_idx, component_type, connection_level))
                    if connector is not None and not connector.is_empty:
                        connector_gap = float(connector.distance(geom))
                        gap = connector_gap if gap is None else min(gap, connector_gap)
                record_check(
                    "drivable",
                    layer,
                    surface,
                    road_idx,
                    component_type,
                    str(component_idx),
                    gap,
                    missing_component=geom is None or geom.is_empty,
                )

            if connection_level not in connectable_levels:
                continue
            for component_idx, component, _, _ in city.junction_side_connector_spans(spans, rule):
                component_type = str(component.get("type", ""))
                layer = "Curb" if component_type == "curb" else city.component_layer_name_for_row(component_type, row)
                record = {
                    "road_idx": road_idx,
                    "connection_level": connection_level,
                    "component_type": component_type,
                    "component_key": str(component_idx),
                    "layer": layer,
                }
                side_records.append(record)
                type_level_roads[(component_type, connection_level)].add(road_idx)

        expected_type_levels = {
            key for key, roads in type_level_roads.items()
            if len(roads) >= 2
        }
        for record in side_records:
            type_level = (record["component_type"], record["connection_level"])
            if type_level not in expected_type_levels:
                continue
            key = (
                record["road_idx"],
                record["layer"],
                record["component_type"],
                record["component_key"],
            )
            geom = component_geom(key)
            connector = connector_geom((surface_idx, record["component_type"], record["connection_level"]))
            gap = (
                float(connector.distance(geom))
                if connector is not None
                and not connector.is_empty
                and geom is not None
                and not geom.is_empty
                else None
            )
            record_check(
                "side_components",
                record["layer"],
                surface,
                record["road_idx"],
                record["component_type"],
                record["component_key"],
                gap,
                missing_connector=connector is None or connector.is_empty,
                missing_component=geom is None or geom.is_empty,
            )

    cleaned_by_layer = {}
    for layer, bucket in result["by_layer"].items():
        cleaned_by_layer[layer] = {
            "checked_count": int(bucket["checked_count"]),
            "disconnected_count": int(bucket["disconnected_count"]),
            "missing_connector_count": int(bucket["missing_connector_count"]),
            "missing_component_count": int(bucket["missing_component_count"]),
            "max_gap_m": round(float(bucket["max_gap_m"]), 3),
            "examples": bucket["examples"],
        }
    result["by_layer"] = dict(sorted(cleaned_by_layer.items()))
    for category in ("drivable", "side_components"):
        result[category]["checked_count"] = int(result[category]["checked_count"])
        result[category]["disconnected_count"] = int(result[category]["disconnected_count"])
        result[category]["missing_connector_count"] = int(result[category]["missing_connector_count"])
        result[category]["missing_component_count"] = int(result[category]["missing_component_count"])
        result[category]["max_gap_m"] = round(float(result[category]["max_gap_m"]), 3)
    result["total_disconnected_count"] = (
        int(result["drivable"]["disconnected_count"])
        + int(result["side_components"]["disconnected_count"])
    )
    return result


def summarize_junction_connectivity(
    city: Any,
    junction_surfaces: list[dict[str, Any]],
    drivable_geoms_by_road: dict[Any, list[Any]],
) -> dict[str, Any]:
    road_geom_cache: dict[Any, Any] = {}

    def road_drivable_geom(road_idx: Any):
        if road_idx not in road_geom_cache:
            geoms = [
                geom
                for geom in drivable_geoms_by_road.get(road_idx, [])
                if geom is not None and not geom.is_empty
            ]
            road_geom_cache[road_idx] = clean_polygonal(city, unary_union(geoms)) if geoms else None
        return road_geom_cache[road_idx]

    surface_geoms = [
        surface["geometry"]
        for surface in junction_surfaces
        if surface.get("geometry") is not None and not surface["geometry"].is_empty
    ]
    junction_union = clean_polygonal(city, unary_union(surface_geoms)) if surface_geoms else None
    if junction_union is None or junction_union.is_empty:
        components = []
    elif junction_union.geom_type == "MultiPolygon":
        components = list(junction_union.geoms)
    else:
        components = [junction_union]
    component_cache: dict[int, Any] = {}

    def component_for_surface(surface_geom):
        surface_key = id(surface_geom)
        if surface_key not in component_cache:
            component = None
            for candidate in components:
                if candidate.intersects(surface_geom) or candidate.distance(surface_geom) <= CONNECTIVITY_TOLERANCE_M:
                    component = candidate
                    break
            component_cache[surface_key] = component or surface_geom
        return component_cache[surface_key]

    checked_members = 0
    disconnected_members = 0
    disconnected_junctions = 0
    max_gap = 0.0
    examples = []
    for surface in junction_surfaces:
        surface_geom = surface.get("geometry")
        if surface_geom is None or surface_geom.is_empty:
            continue
        check_geom = component_for_surface(surface_geom)
        missing = []
        member_roads = sorted({road_idx for road_idx, _ in surface.get("members", [])}, key=str)
        for road_idx in member_roads:
            checked_members += 1
            road_geom = road_drivable_geom(road_idx)
            gap = float(check_geom.distance(road_geom)) if road_geom is not None and not road_geom.is_empty else float("inf")
            if gap <= CONNECTIVITY_TOLERANCE_M:
                continue
            disconnected_members += 1
            if gap != float("inf"):
                max_gap = max(max_gap, gap)
            if len(missing) < 6:
                missing.append(
                    {
                        "road": str(road_idx),
                        "gap_m": None if gap == float("inf") else round(gap, 3),
                    }
                )
        if missing:
            disconnected_junctions += 1
            if len(examples) < 8:
                point = surface.get("point")
                examples.append(
                    {
                        "junction_index": int(surface.get("index", -1)),
                        "x": round(float(point.x), 3) if isinstance(point, Point) else None,
                        "y": round(float(point.y), 3) if isinstance(point, Point) else None,
                        "missing_member_roads": missing,
                    }
                )

    return {
        "tolerance_m": CONNECTIVITY_TOLERANCE_M,
        "checked_member_roads": checked_members,
        "disconnected_member_roads": disconnected_members,
        "disconnected_junction_count": disconnected_junctions,
        "max_gap_m": round(max_gap, 3),
        "examples": examples,
    }


def main() -> None:
    city = load_city_module()
    roads = city.load_layer(city.road_gen.RAW_ROADS)
    prepared_roads = city.prepare_roads_for_surfaces(roads)
    rules = city.road_gen.load_rules()
    buckets = city.junction_point_buckets(prepared_roads)
    junction_surfaces = city.build_rounded_junction_surface_geometries(prepared_roads, rules, buckets)
    junction_polys = [
        item["geometry"]
        for item in junction_surfaces
        if item.get("geometry") is not None and not item["geometry"].is_empty
    ]
    junction_union = clean_polygonal(city, unary_union(junction_polys)) if junction_polys else None
    if junction_union is None or junction_union.is_empty:
        raise RuntimeError("No junction surfaces were generated.")

    core_union = clean_polygonal(city, junction_union.buffer(-CORE_INSET_M, resolution=4, join_style=1))
    junction_mask = prep(junction_union)
    core_mask = prep(core_union) if core_union is not None and not core_union.is_empty else None
    drivable_core_clip_geom = clean_polygonal(city, junction_union)
    clip_profiles = city.junction_clip_range_profiles_by_road(prepared_roads, rules, junction_surfaces)

    stats: dict[str, dict[str, Any]] = defaultdict(new_layer_stats)
    drivable_geoms_by_road: dict[Any, list[Any]] = defaultdict(list)
    component_geoms_by_key: dict[tuple[Any, str, str, str], list[Any]] = defaultdict(list)
    record_component_layers(
        city,
        prepared_roads,
        rules,
        clip_profiles,
        stats,
        drivable_geoms_by_road,
        component_geoms_by_key,
        drivable_core_clip_geom,
        junction_union,
        junction_mask,
        core_union,
        core_mask,
    )
    record_marking_layers(
        city,
        prepared_roads,
        rules,
        clip_profiles,
        stats,
        junction_union,
        junction_mask,
        core_union,
        core_mask,
    )
    assets = record_assets(city, prepared_roads, rules, junction_union, junction_mask, core_mask)
    connectivity = summarize_junction_connectivity(city, junction_surfaces, drivable_geoms_by_road)
    element_connectivity = summarize_junction_element_connectivity(
        city,
        prepared_roads,
        rules,
        junction_surfaces,
        component_geoms_by_key,
        clip_profiles.get("roadside", {}),
    )

    report = {
        "model": "cim_city_junction_stack_check",
        "junction_surface_count": len(junction_surfaces),
        "junction_area_m2": round(float(junction_union.area), 3),
        "junction_core_area_m2": round(float(core_union.area), 3) if core_union is not None and not core_union.is_empty else 0.0,
        "core_inset_m": CORE_INSET_M,
        "interpretation": {
            "overlap_area_m2": "XY footprint area intersecting the junction surface.",
            "deep_area_m2": f"Area that remains inside the junction surface after a {CORE_INSET_M} m inward buffer; this flags true stacking beyond seam blending.",
            "asset_inside_counts": "Street light and tree checks use asset XY center points.",
            "connectivity": "Each selected junction member road must have a drivable surface within the tolerance of its junction surface.",
            "element_connectivity": "Drivable components must touch the junction surface; side components and curbs must touch generated same-plane connector patches.",
        },
        "layers": {},
        "assets": assets,
        "connectivity": connectivity,
        "element_connectivity": element_connectivity,
    }

    for layer, item in sorted(stats.items()):
        report["layers"][layer] = {
            "part_count": int(item["part_count"]),
            "overlap_count": int(item["overlap_count"]),
            "deep_count": int(item["deep_count"]),
            "total_area_m2": round(float(item["total_area"]), 3),
            "overlap_area_m2": round(float(item["overlap_area"]), 6),
            "deep_area_m2": round(float(item["deep_area"]), 6),
            "max_overlap_area_m2": round(float(item["max_overlap_area"]), 6),
            "max_deep_area_m2": round(float(item["max_deep_area"]), 6),
            "examples": item["examples"],
        }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    flagged_layers = {
        layer: {
            "overlap_area_m2": item["overlap_area_m2"],
            "deep_area_m2": item["deep_area_m2"],
            "overlap_count": item["overlap_count"],
            "deep_count": item["deep_count"],
        }
        for layer, item in report["layers"].items()
        if item["deep_area_m2"] > 0.001
        or (layer not in DRIVABLE_LAYERS and item["overlap_area_m2"] > 0.001)
    }
    seam_overlap_layers = {
        layer: {
            "overlap_area_m2": item["overlap_area_m2"],
            "overlap_count": item["overlap_count"],
        }
        for layer, item in report["layers"].items()
        if layer in DRIVABLE_LAYERS and item["overlap_area_m2"] > 0.001 and item["deep_area_m2"] <= 0.001
    }
    print(
        json.dumps(
            {
                "report": str(REPORT_PATH),
                "flagged_layers": flagged_layers,
                "drivable_seam_overlap_layers": seam_overlap_layers,
                "connectivity": connectivity,
                "element_connectivity": element_connectivity,
                "assets": assets,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
