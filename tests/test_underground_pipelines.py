from __future__ import annotations

import sys
from pathlib import Path
import unittest
import math

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from city.underground_pipelines import (  # noqa: E402
    DATASET_LAYERS,
    BOX_CULVERT_COLOR,
    ROUND_PIPE_COLORS,
    box_culvert_profile_m,
    build_underground_pipeline_meshes,
    round_pipe_profile_m,
    well_profile_m,
)


class UndergroundPipelineProfileTests(unittest.TestCase):
    def test_round_pipe_centerline_uses_invert_elevation(self):
        profile = round_pipe_profile_m(
            start_invert_z=8.0,
            end_invert_z=9.0,
            diameter_mm=500,
            wall_mm=50,
        )

        self.assertAlmostEqual(profile["inner_radius_m"], 0.25)
        self.assertAlmostEqual(profile["outer_radius_m"], 0.30)
        self.assertAlmostEqual(profile["start_center_z_m"], 8.25)
        self.assertAlmostEqual(profile["end_center_z_m"], 9.25)
        self.assertAlmostEqual(profile["start_outer_bottom_z_m"], 7.95)

    def test_box_culvert_profile_parses_width_height_and_wall(self):
        profile = box_culvert_profile_m(
            start_invert_z=12.0,
            end_invert_z=13.0,
            spec="4300x3380",
            wall_mm=390,
        )

        self.assertAlmostEqual(profile["inner_width_m"], 4.3)
        self.assertAlmostEqual(profile["inner_height_m"], 3.38)
        self.assertAlmostEqual(profile["outer_width_m"], 5.08)
        self.assertAlmostEqual(profile["outer_height_m"], 4.16)
        self.assertAlmostEqual(profile["start_center_z_m"], 13.69)
        self.assertAlmostEqual(profile["start_outer_bottom_z_m"], 11.61)

    def test_well_profile_uses_design_elevation_depth_and_spec(self):
        profile = well_profile_m(
            design_z=23.0,
            coord_z=22.8,
            depth_m=3.8,
            node_spec="Φ700",
            length_mm=0,
            width_mm=0,
        )

        self.assertAlmostEqual(profile["top_z_m"], 23.0)
        self.assertAlmostEqual(profile["bottom_z_m"], 19.2)
        self.assertAlmostEqual(profile["cover_radius_m"], 0.35)
        self.assertAlmostEqual(profile["chamber_radius_m"], 0.64)
        self.assertAlmostEqual(profile["cover_thickness_m"], 0.05)

    def test_well_profile_matches_ws_reference_when_node_spec_is_missing(self):
        profile = well_profile_m(
            design_z=12.0,
            coord_z=12.0,
            depth_m=3.0,
            node_spec=pd.NA,
            length_mm=0,
            width_mm=0,
            system="Sewer",
            source_layer="ws_wells",
        )

        self.assertAlmostEqual(profile["cover_radius_m"], 0.40)
        self.assertAlmostEqual(profile["chamber_radius_m"], 0.85)
        self.assertAlmostEqual(profile["cover_thickness_m"], 0.04)

    def test_storm_well_without_depth_uses_reference_shallow_gray_cover_style(self):
        profile = well_profile_m(
            design_z=23.0,
            coord_z=23.0,
            depth_m=pd.NA,
            node_spec="Φ700",
            length_mm=700,
            width_mm=700,
            system="Storm",
            source_layer="sys02_ys_wells",
        )

        self.assertAlmostEqual(profile["depth_m"], 3.8)
        self.assertAlmostEqual(profile["cover_radius_m"], 0.35)
        self.assertAlmostEqual(profile["chamber_radius_m"], 0.59)
        self.assertAlmostEqual(profile["cover_thickness_m"], 0.10)

    def test_storm_well_without_depth_or_spec_but_with_system_type_uses_white_reference_cover(self):
        profile = well_profile_m(
            design_z=23.0,
            coord_z=23.0,
            depth_m=pd.NA,
            node_spec=pd.NA,
            length_mm=0,
            width_mm=0,
            system="Storm",
            source_layer="sys02_ys_wells",
            sys_type="雨水管线",
        )

        self.assertAlmostEqual(profile["cover_radius_m"], 0.35)
        self.assertAlmostEqual(profile["chamber_radius_m"], 0.64)
        self.assertAlmostEqual(profile["cover_thickness_m"], 0.05)
        self.assertEqual(profile["cover_material_key"], "white")

    def test_well_profile_includes_hollow_chamber_inner_radius(self):
        profile = well_profile_m(
            design_z=12.0,
            coord_z=12.0,
            depth_m=3.0,
            node_spec=pd.NA,
            length_mm=0,
            width_mm=0,
            system="Sewer",
            source_layer="ws_wells",
        )

        self.assertAlmostEqual(profile["chamber_wall_m"], 0.12)
        self.assertAlmostEqual(profile["chamber_inner_radius_m"], 0.73)

    def test_reference_fbx_pipe_colors_are_used(self):
        self.assertEqual(ROUND_PIPE_COLORS["Sewer"], [255, 0, 255, 255])
        self.assertEqual(ROUND_PIPE_COLORS["Water"], [255, 0, 0, 255])
        self.assertEqual(ROUND_PIPE_COLORS["Storm"], [0, 255, 255, 255])
        self.assertEqual(BOX_CULVERT_COLOR, [0, 255, 255, 255])

    def test_meshes_follow_reference_fbx_instance_split(self):
        layers = {
            "ws_pipes": gpd.GeoDataFrame(
                [
                    {
                        "diam_mm": 500,
                        "wall_mm": 50,
                        "start_z": 8.0,
                        "end_z": 8.0,
                        "geometry": LineString([(0.0, 0.0), (10.0, 0.0)]),
                    }
                ],
                geometry="geometry",
                crs="EPSG:4547",
            ),
            "ws_wells": gpd.GeoDataFrame(
                [
                    {
                        "design_z": 12.0,
                        "depth": 3.0,
                        "node_spec": pd.NA,
                        "len_mm": 0,
                        "wid_mm": 0,
                        "geometry": Point(0.0, 0.0),
                    }
                ],
                geometry="geometry",
                crs="EPSG:4547",
            ),
        }

        meshes, records = build_underground_pipeline_meshes("ws", layers, "cim4")

        self.assertEqual(len(records), 2)
        self.assertEqual(len(meshes), 3)
        self.assertIn("UG_ws_pipes_0000", meshes)
        self.assertIn("UG_ws_wells_0000_Chamber", meshes)
        self.assertIn("UG_ws_wells_0000_Cover", meshes)

    def test_round_pipe_mesh_has_empty_bore_with_inner_wall_vertices(self):
        layers = {
            "ws_pipes": gpd.GeoDataFrame(
                [
                    {
                        "diam_mm": 500,
                        "wall_mm": 50,
                        "start_z": 8.0,
                        "end_z": 8.0,
                        "geometry": LineString([(0.0, 0.0), (10.0, 0.0)]),
                    }
                ],
                geometry="geometry",
                crs="EPSG:4547",
            ),
            "ws_wells": gpd.GeoDataFrame([], geometry=gpd.GeoSeries([], crs="EPSG:4547")),
        }

        meshes, _ = build_underground_pipeline_meshes("ws", layers, "cim4")
        mesh = meshes["UG_ws_pipes_0000"]
        radii = [math.hypot(float(vertex[1]), float(vertex[2]) - 8.25) for vertex in mesh.vertices]

        self.assertTrue(any(abs(radius - 0.25) < 1e-6 for radius in radii))
        self.assertTrue(any(abs(radius - 0.30) < 1e-6 for radius in radii))
        self.assertGreater(min(radii), 0.24)

    def test_box_culvert_mesh_has_empty_rectangular_bore(self):
        def empty_layer() -> gpd.GeoDataFrame:
            return gpd.GeoDataFrame([], geometry=gpd.GeoSeries([], crs="EPSG:4547"))

        layers = {spec["layer"]: empty_layer() for spec in DATASET_LAYERS["sys02"]}
        layers["sys02_ys_box"] = gpd.GeoDataFrame(
            [
                {
                    "spec": "4300x3380",
                    "wall_mm": 390,
                    "start_z": 12.0,
                    "end_z": 12.0,
                    "geometry": LineString([(0.0, 0.0), (10.0, 0.0)]),
                }
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )

        meshes, _ = build_underground_pipeline_meshes("sys02", layers, "cim4")
        mesh = meshes["UG_sys02_ys_box_0000"]
        half_widths = {round(abs(float(vertex[1])), 2) for vertex in mesh.vertices}
        half_heights = {round(abs(float(vertex[2]) - 13.69), 2) for vertex in mesh.vertices}

        self.assertIn(2.54, half_widths)
        self.assertIn(2.15, half_widths)
        self.assertIn(2.08, half_heights)
        self.assertIn(1.69, half_heights)

    def test_well_chamber_mesh_has_empty_vertical_bore(self):
        layers = {
            "ws_pipes": gpd.GeoDataFrame([], geometry=gpd.GeoSeries([], crs="EPSG:4547")),
            "ws_wells": gpd.GeoDataFrame(
                [
                    {
                        "design_z": 12.0,
                        "depth": 3.0,
                        "node_spec": pd.NA,
                        "len_mm": 0,
                        "wid_mm": 0,
                        "geometry": Point(0.0, 0.0),
                    }
                ],
                geometry="geometry",
                crs="EPSG:4547",
            ),
        }

        meshes, _ = build_underground_pipeline_meshes("ws", layers, "cim4")
        chamber = meshes["UG_ws_wells_0000_Chamber"]
        radii = [math.hypot(float(vertex[0]), float(vertex[1])) for vertex in chamber.vertices]

        self.assertTrue(any(abs(radius - 0.73) < 1e-6 for radius in radii))
        self.assertTrue(any(abs(radius - 0.85) < 1e-6 for radius in radii))
        self.assertGreater(min(radii), 0.72)


if __name__ == "__main__":
    unittest.main()
