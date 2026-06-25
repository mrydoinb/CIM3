from __future__ import annotations

import sys
from pathlib import Path
import unittest

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString
from shapely.geometry import Point


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from city.pipeline import (  # noqa: E402
    SUBWAY_CIM3_PROFILE,
    SUBWAY_CIM4_PROFILE,
    SUBWAY_LINE_11_PAIR_AXIS_LATERAL_SPACING_M,
    SUBWAY_TRACK_GAUGE_M,
    SUBWAY_TUNNEL_LINING_THICKNESS_M,
    SUBWAY_TUNNEL_MIN_BEND_RADIUS_M,
    SUBWAY_TUNNEL_OUTER_RADIUS_M,
    SUBWAY_TUNNEL_RADIUS_M,
    SUBWAY_TUNNEL_SWEEP_MAX_SEGMENT_M,
    RAIL_LINES_PATH,
    build_subway_tunnel_meshes,
    load_layer,
    localize,
    resample_subway_centerline_coords,
    safe_subway_lining_centerline_coords,
    smooth_subway_centerline_coords,
    subway_closed_section_sweep,
    subway_centerline_min_bend_radius_m,
    subway_rectangle_section_offsets,
    subway_template_section_sweep,
    subway_tunnel_generation_corridors,
)
from city.geodata import compute_origin  # noqa: E402


def curved_subway_row() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "OBJECTID": "curve-test",
                "name": "测试地铁弯道",
                "type": "subway",
                "geometry": LineString([(0.0, 0.0), (12.0, 0.0), (12.0, 12.0)]),
            }
        ],
        geometry="geometry",
        crs="EPSG:4547",
    )


def mesh_by_component(meshes, component: str):
    matches = [
        mesh
        for mesh in meshes.values()
        if str(mesh.metadata.get("component") or "") == component
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one mesh for {component}, found {len(matches)}")
    return matches[0]


def curved_track_centerline() -> LineString:
    coords = safe_subway_lining_centerline_coords(
        [(0.0, 0.0, -14.0), (12.0, 0.0, -14.0), (12.0, 12.0, -14.0)],
        line_name="测试地铁弯道",
        source_id="curve-test",
        part_index=0,
    )
    return LineString([(coord[0], coord[1]) for coord in coords])


class SubwayCurvedSweepUnitTests(unittest.TestCase):
    def test_straight_centerline_remains_straight_after_smoothing_and_resampling(self):
        curve = resample_subway_centerline_coords(
            smooth_subway_centerline_coords(
                [(0.0, 5.0, -14.0), (12.0, 5.0, -14.0), (24.0, 5.0, -14.0)],
                iterations=2,
            )
        )

        self.assertTrue(curve)
        self.assertTrue(all(abs(point[1] - 5.0) <= 1e-9 for point in curve))
        self.assertEqual(curve[0], (0.0, 5.0, -14.0))
        self.assertEqual(curve[-1], (24.0, 5.0, -14.0))

    def test_resampling_limits_every_sweep_chord(self):
        curve = resample_subway_centerline_coords(
            smooth_subway_centerline_coords(
                [(0.0, 0.0, -14.0), (12.0, 0.0, -14.0), (12.0, 12.0, -14.0)],
                iterations=2,
            )
        )

        chord_lengths = [
            float(np.linalg.norm(np.array(right[:2]) - np.array(left[:2])))
            for left, right in zip(curve, curve[1:])
        ]

        self.assertTrue(chord_lengths)
        self.assertLessEqual(max(chord_lengths), SUBWAY_TUNNEL_SWEEP_MAX_SEGMENT_M + 1e-9)

    def test_curved_lining_is_one_watertight_solid(self):
        curve = resample_subway_centerline_coords(
            smooth_subway_centerline_coords(
                [(0.0, 0.0, -14.0), (12.0, 0.0, -14.0), (12.0, 12.0, -14.0)],
                iterations=2,
            )
        )

        lining = subway_template_section_sweep(
            "curved_lining",
            curve,
            [160, 160, 160, 255],
            thickness_m=SUBWAY_TUNNEL_LINING_THICKNESS_M,
        )

        self.assertIsNotNone(lining)
        self.assertTrue(lining.is_watertight)
        self.assertTrue(lining.is_winding_consistent)
        self.assertEqual(len(lining.split(only_watertight=False)), 1)

    def test_offset_linear_sweep_follows_curve_as_one_solid(self):
        curve = resample_subway_centerline_coords(
            smooth_subway_centerline_coords(
                [(0.0, 0.0, -14.0), (12.0, 0.0, -14.0), (12.0, 12.0, -14.0)],
                iterations=2,
            )
        )

        platform = subway_closed_section_sweep(
            "curved_platform",
            curve,
            subway_rectangle_section_offsets(0.9, 0.32),
            [130, 130, 130, 255],
            lateral_offset_m=1.95,
        )

        self.assertIsNotNone(platform)
        self.assertTrue(platform.is_watertight)
        self.assertEqual(len(platform.split(only_watertight=False)), 1)


class SubwayCurvedTunnelIntegrationTests(unittest.TestCase):
    def test_close_parallel_same_line_sources_get_lateral_translation(self):
        source = gpd.GeoDataFrame(
            [
                {
                    "OBJECTID": "line1-a",
                    "name": "深圳地铁1号线",
                    "type": "subway",
                    "geometry": LineString([(0.0, 0.0), (120.0, 0.0)]),
                },
                {
                    "OBJECTID": "line1-b",
                    "name": "深圳地铁1号线",
                    "type": "subway",
                    "geometry": LineString([(0.0, 2.8), (120.0, 2.8)]),
                },
                {
                    "OBJECTID": "line11-a",
                    "name": "深圳地铁11号线",
                    "type": "subway",
                    "geometry": LineString([(0.0, 20.0), (120.0, 20.0)]),
                },
                {
                    "OBJECTID": "line11-b",
                    "name": "深圳地铁11号线",
                    "type": "subway",
                    "geometry": LineString([(0.0, 22.8), (120.0, 22.8)]),
                },
                {
                    "OBJECTID": "line2-a",
                    "name": "深圳地铁2号线",
                    "type": "subway",
                    "geometry": LineString([(0.0, 40.0), (120.0, 40.0)]),
                },
                {
                    "OBJECTID": "line2-b",
                    "name": "深圳地铁2号线",
                    "type": "subway",
                    "geometry": LineString([(0.0, 42.8), (120.0, 42.8)]),
                },
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )

        corridors = subway_tunnel_generation_corridors(source)

        by_line = {
            line_name: [row for _, row in group.iterrows()]
            for line_name, group in corridors.groupby("_subway_line_name")
        }
        for line_name in ("深圳地铁1号线", "深圳地铁11号线", "深圳地铁2号线"):
            with self.subTest(line_name=line_name):
                rows = by_line[line_name]
                self.assertEqual(len(rows), 2)
                distance = float(rows[0].geometry.distance(rows[1].geometry))
                self.assertGreaterEqual(distance, SUBWAY_TUNNEL_OUTER_RADIUS_M * 2.0)
                self.assertTrue(
                    any(
                        abs(float(row["_subway_lateral_translation_x_m"])) > 0.0
                        or abs(float(row["_subway_lateral_translation_y_m"])) > 0.0
                        for row in rows
                    )
                )

    def test_line_11_uses_larger_same_line_lateral_spacing(self):
        source = gpd.GeoDataFrame(
            [
                {
                    "OBJECTID": "line11-a",
                    "name": "深圳地铁11号线",
                    "type": "subway",
                    "geometry": LineString([(0.0, 20.0), (120.0, 20.0)]),
                },
                {
                    "OBJECTID": "line11-b",
                    "name": "深圳地铁11号线",
                    "type": "subway",
                    "geometry": LineString([(0.0, 22.8), (120.0, 22.8)]),
                },
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )

        corridors = subway_tunnel_generation_corridors(source)

        self.assertEqual(len(corridors), 2)
        for _, corridor in corridors.iterrows():
            self.assertEqual(
                float(corridor["_subway_same_line_track_spacing_m"]),
                SUBWAY_LINE_11_PAIR_AXIS_LATERAL_SPACING_M,
            )

    def test_real_line_2_corridors_have_no_lining_overlap(self):
        railways = load_layer(RAIL_LINES_PATH)
        railways = localize(railways, compute_origin(railways))

        corridors = subway_tunnel_generation_corridors(railways)
        line_2 = corridors[
            corridors["_subway_line_name"] == "深圳地铁2号线"
        ]

        self.assertEqual(len(line_2), 2)
        left, right = list(line_2.geometry)
        self.assertGreaterEqual(
            float(left.distance(right)),
            SUBWAY_TUNNEL_OUTER_RADIUS_M * 2.0,
        )
        self.assertTrue(
            left.buffer(SUBWAY_TUNNEL_OUTER_RADIUS_M)
            .intersection(right.buffer(SUBWAY_TUNNEL_OUTER_RADIUS_M))
            .is_empty
        )
        translations = [
            (
                float(row["_subway_lateral_translation_x_m"]),
                float(row["_subway_lateral_translation_y_m"]),
            )
            for _, row in line_2.iterrows()
        ]
        self.assertLess(
            translations[0][0] * translations[1][0]
            + translations[0][1] * translations[1][1],
            0.0,
        )

    def test_real_line_11_multiline_tracks_are_split_and_clear(self):
        railways = load_layer(RAIL_LINES_PATH)
        railways = localize(railways, compute_origin(railways))

        corridors = subway_tunnel_generation_corridors(railways)
        line_11 = corridors[
            corridors["_subway_line_name"] == "深圳地铁11号线"
        ]

        self.assertGreaterEqual(len(line_11), 2)
        for geometry in line_11.geometry:
            self.assertEqual(geometry.geom_type, "LineString")

        translations = [
            (
                float(row["_subway_lateral_translation_x_m"]),
                float(row["_subway_lateral_translation_y_m"]),
            )
            for _, row in line_11.iterrows()
        ]
        self.assertLess(
            translations[0][0] * translations[1][0]
            + translations[0][1] * translations[1][1],
            0.0,
        )

        geometries = list(line_11.geometry)
        for left_index, left in enumerate(geometries):
            for right in geometries[left_index + 1 :]:
                overlap = left.buffer(SUBWAY_TUNNEL_OUTER_RADIUS_M).intersection(
                    right.buffer(SUBWAY_TUNNEL_OUTER_RADIUS_M)
                )
                self.assertTrue(
                    float(left.distance(right)) >= SUBWAY_TUNNEL_OUTER_RADIUS_M * 2.0
                    or overlap.is_empty
                )

    def test_real_shenzhen_same_line_corridors_clear_each_other_after_translation(self):
        railways = load_layer(RAIL_LINES_PATH)
        railways = localize(railways, compute_origin(railways))

        corridors = subway_tunnel_generation_corridors(railways)
        checked_pairs = 0
        for line_name, group in corridors.groupby("_subway_line_name"):
            rows = [row for _, row in group.iterrows()]
            for left_idx, left in enumerate(rows):
                for right in rows[left_idx + 1 :]:
                    checked_pairs += 1
                    distance = float(left.geometry.distance(right.geometry))
                    near_ratio = min(
                        float(
                            left.geometry.intersection(
                                right.geometry.buffer(SUBWAY_TUNNEL_OUTER_RADIUS_M * 2.0)
                            ).length
                        )
                        / max(float(left.geometry.length), 0.001),
                        float(
                            right.geometry.intersection(
                                left.geometry.buffer(SUBWAY_TUNNEL_OUTER_RADIUS_M * 2.0)
                            ).length
                        )
                        / max(float(right.geometry.length), 0.001),
                    )
                    self.assertTrue(
                        distance >= SUBWAY_TUNNEL_OUTER_RADIUS_M * 2.0 or near_ratio < 0.05,
                        f"{line_name} corridors {left.get('OBJECTID')} and {right.get('OBJECTID')} still have sustained overlap",
                    )
        self.assertGreaterEqual(checked_pairs, 3)

    def test_safe_lining_centerline_increases_smoothing_for_tight_line_11_bends(self):
        coords = [
            (0.0, 0.0, -14.0),
            (12.0, 0.0, -14.0),
            (12.0, 12.0, -14.0),
        ]

        default_curve = resample_subway_centerline_coords(
            smooth_subway_centerline_coords(coords, iterations=2)
        )
        safe_curve = safe_subway_lining_centerline_coords(
            coords,
            line_name="深圳地铁11号线",
            source_id="1230",
            part_index=2,
        )

        self.assertLess(
            subway_centerline_min_bend_radius_m(default_curve),
            SUBWAY_TUNNEL_MIN_BEND_RADIUS_M,
        )
        self.assertGreaterEqual(
            subway_centerline_min_bend_radius_m(safe_curve),
            SUBWAY_TUNNEL_MIN_BEND_RADIUS_M,
        )

    def test_cim3_excludes_evacuation_while_cim4_keeps_current_model(self):
        cim3_meshes = build_subway_tunnel_meshes(
            curved_subway_row(),
            profile=SUBWAY_CIM3_PROFILE,
            depth_by_index={0: -14.0},
        )
        cim4_meshes = build_subway_tunnel_meshes(
            curved_subway_row(),
            profile=SUBWAY_CIM4_PROFILE,
            depth_by_index={0: -14.0},
        )

        cim3_systems = {
            str(mesh.metadata.get("professional_system") or "")
            for mesh in cim3_meshes.values()
        }
        cim4_systems = {
            str(mesh.metadata.get("professional_system") or "")
            for mesh in cim4_meshes.values()
        }

        self.assertEqual(cim3_systems, {"structure", "track"})
        self.assertEqual(cim4_systems, {"structure", "track", "evacuation"})
        self.assertNotIn(
            "Ref08_Platform_Main",
            {
                str(mesh.metadata.get("component") or "")
                for mesh in cim3_meshes.values()
            },
        )
        self.assertIn(
            "Ref08_Platform_Main",
            {
                str(mesh.metadata.get("component") or "")
                for mesh in cim4_meshes.values()
            },
        )

    def test_sleepers_and_fasteners_follow_the_same_smoothed_curve_as_the_rails(self):
        meshes = build_subway_tunnel_meshes(
            curved_subway_row(),
            depth_by_index={0: -14.0},
            enabled_systems=["track"],
        )
        centerline = curved_track_centerline()

        fasteners = mesh_by_component(meshes, "Ref41_Rail_Fastener").split(
            only_watertight=False
        )
        isolation_pads = mesh_by_component(meshes, "Ref03_Rubber_Isolation").split(
            only_watertight=False
        )

        fastener_offsets = [
            centerline.distance(Point(float(part.centroid[0]), float(part.centroid[1])))
            for part in fasteners
        ]
        isolation_offsets = [
            centerline.distance(Point(float(part.centroid[0]), float(part.centroid[1])))
            for part in isolation_pads
        ]

        generated_components = {
            str(mesh.metadata.get("component") or "")
            for mesh in meshes.values()
        }
        self.assertNotIn("Ref39_Rail_Cast_Iron_Part", generated_components)
        for offsets, component in (
            (fastener_offsets, "fasteners"),
            (isolation_offsets, "isolation pads"),
        ):
            with self.subTest(component=component):
                self.assertTrue(offsets)
                self.assertLessEqual(
                    max(abs(offset - SUBWAY_TRACK_GAUGE_M * 0.5) for offset in offsets),
                    0.08,
                )

    def test_endpoint_connected_source_records_merge_without_plan_translation(self):
        source = gpd.GeoDataFrame(
            [
                {
                    "OBJECTID": "part-a",
                    "name": "测试地铁线路",
                    "type": "subway",
                    "geometry": LineString([(0.0, 0.0), (10.0, 0.0)]),
                },
                {
                    "OBJECTID": "part-b",
                    "name": "测试地铁线路",
                    "type": "subway",
                    "geometry": LineString([(10.0, 0.0), (15.0, 5.0)]),
                },
                {
                    "OBJECTID": "part-c",
                    "name": "测试地铁线路",
                    "type": "subway",
                    "geometry": LineString([(15.0, 5.0), (15.0, 15.0)]),
                },
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )

        corridors = subway_tunnel_generation_corridors(source)

        self.assertEqual(len(corridors), 1)
        corridor = corridors.iloc[0]
        self.assertEqual(corridor.geometry.geom_type, "LineString")
        self.assertAlmostEqual(float(corridor.geometry.length), 10.0 + 2**0.5 * 5.0 + 10.0)
        self.assertEqual(float(corridor["_subway_lateral_translation_x_m"]), 0.0)
        self.assertEqual(float(corridor["_subway_lateral_translation_y_m"]), 0.0)
        self.assertEqual(int(corridor["_subway_corridor_source_count"]), 3)

    def test_all_continuous_systems_remain_connected_through_a_turn(self):
        meshes = build_subway_tunnel_meshes(
            curved_subway_row(),
            depth_by_index={0: -14.0},
            enabled_systems=None,
        )

        expected_connected_components = {
            "Ref04_Concrete_Segment": 1,
            "Ref02_Aggregate_Base": 1,
            "Ref37_Rail_Bed_Surface": 1,
            "Ref38_Rail_Aluminum_Part": 2,
            "Ref40_Rail_Chrome_Part": 2,
            "Ref08_Platform_Main": 1,
            "Ref10_Platform_Edge_Strip": 1,
            "Ref11_Platform_Steel_Frame": 1,
            "Ref12_Platform_Concrete_Panel": 1,
        }

        for component, expected_count in expected_connected_components.items():
            with self.subTest(component=component):
                mesh = mesh_by_component(meshes, component)
                self.assertTrue(mesh.is_watertight)
                self.assertEqual(
                    len(mesh.split(only_watertight=False)),
                    expected_count,
                    f"{component} is still assembled from disconnected straight pieces",
                )

        rails = mesh_by_component(meshes, "Ref38_Rail_Aluminum_Part")
        self.assertGreater(float(rails.bounds[1, 0] - rails.bounds[0, 0]), SUBWAY_TRACK_GAUGE_M)
        self.assertGreater(float(rails.bounds[1, 1] - rails.bounds[0, 1]), SUBWAY_TUNNEL_RADIUS_M)
        generated_components = {
            str(mesh.metadata.get("component") or "")
            for mesh in meshes.values()
        }
        generated_systems = {
            str(mesh.metadata.get("professional_system") or "")
            for mesh in meshes.values()
        }
        self.assertNotIn("Ref39_Rail_Cast_Iron_Part", generated_components)
        self.assertNotIn("Ref05_Steel_Plate", generated_components)
        self.assertNotIn("Ref06_Seal_Ring", generated_components)
        self.assertNotIn("Ref07_Bolt", generated_components)
        self.assertEqual(generated_systems, {"structure", "track", "evacuation"})
        self.assertFalse(
            generated_components
            & {
                "Ref14_Contact_Rail",
                "Ref15_Contact_Hanger",
                "Ref16_Contact_Clamp",
                "Ref17_High_Voltage_Cable_Bracket",
                "Ref18_Comm_Cable_Bracket_A",
                "Ref19_Comm_Cable_Bracket_B",
                "Ref20_Leakage_Cable_A",
                "Ref21_Leakage_Cable_B",
                "Ref22_Leakage_Cable_C",
                "Ref26_Lighting_Fixture",
                "Ref27_Lighting_Cable",
                "Ref28_Lighting_Bracket",
            }
        )

    def test_guardrail_is_on_platform_side_nearest_the_tunnel_center(self):
        straight = gpd.GeoDataFrame(
            [
                {
                    "OBJECTID": "straight-platform-test",
                    "name": "测试地铁直线",
                    "type": "subway",
                    "geometry": LineString([(0.0, 0.0), (20.0, 0.0)]),
                }
            ],
            geometry="geometry",
            crs="EPSG:4547",
        )
        meshes = build_subway_tunnel_meshes(
            straight,
            depth_by_index={0: -14.0},
            enabled_systems=None,
        )

        platform = mesh_by_component(meshes, "Ref08_Platform_Main")
        guardrail = mesh_by_component(meshes, "Ref01_Guardrail")

        self.assertGreater(float(platform.centroid[1]), 0.0)
        self.assertGreater(float(guardrail.centroid[1]), 0.0)
        self.assertLess(float(guardrail.centroid[1]), float(platform.centroid[1]))


if __name__ == "__main__":
    unittest.main()
