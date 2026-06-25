# Subway Curved Tunnel TDD Evidence

## Source

No external plan file was supplied. The user journey and acceptance criteria were derived from the reported Blender defect: subway interval tunnel components were generated as disconnected straight pieces at horizontal alignment turns.

## User journey

As a Blender geometry reviewer, I want continuous subway tunnel components to follow the curved railway centerline, so that tunnel lining, track, evacuation, MEP, and communication components do not expose independent straight-segment end faces or gaps at bends.

## Acceptance criteria

1. A straight source alignment remains straight after smoothing and resampling.
2. Every curved sweep chord is no longer than the configured `2.0 m` maximum.
3. Curved tunnel lining and offset rectangular sweeps are watertight single solids.
4. Endpoint-connected source records merge into one corridor without plan translation.
5. The following continuous components remain connected through a 90-degree test turn:
   - lining;
   - invert/track base and rail-bed surface;
   - two running rails and two rail-top surfaces;
   - evacuation platform, panel, frame, and edge strip;
   - contact wire;
   - three leakage cables;
   - lighting cable.

## RED evidence

Command:

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe -m unittest -v tests.test_subway_curved_tunnel
```

Result before the production fix:

```text
Ran 4 tests in 0.124s
FAILED (failures=9)
```

Target failures:

- `Ref08_Platform_Main`: 2 connected components instead of 1.
- `Ref10_Platform_Edge_Strip`: 2 instead of 1.
- `Ref11_Platform_Steel_Frame`: 2 instead of 1.
- `Ref12_Platform_Concrete_Panel`: 2 instead of 1.
- `Ref14_Contact_Rail`: not watertight.
- `Ref20_Leakage_Cable_A`: not watertight.
- `Ref21_Leakage_Cable_B`: not watertight.
- `Ref22_Leakage_Cable_C`: not watertight.
- `Ref27_Lighting_Cable`: not watertight.

These failures reproduced the intended bug: continuous facilities were still assembled from independently generated straight source segments.

## GREEN evidence

The minimal production change moved the failing continuous components onto the same smoothed and resampled centerline sweep used by the tunnel lining. Discrete supports, sleepers, fasteners, brackets, fixtures, and signs remain interval-placed objects.

Command:

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe -m unittest -v tests.test_subway_curved_tunnel
```

Result:

```text
Ran 7 tests in 0.253s
OK
```

Additional validation:

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe -m py_compile src\city\pipeline.py src\subway\checks.py tests\test_subway_curved_tunnel.py
git diff --check -- src/city/pipeline.py src/subway/checks.py tests/test_subway_curved_tunnel.py
```

Both commands passed. Git only emitted the existing LF-to-CRLF working-copy warning.

## Test specification

| # | Guarantee | Test | Type | Result |
|---|---|---|---|---|
| 1 | Straight centerlines remain straight and retain both endpoints | `test_straight_centerline_remains_straight_after_smoothing_and_resampling` | Unit | PASS |
| 2 | Resampled sweep chords do not exceed `2.0 m` | `test_resampling_limits_every_sweep_chord` | Unit | PASS |
| 3 | Curved thick lining is one watertight, consistently wound solid | `test_curved_lining_is_one_watertight_solid` | Unit | PASS |
| 4 | A laterally offset platform section follows a bend as one solid | `test_offset_linear_sweep_follows_curve_as_one_solid` | Unit | PASS |
| 5 | Endpoint-connected source records merge without horizontal translation | `test_endpoint_connected_source_records_merge_without_plan_translation` | Integration | PASS |
| 6 | Continuous structure, track, evacuation, MEP, and communication components remain connected through a turn | `test_all_continuous_systems_remain_connected_through_a_turn` | In-memory end-to-end geometry pipeline | PASS |
| 7 | Sleepers remain centered on the smoothed track alignment and both fastener/pad rows remain under the rails through a turn | `test_sleepers_and_fasteners_follow_the_same_smoothed_curve_as_the_rails` | Integration | PASS |

## Rail alignment regression cycle

The Blender screenshot showed continuous rails curving away from sleepers, isolation pads, and fasteners that were still placed on the original piecewise-linear source segments.

RED command:

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe -m unittest -v tests.test_subway_curved_tunnel.SubwayCurvedTunnelIntegrationTests.test_sleepers_and_fasteners_follow_the_same_smoothed_curve_as_the_rails
```

RED result:

```text
AssertionError: 1.9091883092036777 not less than or equal to 0.05
Ran 1 test in 0.154s
FAILED (failures=1)
```

The failure proved that a sleeper center could be approximately `1.909 m` away from the smoothed rail centerline.

Minimal fix:

- sample sleeper mileage from the same smoothed/resampled centerline used by the rails;
- derive each sleeper's local tangent from that curve;
- place both isolation-pad and fastener rows from the same local normal and half-gauge offset.

GREEN result:

```text
Ran 1 test in 0.146s
OK
```

## Coverage

`pytest` and `coverage.py` are not installed in the project environment, so no dependency was downloaded. A standard-library `trace.Trace` run was combined with AST statement counting for the ten targeted curved-tunnel and corridor functions.

Updated result including curved-track interval sampling:

```text
TARGETED_TOTAL: 236/267 (88.4%)
tests_successful=True
```

Targeted function coverage exceeds the requested 80% threshold. This is scoped coverage for the curved-tunnel implementation, not a claim of 80% coverage for the entire historical `pipeline.py` module.

## Known gaps

- No full Shenzhen Line 4 OBJ or FBX was generated, following the user's instruction that generation commands should be handed off rather than run automatically.
- Blender visual inspection remains the final acceptance step.
- Discrete interval objects intentionally remain separate mesh components.
- Extremely tight source curves below the configured safe bend radius still emit a warning and may require source-alignment correction.

## Selective Line 1 / Line 11 regression cycle

The user reported that Shenzhen Metro Line 1 overlapped because its two source alignments were closer than the generated tunnel outer diameter. The requested follow-up was to enable horizontal translation correction only for Line 1 and Line 11, and to resolve the Line 11 tight-bend warning seen on `source_id='1230'`.

RED command:

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe -m unittest -v tests.test_subway_curved_tunnel
```

RED result:

```text
ImportError: cannot import name 'safe_subway_lining_centerline_coords' from 'city.pipeline'
FAILED (errors=1)
```

The failing test introduced two guarantees before production code existed:

- close parallel sources for `深圳地铁1号线` and `深圳地铁11号线` are separated by at least `2 * SUBWAY_TUNNEL_OUTER_RADIUS_M`;
- tight Line 11 bends are smoothed until the estimated centerline bend radius reaches `SUBWAY_TUNNEL_MIN_BEND_RADIUS_M`.

GREEN command:

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe -m unittest -v tests.test_subway_curved_tunnel
```

GREEN result:

```text
Ran 12 tests in 0.458s
OK
```

Additional validation:

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe -m py_compile src\city\pipeline.py scripts\02_generate_cim_subway_tunnels.py scripts\03_export_cim_subway_tunnels_fbx.py scripts\04_export_cim_subway_tunnels_fbx_blender.py tests\test_subway_curved_tunnel.py
git diff --check
```

`py_compile` passed. `git diff --check` reported only existing LF-to-CRLF working-copy warnings.

Updated guarantees:

| # | Guarantee | Test | Type | Result |
|---|---|---|---|---|
| 8 | Line 1 and Line 11 close parallel source alignments receive selective lateral translation, while another line remains unshifted | `test_line_1_and_11_close_parallel_sources_get_lateral_translation` | Integration | PASS |
| 9 | A tight Line 11 bend is adaptively smoothed until its bend radius is at or above the safe lining radius | `test_safe_lining_centerline_increases_smoothing_for_tight_line_11_bends` | Unit | PASS |
| 10 | The real Shenzhen Line 1 source corridors clear each other by at least two tunnel outer radii after selective translation | `test_real_shenzhen_line_1_corridors_clear_each_other_after_translation` | Data-backed integration | PASS |

## All same-line overlap translation regression cycle

The user broadened the requirement from Line 1 / Line 11 only to all overlapping subway tunnel pairs. The implemented scope is same-line tunnel corridors: endpoint-connected source fragments are merged first, then close same-line corridor pairs are translated as complete axes. This avoids breaking a continuous corridor into separate fragments while separating sustained double-line overlap.

RED command:

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe -m unittest -v tests.test_subway_curved_tunnel.SubwayCurvedTunnelIntegrationTests.test_real_shenzhen_same_line_corridors_clear_each_other_after_translation
```

RED result:

```text
AssertionError: 1.0886683090878781 not greater than or equal to 6.720148 : 深圳地铁2号线 corridors 2927 and 3477 still overlap
FAILED (failures=1)
```

GREEN command:

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe -m unittest -v tests.test_subway_curved_tunnel
```

GREEN result:

```text
Ran 12 tests in 0.508s
OK
```

Additional validation:

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe -m py_compile src\city\pipeline.py tests\test_subway_curved_tunnel.py scripts\02_generate_cim_subway_tunnels.py scripts\03_export_cim_subway_tunnels_fbx.py scripts\04_export_cim_subway_tunnels_fbx_blender.py
git diff --check
```

`py_compile` passed. `git diff --check` reported only LF-to-CRLF working-copy warnings.

Updated guarantee:

| # | Guarantee | Test | Type | Result |
|---|---|---|---|---|
| 11 | Real Shenzhen same-line tunnel corridors are translated after endpoint merging so sustained near-overlap is eliminated without increasing corridor count | `test_real_shenzhen_same_line_corridors_clear_each_other_after_translation` | Data-backed integration | PASS |

## Line 2 / Line 11 source-topology regression cycle

The real Shenzhen source data exposed two cases that the earlier corridor-level check did not cover:

- Line 2 used a fixed world-axis translation for two curved tracks, allowing the translated curves to cross again locally.
- Line 11 stored two opposite-direction tracks plus one continuation segment in a single `MultiLineString`; treating the feature as one corridor left the parallel tracks and near-disconnected continuation unresolved.

RED command:

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe -m unittest tests.test_subway_curved_tunnel.SubwayCurvedTunnelIntegrationTests.test_real_line_2_corridors_have_no_lining_overlap tests.test_subway_curved_tunnel.SubwayCurvedTunnelIntegrationTests.test_real_line_11_multiline_tracks_are_split_and_clear -v
```

RED result:

```text
test_real_line_2_corridors_have_no_lining_overlap ... FAIL
AssertionError: 0.0 not greater than or equal to 6.720148
test_real_line_11_multiline_tracks_are_split_and_clear ... FAIL
AssertionError: 1 not greater than or equal to 2
Ran 2 tests in 0.278s
FAILED (failures=2)
```

Minimal fix:

- explode source `MultiLineString` features into individual track parts before corridor grouping;
- join nearby source parts only when their endpoint tangents describe a continuous alignment;
- create paired tunnel axes with curve-following offsets from each source track instead of a fixed `X/Y` translation;
- retain the larger `52 m` Line 11 pair spacing;
- remove the incorrect hard-coded Line 2 `focus="交"` metadata.

GREEN command:

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe -m unittest tests.test_subway_curved_tunnel -v
```

GREEN result:

```text
Ran 15 tests in 0.585s
OK
```

Measured real-data results:

| Line | Minimum generated centerline distance | Tunnel-buffer overlap area | Configured pair spacing |
|---|---:|---:|---:|
| Shenzhen Metro Line 2 | `45.089 m` | `0.0 m2` | `44.0 m` |
| Shenzhen Metro Line 11 | `56.531 m` | `0.0 m2` | `52.0 m` |

Updated guarantees:

| # | Guarantee | Test | Type | Result |
|---|---|---|---|---|
| 12 | Real Line 2 curve-following paired axes remain farther apart than two tunnel outer radii and their lining buffers do not intersect | `test_real_line_2_corridors_have_no_lining_overlap` | Data-backed integration | PASS |
| 13 | Real Line 11 multipart source geometry is resolved into two continuous track corridors with no tunnel lining overlap | `test_real_line_11_multiline_tracks_are_split_and_clear` | Data-backed integration | PASS |

## Git checkpoint note

No TDD checkpoint commits were created because the working tree already contained multiple unrelated user changes. Creating automatic commits would have risked including or splitting user-owned work. RED and GREEN evidence is preserved in this report instead.
