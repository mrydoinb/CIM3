#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Download the Munich Hauptbahnhof CIM city test area from OpenStreetMap.

The historical filename is kept so existing workflow references do not break.
The content now targets a denser CIM3/CIM4 test area with roads, buildings,
transit points, and rail/subway centerlines.
"""

from __future__ import annotations

from pathlib import Path

import osmnx as ox


OUT_DIR = Path("data/raw")
CENTER_POINT = (48.1402, 11.5600)  # Munich Hauptbahnhof, lat/lon
DIST_M = 1000


def write_features(gdf, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if gdf.empty:
        print(f"[WARN] No features found for {path.name}")
    gdf.to_file(path, driver="GeoJSON")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    road_graph = ox.graph_from_point(
        center_point=CENTER_POINT,
        dist=DIST_M,
        network_type="drive",
        simplify=True,
    )
    _, road_edges = ox.graph_to_gdfs(road_graph)
    write_features(road_edges.reset_index(), OUT_DIR / "road_centerline.geojson")

    buildings = ox.features_from_point(
        CENTER_POINT,
        tags={"building": True},
        dist=DIST_M,
    ).reset_index()
    write_features(buildings, OUT_DIR / "building_footprint.geojson")

    transport = ox.features_from_point(
        CENTER_POINT,
        tags={
            "highway": "bus_stop",
            "railway": ["station", "subway_entrance", "platform"],
            "public_transport": ["platform", "stop_position", "station"],
            "station": "subway",
            "subway": True,
        },
        dist=DIST_M,
    ).reset_index()
    write_features(transport, OUT_DIR / "transport_points.geojson")

    railways = ox.features_from_point(
        CENTER_POINT,
        tags={"railway": ["subway", "light_rail", "rail", "tram"]},
        dist=DIST_M,
    ).reset_index()
    write_features(railways, OUT_DIR / "railway_centerline.geojson")

    print("Munich Hauptbahnhof OSM data download complete:")
    print("1. data/raw/road_centerline.geojson")
    print("2. data/raw/building_footprint.geojson")
    print("3. data/raw/transport_points.geojson")
    print("4. data/raw/railway_centerline.geojson")


if __name__ == "__main__":
    main()
