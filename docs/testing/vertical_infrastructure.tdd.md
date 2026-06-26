# Vertical Infrastructure TDD Evidence

## Source plan

The user supplied the implementation plan in the Codex conversation. The plan
required a 3 m road datum, unchanged absolute utility/subway elevations,
reduced fallback pipe diameters, smaller detailed utility wells, and an
explicit utility-over-subway clearance check.

## User journeys

- As a CIM model reviewer, I want all road components raised by 3 m so the
  complete road model shares one visible surface datum.
- As an underground-infrastructure reviewer, I want utilities to remain above
  subway tunnel envelopes so crossings cannot silently intersect.
- As a Blender reviewer, I want realistic lightweight wells and smaller
  fallback pipes so the underground model reads at a believable scale.

## RED/GREEN task report

| Behavior | RED evidence | GREEN evidence | Guarantee |
|---|---|---|---|
| Road and bridge datum | `python -m unittest tests.test_vertical_infrastructure` failed importing the missing `ROAD_SURFACE_BASE_Z_M` constant | The same target passed 12 tests | Surface roads use Z=3 m and bridges add their existing clearance above it |
| Fallback pipe diameters | Covered by the initial failing feature test module | The focused target passed | Missing values use DN300/DN400/DN200/DN200/DN110 while valid source DN is unchanged |
| Standardized well geometry | Covered by the initial failing feature test module | The focused target passed | CIM4 wells are capped at 1.0 m chamber diameter, use a 0.7 m cover, and are more detailed than CIM3 |
| Utility/subway order | Covered by the initial failing feature test module | The focused target passed | Nearby horizontal pairs compare utility bottom Z against subway outer-envelope top Z and flag violations |
| Existing subway behavior | Not applicable; regression target already existed | `python -m unittest tests.test_subway_curved_tunnel` passed 15 tests | Curved tunnel generation remains compatible |

## Validation results

| # | What is guaranteed | Test or command | Type | Result |
|---|---|---|---|---|
| 1 | Road surface and bridge elevations use the new datum | `tests/test_vertical_infrastructure.py` | Unit | PASS |
| 2 | Fallback and source pipe diameters follow the selected policy | `tests/test_vertical_infrastructure.py` | Unit | PASS |
| 3 | CIM3/CIM4 well geometry and semantic dimensions are stable | `tests/test_vertical_infrastructure.py` | Unit | PASS |
| 4 | Valid and invalid utility/subway vertical pairs are distinguished | `tests/test_vertical_infrastructure.py` | Unit | PASS |
| 5 | Subway semantics expose absolute top/bottom envelopes | `tests/test_vertical_infrastructure.py` | Unit | PASS |
| 6 | All repository tests remain green | `python -m unittest discover -s tests -p "test_*.py"` | Regression | PASS, 27 tests |
| 7 | Modified Python modules compile | `python -m py_compile src/city/pipeline.py src/city/utility_pipes.py tests/test_vertical_infrastructure.py` | Static | PASS |
| 8 | Patch has no whitespace errors | `git diff --check` | Static | PASS |

## Generated-model evidence

- Full CIM4 roads: 424 road semantic objects and 4,352 mesh objects.
- Full CIM4 road OBJ scan: 11,777,881 vertices checked; lowest road vertex is
  Z=3.0 m.
- CIM4 utilities: 6,108 semantic objects, including 1,864 standardized wells.
- Utility/subway clearance: 15,337 nearby pairs checked, zero violations,
  minimum vertical clearance 8.44 m.
- CIM4 subway: 11 tunnel semantic objects and 170 mesh objects.
- Road, utility, and subway OBJ/FBX files were regenerated from the full data
  preset.

## Coverage and known gaps

- The repository has no configured coverage threshold or dedicated coverage
  command. Focused behavior coverage is provided by 12 new unit tests plus the
  existing 15-test subway regression suite.
- Generated OBJ inspection caught an early residual Z≈0 junction-surface issue;
  the final tests include direct junction-surface coverage for that regression.
- Final visual acceptance remains a Blender review task. Automated tests verify
  dimensions, elevations, metadata, and output generation, but do not judge
  the artistic readability of the cover pattern.
- TDD checkpoint commits were intentionally not created because the working
  tree already contained user-owned, uncommitted changes in the same files.
  RED/GREEN evidence is preserved above without bundling those changes into an
  unsolicited commit.
