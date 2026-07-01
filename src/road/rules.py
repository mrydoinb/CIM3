#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Road rule loading, road-class normalization, and cross-section helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import copy
import json
import logging
import re

import geopandas as gpd
import numpy as np
import pandas as pd

from road.schema import LaneLayout, RoadRule


ROOT = Path(__file__).resolve().parents[2]
RULE_PATH = ROOT / "data" / "rules" / "road_rules.json"
SECTION_RULE_PATH = ROOT / "data" / "rules" / "road_section_requirements.json"

ROAD_SECTION_REQUIREMENTS = {
    # section: road class, default total section width, modeled carriageway
    # lane count, and lane width. The total width is also overridden by the
    # source width field when present in data/Data/road50kms.
    "A1": {"road_class": "trunk", "grade": "expressway", "total_width": 118.0, "lane_count": 10, "lane_width": 3.75},
    "A2": {"road_class": "trunk", "grade": "expressway", "total_width": 70.0, "lane_count": 6, "lane_width": 3.75},
    "A3": {"road_class": "trunk", "grade": "expressway", "total_width": 72.0, "lane_count": 6, "lane_width": 3.75},
    "B1": {"road_class": "primary", "grade": "arterial", "total_width": 70.0, "lane_count": 6, "lane_width": 3.75},
    "B2": {"road_class": "primary", "grade": "arterial", "total_width": 70.0, "lane_count": 6, "lane_width": 3.75},
    "B3": {"road_class": "primary", "grade": "arterial", "total_width": 50.0, "lane_count": 6, "lane_width": 3.75},
    "B4": {"road_class": "secondary", "grade": "secondary", "total_width": 40.0, "lane_count": 4, "lane_width": 3.75},
    "B5": {"road_class": "secondary", "grade": "secondary", "total_width": 30.0, "lane_count": 4, "lane_width": 3.5},
    "C1": {"road_class": "secondary", "grade": "secondary", "total_width": 35.0, "lane_count": 4, "lane_width": 3.75},
    "C2": {"road_class": "secondary", "grade": "secondary", "total_width": 27.0, "lane_count": 4, "lane_width": 3.75},
    "C3": {"road_class": "secondary", "grade": "secondary", "total_width": 26.0, "lane_count": 4, "lane_width": 3.5},
    "C4": {"road_class": "secondary", "grade": "secondary", "total_width": 64.0, "lane_count": 8, "lane_width": 3.75},
    "C5": {"road_class": "secondary", "grade": "secondary", "total_width": 45.0, "lane_count": 4, "lane_width": 3.5},
    "D1": {"road_class": "tertiary", "grade": "branch", "total_width": 20.0, "lane_count": 2, "lane_width": 3.25},
    "D2": {"road_class": "tertiary", "grade": "branch", "total_width": 20.0, "lane_count": 2, "lane_width": 3.25},
    "D3": {"road_class": "tertiary", "grade": "branch", "total_width": 20.0, "lane_count": 2, "lane_width": 3.25},
    "D4": {"road_class": "tertiary", "grade": "branch", "total_width": 20.0, "lane_count": 2, "lane_width": 3.25},
    "D5": {
        "road_class": "tertiary",
        "grade": "branch",
        "total_width": 14.0,
        "lane_count": 2,
        "lane_width": 5.0,
        "road_width": 10.0,
    },
    "D6": {
        "road_class": "tertiary",
        "grade": "branch",
        "total_width": 9.0,
        "lane_count": 1,
        "lane_width": 5.0,
        "road_width": 5.0,
    },
}

ROAD_SECTION_ALIASES = {
    "AA": "D1",
    "BB": "D2",
    "CC": "D3",
    "DD": "D4",
    "JJ": "D1",
    "RR": "D1",
    "WW": "D1",
}

_SECTION_REQUIREMENTS_CACHE: dict[str, dict[str, Any]] | None = None

VISUAL_SIDE_RESERVE_LIMITS_M = {
    "expressway": 8.0,
    "arterial": 6.0,
    "secondary": 4.8,
    "branch": 3.0,
}

MODEL_CROSS_SECTIONS_AS_SYMMETRIC = False
SECTION_SYMMETRY_TOLERANCE_M = 0.05
SYMMETRIC_SECTION_FALLBACKS = {
    "A2": "A3",
    "C4": "C5",
    "D2": "D1",
}
SYMMETRIC_FALLBACK_KEEP_MODEL_WIDTH = set()
SYMMETRIC_DEFAULT_SECTION_BY_CATEGORY = {
    "expressway": "A3",
    "arterial": "B3",
    "primary": "B3",
    "secondary": "C5",
    "branch": "D3",
}


def oneway_direction(row: pd.Series) -> int:
    value = (safe_str(row.get("oneway")) or "").strip().lower()
    if value in {"yes", "true", "1"}:
        return 1
    if value in {"-1", "reverse"}:
        return -1
    return 0


def load_rules() -> dict[str, RoadRule]:
    """从规则 JSON 文件中读取并加载所有模板信息，返回规则字典。"""
    with RULE_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: RoadRule(**v) for k, v in data.items() if isinstance(v, dict)}






def normalize_osmid(value: Any) -> str:
    """
    统一 OSM ID 格式。OSMnx 导出的 osmid 有时是 int，有时是 list，此函数统一转为字符串形式。
    """
    if isinstance(value, (list, tuple, set)):
        return "_".join(str(v) for v in value)
    return str(value)


def normalized_key_value(value: Any) -> str:
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, (list, tuple, set)):
        return "|".join(sorted(str(v) for v in value))
    return safe_str(value) or ""


def deduplicate_bidirectional_osm_edges(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if roads.empty or not {"u", "v", "osmid"}.issubset(roads.columns):
        return roads

    keyed = roads.copy()
    keyed["_oneway_dir"] = keyed.apply(oneway_direction, axis=1)
    keyed["_dedupe_key"] = keyed.apply(
        lambda row: (
            tuple(sorted((safe_str(row.get("u")) or "", safe_str(row.get("v")) or ""))),
            normalized_key_value(row.get("osmid")),
            normalized_key_value(row.get("key")),
        ),
        axis=1,
    )
    keyed["_reversed_rank"] = keyed.get("reversed", pd.Series([None] * len(keyed), index=keyed.index)).apply(
        lambda value: 1 if (safe_str(value) or "").lower() == "true" else 0
    )

    keep_indexes: list[Any] = []
    for _, group in keyed.groupby("_dedupe_key", sort=False):
        if len(group) <= 1 or not (group["_oneway_dir"] == 0).all():
            keep_indexes.extend(group.index.tolist())
            continue

        endpoint_pairs = {(safe_str(row.get("u")), safe_str(row.get("v"))) for _, row in group.iterrows()}
        has_reciprocal = any((v, u) in endpoint_pairs for u, v in endpoint_pairs)
        if not has_reciprocal:
            keep_indexes.extend(group.index.tolist())
            continue

        keep_indexes.append(group.sort_values(["_reversed_rank", "length"], ascending=[True, False]).index[0])

    return keyed.loc[keep_indexes].drop(columns=["_oneway_dir", "_dedupe_key", "_reversed_rank"]).reset_index(drop=True)




def safe_str(val: Any) -> str | None:
    """安全地将属性值转换为字符串，若为空值（如 nan, NaT）则返回 None。"""
    if val is None:
        return None
    val_str = str(val)
    if val_str in ("nan", "None", "<NA>", "NaN", "NaT"):
        return None
    return val_str


def make_unique_road_ids(values: Iterable[Any]) -> list[str]:
    base_values = [safe_str(value) or f"R{i:05d}" for i, value in enumerate(values)]
    totals: dict[str, int] = {}
    for value in base_values:
        totals[value] = totals.get(value, 0) + 1

    seen: dict[str, int] = {}
    unique_values: list[str] = []
    for value in base_values:
        seen[value] = seen.get(value, 0) + 1
        if totals[value] == 1:
            unique_values.append(value)
        else:
            unique_values.append(f"{value}_{seen[value] - 1:04d}")
    return unique_values


def parse_lane_count(val: Any, default: int = 2) -> int:
    """从字符串或列表中解析出车道数（提取首个数字），若解析失败则返回默认值 default。"""
    val_str = safe_str(val)
    if not val_str:
        return default
    matches = [int(match) for match in re.findall(r"\d+", val_str)]
    if not matches:
        return default
    return max(1, min(max(matches), 8))


def parsed_lane_count_or_none(val: Any) -> int | None:
    val_str = safe_str(val)
    if not val_str:
        return None
    matches = [int(match) for match in re.findall(r"\d+", val_str)]
    if not matches:
        return None
    return max(1, min(max(matches), 8))


def modal_lane_count(values: Iterable[Any]) -> int | None:
    counts: dict[int, int] = {}
    for value in values:
        lane_count = parsed_lane_count_or_none(value)
        if lane_count is None:
            continue
        counts[lane_count] = counts.get(lane_count, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)[0][0]


def normalize_corridor_lane_counts(roads: gpd.GeoDataFrame) -> pd.Series:
    if roads.empty or "lane_count" not in roads.columns:
        return roads.get("lane_count", pd.Series(dtype=object))

    normalized = roads["lane_count"].copy()
    key_series = roads.apply(
        lambda row: normalized_key_value(row.get("road_name")),
        axis=1,
    )
    for key in sorted(set(key_series)):
        if not key or key == "unknown":
            continue
        mask = key_series == key
        if int(mask.sum()) < 3:
            continue
        baseline_mask = mask & ~roads.apply(is_single_lane_junction_pocket, axis=1) & ~roads.apply(is_circular_junction_row, axis=1)
        corridor_lane_count = modal_lane_count(roads.loc[baseline_mask, "lane_count"])
        if corridor_lane_count is None:
            corridor_lane_count = modal_lane_count(roads.loc[mask, "lane_count"])
        if corridor_lane_count is None:
            continue
        apply_mask = mask & ~roads.apply(is_single_lane_junction_pocket, axis=1) & ~roads.apply(is_circular_junction_row, axis=1)
        normalized.loc[apply_mask] = max(2, corridor_lane_count)

    circular_mask = roads.apply(is_circular_junction_row, axis=1)
    normalized.loc[circular_mask] = 2
    return normalized


def is_circular_junction_row(row: pd.Series) -> bool:
    junction = (normalized_key_value(row.get("junction_type")) or normalized_key_value(row.get("junction"))).lower()
    return "circular" in junction or "roundabout" in junction


def is_single_lane_junction_pocket(row: pd.Series) -> bool:
    return oneway_direction(row) != 0 and parsed_lane_count_or_none(row.get("lane_count")) == 1


def parse_optional_lane_count(val: Any) -> int | None:
    val_str = safe_str(val)
    if not val_str:
        return None
    matches = [int(match) for match in re.findall(r"\d+", val_str)]
    if not matches:
        return None
    return max(0, min(max(matches), 8))


def normalize_directional_lanes(total_lanes: int, forward: int | None, backward: int | None, oneway: int) -> LaneLayout:
    total_lanes = max(1, min(int(total_lanes), 8))
    if oneway == 1:
        return LaneLayout(total_lanes, total_lanes, 0)
    if oneway == -1:
        return LaneLayout(total_lanes, 0, total_lanes)

    if forward is not None or backward is not None:
        forward_count = max(0, int(forward or 0))
        backward_count = max(0, int(backward or 0))
        known_total = forward_count + backward_count
        if known_total <= 0:
            forward_count = total_lanes // 2
            backward_count = total_lanes - forward_count
        elif known_total < total_lanes:
            missing = total_lanes - known_total
            if forward is None:
                forward_count += missing
            elif backward is None:
                backward_count += missing
            else:
                forward_count += missing // 2
                backward_count += missing - missing // 2
        elif known_total > total_lanes:
            total_lanes = min(known_total, 8)
        return LaneLayout(total_lanes, forward_count, backward_count)

    backward_count = total_lanes // 2
    forward_count = total_lanes - backward_count
    return LaneLayout(total_lanes, forward_count, backward_count)


def lane_layout_for_row(row: pd.Series, rule: RoadRule) -> LaneLayout:
    return normalize_directional_lanes(
        rule.lane_count,
        parse_optional_lane_count(row.get("lanes_forward")),
        parse_optional_lane_count(row.get("lanes_backward")),
        oneway_direction(row),
    )


def parse_road_width_m(val: Any) -> float | None:
    """Parse width values and return a plausible full section width."""
    val_str = safe_str(val)
    if not val_str:
        return None
    matches = [float(match) for match in re.findall(r"\d+(?:\.\d+)?", val_str)]
    plausible = [width for width in matches if 2.0 <= width <= 220.0]
    if not plausible:
        return None
    return max(plausible)


def road_section_requirements() -> dict[str, dict[str, Any]]:
    """Load engineering road-section requirements and aliases.

    `data/rules/road_section_requirements.json` may provide a detailed sequence
    of sidewalk, green belt, carriageway, median, and service-lane components.
    The in-code ROAD_SECTION_REQUIREMENTS table is the compact fallback for
    section code, width, lane count, and lane width.
    """
    global _SECTION_REQUIREMENTS_CACHE
    if _SECTION_REQUIREMENTS_CACHE is not None:
        return _SECTION_REQUIREMENTS_CACHE

    requirements = {key: dict(value) for key, value in ROAD_SECTION_REQUIREMENTS.items()}
    if SECTION_RULE_PATH.exists():
        try:
            with SECTION_RULE_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            sections = data.get("sections", data) if isinstance(data, dict) else {}
            for key, value in sections.items():
                if isinstance(value, dict):
                    requirements[str(key).upper()] = dict(value)
        except Exception as exc:
            logging.warning("Unable to load road section requirements: %s", exc)

    for alias, target in ROAD_SECTION_ALIASES.items():
        if target in requirements and alias not in requirements:
            aliased = dict(requirements[target])
            aliased["alias_of"] = target
            requirements[alias] = aliased

    _SECTION_REQUIREMENTS_CACHE = requirements
    return requirements


def road_section_code(value: Any) -> str | None:
    text = (safe_str(value) or "").strip().upper()
    if not text:
        return None
    requirements = road_section_requirements()
    match = re.search(r"[A-D][0-9]", text)
    if match:
        code = ROAD_SECTION_ALIASES.get(match.group(0), match.group(0))
        if code in requirements:
            return code
    code = ROAD_SECTION_ALIASES.get(text, text)
    return code if code in requirements else None


def road_section_requirement(value: Any) -> dict[str, Any] | None:
    section = road_section_code(value)
    if section is None:
        return None
    requirement = road_section_requirements().get(section)
    if requirement is None:
        return None
    result = dict(requirement)
    result.setdefault("section_id", section)
    return result


def row_section_code(row: pd.Series) -> str | None:
    for column in ("section", "road_section", "section_code", "road_name", "name", "roadname", "Name", "NAME"):
        if column not in row:
            continue
        code = road_section_code(row.get(column))
        if code:
            return code
    return None


def row_declared_road_class(row: pd.Series) -> str:
    for column in ("roadclass", "road_class", "highway", "class", "等级"):
        text = safe_str(row.get(column))
        if text:
            return normalize_source_road_class(text)
    return "unclassified"


def infer_section_requirement_from_width(row: pd.Series) -> dict[str, Any] | None:
    width = parse_road_width_m(row.get("osm_width", row.get("width")))
    if width is None:
        return None
    road_class = row_declared_road_class(row)
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for section, requirement in road_section_requirements().items():
        if requirement.get("alias_of"):
            continue
        if str(requirement.get("road_class", "")).lower() != road_class:
            continue
        total_width = float(requirement.get("total_width", 0.0) or 0.0)
        if total_width <= 0.0:
            continue
        candidates.append((abs(total_width - width), section, requirement))
    if not candidates:
        return None

    delta, section, requirement = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
    tolerance = max(0.75, width * 0.04)
    if delta > tolerance:
        return None
    inferred = dict(requirement)
    inferred["inferred_from"] = "roadclass_width"
    inferred["inferred_section"] = section
    inferred.setdefault("section_id", section)
    return inferred


def row_section_requirement(row: pd.Series) -> dict[str, Any] | None:
    code = row_section_code(row)
    return road_section_requirement(code) or infer_section_requirement_from_width(row)


def component_total_width(components: list[dict[str, Any]]) -> float:
    return sum(float(component.get("width", 0.0) or 0.0) for component in components)


def section_components_are_symmetric(
    components: list[dict[str, Any]],
    tolerance_m: float = SECTION_SYMMETRY_TOLERANCE_M,
) -> bool:
    if not components:
        return True
    pairs = zip(components, reversed(components))
    for left, right in pairs:
        if left is right:
            break
        if str(left.get("type", "")) != str(right.get("type", "")):
            return False
        left_width = float(left.get("width", 0.0) or 0.0)
        right_width = float(right.get("width", 0.0) or 0.0)
        if abs(left_width - right_width) > tolerance_m:
            return False
    return True


def modeled_section_code_for_row(row: pd.Series, section_rule: dict[str, Any] | None = None) -> str | None:
    section_rule = section_rule or row_section_requirement(row)
    if section_rule is None:
        return None
    source_code = safe_str(section_rule.get("section_id") or section_rule.get("inferred_section")) or row_section_code(row)
    if not source_code:
        return None

    raw_components = section_rule.get("components")
    if not MODEL_CROSS_SECTIONS_AS_SYMMETRIC or not isinstance(raw_components, list):
        return source_code
    if section_components_are_symmetric(raw_components):
        return source_code

    requirements = road_section_requirements()
    fallback = SYMMETRIC_SECTION_FALLBACKS.get(source_code)
    if fallback in requirements:
        return fallback

    category = (safe_str(section_rule.get("category") or section_rule.get("grade")) or "").lower()
    if not category:
        category = road_class_category_name(safe_str(section_rule.get("road_class")) or row_declared_road_class(row))
    fallback = SYMMETRIC_DEFAULT_SECTION_BY_CATEGORY.get(category)
    if fallback in requirements:
        return fallback
    return source_code


def modeled_section_requirement(row: pd.Series, section_rule: dict[str, Any] | None = None) -> dict[str, Any] | None:
    section_rule = section_rule or row_section_requirement(row)
    if section_rule is None:
        return None

    source_code = safe_str(section_rule.get("section_id") or section_rule.get("inferred_section")) or row_section_code(row)
    model_code = modeled_section_code_for_row(row, section_rule)
    if not model_code:
        return dict(section_rule)

    requirements = road_section_requirements()
    modeled = dict(requirements.get(model_code, section_rule))
    modeled.setdefault("section_id", model_code)
    if source_code and model_code != source_code:
        modeled["source_section_id"] = source_code
        modeled["symmetry_normalized_from"] = source_code
        modeled["source_components"] = section_rule.get("components", [])
        modeled["source_total_width"] = section_rule.get("total_width")
    if section_rule.get("inferred_from"):
        modeled["inferred_from"] = section_rule.get("inferred_from")
        modeled["inferred_section"] = source_code or section_rule.get("inferred_section")
    return modeled


def source_cross_section_components_for_row(row: pd.Series) -> list[dict[str, Any]]:
    section_rule = row_section_requirement(row)
    raw_components = section_rule.get("components") if section_rule else None
    if not isinstance(raw_components, list):
        return []
    return [dict(component) for component in raw_components if float(component.get("width", 0.0) or 0.0) > 0.01]


def normalize_outer_sidewalk_components(
    components: list[dict[str, Any]],
    section_code: str | None = None,
) -> list[dict[str, Any]]:
    """Keep sidewalks as the outermost components."""
    normalized = [
        dict(component)
        for component in components
        if float(component.get("width", 0.0) or 0.0) > 0.01
    ]
    if not normalized:
        return normalized

    def is_sidewalk(component: dict[str, Any]) -> bool:
        return str(component.get("type", "")) == "sidewalk"

    if not any(is_sidewalk(component) for component in normalized):
        return normalized

    if not is_sidewalk(normalized[0]):
        sidewalk_idx = next(
            (idx for idx, component in enumerate(normalized) if is_sidewalk(component)),
            None,
        )
        if sidewalk_idx is not None:
            normalized.insert(0, normalized.pop(sidewalk_idx))

    if not is_sidewalk(normalized[-1]):
        sidewalk_indices = [
            idx
            for idx, component in enumerate(normalized)
            if is_sidewalk(component)
        ]
        movable_indices = [idx for idx in sidewalk_indices if idx != 0]
        if movable_indices:
            normalized.append(normalized.pop(movable_indices[-1]))
        elif sidewalk_indices:
            normalized.append(dict(normalized[sidewalk_indices[-1]]))

    return normalized


def row_target_total_width(row: pd.Series, section_rule: dict[str, Any] | None = None) -> float | None:
    # The section-rule library is sourced from the authored PPT cross-sections.
    # When a road carries a recognized section code, keep its modeled width tied
    # to that authored section instead of stretching to a source width field.
    if section_rule and "total_width" in section_rule:
        return float(section_rule["total_width"])
    width = parse_road_width_m(row.get("osm_width", row.get("width")))
    if width is not None:
        return width
    if section_rule and "total_width" in section_rule:
        return float(section_rule["total_width"])
    return None


def cross_section_components_for_row(row: pd.Series) -> list[dict[str, Any]]:
    """Return the modeled cross-section component sequence for one road.

    The function keeps source engineering sections when they are already
    symmetric. If the source section is one-sided, it applies the configured
    symmetric fallback so the generated 3D road has balanced left/right visual
    components. Components are then scaled to the target width when the source
    row carries a usable width attribute.
    """
    source_section_rule = row_section_requirement(row)
    section_rule = modeled_section_requirement(row, source_section_rule)
    raw_components = section_rule.get("components") if section_rule else None
    if not isinstance(raw_components, list) or not raw_components:
        return []

    components = [dict(component) for component in raw_components if float(component.get("width", 0.0) or 0.0) > 0.01]
    source_code = safe_str(section_rule.get("source_section_id")) if section_rule else None
    model_code = safe_str(section_rule.get("section_id")) if section_rule else None
    normalized_section_code = source_code or model_code or row_section_code(row)
    components = normalize_outer_sidewalk_components(components, normalized_section_code)
    base_width = component_total_width(components)
    target_width = row_target_total_width(row, source_section_rule or section_rule)
    if (
        source_code
        and model_code
        and source_code != model_code
        and source_code in SYMMETRIC_FALLBACK_KEEP_MODEL_WIDTH
    ):
        target_width = float(section_rule.get("total_width", base_width) or base_width)
    if source_code and model_code and source_code != model_code:
        for component in components:
            component["source_section_id"] = source_code
            component["model_section_id"] = model_code
            component["symmetry_normalized"] = True
    if base_width > 0.0 and target_width is not None and abs(target_width - base_width) > 0.05:
        scale = float(target_width) / base_width
        for component in components:
            component["width"] = float(component["width"]) * scale
            component["scaled_from_width"] = base_width
    return components


def component_width_by_type(components: list[dict[str, Any]], types: set[str]) -> float:
    return sum(float(component.get("width", 0.0) or 0.0) for component in components if component.get("type") in types)


def section_category_name(section_rule: dict[str, Any] | None, road_class: str = "") -> str:
    category = safe_str(section_rule.get("category") or section_rule.get("grade")) if section_rule else None
    if category:
        return category
    return road_class_category_name(road_class)


def road_class_category_name(road_class: str = "") -> str:
    text = road_class.lower()
    if "motorway" in text or "trunk" in text:
        return "expressway"
    if "primary" in text:
        return "arterial"
    if "secondary" in text:
        return "secondary"
    return "branch"


def modeled_side_reserve_width(section_rule: dict[str, Any] | None, road_class: str, full_side_reserve: float) -> float:
    category = road_class_category_name(road_class)
    if category == "branch" and "tertiary" not in road_class and "residential" not in road_class and "service" not in road_class:
        category = section_category_name(section_rule, road_class)
    limit = VISUAL_SIDE_RESERVE_LIMITS_M.get(category, VISUAL_SIDE_RESERVE_LIMITS_M["branch"])
    if full_side_reserve <= 0.0:
        return 0.0
    return min(full_side_reserve, limit)


def normalize_source_road_class(value: Any) -> str:
    text = safe_str(value) or ""
    text_lower = text.lower()
    if "快速" in text or "express" in text_lower:
        return "trunk"
    if "主干" in text or "primary" in text_lower or "arterial" in text_lower:
        return "primary"
    if "次干" in text or "secondary" in text_lower:
        return "secondary"
    if "支路" in text or "tertiary" in text_lower:
        return "tertiary"
    if "居住" in text or "residential" in text_lower:
        return "residential"
    return text_lower or "unclassified"


def source_road_name(row: pd.Series) -> str:
    for column in ("name", "roadname", "road_name", "Name", "NAME", "section", "备注"):
        value = row.get(column)
        text = safe_str(value)
        if text:
            return text
    return "unknown"


def source_road_class(row: pd.Series) -> str:
    for column in ("highway", "roadclass", "road_class", "class", "等级"):
        value = row.get(column)
        text = safe_str(value)
        if text:
            return normalize_source_road_class(text)
    section_rule = row_section_requirement(row)
    if section_rule is not None:
        return str(section_rule["road_class"])
    return "unclassified"


def canonical_road_class(value: Any, rules: dict[str, RoadRule]) -> str:
    text = (safe_str(value) or "").lower()
    if text in rules:
        return text

    priority = [
        "motorway",
        "primary",
        "secondary",
        "tertiary",
        "residential",
        "service",
        "living_street",
        "unclassified",
    ]
    for road_class in priority:
        if road_class not in text:
            continue
        if road_class in rules:
            return road_class
        if road_class == "living_street" and "residential" in rules:
            return "residential"
        if road_class == "unclassified" and "tertiary" in rules:
            return "tertiary"
    return "default_road"


def get_road_rule(row: pd.Series, rules: dict[str, RoadRule]) -> RoadRule:
    """依据原始道路属性和断面规则动态计算道路宽度、车道和人行道配置。"""
    source_section_rule = row_section_requirement(row)
    section_rule = modeled_section_requirement(row, source_section_rule)
    declared_road_class = row_declared_road_class(row)
    raw_road_class = safe_str(row.get("road_class")) or declared_road_class
    if source_section_rule is not None and (not raw_road_class or raw_road_class.lower() in {"unknown", "unclassified"}):
        road_class = str(source_section_rule["road_class"])
    else:
        road_class = canonical_road_class(raw_road_class, rules)
    base_rule = rules.get(road_class, rules.get("default_road"))
    rule = copy.copy(base_rule)
    if section_rule is not None:
        rule.lane_count = int(section_rule["lane_count"])
        rule.lane_width = float(section_rule["lane_width"])
        rule.road_width = float(section_rule.get("road_width", rule.lane_count * rule.lane_width))
    cross_section_components = cross_section_components_for_row(row)
    main_component_width = component_width_by_type(cross_section_components, {"main_carriageway", "carriageway"})
    service_component_width = component_width_by_type(cross_section_components, {"service_lane"})
    if cross_section_components and main_component_width > 0.0:
        rule.road_width = main_component_width
        total_component_width = component_total_width(cross_section_components)
        rule.sidewalk_width = max(0.0, (total_component_width - main_component_width - service_component_width) / 2.0)
    
    # 覆盖车道数并重新计算道路总宽度
    forward_lanes = parse_optional_lane_count(row.get("lanes_forward"))
    backward_lanes = parse_optional_lane_count(row.get("lanes_backward"))
    directional_lanes = None
    if forward_lanes is not None or backward_lanes is not None:
        directional_lanes = max(1, (forward_lanes or 0) + (backward_lanes or 0))
    lanes = parse_lane_count(row.get("lane_count"), default=directional_lanes or rule.lane_count)
    if directional_lanes is not None and not safe_str(row.get("lane_count")):
        lanes = directional_lanes
    rule.lane_count = lanes
    if not cross_section_components and (section_rule is None or "road_width" not in section_rule):
        rule.road_width = lanes * rule.lane_width
    osm_width = parse_road_width_m(row.get("osm_width", row.get("width")))
    if cross_section_components:
        pass
    elif section_rule is not None:
        total_width = osm_width or float(section_rule.get("total_width", rule.road_width + rule.sidewalk_width * 2.0))
        carriageway_width = float(section_rule.get("road_width", rule.road_width))
        if total_width > carriageway_width:
            rule.road_width = carriageway_width
            full_side_reserve = max(0.0, (total_width - carriageway_width) / 2.0)
            rule.sidewalk_width = modeled_side_reserve_width(section_rule, road_class, full_side_reserve)
    elif osm_width is not None:
        lane_width = osm_width / max(lanes, 1)
        if 2.4 <= lane_width <= 4.5:
            rule.road_width = osm_width
            rule.lane_width = lane_width
    return rule
