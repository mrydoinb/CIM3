# CIM 城市道路建模技术文档

> 当前可执行脚本已收敛为 `scripts/01` 到 `scripts/04` 的道路快速迭代流程。
> 请以 `scripts/README.md` 为准。下文提及的旧城市、渲染和 QC 包装脚本仅作为历史记录保留。

本文档对应当前重构后的代码结构。当前项目已经去掉 `src/cim_road/` 这一层，核心模块直接位于 `src/` 下，例如 `src/city/`、`src/road/`、`src/junction/`、`src/blender/`。

## 1. 项目定位

本项目是 CIM 道路与城市级三维建模 POC。目标是从本地 GIS/工程数据生成可检查、可导入三维平台的城市模型，重点覆盖：

- 道路系统：机动车道、辅路、非机动车道、人行道、绿化带、中央分隔带、路缘石、车道线、路口面、斑马线、停止线。
- 路口系统：中心线拓扑检测、路口节点聚类、圆角路口面、道路组件回退裁剪、路口语义和路口评分。
- 交通设施：公交站、轨道站点、轨道区间隧道。
- 地下管线：供水、污水、燃气，以及合成补充管线类型。
- 交付模型：OBJ、FBX、分模块 OBJ/FBX、语义 JSON、质检 JSON、渲染预览图。

当前主流程是城市级建模，旧的单独 `road_test.*` 输出入口已经退役。`scripts/01_generate_cim3_road.py` 仅保留兼容提示；道路生成逻辑通过 `src/road/generator.py` 被城市流程复用。

## 2. 当前代码结构

```text
cim_road_poc/
  data/
    Data/
    processed/
    raw/
    rules/
  docs/
    cim_city_technical_documentation.md
    refactor_architecture.md
  output/
    obj/
    fbx/
    semantic/
    qc_report/
    render/
  scripts/
    01_generate_cim3_road.py
    05_generate_cim_city.py
    06_export_cim_city_fbx_blender.py
    07_inspect_fbx_materials_blender.py
    08_render_road_quality_views_blender.py
    09_render_cross_section_diagrams.py
    10_render_road_fbx_preview_blender.py
    11_check_junction_stack.py
  src/
    blender/
    city/
    cli/
    config/
    data_io/
    geometry/
    junction/
    render/
    road/
  pyproject.toml
  run_city_workflow.bat
  run_city_workflow.sh
```

设计原则：

- `scripts/` 只保留兼容入口，每个脚本把 `src/` 加入 `sys.path` 后调用对应模块的 `main()`。
- `src/` 保存真正业务逻辑。
- `src/data_io/`、`src/config/`、`src/geometry/` 目前是预留拆分边界，后续可继续从大文件中迁移读取、配置和几何工具函数。
- 旧路径 `src/cim_road/` 已移除。

## 3. 模块职责

| 模块 | 职责 |
|---|---|
| `src/city/pipeline.py` | 城市级主流程。读取数据，组织道路、建筑、轨道、公交站、管线，导出 OBJ、语义 JSON 和 QC 报告。 |
| `src/road/generator.py` | 道路建模基础库。包含道路规则读取、断面解释、路口检测、路口节点、基础 Mesh、底图和道路资产生成 helper。 |
| `src/junction/stack_check.py` | 路口叠压和连通性质检。复用城市主流程里的路口生成函数，输出专项检查报告。 |
| `src/blender/fbx_export.py` | Blender 后台导入 OBJ、分配材质、导出总 FBX 和分模块 FBX。 |
| `src/blender/fbx_inspect.py` | 反向导入 FBX，统计对象、Mesh 和材质。 |
| `src/blender/road_quality_render.py` | 渲染道路模块质量检查图。 |
| `src/blender/road_fbx_preview.py` | 渲染道路 FBX 顶视预览图。 |
| `src/render/cross_section_svg.py` | 根据道路语义 JSON 输出横断面 SVG 与索引页。 |
| `src/cli/` | `python -m` 方式的轻量命令入口。 |

## 4. 主运行方式

Windows：

```bat
run_city_workflow.bat
```

Git Bash / Linux / macOS：

```bash
./run_city_workflow.sh
```

主流程分两步：

```text
1. python scripts/05_generate_cim_city.py
2. blender --background --python scripts/06_export_cim_city_fbx_blender.py
```

也可以单独运行：

```bash
python scripts/05_generate_cim_city.py
blender --background --python scripts/06_export_cim_city_fbx_blender.py
```

如果使用模块方式运行，需要确保 `src/` 在 `PYTHONPATH` 中，或先安装 editable 包：

```bash
python -m cli.generate_city
python -m cli.export_fbx
python -m cli.check_junction_stack
```

## 5. 运行环境

Python 建议版本：`>=3.10`。

主要 Python 依赖：

- `geopandas`
- `pandas`
- `numpy`
- `shapely`
- `trimesh`
- `rasterio`，可选。当前 `ENABLE_DEM_ELEVATION = False`，高程 DEM 读取默认不启用。

Blender 依赖：

- Blender 自带 `bpy`
- Blender 自带 `mathutils`

根目录脚本支持两个环境变量：

```text
PYTHON_EXE   Python 解释器路径
BLENDER_EXE  Blender 可执行文件路径
```

`run_city_workflow.bat` 默认会尝试使用本机配置的 Python 和 Blender 路径；如果不存在，会提示设置 `BLENDER_EXE`。

## 6. 输入数据

当前城市级主流程以 `data/Data/` 为主要输入目录。道路模块通过 `src/road/generator.py` 中的 `RAW_SOURCE_DIR` 定位：

```text
data/Data/
```

主要数据源：

| 数据 | 当前匹配路径 |
|---|---|
| 道路中心线 | `data/Data/road50kms/*.shp`，或备用 `road_centerline.geojson`、其他 GeoJSON |
| 建筑轮廓 | `building_footprint.geojson`，支持递归查找 |
| 公交站 | `**/公交站*.shp` |
| 轨道线 | `**/轨道线2000.shp` 或 `**/*线2000.shp` |
| 轨道站点 | `**/轨道站点2000.shp` 或 `**/*站点2000.shp` |
| 供水管线 | `**/供水管线.shp` |
| 污水管线 | `**/污水管线.shp` |
| 燃气管线 | `**/rq*.shp` |
| DEM | `data/Data/DEM/DEM/`，当前默认不启用高程采样 |

规则文件：

```text
data/rules/road_rules.json
data/rules/road_section_requirements.json
```

`road_rules.json` 提供默认道路等级、车道数、车道宽度、路面宽度、人行道宽度、路缘石和材质等基础模板。

`road_section_requirements.json` 提供工程断面编号与组件序列，例如 A、B、C、D 类断面，将道路红线宽度拆成 sidewalk、facility_belt、green_belt、non_motor_lane、service_lane、main_carriageway、median 等组件。

## 7. 坐标与单位

当前主数据采用：

```text
TARGET_CRS = EPSG:4547
```

代码注释中说明其为 CGCS2000 3 度带高斯克吕格投影，中央经线 114E。建模单位为米。

城市输出采用局部坐标，`origin` 由数据范围中心派生，用于把大地坐标转换成三维建模空间中的相对坐标，减少大坐标值导致的显示和数值问题。

## 8. 城市生成流程

主入口：

```text
scripts/05_generate_cim_city.py
  -> src/city/pipeline.py::main()
```

核心流程：

```text
加载道路、建筑、公交站、轨道、管线数据
  -> 道路预处理与局部坐标转换
  -> 道路断面组件生成
  -> 路口检测、聚类和路口面生成
  -> 道路组件按路口范围裁剪
  -> 路口斑马线、停止线和道路标线生成
  -> 建筑、轨道、公交站、管线 Mesh 生成
  -> 合并城市 Scene
  -> 导出总 OBJ 和分模块 OBJ
  -> 写入道路、路口、管线语义 JSON
  -> 写入道路、路口、标线、管线 QC 报告
```

当前 `src/city/pipeline.py` 会把 `road_gen.SWEEP_SAMPLE_INTERVAL_M` 设置为 `20.0`，用于城市级道路扫掠采样，降低超大模型输出压力。

## 9. 道路建模逻辑

道路基础逻辑位于：

```text
src/road/generator.py
```

主要能力：

- 暴露道路数据路径和规则读取 helper，城市流程负责实际读取与预处理。
- 解释道路等级、车道数、单行、桥梁、层级、断面编号等属性。
- 根据 `road_section_requirements.json` 将道路拆成多种横断面组件。
- 为城市流程提供基础 Mesh、GIS 底图、路灯、行道树和路口几何 helper。
- 向城市流程暴露可复用函数，例如 `load_rules()`、`cross_section_components_for_row()`、`attach_junction_distances()`、`build_junction_nodes()`。

旧 road-only 入口已退役：

```bash
python scripts/01_generate_cim3_road.py
```

该入口只提示改用 `scripts/05_generate_cim_city.py` 或 `python -m cli.generate_city`，不再生成 `road_test.*`。

## 10. 路口生成逻辑

当前路口逻辑分布在 `src/road/generator.py` 和 `src/city/pipeline.py` 中，专项检查在 `src/junction/stack_check.py`。

关键阶段：

1. `detect_junction_points()` 从道路中心线端点、相交关系和缓冲面关系检测候选路口点。
2. `cluster_corridor_junction_points()` 对走廊型或近距离候选点进行聚类，减少一个路口被拆成多个节点。
3. `attach_junction_distances()` 把路口沿线里程写入道路行的 `junction_distances_json`。
4. `junction_point_buckets()` 和相关函数在城市流程中聚合道路成员。
5. `build_junction_nodes()` 根据连接道路构造 RoadSocket、LaneSocket 和 LaneConnector。
6. `build_rounded_junction_surface_geometries()` 生成圆角路口面。
7. `build_rounded_junction_surface_meshes()` 把路口面转为 `Junction_Surface` Mesh。
8. 道路面、路侧、人行道、标线会按路口影响范围裁剪或回退，避免路口中心叠压。
9. `add_junction_crosswalks_and_stop_lines()` 生成斑马线和停止线。
10. `build_city_junction_semantic()` 写出路口拓扑、arms、movements、设计选项和质量标记。

重要参数位于 `src/city/pipeline.py`：

```text
ENABLE_ROUNDED_JUNCTION_SURFACES = True
GENERATE_JUNCTION_CROSSWALKS = True
GENERATE_JUNCTION_STOP_LINES = True
JUNCTION_BUCKET_CLUSTER_M = 16.0
JUNCTION_MARKING_CLEARANCE_M = 11.0
JUNCTION_SURFACE_Z_OFFSET_M = 0.026
JUNCTION_PATCH_SMOOTH_M = 2.2
JUNCTION_PATCH_MIN_THROAT_M = 8.0
JUNCTION_PATCH_MAX_THROAT_M = 38.0
JUNCTION_MARKING_SURFACE_CLEARANCE_M = 2.6
CROSSWALK_BAND_LENGTH_M = 4.0
STOP_LINE_TO_CROSSWALK_GAP_M = 3.0
```

路口专项 QC：

```bash
python scripts/11_check_junction_stack.py
```

输出：

```text
output/qc_report/cim_city_junction_stack_check.json
```

该检查会复用城市流程中的路口面、道路组件和标线生成逻辑，统计路口面与道路组件、标线、道路资产之间的平面叠压，并检查选定路口成员道路是否与路口面连通。

## 11. 管线与地下空间

管线逻辑位于 `src/city/pipeline.py`：

- `build_utility_pipe_meshes()`：从供水、污水、燃气 SHP 构造管线 Mesh。
- `build_synthetic_utility_pipe_meshes()`：当数据不足时生成合成补充管线。
- `build_city_utility_pipe_semantic()`：写出管线语义。
- `build_city_utility_pipe_qc()`：写出管线 QC。

内置管线类型包括 Water、Sewer、Gas、Power、Telecom 等，包含默认管径、最小管径、覆土深度、横向偏移、材料类别、流态类型和参考标准。

轨道地下空间：

- `build_subway_tunnel_meshes()` 生成轨道区间隧道。
- `build_transit_node_meshes()` 生成轨道站点体块和公交站点体块。

## 12. 输出成果

城市级 OBJ：

```text
output/obj/cim_city.obj
output/obj/modules/cim_city_roads.obj
output/obj/modules/cim_city_buildings.obj
output/obj/modules/cim_city_subway_tunnels.obj
output/obj/modules/cim_city_subway_stations.obj
output/obj/modules/cim_city_bus_stops.obj
output/obj/modules/cim_city_utility_pipes.obj
```

城市级 FBX：

```text
output/fbx/cim_city.fbx
output/fbx/modules/cim_city_roads.fbx
output/fbx/modules/cim_city_buildings.fbx
output/fbx/modules/cim_city_subway_tunnels.fbx
output/fbx/modules/cim_city_subway_stations.fbx
output/fbx/modules/cim_city_bus_stops.fbx
output/fbx/modules/cim_city_utility_pipes.fbx
```

语义 JSON：

```text
output/semantic/cim_city_roads_semantic.json
output/semantic/cim_city_roads_classification.json
output/semantic/cim_city_junctions_semantic.json
output/semantic/cim_city_utility_pipes_semantic.json
```

道路 OBJ / FBX 中的道路组件会按道路类型分组导出，例如
`Sidewalk_RoadType_Branch_All`、`Road_Surface_Main_RoadType_Arterial_All`。
跨道路类型的路口连接补片使用 `RoadType_Shared`，路口面使用
`Junction_Surface_Shared_All`。

QC 报告：

```text
output/qc_report/cim_city_roads_model_score.json
output/qc_report/cim_city_junction_score.json
output/qc_report/cim_city_marking_alignment_qc.json
output/qc_report/cim_city_utility_pipe_qc.json
output/qc_report/cim_city_junction_stack_check.json
```

渲染与检查图：

```text
output/render/road_quality_overview.png
output/render/road_quality_core.png
output/render/road_quality_north.png
output/render/road_quality_south.png
output/render/cim_city_roads_fbx_top.png
output/render/cross_sections/index.html
output/render/cross_sections/*.svg
```

## 13. FBX 导出与材质

FBX 导出入口：

```text
scripts/06_export_cim_city_fbx_blender.py
  -> src/blender/fbx_export.py::main()
```

流程：

```text
导入 output/obj/cim_city.obj
  -> 根据对象名前缀和层名分配材质
  -> 导出 output/fbx/cim_city.fbx
  -> 逐个导入 output/obj/modules/*.obj
  -> 导出 output/fbx/modules/*.fbx
```

材质按照道路、标线、绿化、建筑、轨道、公交站、管线等对象类型设置。导出脚本还支持底图纹理路径：

```text
output/textures/google_static_map.png
output/textures/world_imagery_basemap.png
```

## 14. 检查与可视化工具

FBX 材质检查：

```bash
blender --background --python scripts/07_inspect_fbx_materials_blender.py
```

道路质量渲染：

```bash
blender --background --python scripts/08_render_road_quality_views_blender.py
```

横断面 SVG：

```bash
python scripts/09_render_cross_section_diagrams.py
```

可选参数：

```bash
python scripts/09_render_cross_section_diagrams.py --basis modeled
python scripts/09_render_cross_section_diagrams.py --basis source
python scripts/09_render_cross_section_diagrams.py --section B2
```

道路 FBX 顶视预览：

```bash
blender --background --python scripts/10_render_road_fbx_preview_blender.py
```

路口叠压检查：

```bash
python scripts/11_check_junction_stack.py
```

## 15. 当前已知结构债务

第一轮重构已经完成目录拆分和入口瘦身，但仍有两个大文件需要继续拆：

```text
src/road/generator.py
src/city/pipeline.py
```

建议下一阶段拆分：

```text
src/road/
  rules.py
  cross_section.py
  preparation.py
  surfaces.py
  markings.py
  assets.py
  scoring.py

src/junction/
  detection.py
  clustering.py
  design_options.py
  surfaces.py
  clipping.py
  markings.py
  semantics.py
  qc.py

src/city/
  buildings.py
  transit.py
  utilities.py
  scene.py
  semantics.py
  scoring.py

src/data_io/
  geodata.py
  obj_export.py
  semantic_json.py
  qc_json.py
```

其中路口模块优先级最高，因为当前路口检测、路口面、道路裁剪、标线、语义和 QC 都围绕同一组几何状态展开，继续集中在大文件中会提高修改风险。

## 16. 常见维护点

- 修改道路宽度或断面：优先改 `data/rules/road_section_requirements.json`，必要时再改 `src/road/generator.py` 的断面解释逻辑。
- 修改道路等级默认值：改 `data/rules/road_rules.json`。
- 修改路口面大小或圆角：看 `src/city/pipeline.py` 中 `JUNCTION_*` 参数，以及 `build_rounded_junction_surface_geometries()`。
- 修改路口标线位置：看 `CROSSWALK_*`、`STOP_LINE_*`、`JUNCTION_MARKING_*` 参数。
- 修改 FBX 材质：看 `src/blender/fbx_export.py`。
- 修改横断面图：看 `src/render/cross_section_svg.py`。
- 修改路口叠压检查：看 `src/junction/stack_check.py`。

## 17. 快速验证命令

语法检查：

```bash
python -c "import ast,pathlib; files=list(pathlib.Path('scripts').glob('*.py'))+list(pathlib.Path('src').rglob('*.py')); [ast.parse(p.read_text(encoding='utf-8-sig'), filename=str(p)) for p in files]; print('syntax ok', len(files))"
```

核心导入检查：

```bash
python -c "import sys; sys.path.insert(0, 'src'); import road.generator; import city.pipeline; import junction.stack_check; import data_io; print('core imports ok')"
```

横断面脚本帮助：

```bash
python scripts/09_render_cross_section_diagrams.py --help
```

Git 空白检查：

```bash
git diff --check
```
