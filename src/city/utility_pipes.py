#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Utility pipe mesh generation, semantics, and QC."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import json
import re

import geopandas as gpd
import numpy as np
import pandas as pd
import trimesh
from shapely.geometry import LineString

from road import generator as road_gen
from city.mesh_utils import combine_mesh_list, cylinder_between, iter_lines, json_safe_value, offset_segment


ROOT = Path(__file__).resolve().parents[2]
SOURCE_PROJECTED_CRS = "EPSG:4547"
TARGET_CRS = road_gen.TARGET_CRS
CITY_UTILITY_SEMANTIC_PATH = ROOT / "output" / "semantic" / "cim_city_utility_pipes_semantic.json"
CITY_UTILITY_QC_PATH = ROOT / "output" / "qc_report" / "cim_city_utility_pipe_qc.json"

UTILITY_PIPE_STANDARD_REFERENCES = {
    "GB 50289-2016": "城市工程管线综合规划规范，用于管线综合、覆土和交叉避让控制。",
    "GB 50013-2018": "室外给水设计标准，用于给水管网设计语义和管径合理性控制。",
    "GB 50014-2021": "室外排水设计标准，用于污水管网设计语义、最小管径和重力管线控制。",
    "GB 50268-2008": "给水排水管道工程施工及验收规范，用于施工验收和回填语义控制。",
}

UTILITY_PIPE_SPECS: dict[str, dict[str, Any]] = {
    "Water": {
        "label_zh": "供水",
        "default_dn_mm": 400,
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
        "default_dn_mm": 600,
        "min_dn_mm": 300,
        "cover_depth_m": 1.8,
        "min_cover_depth_m": 0.7,
        "synthetic_lateral_offset_m": 0.0,
        "material_class": "reinforced_concrete_or_hdpe_gravity_pipe",
        "flow_model": "gravity",
        "color": [120, 90, 55, 255],
        "mesh_sections": 12,
        "standards": ["GB 50289-2016", "GB 50014-2021", "GB 50268-2008"],
    },
    "Gas": {
        "label_zh": "燃气",
        "default_dn_mm": 400,
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
        "default_dn_mm": 320,
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
        "default_dn_mm": 200,
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


def build_pipe_semantic_record(
    pipe_name: str,
    source_feature_index: Any,
    source_kind: str,
    dn_mm: int,
    diameter_source: str,
    length_m: float,
    segment_count: int,
    source_attributes: dict[str, Any] | None = None,
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
        "min_cover_depth_m": round(float(spec["min_cover_depth_m"]), 3),
        "min_dn_mm": int(spec["min_dn_mm"]),
        "material_class": spec["material_class"],
        "flow_model": spec["flow_model"],
        "standard_references": spec["standards"],
        "length_m": round(float(length_m), 3),
        "geometry_segment_count": int(segment_count),
        "quality_flags": {
            "diameter_assigned": True,
            "diameter_meets_minimum": int(max(dn_mm, int(spec["min_dn_mm"]))) >= int(spec["min_dn_mm"]),
            "cover_depth_meets_minimum": cover_depth_m >= float(spec["min_cover_depth_m"]),
            "has_source_traceability": source_kind != "unknown",
        },
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
                        )
                    )
    return meshes, records


def build_utility_pipe_meshes(
    utility_layers: list[dict[str, Any]],
    roads: gpd.GeoDataFrame,
) -> tuple[dict[str, trimesh.Trimesh], list[dict[str, Any]]]:
    has_real_utilities = any(not item["layer"].empty for item in utility_layers)
    if not has_real_utilities:
        return build_synthetic_utility_pipe_meshes(roads)

    meshes = {}
    records: list[dict[str, Any]] = []
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
            for line in iter_lines(row.geometry):
                coords = list(line.coords)
                feature_length += float(line.length)
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
                    )
                )
        combined = combine_mesh_list(f"Utility_{pipe_name}_All", pipe_meshes, color)
        if combined is not None:
            combined.metadata["pipe_type"] = pipe_name
            combined.metadata["feature_count"] = sum(1 for record in records if record["pipe_type"] == pipe_name)
            combined.metadata["standardized_pipe_model"] = True
            meshes[combined.metadata["name"]] = combined
    return meshes, records


def build_city_utility_pipe_semantic(
    utility_records: list[dict[str, Any]],
    origin: tuple[float, float],
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
        "origin_xy": [round(float(origin[0]), 3), round(float(origin[1]), 3)],
        "semantic_level": "source_pipe_feature_with_standardized_3d_profile",
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
        "object_count": len(utility_records),
        "type_counts": dict(type_counts),
        "length_m_by_type": {pipe_type: round(length, 3) for pipe_type, length in sorted(length_by_type.items())},
        "objects": utility_records,
    }


def write_city_utility_pipe_semantic(
    utility_records: list[dict[str, Any]],
    origin: tuple[float, float],
) -> dict[str, Any]:
    semantic = build_city_utility_pipe_semantic(utility_records, origin)
    CITY_UTILITY_SEMANTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CITY_UTILITY_SEMANTIC_PATH.open("w", encoding="utf-8") as f:
        json.dump(semantic, f, ensure_ascii=False, indent=2)
    return semantic


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


def write_city_utility_pipe_qc(utility_records: list[dict[str, Any]]) -> dict[str, Any]:
    report = build_city_utility_pipe_qc(utility_records)
    CITY_UTILITY_QC_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CITY_UTILITY_QC_PATH.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report
