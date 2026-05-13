# CIM3/CIM4 城市建模技术文档

## 1. 文档目的

本文档说明当前 `cim_road_poc` 项目的数据来源、处理流程、核心算法、脚本职责、输出文件含义、材质导出逻辑和验证方法。项目当前目标是构建一个覆盖地表道路与地下空间结构的 CIM 城市切片，用于验证 CIM3/CIM4 级别的道路、交通设施、地铁空间和地下管线自动生成能力。

当前实验区域为德国慕尼黑中央火车站 `München Hauptbahnhof` 周边 1 公里范围，坐标系为 UTM Zone 32N，即 `EPSG:32632`。该区域相较安联球场更适合本任务，因为它具备密集路网、公交站点、地铁站、地下或半地下轨道线路，以及更适合管线规则生成的城市街区结构。

## 2. 建设范围

当前城市模型包含以下对象类型：

| 类别 | 对象 | 数据来源 | 生成方式 | 输出位置 |
|---|---|---|---|---|
| 地表道路 | 道路面 | OSM road centerline | 中心线缓冲、拓扑合并、面网格化 | `cim_city.obj/fbx` |
| 地表道路 | 人行道 | OSM road centerline + 规则 | 道路总宽外扩后差集 | `cim_city.obj/fbx` |
| 地表道路 | 路缘石 | OSM road centerline + 规则 | 道路边界外扩差集并挤出 | `cim_city.obj/fbx` |
| 地表道路 | 车道标线 | OSM road centerline + 规则 | 中心线偏移并裁剪到道路面 | `cim_city.obj/fbx` |
| 地上建筑 | 建筑体块 | OSM building footprint | 建筑轮廓挤出 | `cim_city.obj/fbx` |
| 公共交通 | 公交站 | OSM transport points | 点位盒体表达 | `cim_city.obj/fbx` |
| 地下交通 | 地铁站 | OSM transport points | 站点盒体表达 | `cim_city.obj/fbx` |
| 地下交通 | 地铁区间隧道 | OSM railway centerline | 线段圆柱体扫掠 | `cim_city.obj/fbx` |
| 地下管线 | 给水管 | OSM road centerline | 沿道路中心线偏移生成 | `cim_city.obj/fbx` |
| 地下管线 | 污水管 | OSM road centerline | 沿道路中心线偏移生成 | `cim_city.obj/fbx` |
| 地下管线 | 电力管线 | OSM road centerline | 沿道路中心线偏移生成 | `cim_city.obj/fbx` |
| 地下管线 | 通信管线 | OSM road centerline | 沿道路中心线偏移生成 | `cim_city.obj/fbx` |

## 3. 实验区域

### 3.1 当前区域

下载脚本中配置：

```python
CENTER_POINT = (48.1402, 11.5600)
DIST_M = 1000
```

含义：

- 纬度：`48.1402`
- 经度：`11.5600`
- 范围：以慕尼黑中央火车站为中心，半径 1000 米。
- 坐标转换：下载到的 WGS84 经纬度数据统一转换到 `EPSG:32632`。

### 3.2 为什么替换安联球场

安联球场周边更适合简单道路和建筑 POC，但不适合完整 CIM3/CIM4 城市地下空间验证。主要原因：

- Fröttmaning 站附近 U-Bahn 线路多为地面或高架运行，缺少典型地下地铁区间。
- 球场周边路网、公交、建筑和管线形态相对单一。
- 缺少可用于验证地下站厅、地下区间和复杂城市管线布设的高密度城市语境。

慕尼黑中央火车站具备更高价值：

- 多条 U-Bahn / S-Bahn / Tram / Rail 线汇聚。
- OSM 中可获取较丰富的 `railway`、`station`、`tunnel`、`layer` 等字段。
- 地表道路与公交站密集。
- 适合通过道路中心线参数化生成地下综合管线。

## 4. 数据输入

### 4.1 原始数据目录

```text
data/raw/
  road_centerline.geojson
  building_footprint.geojson
  transport_points.geojson
  railway_centerline.geojson
```

### 4.2 数据文件说明

#### `road_centerline.geojson`

道路中心线数据，由 OSMnx 从 OSM 路网下载生成。主要用途：

- 道路面生成。
- 人行道、路缘石、车道标线生成。
- 地下管线沿道路布设。

关键字段：

- `highway`：道路等级。
- `lanes`：车道数。
- `name`：道路名称。
- `bridge`：是否桥梁。
- `layer`：道路层级。
- `osmid`：OSM 标识。

#### `building_footprint.geojson`

建筑轮廓数据。主要用途：

- 生成建筑体块。
- 如果存在 `height` 字段，优先使用真实高度。
- 如果存在 `building:levels` 字段，按层数估算高度。
- 如果两者都没有，使用默认高度。

当前默认参数：

```python
BUILDING_DEFAULT_HEIGHT_M = 12.0
BUILDING_LEVEL_HEIGHT_M = 3.2
```

#### `transport_points.geojson`

公共交通点或面要素。主要用途：

- 识别公交站。
- 识别地铁站、站台、站点入口等。

关键字段：

- `highway=bus_stop`
- `bus=yes`
- `railway=station`
- `station=subway`
- `public_transport=*`
- `name` 中包含 `subway`、`U-Bahn`、`station` 等词。

#### `railway_centerline.geojson`

轨道交通线数据。当前下载标签：

```python
railway_tags = {
    "railway": ["subway", "light_rail", "rail", "tram"]
}
```

主要用途：

- 从 `railway=subway`、`tunnel=yes` 或 `layer < 0` 的要素中生成地铁区间隧道。

关键字段：

- `railway`
- `tunnel`
- `layer`
- `name`

## 5. 规则参数

道路基础规则位于：

```text
data/rules/road_rules.json
```

当前默认规则：

```json
{
  "default_road": {
    "lane_count": 2,
    "lane_width": 3.5,
    "road_width": 7.0,
    "sidewalk_width": 2.0,
    "curb_width": 0.3,
    "curb_height": 0.15,
    "lane_marking_width": 0.15,
    "road_z": 0.0,
    "lane_marking_z_offset": 0.015,
    "material": "asphalt",
    "sidewalk_material": "concrete",
    "curb_material": "curb_concrete",
    "marking_material": "white_marking"
  }
}
```

用途：

- `lane_count` / `lane_width`：决定默认道路宽度。
- `road_width`：道路面缓冲宽度。
- `sidewalk_width`：人行道外扩宽度。
- `curb_width` / `curb_height`：路缘石宽度和高度。
- `lane_marking_width`：车道标线宽度。
- `lane_marking_z_offset`：标线相对于道路面的高度偏移，避免闪烁。

## 6. 生成流程

### 6.1 城市一键流程

Windows：

```bat
run_city_workflow.bat
```

Git Bash / Linux / macOS：

```bash
./run_city_workflow.sh
```

流程分三步：

```text
[1/3] scripts/00_download_allianz_arena_osm.py
[2/3] scripts/05_generate_cim_city.py
[3/3] scripts/06_export_cim_city_fbx_blender.py
```

### 6.2 第一步：下载 OSM 数据

脚本：

```text
scripts/00_download_allianz_arena_osm.py
```

输出：

```text
data/raw/road_centerline.geojson
data/raw/building_footprint.geojson
data/raw/transport_points.geojson
data/raw/railway_centerline.geojson
```

说明：

- 脚本名称仍保留 `allianz_arena`，但内部区域已更新为慕尼黑中央火车站。
- 数据下载需要访问 Overpass API。
- 下载结果是后续城市模型生成的唯一原始矢量输入。

### 6.3 第二步：生成 CIM 城市 OBJ

脚本：

```text
scripts/05_generate_cim_city.py
```

输出：

```text
output/obj/cim_city.obj
```

主要步骤：

1. 读取四类 GeoJSON。
2. 将所有数据转换到 `EPSG:32632`。
3. 计算城市切片局部原点。
4. 将全局坐标平移到局部坐标，降低三维软件中的大坐标精度风险。
5. 生成道路、人行道、路缘、标线。
6. 生成建筑体块。
7. 生成地铁区间隧道。
8. 生成地下管线。
9. 生成公交站和地铁站盒体。
10. 汇总为 `trimesh.Scene` 并导出 OBJ。

### 6.4 第三步：导出带材质 FBX

脚本：

```text
scripts/06_export_cim_city_fbx_blender.py
```

输入：

```text
output/obj/cim_city.obj
```

输出：

```text
output/fbx/cim_city.fbx
```

说明：

- 使用 Blender 后台模式导入 OBJ。
- 根据对象名称前缀分配材质。
- 使用 FBX 友好的简单 Principled BSDF 材质，不依赖复杂节点树。
- 导出的 FBX 可被 Blender 重新导入并识别材质。

## 7. 核心算法说明

### 7.1 坐标系统与局部化

所有 OSM 数据初始为经纬度坐标，统一转换到：

```text
EPSG:32632
```

该坐标系适用于慕尼黑区域。转换后再计算所有图层的包围盒中心作为局部原点：

```python
origin = ((minx + maxx) / 2.0, (miny + maxy) / 2.0)
```

所有几何执行平移：

```python
x_local = x_global - origin_x
y_local = y_global - origin_y
```

目的：

- 降低 FBX/Blender/游戏引擎中的大坐标浮点误差。
- 让模型中心靠近世界坐标原点。
- 便于后续叠加地上和地下对象。

### 7.2 道路连接优化

道路生成逻辑来自 `scripts/01_generate_cim3_road.py`，并被城市脚本复用。

优化内容：

- 同层级道路按拓扑组进行合并。
- 桥梁道路不会直接和地面道路融合。
- 端点距离小于阈值的同层道路会补短连接线。

关键参数：

```python
ROAD_LINK_GAP_TOLERANCE_M = 2.5
MIN_CONNECTOR_LENGTH_M = 0.05
BRIDGE_ELEVATION_GROUP_M = 3.0
```

作用：

- 修复 OSM 路网端点轻微断裂导致的道路面不连续问题。
- 减少道路、侧步道、路缘和车道标线被切成大量碎片。
- 保留桥梁与地面道路的立体关系。

### 7.3 道路面、人行道、路缘与标线

道路面：

- 对道路中心线按 `road_width` 缓冲。
- 多条道路合并后转成面。
- 再生成顶面网格。

人行道：

- 对中心线按 `road_width + 2 * sidewalk_width` 缓冲。
- 与道路面做差集。

路缘：

- 基于道路边界外扩 `curb_width`。
- 使用 `curb_height` 挤出为低矮实体。

车道标线：

- 根据车道数和道路宽度计算偏移。
- 标线面裁剪在道路面内部。
- 标线 z 值使用 `lane_marking_z_offset` 微抬。

### 7.4 建筑体块

建筑输入为 Polygon 或 MultiPolygon。生成高度规则：

1. 若存在 `height` 字段并可解析，使用该高度。
2. 否则若存在 `building:levels` 字段，使用 `levels * 3.2m`。
3. 否则使用默认高度 `12m`。

当前建筑仅做体块表达，不生成窗、门、屋顶细节。该等级适合城市背景、CIM3 场景上下文和地下空间关系验证。

### 7.5 地铁区间隧道

地铁隧道从 `railway_centerline.geojson` 生成。识别规则：

```python
railway == "subway" or tunnel in {"yes", "true"} or layer < 0
```

生成方式：

- 将每条 LineString 拆成连续线段。
- 每个线段生成一段圆柱体。
- 圆柱半径为 `2.6m`。
- 默认深度为 `-14m`。

当前参数：

```python
SUBWAY_TUNNEL_RADIUS_M = 2.6
SUBWAY_TUNNEL_DEPTH_M = -14.0
```

说明：

- 当前隧道为规则圆形区间表达，适合 CIM3/CIM4 的空间占位与关系验证。
- 后续可根据 `layer`、线路名、站点拓扑进一步区分不同深度。

### 7.6 地铁站

地铁站从 `transport_points.geojson` 中识别。识别规则基于字段组合：

```text
railway / station / subway / public_transport / name
```

如果字段中出现 `subway`、`u-bahn`、`station` 等语义，生成地铁站体块。

当前参数：

```python
SUBWAY_STATION_DEPTH_M = -11.0
SUBWAY_STATION_SIZE_M = (34.0, 16.0, 7.0)
```

说明：

- 当前地铁站为参数化盒体。
- 盒体中心位于站点代表点。
- 该表达用于验证地下站点与隧道、道路和建筑的空间关系。

### 7.7 公交站

公交站识别规则：

```text
highway=bus_stop
bus=yes
```

生成方式：

- 在点位处生成小型盒体。
- 当前尺寸为 `3.0m x 1.4m x 2.6m`。
- z 中心为 `1.3m`，即落在地表。

### 7.8 地下综合管线

OSM 通常不提供真实地下管线数据，因此当前管线采用规则生成。生成基础为道路中心线。

当前管线类型：

| 类型 | 深度 z | 道路横向偏移 | 半径 | 材质颜色 |
|---|---:|---:|---:|---|
| Water | `-2.0m` | `-1.4m` | `0.22m` | 蓝色 |
| Sewer | `-3.2m` | `0.0m` | `0.32m` | 棕色 |
| Power | `-1.5m` | `1.4m` | `0.16m` | 黄色 |
| Telecom | `-1.1m` | `2.2m` | `0.10m` | 洋红 |

生成方式：

1. 读取每条道路中心线。
2. 将道路线拆为相邻点组成的线段。
3. 对每类管线按横向偏移计算新线段。
4. 在固定深度生成圆柱体。

当前管线属于“参数化模拟管网”，不是实测管线。它的价值是：

- 提供 CIM3/CIM4 的地下空间结构验证对象。
- 支撑道路与管线之间的空间关系检查。
- 可作为后续真实管线数据接入前的冷启动方案。

## 8. 材质系统

### 8.1 城市 FBX 材质

城市 FBX 使用 `scripts/06_export_cim_city_fbx_blender.py` 分配材质。映射规则基于对象名前缀：

| 对象前缀 | 材质名 |
|---|---|
| `Road_Surface` | `CIM_Road_Asphalt` |
| `Sidewalk` | `CIM_Sidewalk_Concrete` |
| `Curb` | `CIM_Curb_Light_Concrete` |
| `Lane_Marking` | `CIM_Lane_Marking_White` |
| `Building` | `CIM_Building_Concrete` |
| `Subway_Tunnel` | `CIM_Subway_Tunnel_Dark_Concrete` |
| `Subway_Station` | `CIM_Subway_Station_Blue` |
| `Bus_Stop` | `CIM_Bus_Stop_Green` |
| `Utility_Water` | `CIM_Utility_Water_Blue` |
| `Utility_Sewer` | `CIM_Utility_Sewer_Brown` |
| `Utility_Power` | `CIM_Utility_Power_Yellow` |
| `Utility_Telecom` | `CIM_Utility_Telecom_Magenta` |

这些材质是 FBX 友好的基础材质，主要由 Base Color、Roughness、Metallic 组成。它们不依赖复杂贴图节点，因此在 FBX 中更稳定。

### 8.2 道路单体 PBR 材质

道路单体测试流程仍保留：

```text
scripts/01_generate_cim3_road.py
```

该流程用于更真实的道路材质验证，包括柏油、人行道铺装、路缘混凝土等贴图材质。但当前 CIM 城市总装输出以 `cim_city.obj/fbx` 为准。

## 9. 输出文件说明

当前城市构建任务只需要保留：

```text
output/obj/cim_city.obj
output/fbx/cim_city.fbx
```

### 9.1 `output/obj/cim_city.obj`

用途：

- 几何交换。
- 快速检查三维实体是否生成成功。
- 可被 Blender 导入后继续编辑。

特点：

- 包含地上和地下对象。
- 体积较大。
- OBJ 对材质和层级语义支持有限，因此主要作为几何中间结果。

### 9.2 `output/fbx/cim_city.fbx`

用途：

- 最终展示。
- 用于 Blender、Unity、Unreal、CityEngine 等三维软件。
- 保留对象和材质区分。

特点：

- 已按对象类型分配材质。
- 适合直接查看地上/地下结构关系。
- 当前是本任务的主要交付模型。

## 10. 当前生成结果

最近一次生成统计：

```text
road_meshes: 8
buildings: 2830
subway_tunnels: 838
utility_pipes: 15604
subway_stations: 103
bus_stops: 149
```

当前输出文件：

```text
output/obj/cim_city.obj
output/fbx/cim_city.fbx
```

文件大小会随 OSM 数据下载结果变化。

## 11. 验证方法

### 11.1 Python 语法检查

```bash
python -c "import ast, pathlib; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8')) for p in ['scripts/00_download_allianz_arena_osm.py','scripts/05_generate_cim_city.py','scripts/06_export_cim_city_fbx_blender.py','scripts/07_inspect_fbx_materials_blender.py']]; print('syntax ok')"
```

### 11.2 重新生成城市 OBJ

```bash
python scripts/05_generate_cim_city.py
```

成功后应看到类似输出：

```text
CIM city OBJ generated:
- output/obj/cim_city.obj
- road_meshes: ...
- buildings: ...
- subway_tunnels: ...
- utility_pipes: ...
- subway_stations: ...
- bus_stops: ...
```

### 11.3 导出城市 FBX

```bash
blender --background --python scripts/06_export_cim_city_fbx_blender.py
```

成功后输出：

```text
CIM city FBX exported: output/fbx/cim_city.fbx
```

### 11.4 反向导入检查 FBX 材质

```bash
blender --background --python scripts/07_inspect_fbx_materials_blender.py
```

成功后应看到材质统计，例如：

```text
CIM_Road_Asphalt
CIM_Building_Concrete
CIM_Subway_Tunnel_Dark_Concrete
CIM_Utility_Water_Blue
CIM_Utility_Sewer_Brown
CIM_Utility_Power_Yellow
CIM_Utility_Telecom_Magenta
```

这一步用于确认材质确实写入了 FBX，而不是只存在于临时 Blender 场景中。

## 12. 脚本职责

| 脚本 | 职责 |
|---|---|
| `scripts/00_download_allianz_arena_osm.py` | 下载慕尼黑中央火车站周边 OSM 原始数据 |
| `scripts/01_generate_cim3_road.py` | 道路单体 CIM3 生成，城市流程复用其道路几何算法 |
| `scripts/04_inspect_blend_materials.py` | 检查 Blender 文件材质 |
| `scripts/05_generate_cim_city.py` | 生成 CIM 城市 OBJ |
| `scripts/06_export_cim_city_fbx_blender.py` | 导出带材质的 CIM 城市 FBX |
| `scripts/07_inspect_fbx_materials_blender.py` | 反向导入 FBX 并统计材质 |

## 13. CIM3 与 CIM4 对应关系

### CIM3 当前覆盖

- 道路中心线到三维道路面的规则生成。
- 人行道、路缘、标线等道路附属构件。
- 建筑体块。
- 公交站点。
- 地下综合管线的参数化布设。
- OBJ/FBX 三维模型输出。

### CIM4 当前覆盖或接近覆盖

- 地铁站与地铁区间隧道的地下空间表达。
- 地上道路、建筑与地下交通、地下管线的组合关系。
- 局部城市空间中的多层级设施装配。
- 不同对象类型的材质区分和模型可视化。

### 仍需增强的 CIM4 能力

- 地铁站内部结构，如站厅、站台、扶梯、出入口、设备间。
- 隧道与站点之间的拓扑连接关系。
- 管线井、检查井、管线交叉避让。
- 建筑地下室、车库与轨道空间的关系。
- 更完整的语义 JSON 或 IFC/CityGML 输出。
- 根据 `layer`、`level`、线路名自动计算不同地铁线路深度。

## 14. 已知限制

1. OSM 数据不是工程实测数据，地铁和站点信息完整度取决于 OSM 社区维护情况。
2. 地下管线为规则模拟，不代表真实管线位置。
3. 地铁站当前为盒体表达，不含内部空间。
4. 地铁隧道当前按固定深度生成，未完全使用 `layer` 做多层深度推导。
5. 建筑高度在缺少 `height` 或 `building:levels` 时使用默认值。
6. OBJ 不适合承载完整语义，FBX 也不是严格 CIM 语义格式。
7. 大规模城市切片会产生较大的 OBJ/FBX 文件，导入和导出耗时会增加。

## 15. 后续建议

短期建议：

- 将 `00_download_allianz_arena_osm.py` 重命名为 `00_download_munich_hbf_osm.py`，避免历史命名造成误解。
- 增加 `output/metadata/cim_city_summary.json`，记录对象数量、区域、坐标原点和生成参数。
- 为地铁隧道按线路名分组，并用不同颜色显示。
- 将地铁站盒体与相邻隧道端点做连接校正。

中期建议：

- 生成管线检查井、雨水口、交叉避让和支管。
- 将公交站、地铁站、道路、管线输出为独立图层或集合。
- 增加 CityGML、IFC 或 3D Tiles 输出。
- 增加质量检查报告，例如道路断点、隧道孤立段、站点与线路距离。

长期建议：

- 接入真实地下管线数据。
- 基于地铁线路拓扑生成站厅、站台和换乘通道。
- 建立 CIM 对象 ID 体系，支持后续查询、分析和仿真。
- 将三维模型与语义数据库联动，形成可检索的 CIM 城市资产。
