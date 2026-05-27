#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Typed records shared by road generation and city junction code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shapely.geometry import LineString, Point, Polygon


@dataclass
class RoadRule:
    lane_count: int
    lane_width: float
    road_width: float
    sidewalk_width: float
    curb_width: float
    curb_height: float
    lane_marking_width: float
    road_z: float
    lane_marking_z_offset: float
    material: str
    sidewalk_material: str
    curb_material: str
    marking_material: str


@dataclass
class LaneSocket:
    lane_socket_id: str
    parent_socket_id: str
    road_id: str
    lane_index: int
    lane_role: str
    center: tuple[float, float]
    tangent: tuple[float, float]
    normal: tuple[float, float]
    lane_width: float
    allowed_movements: list[str]
    lane_id: str = ""
    movement: str = "through"
    allowed_turns: list[str] | None = None
    traffic_direction: str = "unknown"
    signal_group: str = "unsignalized"
    stop_control: str = "yield_or_stop_line"


@dataclass
class RoadSocket:
    socket_id: str
    road_id: str
    junction_id: str
    distance_m: float
    center: tuple[float, float]
    tangent: tuple[float, float]
    normal: tuple[float, float]
    road_width: float
    lane_count: int
    lane_width: float
    forward_lane_count: int
    backward_lane_count: int
    sidewalk_width: float
    curb_width: float
    clip_radius: float
    clip_distance: float
    line_direction_sign: float
    elevation: float
    approach_type: str
    left_edge: tuple[float, float]
    right_edge: tuple[float, float]
    lane_sockets: list[LaneSocket]
    road_class: str = "unknown"
    node_distance_m: float = 0.0
    generation_method: str = "socket"
    road_name: str = "unknown"


@dataclass
class JunctionNode:
    junction_id: str
    center: Point
    radius: float
    sockets: list[RoadSocket]
    junction_type: str = "UNKNOWN"
    hierarchy: str = "LOCAL_JUNCTION"
    surface_strategy: str = "edge_intersection"
    detection_method: str = "endpoint_cluster"
    metadata: dict[str, Any] | None = None


@dataclass
class LaneConnector:
    connector_id: str
    junction_id: str
    from_lane: LaneSocket
    to_lane: LaneSocket
    movement_type: str
    centerline: LineString
    width: float
    surface_polygon: Polygon
    marking_guides: list[LineString]


@dataclass(frozen=True)
class LaneLayout:
    lane_count: int
    forward_count: int
    backward_count: int
    center_turn_count: int = 0
