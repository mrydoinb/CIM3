# CIM 道路/城市建模技术文档（当前数据版）

更新日期：2026-05-20

## 1. 文档目的

本文档说明当前 `cim_road_poc` 项目的有效数据源、生成脚本、核心建模逻辑、输出成果、语义/质检文件和运行环境要求。

当前仓库已经从早期的慕尼黑 OSM POC 演进到“工程数据驱动的城市道路与地下管线模型”。主生成脚本 `scripts/05_generate_cim_city.py` 现在优先读取 `data/Data` 下的本地工程数据，而不是 `data/raw` 下的慕尼黑 OSM GeoJSON。

当前有效主模型以深圳试验区工程数据为核心，数据特征包括：

- 公交站数据字段中 `city_name=深圳市`。
- 轨道线包含深圳地铁 1、2、3、4、10、11 号线以及广深港高速铁路。
- 轨道站点包含市民中心、购物公园、少年宫、福田等站点。
- 道路、管线主数据采用工程投影坐标，脚本中未声明 CRS 时按 `EPSG:4547` 解释，再转换到模型坐标 `EPSG:3857`。

## 2. 当前结论摘要

当前主模型 `output/obj/cim_city.obj` 的对象清单显示，主 OBJ 包含以下当前有效内容：

| 模块 | 当前主 OBJ 对象数 | 说明 |
|---|---:|---|
| 道路分层模型 | 19 | 道路主路面、辅路、支路、非机动车道、人行道、绿化带、设施带、侧分带、中央分隔带、路缘石、路口面、斑马线、停止线、白色/黄色标线、路灯、树木 |
| 轨道/地铁站体块 | 11 | 由轨道站点 SHP 生成，当前命名为 `Subway_Station_*` |
| 公交站 | 121 | 由公交站 SHP 生成 |
| 地下/市政管线 | 3 | 供水、污水、燃气三类合并管线 |
| 建筑体块 | 0 | 当前 `data/Data` 未提供建筑轮廓，主 OBJ 不含 `Building_*` |
| 轨道交通隧道 | 1 | 由 `轨道线2000.shp` 生成，主 OBJ 包含合并对象 `Subway_Tunnel_All` |

当前道路质检结果：

```text
score: 100.0
grade: A
road_centerline_count: 416
symmetric_modeled_count: 416
width_match_count: 416
section_fit_count: 416
component_complete_count: 416
semantic_complete_count: 416
transition_curve_count: 330
roads_with_transition_curves: 124
roads_with_junction_marking_clearance: 416
symmetry_normalized_count: 54
```

当前路口专项质检结果：

```text
score: 100.0
grade: A
junction_count: 758
semantic_junction_count: 758
total_arm_count: 2638
total_allowed_movement_count: 6750
expected_marked_approach_count: 3273
junction_surface_count: 758
crosswalk_stripe_count: 69281
stop_line_count: 3273
```

当前道路标线对齐质检结果：

```text
score: 100.0
grade: A
checked_marking_count: 10956
suppressed_white_edge_at_double_yellow: 720
issue_count: 0
max_lateral_overhang_m: 0.0
junction_marking_lateral_inset_m: 0.3
marking_sweep_sample_interval_m: 8.0
max_solid_marking_segment_m: 8.0
```

## 3. 数据目录与使用关系

### 3.1 当前主流程使用的数据

当前 `scripts/01_generate_cim3_road.py` 和 `scripts/05_generate_cim_city.py` 的有效数据根目录为：

```text
data/Data/
```

主要输入如下：

| 数据 | 当前文件 | 要素数 | 几何类型 | 当前用途 |
|---|---|---:|---|---|
| 道路中心线 | `data/Data/road50kms/道路修改50kms.shp` | 411 | PolyLine | 道路断面、路面、路缘、标线、绿化/设施带、路灯、树木、道路语义与质检 |
| 公交站 | `data/Data/公交站【筛选首末站+地铁接驳站】/*.shp` | 121 | MultiPoint | 公交站模型 |
| 轨道交通隧道线 | `data/Data/轨道线和站点转坐标2000/轨道线2000.shp` | 15 | PolyLine | 轨道交通隧道数据集，当前生成 `Subway_Tunnel_All` |
| 轨道站点 | `data/Data/轨道线和站点转坐标2000/轨道站点2000.shp` | 11 | Point | 轨道站点体块 |
| 供水管线 | `data/Data/供水+污水/供水管线.shp` | 1747 | PolyLineZ | 供水管线；当前建模有效记录 1746 条 |
| 污水管线 | `data/Data/供水+污水/污水管线.shp` | 3010 | PolyLine | 污水管线 |
| 燃气管线 | `data/Data/rq规划试验区校核/rq规划标注管径试验区校核.shp` | 736 | PolyLine | 燃气管线 |
| 污水管点 | `data/Data/cad+污水管点/污水管点.shp` | 616 | Point | 当前保留，主生成脚本暂未使用 |
| DEM | `data/Data/DEM/DEM/区域.tif` | 1 | Raster | 当前保留，`ENABLE_DEM_ELEVATION=False` 时不参与地形起伏 |

### 3.2 OSM 数据目录

仓库中仍保留多个 OSM GeoJSON 数据目录，但它们不是当前主生成脚本的默认输入。

| 目录 | 道路 | 建筑 | 交通点 | 轨道线 | 状态 |
|---|---:|---:|---:|---:|---|
| `data/raw` | 715 | 2835 | 441 | 452 | 历史慕尼黑 OSM 数据 |
| `data/raw_beijing_yizhuang` | 251 | 259 | 77 | 2 | 当前下载脚本输出目录 |
| `data/raw_suzhou_dushu_lake` | 121 | 271 | 56 | 4 | 苏州独墅湖 OSM 测试数据 |

`scripts/00_download_allianz_arena_osm.py` 的文件名是历史遗留名称。当前脚本内容已经改为下载北京亦庄 OSM 数据，并输出到：

```text
data/raw_beijing_yizhuang/
```

当前城市生成脚本没有自动切换到该目录。如需使用北京亦庄或苏州 OSM 数据生成主模型，需要调整 `RAW_SOURCE_DIR`、道路输入路径和相关工程数据依赖。

## 4. 主流程与脚本入口

### 4.1 一键流程

Windows：

```bat
run_city_workflow.bat
```

Git Bash / Linux / macOS：

```bash
./run_city_workflow.sh
```

流程为：

```text
[1/3] scripts/00_download_allianz_arena_osm.py
[2/3] scripts/05_generate_cim_city.py
[3/3] scripts/06_export_cim_city_fbx_blender.py
```

需要注意：

- 脚本日志中已经写为 “Downloading Beijing Yizhuang OSM source data”。
- 第一步输出 `data/raw_beijing_yizhuang`。
- 第二步当前仍读取 `data/Data` 工程数据。
- 一键脚本中的默认 `PYTHON_EXE` 和 `BLENDER_EXE` 是本机历史路径，当前机器上检测为不存在，需要运行前手动设置。

### 4.2 城市 OBJ 生成

脚本：

```text
scripts/05_generate_cim_city.py
```

关键配置：

```python
RAW_DIR = road_gen.RAW_SOURCE_DIR          # 当前为 data/Data
OUT_PATH = output/obj/cim_city.obj
TARGET_CRS = road_gen.TARGET_CRS          # 当前为 EPSG:3857
SOURCE_PROJECTED_CRS = "EPSG:4547"
GENERATE_ROAD_ASSETS = True
GENERATE_SUBWAY_TUNNELS = True
GENERATE_UTILITY_PIPES = True
ENABLE_TRANSITION_CURVES = True
ENABLE_ROUNDED_JUNCTION_SURFACES = True
GENERATE_JUNCTION_CROSSWALKS = True
GENERATE_JUNCTION_STOP_LINES = True
```

输出：

```text
output/obj/cim_city.obj
output/obj/modules/cim_city_roads.obj
output/obj/modules/cim_city_bus_stops.obj
output/obj/modules/cim_city_subway_stations.obj
output/obj/modules/cim_city_utility_pipes.obj
output/semantic/cim_city_roads_semantic.json
output/semantic/cim_city_junctions_semantic.json
output/qc_report/cim_city_roads_model_score.json
output/qc_report/cim_city_junction_score.json
output/qc_report/cim_city_marking_alignment_qc.json
```

### 4.3 FBX 导出

脚本：

```text
scripts/06_export_cim_city_fbx_blender.py
```

输入：

```text
output/obj/cim_city.obj
output/obj/modules/*.obj
```

输出：

```text
output/fbx/cim_city.fbx
output/fbx/modules/*.fbx
```

该脚本会把主 OBJ 和模块 OBJ 导入 Blender，按对象名前缀分配材质，再导出 FBX。

## 5. 坐标系统与局部化

当前道路生成脚本使用：

```python
TARGET_CRS = "EPSG:3857"
```

城市生成脚本对没有 CRS 的图层进行兜底判断：

```python
SOURCE_PROJECTED_CRS = "EPSG:4547"
```

处理逻辑：

1. 读取 SHP 或 GeoJSON。
2. 如果图层没有 CRS，且坐标绝对值大于 1000，则按 `EPSG:4547` 设置源 CRS。
3. 将图层统一转换到 `EPSG:3857`。
4. 计算所有有效图层的包围盒中心作为局部原点。
5. 将所有几何平移到局部坐标，降低 OBJ/FBX 在三维软件中的大坐标精度风险。

当前道路语义文件记录的局部原点：

```json
{
  "model_crs": "EPSG:3857",
  "local_origin": {
    "x": 12689247.485285211,
    "y": 2579521.4726414247,
    "z": 0.0
  }
}
```

## 6. 道路断面与建模规则

### 6.1 基础规则

道路基础规则位于：

```text
data/rules/road_rules.json
```

该文件保留了 `default_road`、`motorway`、`primary`、`secondary`、`tertiary`、`residential`、`service` 等通用规则，用于缺少断面规则时的兜底建模。

### 6.2 工程断面规则

当前道路断面规则位于：

```text
data/rules/road_section_requirements.json
```

该文件定义 A、B、C、D 系列断面：

| 系列 | 类型 | 示例断面 | 说明 |
|---|---|---|---|
| A | 快速路 | A1、A2、A3 | 多车道、中央分隔带、绿化/服务车道等复杂断面 |
| B | 主干路/次干路 | B1 至 B5 | 主路、中央分隔带、非机动车道、侧分带、人行道 |
| C | 次干路 | C1 至 C5 | 中等宽度城市道路断面 |
| D | 支路 | D1 至 D6 | 支路、窄路、人行道或单车行断面 |

当前道路 SHP 字段统计：

```text
roadclass:
  支路: 346
  次干路: 43
  主干路: 18
  快速路: 4

section:
  D5: 172
  D1: 69
  C1: 31
  D6: 24
  D2: 24
  C2: 10
  D3: 9
  JJ: 8
```

语义/质检阶段会对道路进行拆分、别名映射和对称化处理，因此质检中的道路中心线数量为 416。

当前 A1 已按滨河大道快速路断面修正为 `93.5m` 宽的“主线双向 8 车道 + 辅道双向 6 车道”断面，并设置 `use_rule_width=true`，因此即使 SHP 中 A1 的 `width` 字段仍为 `138.0`，建模和语义输出也会以规则宽度 `93.5m` 为准。两条 A1 道路当前均保持为 `source_section_code=A1`、`modeled_section_code=A1`，不再 fallback 到 A3。

A1 当前断面组成如下：

```text
sidewalk 3.5
facility_belt 1.5
green_belt 5.75
non_motor_lane 3.0
service_lane 10.5
side_divider 4.5
main_carriageway 15.0
median 6.0
main_carriageway 15.0
side_divider 4.5
service_lane 10.5
non_motor_lane 3.0
green_belt 5.75
facility_belt 1.5
sidewalk 3.5
```

### 6.3 对称断面策略

当前启用：

```python
MODEL_CROSS_SECTIONS_AS_SYMMETRIC = True
```

非对称断面会按以下规则归一化：

```python
A2 -> A3
C4 -> C5
D2 -> D1
D6 -> D5
```

质检结果显示：

```text
symmetry_normalized_count: 54
```

这表示有 54 条道路中心线使用了对称断面 fallback，以减少单侧断面导致的视觉和拓扑异常。A1 已经修正为对称的滨河大道断面，因此不再参与 fallback。

### 6.4 当前道路对象层

当前 `output/obj/cim_city.obj` 中道路模块包含 19 个对象层：

```text
Road_Surface_Main_All
Road_Surface_Service_All
Road_Surface_Branch_All
Non_Motor_Lane_All
Sidewalk_All
Green_Belt_All
Facility_Belt_All
Side_Divider_All
Median_All
Curb_All
Junction_Surface_All
Lane_Marking_White_All
Lane_Marking_Yellow_All
Crosswalk_All
Stop_Line_All
Street_Light_All
Street_Light_Lamp_All
Tree_Crown_All
Tree_Trunk_All
```

核心生成逻辑：

- 按道路中心线扫掠断面组件。
- 将主车行道、辅道、支路、非机动车道、停车带、人行道、绿化带等分层输出。
- 对路口生成 `Junction_Surface_All`，补齐交叉口冲突区铺装。
- 在路口接近区清除标线，避免标线穿越交叉口。
- 在有效进口道生成 `Crosswalk_All` 和 `Stop_Line_All`。
- 路口斑马线和停止线向车行面内缩 `0.3m`，避免标线横向越出道路铺装范围。
- 连续白色边线和黄色中心线按道路曲线每 `8m` 采样扫掠，避免弯路标线被起终点拉成穿越空地的直线。
- 相邻两块主车行道之间生成双黄线时，自动压掉共享边界两侧的白色边线，避免出现“白线夹双黄线”。
- 生成白色/黄色车道标线。
- 生成路灯和树木资产层。
- 对锐角折线应用过渡曲线。
- 输出路口拓扑语义节点，记录路口类型、主次关系、arm 和转向矩阵。

当前树和路灯已按“增强可视化”参数生成，较早期版本更高、更大、更密：

| 道路类别 | 路灯间距 | 路灯高度 | 树间距 | 树尺度 |
|---|---:|---:|---:|---:|
| 快速路 | 34m | 11.5m | 17m | 1.55 |
| 主干路 | 32m | 10.5m | 16m | 1.45 |
| 次干路 | 30m | 9.2m | 16m | 1.32 |
| 支路 | 28m | 7.8m | 18m | 1.15 |

长道路会在候选点中均匀抽样，避免资产只集中在道路起点附近。当前每条道路资产上限为：

```python
MAX_STREET_LIGHTS_PER_ROAD = 800
MAX_TREES_PER_ROAD = 900
```

当前路口与曲线处理参数：

```python
ENABLE_TRANSITION_CURVES = True
ENABLE_ROUNDED_JUNCTION_SURFACES = True
GENERATE_JUNCTION_CROSSWALKS = True
GENERATE_JUNCTION_STOP_LINES = True
JUNCTION_MARKING_CLEARANCE_M = 11.0
JUNCTION_SURFACE_Z_OFFSET_M = 0.026
JUNCTION_PATCH_SMOOTH_M = 2.2
JUNCTION_PATCH_MIN_THROAT_M = 8.0
JUNCTION_PATCH_MAX_THROAT_M = 38.0
CROSSWALK_BAND_LENGTH_M = 4.0
CROSSWALK_STRIPE_WIDTH_M = 0.45
CROSSWALK_STRIPE_GAP_M = 0.60
STOP_LINE_WIDTH_M = 0.45
STOP_LINE_TO_CROSSWALK_GAP_M = 3.0
JUNCTION_MARKING_LATERAL_INSET_M = 0.30
```

路口专项评分体系位于 `output/qc_report/cim_city_junction_score.json`，按 100 分制评估：

| 指标 | 权重 |
|---|---:|
| junction_surface_continuity | 16 |
| approach_marking_clearance | 12 |
| pedestrian_crossing | 12 |
| stop_control_marking | 10 |
| topology_semantic_completeness | 16 |
| lane_movement_semantic | 14 |
| asset_visibility_clearance | 8 |
| layer_material_separation | 7 |
| semantic_traceability | 5 |

## 7. 交通站点与管线

### 7.1 公交站

输入：

```text
data/Data/公交站【筛选首末站+地铁接驳站】/*.shp
```

数据统计：

```text
features: 121
type:
  首末站: 96
  地铁接驳站: 25
city_name:
  深圳市: 121
```

识别逻辑：

- 如果存在 `highway=bus_stop` 或 `bus=yes`，识别为公交站。
- 对当前工程 SHP，若存在 `station_ui`、`line_name`、`raw_name` 字段，也识别为公交站。

生成形态：

- 底座：`4.2m x 1.8m x 0.24m`
- 站亭：`3.6m x 0.18m x 2.3m`
- 顶棚：`4.4m x 2.0m x 0.18m`

### 7.2 轨道站点

输入：

```text
data/Data/轨道线和站点转坐标2000/轨道站点2000.shp
```

数据统计：

```text
features: 11
type: station
type修正:
  地铁站: 10
  高铁站: 1
city:
  深圳: 11
```

当前脚本识别规则会将字段中包含 `station`、`subway`、`u-bahn` 的站点生成 `Subway_Station_*` 盒体。因此当前 11 个站点都进入站点体块模块。

站点参数：

```python
SUBWAY_STATION_DEPTH_M = -11.0
SUBWAY_STATION_SIZE_M = (34.0, 16.0, 7.0)
```

### 7.3 轨道交通隧道

输入：

```text
data/Data/轨道线和站点转坐标2000/轨道线2000.shp
```

根据当前数据说明，`轨道线2000.shp` 本身就是轨道交通数据集中的隧道线，而不是只作为普通轨道参考线。

数据统计：

```text
features: 15
type:
  subway: 14
  高铁线: 1
name:
  深圳地铁4号线: 6
  深圳地铁1号线: 2
  深圳地铁3号线: 2
  深圳地铁2号线: 2
  深圳地铁11号线: 1
  深圳地铁10号线: 1
  广深港高速铁路: 1
```

当前状态：

```python
GENERATE_SUBWAY_TUNNELS = True
```

脚本会识别 `railway=subway`、`tunnel=yes`、`layer < 0`，也会识别 `轨道线2000.shp` 中的 `type=subway/高铁线` 以及名称中的“地铁、轨道、铁路、高铁”等语义，并以固定深度生成圆柱隧道。当前 15 条轨道交通隧道线被合并输出为：

```text
Subway_Tunnel_All
```

```python
SUBWAY_TUNNEL_RADIUS_M = 2.6
SUBWAY_TUNNEL_DEPTH_M = -14.0
```

### 7.4 管线

当前脚本优先使用真实管线图层。只在没有真实管线时，才沿道路中心线生成模拟管线。

当前真实管线输入和三维建模规则：

| 类型 | 文件 | 有效建模记录 | 默认 DN | 管顶覆土 | 中心深度 | 管径来源 |
|---|---|---:|---:|---:|---:|---|
| 供水 | `data/Data/供水+污水/供水管线.shp` | 1746 | DN400 | `1.0m` | `1.2m` | 当前 SHP 无管径字段，使用标准化默认值 |
| 污水 | `data/Data/供水+污水/污水管线.shp` | 3010 | DN600 | `1.8m` | `2.1m` | 当前 SHP 无管径字段，使用标准化默认值 |
| 燃气 | `data/Data/rq规划试验区校核/rq规划标注管径试验区校核.shp` | 736 | DN400 | `1.6m` | `1.65m-1.9m` | 优先从 `管径`、`RefName_1` 等字段解析，缺失时使用默认值 |

管线建模依据写入 `scripts/05_generate_cim_city.py` 的 `UTILITY_PIPE_SPECS`：

- 管线综合、覆土和交叉避让参考 `GB 50289-2016 城市工程管线综合规划规范`。
- 供水管网语义和管径合理性参考 `GB 50013-2018 室外给水设计标准`。
- 污水重力管线语义、最小 DN300 控制参考 `GB 50014-2021 室外排水设计标准`。
- 给水排水施工验收与回填语义参考 `GB 50268-2008 给水排水管道工程施工及验收规范`。

当前供水、污水 SHP 字段只有 `FID_`、`Entity`、`Layer`、`Color`、`Linetype`、`Elevation`、`RefName` 等 CAD 属性，没有可直接读取的管径、材质、埋深字段。因此脚本会：

1. 先在 `管径`、`DN`、`D`、`Diameter`、`规格`、`备注`、`RefName`、`RefName_1` 等字段里用正则解析 DN。
2. 若字段不存在或无法解析，则写入显式的 `fallback_standard_default`。
3. 用 `管顶覆土 + 半径` 计算管线中心深度，保证供水位于污水上方，当前 `water_sewer_vertical_order_ok=true`。
4. 将每根源管线的 DN、覆土、中心深度、材质类别、来源字段、标准引用写入管线语义 JSON。

主 OBJ 中三类管线合并为：

```text
Utility_Water_All
Utility_Sewer_All
Utility_Gas_All
```

当前管线专项输出：

```text
output/semantic/cim_city_utility_pipes_semantic.json
output/qc_report/cim_city_utility_pipe_qc.json
```

当前管线 QC 结果：

```text
score: 100.0
grade: A
pipe_feature_count: 5492
pipe_feature_count_by_type: Water 1746, Sewer 3010, Gas 736
fallback_diameter_count: 5289
attribute_diameter_count: 203
water_sewer_vertical_order_ok: true
```

保留但当前未使用的模拟管线类型：

```python
Water
Sewer
Power
Telecom
```

## 8. 建筑与历史模块状态

当前 `data/Data` 下没有被主生成脚本识别的 `building_footprint.geojson` 或建筑 SHP，因此当前主模型不包含建筑体块。

输出目录中仍可看到历史模块文件：

```text
output/obj/modules/cim_city_buildings.obj
output/fbx/modules/cim_city_buildings.fbx
```

当前 `output/obj/cim_city.obj` 对象清单未包含 `Building_*`，因此建筑模块不应计入当前主模型统计。`cim_city_subway_tunnels.obj/fbx` 已由本轮生成更新，代表 `轨道线2000.shp` 生成的轨道交通隧道。

## 9. 当前输出成果

### 9.1 主输出

```text
output/obj/cim_city.obj
output/fbx/cim_city.fbx
```

当前文件大小：

| 文件 | 大小 |
|---|---:|
| `output/obj/cim_city.obj` | 2,357,361,449 bytes |
| `output/fbx/cim_city.fbx` | 550,370,444 bytes |

### 9.2 当前有效模块输出

| 模块 | OBJ | FBX |
|---|---:|---:|
| 道路 | `output/obj/modules/cim_city_roads.obj`，约 2034.41 MB | `output/fbx/modules/cim_city_roads.fbx`，约 475.31 MB |
| 轨道交通隧道 | `output/obj/modules/cim_city_subway_tunnels.obj`，约 8.40 MB | `output/fbx/modules/cim_city_subway_tunnels.fbx`，约 3.05 MB |
| 公交站 | `output/obj/modules/cim_city_bus_stops.obj`，约 0.28 MB | `output/fbx/modules/cim_city_bus_stops.fbx`，约 0.36 MB |
| 轨道站点 | `output/obj/modules/cim_city_subway_stations.obj`，约 0.01 MB | `output/fbx/modules/cim_city_subway_stations.fbx`，约 0.04 MB |
| 管线 | `output/obj/modules/cim_city_utility_pipes.obj`，约 189.07 MB | `output/fbx/modules/cim_city_utility_pipes.fbx`，约 46.15 MB |

### 9.3 语义与质检输出

```text
output/semantic/cim_city_roads_semantic.json
output/semantic/cim_city_junctions_semantic.json
output/semantic/cim_city_utility_pipes_semantic.json
output/qc_report/cim_city_roads_model_score.json
output/qc_report/cim_city_junction_score.json
output/qc_report/cim_city_marking_alignment_qc.json
output/qc_report/cim_city_utility_pipe_qc.json
```

道路语义文件当前包含 416 个道路对象记录，记录字段包括：

- 源道路 ID、道路名称、道路等级。
- 源断面编号与建模断面编号。
- 是否经过对称化归一。
- 源宽度、目标宽度、建模宽度和宽度误差。
- 过渡曲线数量。
- 路口标线清空距离。
- 缺失组件列表。
- 源断面组件、建模断面组件和各组件横向 span。

路口语义文件当前包含 758 个路口对象记录，记录字段包括：

- 路口 ID、路口类型、层级和中心点坐标。
- 连接道路数量、进口/出口 arm 数量、断面和道路等级统计。
- 每个 arm 的道路 ID、道路等级、方向角、宽度、车道估算和标线适配情况。
- `movements` 转向矩阵，记录 arm 到 arm 的直行、左转、右转和掉头关系。
- `quality_flags`，标记拓扑语义、转向语义、标线和主次路角色是否完整。

管线语义文件当前包含 5492 个管线对象记录，记录字段包括：

- `pipe_type`、中文类型、源 SHP 要素索引和源字段摘要。
- `dn_mm`、`diameter_source`、半径、管顶覆土、中心深度、管底深度。
- 材质类别、压力/重力流模型和标准引用。
- 几何长度、分段数量和管径/覆土合规标记。

质检报告评分权重：

| 指标 | 权重 |
|---|---:|
| geometry_symmetry_and_width | 25 |
| section_rule_fit | 18 |
| component_completeness | 17 |
| semantic_attribute_completeness | 15 |
| visual_layer_material_separation | 15 |
| intersection_curve_treatment | 10 |

当前报告结论：

```text
54 road centerlines used symmetric fallback sections to remove one-sided cross-section artifacts.
330 sharp polyline corners were converted to transition curves.
Rounded junction surfaces are generated and lane markings are cleared inside junction approach zones.
Crosswalk and stop-line layers are generated for valid junction approaches.
```

路口专项评分报告采用 100 分制，当前结果为：

```text
score: 100.0
grade: A
junction_count: 758
semantic_junction_count: 758
total_arm_count: 2638
total_allowed_movement_count: 6750
expected_marked_approach_count: 3273
junction_surface_count: 758
crosswalk_stripe_count: 69281
stop_line_count: 3273
```

标线对齐报告检查所有道路白线、黄线、斑马线和停止线是否超出所属道路/车行面横向范围，当前结果为：

```text
score: 100.0
grade: A
checked_marking_count: 10956
suppressed_white_edge_at_double_yellow: 720
issue_count: 0
max_lateral_overhang_m: 0.0
marking_sweep_sample_interval_m: 8.0
max_solid_marking_segment_m: 8.0
```

管线专项评分报告采用 100 分制，当前结果为：

```text
score: 100.0
grade: A
pipe_feature_count: 5492
pipe_feature_count_by_type: Water 1746, Sewer 3010, Gas 736
diameter_source_counts: fallback_standard_default 5289, attribute 203
center_depth_range_m_by_type: Water 1.2, Sewer 2.1, Gas 1.65-1.9
dn_range_mm_by_type: Water DN400, Sewer DN600, Gas DN100-DN600
water_sewer_vertical_order_ok: true
```

### 9.4 渲染检查输出

当前 `output/render/` 下保留多组检查图：

```text
cim_city_fbx_top.png
cim_city_roads_fbx_top.png
road_quality_overview.png
road_quality_core.png
road_quality_north.png
road_quality_south.png
cityengine_check_overview.png
cityengine_check_core.png
cityengine_check_north.png
cityengine_check_south.png
street_light_check_core.png
```

相关脚本：

```text
scripts/08_render_road_quality_views_blender.py
scripts/10_render_road_fbx_preview_blender.py
```

## 10. 材质系统

FBX 导出脚本按对象名前缀分配材质。当前主要映射如下：

| 对象前缀 | 材质名 |
|---|---|
| `Road_Surface_Main` | `CIM_Road_Main_Asphalt` |
| `Road_Surface_Service` | `CIM_Road_Service_Asphalt` |
| `Road_Surface_Branch` | `CIM_Road_Branch_Asphalt` |
| `Non_Motor_Lane` | `CIM_Non_Motor_Lane_Asphalt` |
| `Parking_Lane` | `CIM_Parking_Lane_Asphalt` |
| `Sidewalk` | `CIM_Sidewalk_Warm_Concrete` |
| `Green_Belt` | `CIM_Roadside_Green_Belt` |
| `Facility_Belt` | `CIM_Facility_Belt_Stone_Green` |
| `Side_Divider` | `CIM_Side_Divider_Low_Green` |
| `Median` | `CIM_Median_Concrete` |
| `Curb` | `CIM_Curb_Light_Concrete` |
| `Lane_Marking_White` | `CIM_Lane_Marking_White` |
| `Lane_Marking_Yellow` | `CIM_Lane_Marking_Yellow` |
| `Junction_Surface` | `CIM_Road_Asphalt_Fine` |
| `Crosswalk` | `CIM_Crosswalk_White` |
| `Stop_Line` | `CIM_Stop_Line_White` |
| `Street_Light` | `CIM_Street_Light_Painted_Metal` |
| `Street_Light_Lamp` | `CIM_Street_Light_Warm_Lamp` |
| `Tree_Trunk` | `CIM_Tree_Bark` |
| `Tree_Crown` | `CIM_Tree_Canopy_Varied` |
| `Subway_Station` | `CIM_Subway_Station_Blue` |
| `Bus_Stop` | `CIM_Bus_Stop_Green` |
| `Utility_Water` | `CIM_Utility_Water_Blue` |
| `Utility_Sewer` | `CIM_Utility_Sewer_Brown` |
| `Utility_Gas` | `CIM_Utility_Gas_Orange` |
| `Utility_Power` | `CIM_Utility_Power_Yellow` |
| `Utility_Telecom` | `CIM_Utility_Telecom_Magenta` |

材质使用简单 Principled BSDF 参数，主要依赖 Base Color、Roughness、Metallic，便于 FBX 跨软件导入。

## 11. 验证方法

### 11.1 Python 语法检查

```bash
python -c "import ast, pathlib; files=['scripts/00_download_allianz_arena_osm.py','scripts/01_generate_cim3_road.py','scripts/05_generate_cim_city.py','scripts/06_export_cim_city_fbx_blender.py','scripts/07_inspect_fbx_materials_blender.py','scripts/08_render_road_quality_views_blender.py','scripts/10_render_road_fbx_preview_blender.py']; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8')) for p in files]; print('syntax ok')"
```

本次检查结果：

```text
syntax ok
```

### 11.2 重新生成城市 OBJ

```bash
python scripts/05_generate_cim_city.py
```

成功后应看到：

```text
CIM city OBJ generated:
- output/obj/cim_city.obj
- module OBJ outputs:
  - roads: output/obj/modules/cim_city_roads.obj
  - subway_stations: output/obj/modules/cim_city_subway_stations.obj
  - bus_stops: output/obj/modules/cim_city_bus_stops.obj
  - utility_pipes: output/obj/modules/cim_city_utility_pipes.obj
- road semantic objects: ...
- junction semantic objects: ...
- utility semantic objects: ...
- road model score: ...
- junction score: ...
- marking alignment qc: ...
- utility pipe qc: ...
```

### 11.3 导出城市 FBX

```bash
blender --background --python scripts/06_export_cim_city_fbx_blender.py
```

### 11.4 检查 FBX 材质

```bash
blender --background --python scripts/07_inspect_fbx_materials_blender.py
```

### 11.5 渲染道路检查图

```bash
blender --background --python scripts/08_render_road_quality_views_blender.py
blender --background --python scripts/10_render_road_fbx_preview_blender.py
```

## 12. 当前运行环境注意事项

当前主生成环境为：

```text
D:\ProgramData\miniconda3\envs\cim-road\python.exe
```

该环境已可运行城市模型生成脚本，以下依赖导入正常：

```text
geopandas
pandas
numpy
shapely
trimesh
rasterio
```

当前 `osmnx` 仍未安装，因此 OSM 下载脚本 `scripts/00_download_allianz_arena_osm.py` 不能在该环境中运行；如果需要重新下载 OSM 数据，需要补装：

```text
osmnx
```

Blender 路径：

```text
C:\Program Files\Blender Foundation\Blender 5.1\blender.exe
```

可按如下方式设置环境变量：

```bat
set "PYTHON_EXE=D:\ProgramData\miniconda3\envs\cim-road\python.exe"
set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

## 13. 已知限制

1. `00_download_allianz_arena_osm.py` 文件名仍是历史名称，内容已改为北京亦庄 OSM 下载。
2. `run_city_workflow.*` 第一步下载 OSM 数据，但第二步当前默认读取 `data/Data` 工程数据，两者尚未完全统一。
3. 当前主模型未包含建筑体块，因为 `data/Data` 中没有被脚本识别的建筑轮廓数据。
4. 当前轨道交通隧道按固定深度 `-14m` 生成，尚未按线路、车站、埋深或层级细化。
5. `Subway_Station_*` 命名会覆盖所有被识别为 station 的轨道站点，其中包括 1 个高铁站。
6. 当前供水、污水源 SHP 缺少管径、材质、埋深字段，因此三维尺寸使用标准化默认值；燃气已可从部分字段解析管径。
7. 历史模块文件仍留在 `output/obj/modules` 和 `output/fbx/modules`，需要清理或重生成以避免误读。
8. 当前环境可运行生成脚本，但本地给水/污水参数 PDF 尚未自动解析进模型。

## 14. 后续建议

短期建议：

- 将 `scripts/00_download_allianz_arena_osm.py` 重命名为 `00_download_beijing_yizhuang_osm.py`，并同步修改 README 与工作流脚本。
- 明确 `data/Data`、`data/raw_beijing_yizhuang`、`data/raw_suzhou_dushu_lake` 的主备关系。
- 保持 `cim-road` Python 环境和 Blender 5.1 导出环境一致，定期重新运行 `scripts/05_generate_cim_city.py` 清理旧模块。
- 将 `GENERATE_SUBWAY_TUNNELS` 和隧道识别规则写入配置文件，而不是只写在脚本常量中。
- 将轨道站点命名从 `Subway_Station_*` 调整为 `Transit_Station_*` 或按 `type修正` 区分地铁站/高铁站。

中期建议：

- 接入建筑轮廓数据，恢复当前工程数据版本的建筑体块。
- 补充供水、污水的管径、材质和埋深字段，替代当前 `fallback_standard_default`。
- 增加 `output/metadata/cim_city_summary.json`，记录输入数据、对象数量、CRS、局部原点、脚本版本和生成时间。
- 清理历史 OSM 版本输出，或把不同区域的输出放入区域子目录。
- 增加自动验证脚本，检查主 OBJ、模块 OBJ、FBX、语义 JSON 和质检 JSON 是否来自同一次生成。

长期建议：

- 输出 CityGML、IFC 或 3D Tiles，承载更完整的 CIM 语义。
- 建立 CIM 对象 ID 体系，让道路断面、站点、管线和三维对象可追踪。
- 加入管线节点、检查井、阀门、雨水口和交叉避让规则。
- 基于轨道线拓扑生成站厅、站台、换乘通道和区间隧道。
- 将模型生成参数、质检评分和渲染检查纳入 CI 或批处理报告。
