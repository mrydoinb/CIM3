#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Download the Beijing Yizhuang CIM city test area from OpenStreetMap.

The historical filename is kept so existing workflow references do not break.
The content now targets a cleaner CIM3/CIM4 test area with roads, buildings,
transit points, and rail/subway centerlines.
"""

from __future__ import annotations

import os
from pathlib import Path

import osmnx as ox


OUT_DIR = Path("data/raw_beijing_yizhuang")
CENTER_POINT = (39.7937, 116.5060)  # Beijing Yizhuang, lat/lon
PLACE_QUERY = os.getenv("OSM_PLACE_QUERY", "Beijing Yizhuang, Beijing, China")
DIST_M = float(os.getenv("OSM_DIST_M", "0") or 0.0)


def use_point_radius() -> bool:
    return DIST_M > 0.0


def graph_from_scope():
    if use_point_radius():
        return ox.graph_from_point(
            center_point=CENTER_POINT,
            dist=DIST_M,
            network_type="drive",
            simplify=True,
        )
    return ox.graph_from_place(
        PLACE_QUERY,
        network_type="drive",
        simplify=True,
    )


def features_from_scope(tags: dict):
    if use_point_radius():
        return ox.features_from_point(
            CENTER_POINT,
            tags=tags,
            dist=DIST_M,
        )
    return ox.features_from_place(PLACE_QUERY, tags=tags)


def write_features(gdf, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if gdf.empty:
        print(f"[WARN] No features found for {path.name}")
    if path.exists():
        path.unlink()
    gdf.to_file(path, driver="GeoJSON")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if use_point_radius():
        print(f"Downloading OSM data around {CENTER_POINT} within {DIST_M:g} m...")
    else:
        print(f"Downloading OSM data for place: {PLACE_QUERY}")

    road_graph = graph_from_scope()
    _, road_edges = ox.graph_to_gdfs(road_graph)
    write_features(road_edges.reset_index(), OUT_DIR / "road_centerline.geojson")

    buildings = features_from_scope(tags={"building": True}).reset_index()
    write_features(buildings, OUT_DIR / "building_footprint.geojson")

    transport = features_from_scope(
        tags={
            "highway": "bus_stop",
            "railway": ["station", "subway_entrance", "platform"],
            "public_transport": ["platform", "stop_position", "station"],
            "station": "subway",
            "subway": True,
        },
    ).reset_index()
    write_features(transport, OUT_DIR / "transport_points.geojson")

    railways = features_from_scope(
        tags={"railway": ["subway", "light_rail", "rail", "tram"]},
    ).reset_index()
    write_features(railways, OUT_DIR / "railway_centerline.geojson")

    print("Beijing Yizhuang OSM data download complete:")
    print("1. data/raw_beijing_yizhuang/road_centerline.geojson")
    print("2. data/raw_beijing_yizhuang/building_footprint.geojson")
    print("3. data/raw_beijing_yizhuang/transport_points.geojson")
    print("4. data/raw_beijing_yizhuang/railway_centerline.geojson")


if __name__ == "__main__":
    main()
