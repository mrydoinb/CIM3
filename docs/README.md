# CIM Road Documentation

## 文档边界

本文档是当前 `docs/` 目录的统一入口，用来记录已经落地的道路生成流程、技术路线、语义文件和结果输出。

后续开始地铁/轨道交通建模时，请在本文档的“地铁后续区”或新的地铁专用文档中继续补充，不要把道路生成说明和地铁生成说明混在一起。

当前保留的道路资料：

- `docs/README.md`：道路生成、CIM3/CIM4、语义关联、输出路径的统一说明。
- `docs/道路参数.xlsx`：道路断面和参数类原始资料。

已清理的旧文档：

- `docs/cim3_cim4_generation_workflow.md`：内容已合并到本文档。
- `docs/cim_city_technical_documentation.md`：旧流程和旧脚本说明，且存在乱码。
- `docs/city_infrastructure_dataset_technical_route.md`：旧综合技术路线，和当前道路成果混杂，且存在乱码。
- `docs/refactor_architecture.md`：旧架构说明，输出路径和当前 CIM3/CIM4 分级输出不一致。

## 当前道路成果

当前道路生成已经拆分为两个输出等级：

- `cim3`：轻量道路模型，适合快速浏览、批量检查和轻量交付。
- `cim4`：精细道路模型，适合完整构件级表达和 Blender 视觉检查。

两个等级共享同一套道路拓扑、路口检测、路口面生成、道路裁剪、侧向构件连接和路口语义逻辑。这样后续修复一个路口问题时，CIM3 和 CIM4 会同时受益，不需要维护两套分叉的几何逻辑。

## 生成命令

生成 full 数据源下的 CIM3 和 CIM4：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_roads.py --source full --level both
```

只生成 CIM3：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_roads.py --source full --level cim3
```

只生成 CIM4：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_roads.py --source full --level cim4
```

导出 CIM3 和 CIM4 的 FBX：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\03_export_cim_roads_fbx.py --level both --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

只导出某一个等级：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\03_export_cim_roads_fbx.py --level cim3 --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\03_export_cim_roads_fbx.py --level cim4 --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

## 核心代码入口

道路生成主入口：

```text
scripts/02_generate_cim_roads.py
```

FBX 导出入口：

```text
scripts/03_export_cim_roads_fbx.py
scripts/04_export_cim_roads_fbx_blender.py
```

道路生成核心逻辑：

```text
src/city/pipeline.py
```

道路基础构件、颜色、断面和几何 helper：

```text
src/road/generator.py
```

Blender OBJ/FBX 导出逻辑：

```text
src/blender/fbx_export.py
```

## 技术路线

道路生成采用同一套 pipeline、多 profile 输出的路线。

主要步骤：

1. 读取道路中心线 SHP。
2. 清洗无效几何并展开 MultiLineString。
3. 生成稳定 `road_id`、`road_name`、`road_class`、宽度、断面和长度。
4. 识别路口节点和道路端点聚类。
5. 生成路口面、道路面和道路侧向构件。
6. 根据 profile 控制是否生成路缘石、纵向车道标线、路灯、树等精细构件。
7. 输出 OBJ、FBX 和语义 JSON。
8. 输出单路口 debug 模型，便于在 Blender 中检查问题路口。

当前 profile 定义在：

```text
src/city/pipeline.py
```

相关对象：

```text
RoadGenerationProfile
CIM3_PROFILE
CIM4_PROFILE
```

## CIM3 和 CIM4 区别

CIM3 当前配置：

```text
name = cim3
mesh_granularity = component
generate_assets = False
generate_lane_markings = False
generate_junction_markings = True
generate_side_component_connectors = True
generate_curbs = False
semantic_level = lightweight_component_layers_with_full_junction_handling
```

CIM4 当前配置：

```text
name = cim4
mesh_granularity = component
generate_assets = True
generate_lane_markings = True
generate_junction_markings = True
generate_side_component_connectors = True
generate_curbs = True
semantic_level = fine_component_with_assets_markings_and_full_junction_semantics
```

CIM3 保留：

- 主路面、服务路面、支路面和路口面。
- `Sidewalk`、`Green_Belt`、`Facility_Belt`、`Side_Divider`、`Median`、`Non_Motor_Lane`、`Parking_Lane` 等道路构件层。
- 路口面、斑马线、停止线。
- 路口 arms、movements、design option、quality flags 等语义信息。

CIM3 不生成：

- 路缘石。
- 纵向车道标线。
- 路灯、树等道路资产。

CIM4 在 CIM3 的基础上增加：

- 路缘石。
- 纵向车道标线。
- 路灯、树等道路资产。
- 更完整的构件级视觉细节。

重要约定：

- CIM3 和 CIM4 的路口处理保持一致。
- CIM3 不把 `Sidewalk`、`Green_Belt`、`Facility_Belt` 合并成 `Roadside_Reserve`。
- CIM3 和 CIM4 都保留独立道路构件层，方便语义查询和模型检查。

## 路口处理

当前道路路口处理由 `src/city/pipeline.py` 统一控制。

主要逻辑包括：

- 路口节点聚类。
- 简化圆角路口面生成。
- 道路接近路口处的裁剪和退让。
- D6 one-sided sidewalk protection。
- 外侧人行道圆角连接。
- 穿越道路的侧向构件连接。
- 斑马线和停止线生成。
- 路口语义 arms、movements、design option 和 quality flags 输出。

已经处理过的典型问题：

- `支路350-Sidewalk` 曾在 full 影像中深入路口中间。
- 原因是 D6 one-sided sidewalk protection 在高速/匝道合流节点附近把支路人行道保护段延伸到了路口核心区。
- 当前修复策略是在存在 expressway arm 的路口中，只允许 expressway arm 参与对应的 one-sided sidewalk protection，避免支路人行道深入高速合流中心。
- 这个修复作用在共享路口逻辑中，所以 CIM3 和 CIM4 都会同步使用。

## 输出目录

CIM3 OBJ：

```text
output/obj/modules/cim3/city_roads.obj
```

CIM4 OBJ：

```text
output/obj/modules/cim4/city_roads.obj
```

CIM3 FBX：

```text
output/fbx/modules/cim3/city_roads.fbx
```

CIM4 FBX：

```text
output/fbx/modules/cim4/city_roads.fbx
```

CIM3 语义目录：

```text
output/semantic/cim3/
```

CIM4 语义目录：

```text
output/semantic/cim4/
```

单路口 debug OBJ/FBX 仍按路口独立输出，供 Blender 单独加载检查：

```text
output/obj/junctions/
output/fbx/junctions/
```

## 语义文件

每个等级都会输出独立语义文件。

道路语义：

```text
output/semantic/cim3/city_roads_semantic.json
output/semantic/cim4/city_roads_semantic.json
```

路口语义：

```text
output/semantic/cim3/city_junctions_semantic.json
output/semantic/cim4/city_junctions_semantic.json
```

道路分类：

```text
output/semantic/cim3/city_roads_classification.json
output/semantic/cim4/city_roads_classification.json
```

模型对象属性：

```text
output/semantic/cim3/city_roads_mesh_attributes.json
output/semantic/cim4/city_roads_mesh_attributes.json
```

源 SHP 属性：

```text
output/semantic/cim3/city_roads_source_attributes.json
output/semantic/cim4/city_roads_source_attributes.json
```

## 语义和模型的关联方式

模型对象和语义文件通过名称与道路记录关联。

主要关联链路：

1. Blender/OBJ 中的对象名。
2. `city_roads_mesh_attributes.json` 中的 `object_name`。
3. 对应的 `road_name`、`source_road_id`、`road_class`、`component`。
4. `city_roads_source_attributes.json` 中的 `records_by_name`。
5. 原始 SHP 中同名 `name` 字段的属性记录。

`city_roads_source_attributes.json` 的关联主键是：

```text
association_key = name
```

该文件保留原始 SHP 属性字段：

```text
Id
roadclass
width
section
Length
备注
name
```

使用方式：

- 如果要从模型对象追溯到源数据，先在 `city_roads_mesh_attributes.json` 找对象名。
- 拿到 `road_name` 后，到 `city_roads_source_attributes.json.records_by_name` 查询原始 SHP 属性。
- 如果同名道路有多段，`records_by_name` 会保留数组，不会覆盖。

## 最近 full 输出统计

最近一次 full 数据源生成结果：

```text
CIM3 road semantic records: 424
CIM3 junction semantic records: 958
CIM3 mesh attribute layers: 2246
CIM3 source attribute records: 424
```

```text
CIM4 road semantic records: 424
CIM4 junction semantic records: 958
CIM4 mesh attribute layers: 4352
CIM4 source attribute records: 424
```

## Blender 检查建议

道路整体检查：

- 加载 `output/fbx/modules/cim3/city_roads.fbx` 检查轻量结果。
- 加载 `output/fbx/modules/cim4/city_roads.fbx` 检查完整精细结果。

路口问题检查：

- 优先加载 `output/fbx/junctions/Jxxxx.fbx`。
- 对照 `output/semantic/cim3/city_junctions_semantic.json` 或 `output/semantic/cim4/city_junctions_semantic.json` 查看该路口连接道路和路口类型。

如果用户明确指出某个路口，例如 `J0006`，优先检查：

```text
output/semantic/cim3/city_junctions_semantic.json
output/semantic/cim4/city_junctions_semantic.json
output/obj/junctions/J0006.obj
output/fbx/junctions/J0006.fbx
```

## 地铁后续区

地铁/轨道交通建模后续建议单独记录以下内容：

- 地铁数据源。
- 站点、区间、出入口、风亭、附属设施的数据结构。
- 地铁 CIM3/CIM4 或 LOD 分级规则。
- 地铁 OBJ/FBX 输出目录。
- 地铁语义 JSON 和模型对象关联方式。
- 地铁与道路、公交、地下管线的空间关系。

地铁开始后，不建议继续修改道路章节来描述地铁逻辑。道路和地铁可以共享总技术路线，但应分开记录生成入口、输出路径和语义文件。
