# CIM 道路模型构建与 CityEngine 式材质贴合技术方案

## 1. 项目目标

本项目当前目标是仿照 CityEngine 的规则化建模思路，构建可交互 3D 城市中的道路模型子系统。当前阶段聚焦道路系统，包括道路中心线数据处理、道路铺装几何生成、路缘石与人行道构建、标线生成、语义记录、质量检查，以及基于规则的 PBR/程序化材质贴合。

方案核心思想是将道路生成拆为两个相对独立的引擎：

- 几何生成引擎：使用 Python、GeoPandas、Shapely、Trimesh 将二维道路中心线生成带高程的三维道路组件。
- 材质贴合引擎：使用 Blender Headless 读取生成的 OBJ，根据道路规则与材质库执行类似 CityEngine CGA 的材质决策、投影 UV、PBR 节点构建和格式导出。

最终输出面向后续 WebGL、Unity、Unreal 或 CIM 平台的三维资产，包括基础几何 GLB、材质增强 GLB、FBX、Blend 工程、语义 JSON 和质检 JSON。

## 2. 当前代码结构

```text
cim_road_poc/
├── assets/
│   └── textures/
│       ├── asphalt/          # 沥青 PBR 贴图
│       ├── concrete/         # 人行道混凝土/铺装 PBR 贴图
│       ├── curb_concrete/    # 路缘石混凝土 PBR 贴图
│       └── road_marking/     # 标线贴图预留目录
├── data/
│   ├── raw/
│   │   ├── road_centerline.geojson
│   │   ├── building_footprint.geojson
│   │   ├── transport_points.geojson
│   │   └── road_centerline/*.tif   # DGM/DEM 切片
│   ├── processed/
│   │   └── road_centerline_local.geojson
│   └── rules/
│       └── road_rules.json   # 道路规则
├── scripts/
│   ├── 00_download_allianz_arena_osm.py # 下载 OSM 数据
│   ├── 01_generate_cim3_road.py # 几何生成
│   ├── 02_export_fbx_blender.py # FBX 导出
│   ├── 03_apply_materials_blender.py # 材质贴合
│   └── 04_inspect_blend_materials.py # 材质验证脚本
├── output/
│   ├── obj/road_test.obj # 基础 OBJ
│   ├── gltf/road_test.glb # 基础 GLB
│   ├── gltf/road_test_realistic.glb # 真实材质 GLB
│   ├── fbx/road_test.fbx # FBX
│   ├── road_test_realistic.blend # 材质贴合预览
│   ├── semantic/road_test_semantic.json # 语义
│   └── qc_report/road_test_qc_report.json # 质检
├── run_workflow.bat # Windows
└── run_workflow.sh # Linux/macOS 或 Git Bash
```

## 3. 数据输入与规则配置

### 3.1 原始数据

当前道路生成使用以下输入：

- `data/raw/road_centerline.geojson`：OSM 道路中心线，是道路几何生成的主输入。
- `data/raw/road_centerline/*.tif`：可选 DGM/DEM 高程切片，用于给道路端点采样地表高程。
- `data/rules/road_rules.json`：道路模板规则，控制车道数、道路宽度、人行道宽度、路缘石尺寸、标线宽度和材质语义。

### 3.2 规则结构

当前 `road_rules.json` 中的 `default_road` 规则包含：

```json
{
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
```

这些字段分为两类：

- 几何参数：`lane_count`、`lane_width`、`road_width`、`sidewalk_width`、`curb_width`、`curb_height`、`lane_marking_width`。
- 材质参数：`material`、`sidewalk_material`、`curb_material`、`marking_material`。

后续可以按 OSM `highway` 类型扩展多套规则，例如 `motorway`、`primary`、`residential`、`service` 等，使不同道路等级具有不同宽度、路缘石、人行道和材质。

## 4. 几何生成流程

几何生成入口为 `scripts/01_generate_cim3_road.py`。

### 4.1 坐标与属性预处理

主函数 `read_and_prepare_roads()` 完成以下工作：

1. 读取 `road_centerline.geojson`。
2. 若缺失 CRS，则默认设置为 `EPSG:4326`。
3. 投影到 `EPSG:32632`，统一单位为米。
4. 拆分 `MultiLineString` 为单条 `LineString`。
5. 标准化 OSM 属性，包括 `road_id`、`road_name`、`road_class`、`lane_count`、`maxspeed`、`oneway`、`is_bridge`、`road_ref`、`access`。
6. 读取 DGM/DEM 切片，采样起点和终点地表高程。
7. 对桥梁和 OSM `layer` 进行高程修正。
8. 计算局部坐标原点，将全局投影坐标转换到局部坐标系。
9. 输出 `data/processed/road_centerline_local.geojson`。

### 4.2 动态道路宽度

`get_road_rule()` 会读取道路行属性中的 `lane_count`，覆盖默认车道数，并重新计算：

```text
road_width = lane_count * lane_width
```

这使道路宽度能够根据 OSM `lanes` 字段动态变化。当前人行道宽度、路缘石宽度等仍来自 `default_road`，后续可以扩展为按道路等级差异化配置。

### 4.3 高程分层与道路面生成

`generate_planar_geometries()` 按 `elevation` 对道路分组，每个高程组独立执行二维布尔运算：

- 道路面：中心线按 `road_width` buffer。
- 总铺装面：中心线按 `road_width + 2 * sidewalk_width` buffer。
- 人行道：`total_surface - road_surface`。
- 路缘石：`road_surface.buffer(curb_width) - road_surface`。
- 标线：中心线按 `lane_marking_width` buffer 后与道路面相交。

按高程分层的原因是避免高架道路、桥梁和地面道路在二维平面上被错误 union，保证立交和上下层道路在三维空间中保持独立。

### 4.4 三维网格生成

当前网格生成分为两类：

- `polygon_to_top_mesh()`：用于道路面、人行道、标线，只生成顶面三角网。
- `polygon_to_extruded_mesh()`：用于路缘石，生成上下表面和侧面，形成有厚度的实体。

各组件命名规则如下：

```text
Road_Surface_{layer_index}
Sidewalk_{layer_index}
Curb_{layer_index}
Lane_Marking_{layer_index}
```

这个命名规则是后续材质贴合和语义匹配的重要约定。

### 4.5 输出

几何阶段输出：

- `output/obj/road_test.obj`：供 Blender 材质后处理使用的基础 OBJ。
- `output/gltf/road_test.glb`：基础 GLB，主要用于几何快速预览。
- `output/semantic/road_test_semantic.json`：语义对象清单。
- `output/qc_report/road_test_qc_report.json`：几何、网格和数据质量检查结果。

## 5. CityEngine 式材质贴合流程

材质贴合入口为 `scripts/03_apply_materials_blender.py`。

### 5.1 设计目标

旧版本材质逻辑主要是按对象名硬编码分配固定材质。当前重构后的设计更接近 CityEngine/CGA：

```text
道路规则 road_rules.json
        ↓
组件材质字段映射 COMPONENT_RULE_FIELDS
        ↓
材质 id，例如 asphalt / concrete / curb_concrete / white_marking
        ↓
材质库 MATERIAL_LIBRARY
        ↓
PBR 贴图或程序化 fallback
        ↓
稳定投影 UV
        ↓
GLB / Blend / FBX 输出
```

### 5.2 组件到规则字段的映射

`COMPONENT_RULE_FIELDS` 定义组件类型与规则字段关系：

```python
COMPONENT_RULE_FIELDS = {
    "road_surface": "material",
    "sidewalk": "sidewalk_material",
    "curb": "curb_material",
    "lane_marking": "marking_material",
}
```

这意味着：

- `Road_Surface_*` 读取 `default_road.material`。
- `Sidewalk_*` 读取 `default_road.sidewalk_material`。
- `Curb_*` 读取 `default_road.curb_material`。
- `Lane_Marking_*` 读取 `default_road.marking_material`。

### 5.3 材质库

`MATERIAL_LIBRARY` 是当前材质系统的核心。每个材质 id 包含：

- `name`：Blender 中的材质名称。
- `texture_type`：用于贴图搜索的类型标签。
- `texture_dir`：贴图目录。
- `projection_size_m`：投影 UV 的米制尺度。
- `viewport_color`：Blender Solid 视图和对象视图颜色。
- `fallback`：找不到贴图时的程序化材质参数。

当前内置材质：

| 材质 id | Blender 材质名 | 用途 | 贴图目录 |
|---|---|---|---|
| `asphalt` | `MAT_Asphalt` | 主道路沥青 | `assets/textures/asphalt` |
| `concrete` | `MAT_Concrete` | 人行道铺装 | `assets/textures/concrete` |
| `curb_concrete` | `MAT_Curb` | 路缘石 | `assets/textures/curb_concrete` |
| `white_marking` | `MAT_White_Marking` | 道路标线 | `assets/textures/road_marking` |

### 5.4 PBR 贴图搜索

材质脚本通过文件名关键字自动识别贴图角色：

- Base Color：`basecolor`、`base_color`、`diffuse`、`diff`、`color`、`albedo`
- Roughness：`roughness`、`rough`
- Normal：`normal_gl`、`nor_gl`、`normal`、`nor`
- AO：`ao`、`ambient_occlusion`、`ambientocclusion`

支持扩展名：

```text
.jpg, .jpeg, .png, .exr, .tif, .tiff
```

如果找到贴图，则构建 Principled BSDF PBR 节点；如果缺失贴图，则使用 `Noise Texture + ColorRamp + Bump` 生成程序化材质，保证任何情况下都有可见材质。

### 5.5 投影 UV

旧方案使用 Blender `smart_project`，容易导致道路分段之间贴图方向和密度不稳定。当前方案使用 `assign_projected_uv()`，按世界/局部模型坐标生成稳定 UV：

- 水平面使用 `x/y` 投影。
- 接近 X 方向的竖向侧面使用 `y/z` 投影。
- 接近 Y 方向的竖向侧面使用 `x/z` 投影。
- UV 值除以 `projection_size_m`，以米为尺度控制贴图重复。

这对应 CityEngine 中常见的 `setupProjection()` 和 `projectUV()` 逻辑：材质不是随意展开，而是以规则化投影方式贴合到生成面上。

### 5.6 Blender 可视检查增强

为了避免打开 `.blend` 后在 Solid 视图中看不到贴图效果，脚本还做了以下处理：

- 设置 `mat.diffuse_color`。
- 设置 `obj.color`。
- 清理 OBJ 导入时带入的旧顶点色，避免与 PBR 材质混淆。
- 保存 `.blend` 前配置 Cycles、世界颜色和一个 Sun Light，便于在 Material Preview/Rendered 下检查。

### 5.7 材质验证脚本

`scripts/04_inspect_blend_materials.py` 用于验证 `.blend` 中材质是否真实绑定：

输出内容包括：

- 每种材质绑定的对象数量。
- 是否存在没有材质的对象。
- 是否存在没有 UV 的对象。
- 每个材质节点引用的贴图路径。

当前验证结果为：

```text
MAT_Asphalt: 42
MAT_Concrete: 42
MAT_Curb: 42
MAT_White_Marking: 42
missing_material_objects: 0
objects_without_uv: 0
```

## 6. FBX 导出流程

FBX 导出入口为 `scripts/02_export_fbx_blender.py`。

流程如下：

1. 清空 Blender 场景。
2. 导入 `output/obj/road_test.obj`。
3. 动态加载 `03_apply_materials_blender.py`。
4. 执行 `ensure_dirs()` 和 `apply_materials_to_scene()`。
5. 导出 `output/fbx/road_test.fbx`。

该脚本复用同一套 CityEngine 式材质逻辑，保证 GLB、Blend、FBX 的材质分配规则一致。

## 7. 自动化工作流

Windows：

```bat
run_workflow.bat
```

Linux/macOS 或 Git Bash：

```bash
./run_workflow.sh
```

完整流程：

```text
01_generate_cim3_road.py
        ↓
生成 OBJ / 基础 GLB / semantic JSON / QC JSON
        ↓
03_apply_materials_blender.py
        ↓
生成 road_test_realistic.glb / road_test_realistic.blend
        ↓
02_export_fbx_blender.py
        ↓
生成 road_test.fbx
```

## 8. 当前成果与验证状态

### 8.1 已验证输出

- `output/obj/road_test.obj`：可被 Blender 正常导入。
- `output/gltf/road_test_realistic.glb`：包含 4 类材质、9 张贴图、168 个 mesh。
- `output/road_test_realistic.blend`：材质和 UV 均已绑定。
- `output/fbx/road_test.fbx`：通过 Blender 后台导出。

### 8.2 当前 GLB 材质信息

```text
materials:
- MAT_Curb
- MAT_White_Marking
- MAT_Asphalt
- MAT_Concrete

images: 9
meshes: 168
```

### 8.3 当前材质绑定统计

```text
MAT_Curb: 42
MAT_White_Marking: 42
MAT_Asphalt: 42
MAT_Concrete: 42
missing_material_objects: 0
objects_without_uv: 0
```

## 9. 当前问题与风险

### 9.1 代码中文注释和历史文档存在乱码

部分早期脚本、README、docs 文件中的中文已经出现 mojibake。虽然大部分 Python 语法仍可运行，但会影响维护、协作和语义文档质量。建议后续逐步重写注释和文档，不要直接覆盖历史输出。

### 9.2 几何生成仍以融合面为主

当前同高程道路会被 union 成合并面，适合快速生成连续道路铺装，但不利于保留每条道路的独立拓扑、道路等级、车道级材质差异。后续若要支持精细交互和对象级选中，需要从“高程层合并对象”升级为“道路段/路口/车道级对象”。

### 9.3 标线逻辑仍是中心线 buffer

当前 `Lane_Marking` 是道路中心线的窄 buffer，不是真实交通标线系统。它没有区分：

- 车道分隔线
- 边缘线
- 停止线
- 斑马线
- 导向箭头
- 虚线/实线

后续需要引入 lane-level 生成和 decal/mesh overlay 机制。

### 9.4 地形贴合仍较粗

当前道路高程主要采样中心线起终点，并使用平均高程生成平面层。对于有明显坡度的道路或起伏地形，道路面不会沿线连续贴合 DEM。后续需要沿道路中心线采样多点，并对道路面进行分段或细分。

### 9.5 FBX 贴图外部依赖

FBX 导出当前复用 Blender 材质节点，但跨软件迁移时贴图路径和 EXR 支持可能存在差异。后续建议增加贴图复制、打包或统一转换为 PNG/JPG 的资产发布步骤。

## 10. 后续演进路线

### 阶段一：规则系统扩展

目标是让 `road_rules.json` 从单一 `default_road` 升级为多类型道路规则。

建议新增：

```json
{
  "motorway": {},
  "primary": {},
  "secondary": {},
  "residential": {},
  "service": {}
}
```

每类规则可独立配置：

- 车道默认数
- 车道宽度
- 人行道宽度
- 是否生成路缘石
- 是否生成标线
- 主材质、人行道材质、路缘石材质、标线材质

### 阶段二：材质库外置

当前 `MATERIAL_LIBRARY` 写在 `03_apply_materials_blender.py` 中。后续建议迁移到：

```text
data/rules/material_library.json
```

这样规则和材质都可以由配置驱动，不需要改 Python 代码。

### 阶段三：车道级几何

当前道路面是按整条道路宽度 buffer。后续应生成：

- 单车道面
- 左右边缘线
- 中央分隔线
- 路肩
- 匝道合流区
- 路口渠化岛

这将更接近 CityEngine 的 split/comp 规则建模方式。

### 阶段四：真实标线与贴花系统

建议新增 decal 生成层：

```text
Road_Surface
└── Marking_Decal
    ├── lane_line_solid
    ├── lane_line_dashed
    ├── stop_line
    ├── zebra_crossing
    └── direction_arrow
```

实现方式可以是：

- 独立薄面 mesh，略高于道路面。
- 使用透明 PNG 标线贴图。
- 按道路方向计算 UV 和重复间距。

### 阶段五：地形与道路耦合

建议将道路中心线按固定间距重采样，例如每 5m 或 10m 一个点，采样 DEM 后生成带纵坡的道路网格。对于大面积道路面，需要进一步三角化细分，避免整块平面悬空或穿地。

### 阶段六：交互式 3D 城市接入

面向前端或游戏引擎时，需要保留对象级语义：

- 对象 id
- 道路 id
- 道路等级
- 材质 id
- 高程层
- 面积和长度
- 来源 OSM id

当前 `semantic.json` 已提供基础能力，后续应将这些属性写入 GLB extras 或独立数据库索引，方便点击查询、过滤显示和动态替换材质。

## 11. 推荐开发规范

### 11.1 命名规范

几何对象命名应保持稳定：

```text
Road_Surface_{i}
Sidewalk_{i}
Curb_{i}
Lane_Marking_{i}
```

新增对象建议遵循：

```text
Lane_Surface_{road_id}_{lane_index}
Road_Shoulder_{road_id}_{side}
Marking_Decal_{road_id}_{type}_{index}
Intersection_Surface_{node_id}
```

### 11.2 材质 id 规范

材质 id 使用小写蛇形命名：

```text
asphalt
concrete
curb_concrete
white_marking
zebra_marking
red_bus_lane
green_bike_lane
```

### 11.3 验证规范

每次修改后建议至少运行：

```powershell
python -c "import ast, pathlib; ast.parse(pathlib.Path('scripts/01_generate_cim3_road.py').read_text(encoding='utf-8')); ast.parse(pathlib.Path('scripts/03_apply_materials_blender.py').read_text(encoding='utf-8'))"
& 'D:\ruanjian\Blender 5.1\blender.exe' --background --python scripts\03_apply_materials_blender.py
& 'D:\ruanjian\Blender 5.1\blender.exe' --background --python scripts\04_inspect_blend_materials.py
```

关键检查项：

- 是否所有 mesh 都有材质。
- 是否所有 mesh 都有 UV。
- GLB 是否包含预期材质。
- 主道路、人行道、路缘石、标线是否视觉上可区分。
- 输出文件是否被刷新。

## 12. 总结

当前代码已经形成一条可运行的 CIM 道路 POC 管线：

```text
OSM/DGM 数据
  -> 道路属性清洗
  -> 高程修正
  -> Shapely 平面生成
  -> Trimesh 三维网格
  -> OBJ/GLB/语义/QC 输出
  -> Blender 规则驱动材质贴合
  -> PBR GLB / Blend / FBX 输出
```

最新材质系统已经从“按对象名固定分配材质”升级为“规则字段驱动材质 id，材质库控制贴图与投影”的结构。它与 CityEngine 的核心思想一致：几何组件由规则生成，材质由规则语义驱动，并通过稳定投影方式贴合到生成模型上。后续重点应放在多道路等级规则、车道级几何、真实标线贴花、地形连续贴合和交互语义写入 GLB 等方向。
