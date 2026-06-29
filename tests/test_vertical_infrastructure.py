from __future__ import annotations

import sys
from pathlib import Path
import unittest

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from city.pipeline import (  # noqa: E402
    CIM3_PROFILE,
    CIM4_PROFILE,
    ROAD_SURFACE_BASE_Z_M,
    SUBWAY_TUNNEL_OUTER_RADIUS_M,
    JUNCTION_SURFACE_Z_OFFSET_M,
    build_city_road_semantic,
    build_sewer_only_utility_layer_sets,
    build_rounded_junction_surface_meshes,
    junction_surface_base_z,
    prepare_roads_for_surfaces,
    road_generation_profile_with_tree_switch,
    subway_tunnel_semantic_record,
)
from city.utility_pipes import (  # noqa: E402
    UTILITY_PIPE_SPECS,
    UTILITY_WELL_CHAMBER_DIAMETER_M,
    UTILITY_WELL_COVER_DIAMETER_M,
    UTILITY_WELL_COVER_THICKNESS_M,
    build_utility_well_mesh,
    build_utility_well_semantic_record,
    build_utility_pipe_meshes,
    collect_pipe_intersection_well_centers,
    extend_pipe_line_to_well_connections,
    extract_pipe_diameter_mm,
    validate_utility_subway_vertical_clearance,
)


class RoadElevationTests(unittest.TestCase):
    def test_surface_roads_start_at_three_meter_absolute_elevation(self):
        roads = gpd.GeoDataFrame(
            [{"id": "surface", "highway": "primary", "geometry": LineString([(0, 0), (20, 0)])}],
            geometry="geometry",
            crs="EPSG:4547",
        )

        prepared = prepare_roads_for_surfaces(roads)

        self.assertEqual(float(prepared.iloc[0]["ground_z_start"]), ROAD_SURFACE_BASE_Z_M)
        self.assertEqual(float(prepared.iloc[0]["road_z_mean"]), ROAD_SURFACE_BASE_Z_M)
        self.assertEqual(float(prepared.iloc[0]["elevation"]), ROAD_SURFACE_BASE_Z_M)

    def test_bridge_clearance_is_added_above_three_meter_surface_base(self):
        roads = gpd.GeoDataFrame(
            [
                {
                    "id": "bridge",
                    "highway": "primary",
                    "bridge": "yes",
                    "geometry": LineString([(0, 0), (20, 0)]),
                }
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )

        prepared = prepare_roads_for_surfaces(roads)
        row = prepared.iloc[0]

        self.assertEqual(
            float(row["road_z_mean"]),
            ROAD_SURFACE_BASE_Z_M + float(row["bridge_clearance"]),
        )

    def test_junction_patch_base_uses_member_road_elevation(self):
        roads = gpd.GeoDataFrame(
            [
                {"road_z_mean": ROAD_SURFACE_BASE_Z_M, "geometry": LineString([(0, 0), (1, 0)])},
                {"road_z_mean": ROAD_SURFACE_BASE_Z_M, "geometry": LineString([(0, 0), (0, 1)])},
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )

        self.assertEqual(
            junction_surface_base_z(roads, [(0, 0.0), (1, 0.0)]),
            ROAD_SURFACE_BASE_Z_M,
        )

    def test_junction_surface_mesh_uses_member_road_elevation(self):
        roads = gpd.GeoDataFrame(
            [{"road_z_mean": ROAD_SURFACE_BASE_Z_M, "geometry": LineString([(0, 0), (1, 0)])}],
            geometry="geometry",
            crs="EPSG:4547",
        )
        surfaces = [
            {
                "index": 0,
                "geometry": LineString([(0, 0), (1, 0), (1, 1), (0, 0)]).buffer(0.01),
                "members": [(0, 0.0)],
            }
        ]

        meshes = build_rounded_junction_surface_meshes(roads, surfaces)

        self.assertEqual(len(meshes), 1)
        self.assertAlmostEqual(
            float(meshes[0].bounds[0, 2]),
            ROAD_SURFACE_BASE_Z_M + JUNCTION_SURFACE_Z_OFFSET_M,
            places=6,
        )


class UtilityDiameterTests(unittest.TestCase):
    def test_missing_diameters_use_reduced_defaults(self):
        expected = {
            "Water": 300,
            "Sewer": 400,
            "Gas": 200,
            "Power": 200,
            "Telecom": 110,
        }

        for pipe_type, expected_dn in expected.items():
            with self.subTest(pipe_type=pipe_type):
                self.assertEqual(UTILITY_PIPE_SPECS[pipe_type]["default_dn_mm"], expected_dn)
                self.assertEqual(
                    extract_pipe_diameter_mm(pd.Series(dtype=object), pipe_type),
                    (expected_dn, "fallback_standard_default"),
                )

    def test_source_diameter_is_not_scaled(self):
        dn_mm, source = extract_pipe_diameter_mm(pd.Series({"DN": "DN800"}), "Water")

        self.assertEqual(dn_mm, 800)
        self.assertEqual(source, "attribute:DN")

    def test_sewer_pipe_uses_reference_magenta_material_color(self):
        self.assertEqual(UTILITY_PIPE_SPECS["Sewer"]["color"], [210, 24, 224, 255])
        self.assertEqual(UTILITY_PIPE_SPECS["Sewer"]["material_class"], "magenta_coated_gravity_pipe")


class UtilityLayerSelectionTests(unittest.TestCase):
    def test_utility_generation_uses_only_sewer_layers(self):
        water_lines = gpd.GeoDataFrame(
            [{"geometry": LineString([(0.0, 0.0), (1.0, 0.0)])}],
            geometry="geometry",
            crs="EPSG:4547",
        )
        sewer_lines = gpd.GeoDataFrame(
            [{"geometry": LineString([(0.0, 1.0), (1.0, 1.0)])}],
            geometry="geometry",
            crs="EPSG:4547",
        )
        gas_lines = gpd.GeoDataFrame(
            [{"geometry": LineString([(0.0, 2.0), (1.0, 2.0)])}],
            geometry="geometry",
            crs="EPSG:4547",
        )
        sewer_points = gpd.GeoDataFrame(
            [{"geometry": LineString([(0.0, 1.0), (0.0, 1.0)]).centroid}],
            geometry="geometry",
            crs="EPSG:4547",
        )

        utility_layers, utility_node_layers = build_sewer_only_utility_layer_sets(
            water_lines,
            sewer_lines,
            gas_lines,
            sewer_points,
        )

        self.assertEqual([item["pipe_type"] for item in utility_layers], ["Sewer"])
        self.assertIs(utility_layers[0]["layer"], sewer_lines)
        self.assertEqual([item["pipe_type"] for item in utility_node_layers], ["Sewer"])
        self.assertIs(utility_node_layers[0]["layer"], sewer_points)


class RoadTreeSwitchTests(unittest.TestCase):
    def test_cim4_profile_enables_trees_by_default(self):
        self.assertTrue(CIM4_PROFILE.generate_trees)
        self.assertFalse(CIM3_PROFILE.generate_trees)

    def test_tree_switch_overrides_only_cim4(self):
        cim4_without_trees = road_generation_profile_with_tree_switch("cim4", generate_trees=False)
        cim3_with_override = road_generation_profile_with_tree_switch("cim3", generate_trees=True)

        self.assertFalse(cim4_without_trees.generate_trees)
        self.assertFalse(cim3_with_override.generate_trees)

    def test_semantic_profile_exposes_tree_switch(self):
        roads = gpd.GeoDataFrame(geometry=[], crs="EPSG:4547")

        semantic = build_city_road_semantic(roads, (0.0, 0.0), CIM4_PROFILE)

        self.assertIn("generate_trees", semantic["generation_profile"])
        self.assertEqual(semantic["generation_profile"]["generate_trees"], CIM4_PROFILE.generate_trees)


class UtilityWellTests(unittest.TestCase):
    def test_cim4_well_uses_standardized_chamber_and_cover_dimensions(self):
        mesh = build_utility_well_mesh(
            "Utility_MEP_Well_Test",
            (0.0, 0.0),
            UTILITY_WELL_CHAMBER_DIAMETER_M * 0.5,
            3.0,
            "cim4",
        )

        self.assertGreater(float(mesh.metadata["well_base_flange_diameter_m"]), UTILITY_WELL_CHAMBER_DIAMETER_M)
        self.assertGreater(float(mesh.extents[0]), UTILITY_WELL_CHAMBER_DIAMETER_M)
        self.assertGreater(float(mesh.extents[1]), UTILITY_WELL_CHAMBER_DIAMETER_M)
        self.assertAlmostEqual(float(mesh.bounds[1, 2]), -0.05, places=6)
        self.assertEqual(mesh.metadata["well_style"], "straight_chamber_with_raised_rims_and_flat_cover")
        self.assertEqual(mesh.metadata["well_has_tapered_cone"], False)
        self.assertEqual(mesh.metadata["well_top_cover_style"], "plain_recessed_disc")

    def test_cim4_well_is_more_detailed_than_cim3(self):
        cim3 = build_utility_well_mesh("Well_CIM3", (0.0, 0.0), 0.5, 3.0, "cim3")
        cim4 = build_utility_well_mesh("Well_CIM4", (0.0, 0.0), 0.5, 3.0, "cim4")

        self.assertGreater(len(cim4.faces), len(cim3.faces))

    def test_well_semantics_preserve_source_ring_size_and_standard_dimensions(self):
        record = build_utility_well_semantic_record(
            "ring-1",
            "source_shp_ring",
            (1.0, 2.0),
            UTILITY_WELL_CHAMBER_DIAMETER_M * 0.5,
            3.0,
            "Sewer",
            source_rule="closed_ring_to_well",
            source_ring_radius_m=1.4,
        )

        self.assertEqual(record["well_type"], "straight_precast_concrete_chamber")
        self.assertEqual(record["well_has_tapered_cone"], False)
        self.assertEqual(record["well_top_cover_style"], "plain_recessed_disc")
        self.assertEqual(record["well_chamber_diameter_m"], UTILITY_WELL_CHAMBER_DIAMETER_M)
        self.assertEqual(record["well_cover_diameter_m"], UTILITY_WELL_COVER_DIAMETER_M)
        self.assertEqual(record["well_cover_thickness_m"], UTILITY_WELL_COVER_THICKNESS_M)
        self.assertEqual(record["source_ring_diameter_m"], 2.8)

    def test_pipe_endpoint_near_well_extends_into_well_chamber(self):
        coords, extension_count, connections = extend_pipe_line_to_well_connections(
            [(1.2, 0.0), (10.0, 0.0)],
            [(0.0, 0.0)],
            search_radius_m=2.0,
        )

        self.assertEqual(extension_count, 1)
        self.assertEqual(coords[0], (0.0, 0.0))
        self.assertEqual(coords[-1], (10.0, 0.0))
        self.assertEqual(connections[0]["endpoint"], "start")
        self.assertAlmostEqual(connections[0]["extension_length_m"], 1.2)

    def test_pipe_endpoint_far_from_well_is_not_extended(self):
        coords, extension_count, connections = extend_pipe_line_to_well_connections(
            [(3.0, 0.0), (10.0, 0.0)],
            [(0.0, 0.0)],
            search_radius_m=2.0,
        )

        self.assertEqual(extension_count, 0)
        self.assertEqual(coords[0], (3.0, 0.0))
        self.assertEqual(connections, [])

    def test_water_wells_can_be_derived_from_pipe_intersections_without_rings(self):
        layer = gpd.GeoDataFrame(
            [
                {"geometry": LineString([(-5.0, 0.0), (0.0, 0.0)])},
                {"geometry": LineString([(0.0, 0.0), (5.0, 0.0)])},
                {"geometry": LineString([(10.0, 0.0), (15.0, 0.0)])},
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )

        centers = collect_pipe_intersection_well_centers(layer)

        self.assertEqual(centers, [(0.0, 0.0)])

    def test_water_wells_can_be_derived_from_geometric_crossings_without_split_vertices(self):
        layer = gpd.GeoDataFrame(
            [
                {"geometry": LineString([(-5.0, 0.0), (5.0, 0.0)])},
                {"geometry": LineString([(0.0, -5.0), (0.0, 5.0)])},
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )

        centers = collect_pipe_intersection_well_centers(layer)

        self.assertEqual(centers, [(0.0, 0.0)])

    def test_pipe_endpoint_near_line_gets_derived_well(self):
        layer = gpd.GeoDataFrame(
            [
                {"geometry": LineString([(0.0, -5.0), (0.0, 5.0)])},
                {"geometry": LineString([(0.35, 0.0), (5.0, 0.0)])},
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )

        centers = collect_pipe_intersection_well_centers(layer)

        self.assertEqual(centers, [(0.0, 0.0)])

    def test_any_pipe_type_with_intersections_gets_derived_wells(self):
        gas_layer = gpd.GeoDataFrame(
            [
                {"geometry": LineString([(-5.0, 0.0), (5.0, 0.0)])},
                {"geometry": LineString([(0.0, -5.0), (0.0, 5.0)])},
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )
        roads = gpd.GeoDataFrame(geometry=[], crs="EPSG:4547")

        _, records = build_utility_pipe_meshes(
            [{"pipe_type": "Gas", "layer": gas_layer}],
            roads,
            detail_level="cim3",
        )

        derived_gas_wells = [
            record
            for record in records
            if record.get("connected_pipe_type") == "Gas"
            and record.get("source_rule") == "pipe_intersection_to_well"
        ]
        self.assertEqual(len(derived_gas_wells), 1)
        self.assertEqual(derived_gas_wells[0]["center_xy_m"], [0.0, 0.0])

    def test_cross_type_intersections_do_not_create_overlapping_derived_wells(self):
        water_layer = gpd.GeoDataFrame(
            [
                {"geometry": LineString([(-5.0, 0.0), (5.0, 0.0)])},
                {"geometry": LineString([(0.0, -5.0), (0.0, 5.0)])},
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )
        gas_layer = gpd.GeoDataFrame(
            [
                {"geometry": LineString([(-5.0, 0.0), (5.0, 0.0)])},
                {"geometry": LineString([(0.0, -5.0), (0.0, 5.0)])},
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )
        roads = gpd.GeoDataFrame(geometry=[], crs="EPSG:4547")

        _, records = build_utility_pipe_meshes(
            [
                {"pipe_type": "Water", "layer": water_layer},
                {"pipe_type": "Gas", "layer": gas_layer},
            ],
            roads,
            detail_level="cim3",
        )

        derived_wells = [
            record
            for record in records
            if record.get("source_rule") == "pipe_intersection_to_well"
        ]
        self.assertEqual(derived_wells, [])

    def test_pipe_bend_vertex_gets_derived_well(self):
        layer = gpd.GeoDataFrame(
            [
                {"geometry": LineString([(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)])},
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )

        centers = collect_pipe_intersection_well_centers(layer)

        self.assertEqual(centers, [(5.0, 0.0)])

    def test_nearly_straight_pipe_vertex_does_not_get_derived_well(self):
        layer = gpd.GeoDataFrame(
            [
                {"geometry": LineString([(0.0, 0.0), (5.0, 0.0), (10.0, 0.1)])},
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )

        centers = collect_pipe_intersection_well_centers(layer)

        self.assertEqual(centers, [])


class VerticalClearanceTests(unittest.TestCase):
    def test_overlapping_utility_above_subway_passes(self):
        utility_records = [
            {
                "object_id": "Utility_Water_1",
                "pipe_type": "Water",
                "bottom_z_m": -1.5,
                "horizontal_bounds_xy_m": [0.0, -1.0, 10.0, 1.0],
            }
        ]
        subway_records = [
            {
                "object_name": "Subway_1",
                "tunnel_depth_m": -14.0,
                "outer_radius_m": SUBWAY_TUNNEL_OUTER_RADIUS_M,
                "horizontal_bounds_xy_m": [4.0, -2.0, 6.0, 2.0],
            }
        ]

        result = validate_utility_subway_vertical_clearance(utility_records, subway_records)

        self.assertTrue(result["vertical_order_ok"])
        self.assertEqual(result["checked_pair_count"], 1)
        self.assertGreater(result["minimum_vertical_clearance_m"], 0.0)
        self.assertTrue(utility_records[0]["quality_flags"]["above_nearby_subway"])

    def test_overlapping_utility_below_subway_top_is_flagged(self):
        utility_records = [
            {
                "object_id": "Utility_Sewer_1",
                "pipe_type": "Sewer",
                "bottom_z_m": -12.0,
                "horizontal_bounds_xy_m": [0.0, -1.0, 10.0, 1.0],
            }
        ]
        subway_records = [
            {
                "object_name": "Subway_1",
                "tunnel_depth_m": -14.0,
                "outer_radius_m": 3.0,
                "horizontal_bounds_xy_m": [4.0, -2.0, 6.0, 2.0],
            }
        ]

        result = validate_utility_subway_vertical_clearance(utility_records, subway_records)

        self.assertFalse(result["vertical_order_ok"])
        self.assertEqual(result["violation_count"], 1)
        self.assertFalse(utility_records[0]["quality_flags"]["above_nearby_subway"])

    def test_subway_semantics_expose_absolute_vertical_extents(self):
        row = pd.Series(
            {
                "OBJECTID": "metro-1",
                "name": "Metro 1",
                "type": "subway",
                "geometry": LineString([(0.0, 0.0), (10.0, 0.0)]),
            }
        )

        record = subway_tunnel_semantic_record(row, 0, (0.0, 0.0), {0: -14.0})

        self.assertEqual(record["absolute_z_datum"], "model_local_z_meter")
        self.assertEqual(record["road_surface_base_z_m"], ROAD_SURFACE_BASE_Z_M)
        self.assertAlmostEqual(
            record["tunnel_top_z_m"],
            -14.0 + SUBWAY_TUNNEL_OUTER_RADIUS_M,
            places=3,
        )
        self.assertEqual(record["horizontal_bounds_xy_m"], [0.0, 0.0, 10.0, 0.0])


if __name__ == "__main__":
    unittest.main()
