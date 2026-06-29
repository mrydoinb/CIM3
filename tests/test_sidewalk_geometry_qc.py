from __future__ import annotations

import sys
from pathlib import Path
import unittest

from shapely.geometry import Polygon


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from city.pipeline import (  # noqa: E402
    build_city_sidewalk_topology_qc,
    mesh_xy_footprint,
    trim_shared_sidewalk_overlaps,
    trim_sidewalk_pair_overlaps_by_priority,
    trim_sidewalk_meshes_against_conflicts,
)
from road import generator as road_gen  # noqa: E402


def top_mesh(name: str, geom: Polygon, layer_name: str = "Sidewalk"):
    mesh = road_gen.polygon_to_top_mesh(geom, 0.0, name)
    mesh.metadata.update({"name": name, "layer_name": layer_name})
    return mesh


class SidewalkGeometryQcTests(unittest.TestCase):
    def test_mesh_xy_footprint_preserves_concave_sidewalk_shape(self):
        concave = Polygon(
            [
                (0.0, 0.0),
                (4.0, 0.0),
                (4.0, 1.0),
                (1.0, 1.0),
                (1.0, 4.0),
                (0.0, 4.0),
            ]
        )
        mesh = top_mesh("Sidewalk_L", concave)

        footprint = mesh_xy_footprint(mesh)

        self.assertIsNotNone(footprint)
        self.assertAlmostEqual(float(footprint.area), float(concave.area), places=5)
        self.assertLess(float(footprint.area), float(concave.convex_hull.area))

    def test_trim_sidewalk_removes_parts_entering_road_or_junction(self):
        sidewalk = top_mesh("Sidewalk_Test", Polygon([(0, 0), (6, 0), (6, 2), (0, 2)]))
        conflict = Polygon([(4, -1), (8, -1), (8, 3), (4, 3)])

        cleaned, stats = trim_sidewalk_meshes_against_conflicts([sidewalk], conflict, clearance_m=0.0)

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(stats["trimmed_count"], 1)
        self.assertEqual(stats["removed_count"], 0)
        footprint = mesh_xy_footprint(cleaned[0])
        self.assertIsNotNone(footprint)
        self.assertAlmostEqual(float(footprint.area), 8.0, places=4)
        self.assertLess(float(footprint.intersection(conflict).area), 1e-6)

    def test_sidewalk_topology_qc_reports_gaps_and_overlaps(self):
        road_meshes = {
            "Sidewalk_A": top_mesh("Sidewalk_A", Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])),
            "Sidewalk_B": top_mesh("Sidewalk_B", Polygon([(2.5, 0), (4.5, 0), (4.5, 2), (2.5, 2)])),
            "Sidewalk_C": top_mesh("Sidewalk_C", Polygon([(4.0, 0), (5.0, 0), (5.0, 2), (4.0, 2)])),
            "Road_Surface_Main": top_mesh(
                "Road_Surface_Main",
                Polygon([(3.5, -1), (6.0, -1), (6.0, 3), (3.5, 3)]),
                layer_name="Road_Surface_Main",
            ),
        }

        report = build_city_sidewalk_topology_qc(
            road_meshes,
            connectivity_tolerance_m=0.2,
            near_gap_max_m=1.0,
            overlap_area_tolerance_m2=0.001,
        )

        self.assertGreater(report["connectivity"]["near_gap_count"], 0)
        self.assertGreater(report["overlap"]["sidewalk_pair_overlap_count"], 0)
        self.assertGreater(report["overlap"]["road_or_junction_overlap_count"], 0)

    def test_shared_sidewalk_overlap_cleanup_preserves_road_sidewalk(self):
        road_sidewalk = top_mesh("Road_1-Sidewalk", Polygon([(0, 0), (4, 0), (4, 2), (0, 2)]))
        shared_sidewalk = top_mesh("Sidewalk_Shared_shared", Polygon([(2, 0), (6, 0), (6, 2), (2, 2)]))
        road_meshes = {
            "Road_1-Sidewalk": road_sidewalk,
            "Sidewalk_Shared_shared": shared_sidewalk,
        }

        stats = trim_shared_sidewalk_overlaps(road_meshes)

        self.assertEqual(stats["trimmed_count"], 1)
        road_footprint = mesh_xy_footprint(road_meshes["Road_1-Sidewalk"])
        shared_footprint = mesh_xy_footprint(road_meshes["Sidewalk_Shared_shared"])
        self.assertIsNotNone(road_footprint)
        self.assertIsNotNone(shared_footprint)
        self.assertAlmostEqual(float(road_footprint.area), 8.0, places=4)
        self.assertLess(float(road_footprint.intersection(shared_footprint).area), 1e-6)

    def test_sidewalk_pair_overlap_cleanup_preserves_higher_priority_mesh(self):
        major = top_mesh("Major-Sidewalk", Polygon([(0, 0), (4, 0), (4, 2), (0, 2)]))
        minor = top_mesh("Minor-Sidewalk", Polygon([(2, 0), (6, 0), (6, 2), (2, 2)]))
        major.metadata["road_priority"] = 5
        minor.metadata["road_priority"] = 1
        road_meshes = {
            "Major-Sidewalk": major,
            "Minor-Sidewalk": minor,
        }

        stats = trim_sidewalk_pair_overlaps_by_priority(road_meshes)

        self.assertEqual(stats["trimmed_count"], 1)
        major_footprint = mesh_xy_footprint(road_meshes["Major-Sidewalk"])
        minor_footprint = mesh_xy_footprint(road_meshes["Minor-Sidewalk"])
        self.assertAlmostEqual(float(major_footprint.area), 8.0, places=4)
        self.assertLess(float(major_footprint.intersection(minor_footprint).area), 1e-6)


if __name__ == "__main__":
    unittest.main()
