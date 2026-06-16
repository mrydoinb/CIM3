# Road Generation Scripts

The generation workflow uses existing source datasets. It does not clip or
create source road data.

## Select Source Data

Use `--source` when running `02_generate_cim_roads.py`.

```powershell
# Fast sample: 快速路2 and its intersecting approaches, full-detail CIM4
python scripts/02_generate_cim_roads.py --source expressway2 --level cim4

# Complete 300 km road centerline dataset, generate CIM3 and CIM4 together
python scripts/02_generate_cim_roads.py --source full --level both

# Complete 300 km road centerline dataset, lightweight CIM3 only
python scripts/02_generate_cim_roads.py --source full --level cim3

# Complete 300 km road centerline dataset, full-detail CIM4
python scripts/02_generate_cim_roads.py --source full --level cim4

# Existing generic 1/10 sample dataset
python scripts/02_generate_cim_roads.py --source clip-1-10 --level cim4
```

Show all configured source paths:

```powershell
python scripts/02_generate_cim_roads.py --list-sources
```

Use an arbitrary existing road layer:

```powershell
python scripts/02_generate_cim_roads.py `
  --roads-file "path/to/roads.shp" `
  --data-dir "path/to/data"
```

`--roads-file` selects the road centerline layer. `--data-dir` selects the
directory used to discover related source layers.

## Export FBX

After road generation:

```powershell
python scripts/03_export_cim_roads_fbx.py `
  --level both `
  --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

## Outputs

```text
output/obj/modules/cim3/city_roads.obj
output/fbx/modules/cim3/city_roads.fbx
output/semantic/cim3/city_roads_semantic.json
output/semantic/cim3/city_roads_source_attributes.json
output/semantic/cim3/city_roads_mesh_attributes.json
output/semantic/cim3/city_junctions_semantic.json

output/obj/modules/cim4/city_roads.obj
output/fbx/modules/cim4/city_roads.fbx
output/semantic/cim4/city_roads_semantic.json
output/semantic/cim4/city_roads_source_attributes.json
output/semantic/cim4/city_roads_mesh_attributes.json
output/semantic/cim4/city_junctions_semantic.json
```

Generation QC and separate junction debug models remain opt-in:

```powershell
$env:CIM_ROAD_RUN_QC = "1"
$env:CIM_ROAD_EXPORT_JUNCTION_DEBUG = "1"
```
