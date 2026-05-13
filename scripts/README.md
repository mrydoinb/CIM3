# Scripts 技术索引

本目录包含 CIM 道路与 CIM 城市模型生成脚本。当前主流程是城市级流程，入口为项目根目录的 `run_city_workflow.bat` 或 `run_city_workflow.sh`。

## 城市级主流程

```text
00_download_allianz_arena_osm.py
  -> 05_generate_cim_city.py
  -> 06_export_cim_city_fbx_blender.py
```

### `00_download_allianz_arena_osm.py`

职责：下载慕尼黑中央火车站周边 OSM 数据。

输出：

```text
data/raw/road_centerline.geojson
data/raw/building_footprint.geojson
data/raw/transport_points.geojson
data/raw/railway_centerline.geojson
```

说明：脚本名称保留了历史上的 `allianz_arena`，但实际中心点已经改为 München Hauptbahnhof。

### `05_generate_cim_city.py`

职责：生成 CIM 城市 OBJ。

输入：

```text
data/raw/road_centerline.geojson
data/raw/building_footprint.geojson
data/raw/transport_points.geojson
data/raw/railway_centerline.geojson
```

输出：

```text
output/obj/cim_city.obj
```

生成对象：

- 道路面、人行道、路缘、车道标线。
- 建筑体块。
- 公交站。
- 地铁站体块。
- 地铁区间隧道。
- 给水、污水、电力、通信管线。

### `06_export_cim_city_fbx_blender.py`

职责：将 `cim_city.obj` 导入 Blender，按对象类型分配材质，并导出 `cim_city.fbx`。

输出：

```text
output/fbx/cim_city.fbx
```

材质包括：

- `CIM_Road_Asphalt`
- `CIM_Sidewalk_Concrete`
- `CIM_Curb_Light_Concrete`
- `CIM_Lane_Marking_White`
- `CIM_Building_Concrete`
- `CIM_Subway_Tunnel_Dark_Concrete`
- `CIM_Subway_Station_Blue`
- `CIM_Bus_Stop_Green`
- `CIM_Utility_Water_Blue`
- `CIM_Utility_Sewer_Brown`
- `CIM_Utility_Power_Yellow`
- `CIM_Utility_Telecom_Magenta`

### `07_inspect_fbx_materials_blender.py`

职责：反向导入 `output/fbx/cim_city.fbx`，统计 Mesh 数量与材质名称，用于确认材质已经写入 FBX。

运行：

```bash
blender --background --python scripts/07_inspect_fbx_materials_blender.py
```

## Road-only OBJ generation

The old road-only Blender FBX/material scripts were removed. The remaining road-only entry is:

```text
01_generate_cim3_road.py
```

### `01_generate_cim3_road.py`

Responsibilities:

- Generates CIM3 road geometry from road centerlines.
- Generates road surfaces, sidewalks, curbs, and lane markings.
- Provides reusable road generation functions for the city workflow.

### `04_inspect_blend_materials.py`

职责：检查 Blender 文件中的材质状态，主要用于道路 PBR 调试。

## 推荐命令

生成完整 CIM 城市：

```bat
run_city_workflow.bat
```

仅重新生成城市 OBJ：

```bash
python scripts/05_generate_cim_city.py
```

仅重新导出城市 FBX：

```bash
blender --background --python scripts/06_export_cim_city_fbx_blender.py
```

检查城市 FBX 材质：

```bash
blender --background --python scripts/07_inspect_fbx_materials_blender.py
```
