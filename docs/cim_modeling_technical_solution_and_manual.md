# CIM 自动建模技术方案与操作手册

## 1. 项目概述

本项目面向城市道路及相关交通基础设施的 CIM 自动建模，基于现有工程 GIS 数据，通过规则化几何生成、语义关联和 Blender 后台转换，输出可在 Blender、Unity、Unreal、CityEngine 等三维平台中查看和二次使用的 OBJ/FBX 模型及配套 JSON 语义成果。

当前建模重点为道路系统，已形成 CIM3 与 CIM4 两级输出能力：

- CIM3：轻量化道路模型，适合快速浏览、批量检查、轻量交付和方案阶段汇报。
- CIM4：精细化构件级道路模型，适合完整表达道路构件、标线、路缘石、路灯、树木等细节，并用于 Blender 可视化检查。

同时，项目保留轨道区间隧道、公交站点、轨道站点、地下管线等模块化扩展能力。道路、轨道和管线采用相对独立的生成入口与输出目录，便于按专业分批生成、检查和交付。

## 2. 建设目标

本方案的建设目标包括：

1. 实现从道路中心线 SHP/GeoJSON 到三维 CIM 道路模型的自动生成。
2. 建立道路等级、断面组成、路口拓扑、标线、附属设施与模型对象之间的规则化映射。
3. 输出 OBJ、FBX 与语义 JSON，使几何模型和源数据属性可追溯。
4. 支持 CIM3 与 CIM4 两类精度等级，满足快速检查和精细展示两种应用场景。
5. 支持单路口 debug 模型输出，便于针对路口缝隙、重叠、错位等问题在 Blender 中独立检查。
6. 建立可重复执行的操作流程，使后续换数据、改规则、重新生成和成果检查具备稳定路径。

## 3. 技术方案

### 3.1 总体技术路线

整体路线为：

```text
工程 GIS 数据
  -> 数据源选择与路径配置
  -> 坐标系统一与本地化
  -> 道路属性规范化
  -> 道路断面规则匹配
  -> 路口拓扑识别与聚类
  -> 道路、路口、标线、道路侧向构件建模
  -> OBJ 模块输出
  -> 语义 JSON 输出
  -> Blender 后台导出 FBX
  -> Blender 人工视觉检查与问题回溯
```

道路生成主入口为：

```text
scripts/02_generate_cim_roads.py
```

FBX 导出主入口为：

```text
scripts/03_export_cim_roads_fbx.py
scripts/04_export_cim_roads_fbx_blender.py
```

核心建模逻辑位于：

```text
src/city/pipeline.py
src/road/generator.py
src/road/rules.py
src/blender/fbx_export.py
```

### 3.2 数据输入

道路建模主要读取道路中心线数据，支持预设数据源和自定义道路文件。

当前脚本内置的数据源预设包括：

- `expressway2`：快速样例数据，适合小范围调试和路口问题复现。
- `full`：完整道路中心线数据，适合正式生成。
- `clip-1-10`：已有 1/10 裁剪样例数据，适合中等规模测试。

道路数据通过 `--source` 指定，也可通过 `--roads-file` 和 `--data-dir` 指向任意已有道路图层。

主要输入字段包括道路名称、道路等级、宽度、断面编码、长度及原始备注字段。字段会在生成过程中写入 `city_roads_source_attributes.json`，用于模型对象和源 SHP 属性回溯。

### 3.3 坐标与空间处理

当前工程数据采用 CGCS2000 三度带高斯克吕格投影，代码中目标坐标系为：

```text
EPSG:4547
```

生成流程会先读取源图层，再计算项目局部原点，将大地坐标转换为局部建模坐标。这样可以减少三维软件中因大坐标导致的精度问题，也便于 OBJ/FBX 在 Blender 中稳定显示。

### 3.4 道路断面建模

道路断面由规则表和代码规则共同控制。断面构件包括但不限于：

- 机动车道
- 非机动车道
- 停车带
- 中央分隔带
- 侧分带
- 人行道 `Sidewalk`
- 设施带 `Facility_Belt`
- 绿化带 `Green_Belt`
- 路缘石
- 护栏、路灯、树木等道路资产

道路中心线会按断面规则向两侧展开，生成不同构件的带状面。CIM3 与 CIM4 共享同一套路口和断面处理逻辑，只是在精细构件是否生成上有所区别。

### 3.5 路口识别与处理

路口处理是当前道路建模的重点。系统会综合以下信号识别路口：

- 道路端点捕捉
- 道路中心线真实相交
- 不同等级道路的走廊交叉
- 道路面之间的有效重叠区域
- 路口节点聚类

识别到路口后，系统会在道路记录中写入沿中心线的路口里程信息，并据此完成道路裁剪、路口面生成、侧向构件连接、斑马线、停止线和转向箭头等构件布置。

针对用户常见的视觉检查问题，例如路口缝隙、道路面重叠、人行道深入路口核心区、绿化带错位等，当前方案优先通过共享的平面几何构造进行修复，包括 union、intersection、difference、buffer、clip 等操作，而不是依赖大量局部补丁。

### 3.6 CIM3 与 CIM4 分级方案

CIM3 面向轻量化交付，主要保留：

- 主路面、辅路面、支路面和路口面
- 人行道、绿化带、设施带、侧分带、中央分隔带等道路构件层
- 路口斑马线、停止线
- 路口 arms、movements、design option、quality flags 等语义信息

CIM3 默认不生成：

- 路缘石
- 纵向车道标线
- 路灯、树木等道路资产

CIM4 在 CIM3 基础上增加：

- 路缘石
- 纵向车道标线
- 路灯、树木等道路资产
- 更完整的构件级视觉细节
- 更适合 Blender 最终视觉核验的精细模型

两级模型共享道路拓扑、路口识别和路口侧向构件连接逻辑，确保在修复一个路口或一类断面问题时，CIM3 与 CIM4 可同步受益。

### 3.7 语义成果设计

每次生成会输出几何模型和语义 JSON。语义文件用于说明模型对象对应的道路、源数据属性、断面构件、路口关系和分类统计。

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

模型对象属性：

```text
output/semantic/cim3/city_roads_mesh_attributes.json
output/semantic/cim4/city_roads_mesh_attributes.json
```

源数据属性：

```text
output/semantic/cim3/city_roads_source_attributes.json
output/semantic/cim4/city_roads_source_attributes.json
```

道路分类：

```text
output/semantic/cim3/city_roads_classification.json
output/semantic/cim4/city_roads_classification.json
```

语义关联方式为：

```text
Blender/OBJ 对象名称
  -> city_roads_mesh_attributes.json.object_name
  -> road_name / road_class / component / source_road_id
  -> city_roads_source_attributes.json.records_by_name
  -> 原始 SHP 属性记录
```

### 3.8 成果输出

道路 OBJ 输出：

```text
output/obj/modules/cim3/city_roads.obj
output/obj/modules/cim4/city_roads.obj
```

道路 FBX 输出：

```text
output/fbx/modules/cim3/city_roads.fbx
output/fbx/modules/cim4/city_roads.fbx
```

单路口 debug 输出：

```text
output/obj/junctions/Jxxxx.obj
output/fbx/junctions/Jxxxx.fbx
output/semantic/cim_city_junctions_debug_manifest.json
```

轨道区间隧道模块输出：

```text
output/obj/modules/cim4/subway_tunnels.obj
output/fbx/modules/cim4/subway_tunnels.fbx
output/semantic/cim4/subway_tunnels_semantic.json
output/semantic/cim4/subway_tunnels_source_attributes.json
output/semantic/cim4/subway_tunnels_mesh_attributes.json
```

## 4. 操作手册

### 4.1 运行环境

推荐环境：

- Windows 操作系统
- Python 环境：`D:\ProgramData\miniconda3\envs\cim-road\python.exe`
- Blender：`C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`
- 项目目录：`C:\Users\22838\Desktop\chk\CIMAgent\cim_road_poc`

进入项目目录：

```powershell
cd C:\Users\22838\Desktop\chk\CIMAgent\cim_road_poc
```

### 4.2 查看可用道路数据源

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_roads.py --list-sources
```

输出中会列出每个数据源对应的 `data_dir` 和 `roads_file`。

### 4.3 生成道路模型

生成快速样例 CIM4：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_roads.py --source expressway2 --level cim4
```

生成完整数据 CIM3：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_roads.py --source full --level cim3
```

生成完整数据 CIM4：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_roads.py --source full --level cim4
```

同时生成完整数据 CIM3 与 CIM4：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_roads.py --source full --level both
```

使用自定义道路文件：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_roads.py --roads-file "path\to\roads.shp" --data-dir "path\to\data" --level cim4
```

### 4.4 导出道路 FBX

生成 OBJ 后，执行 FBX 导出：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\03_export_cim_roads_fbx.py --level cim4 --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

同时导出 CIM3 与 CIM4：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\03_export_cim_roads_fbx.py --level both --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

导出完成后检查：

```text
output/fbx/modules/cim3/city_roads.fbx
output/fbx/modules/cim4/city_roads.fbx
```

### 4.5 生成单路口 debug 模型

默认情况下，单路口 debug 模型不启用。需要检查具体路口时，先设置环境变量：

```powershell
$env:CIM_ROAD_EXPORT_JUNCTION_DEBUG = "1"
```

然后重新生成道路：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_roads.py --source full --level cim4
```

再导出 FBX：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\03_export_cim_roads_fbx.py --level cim4 --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

检查目标路口，例如 `J0006`：

```text
output/obj/junctions/J0006.obj
output/fbx/junctions/J0006.fbx
output/semantic/cim_city_junctions_debug_manifest.json
```

### 4.6 启用质量检查报告

质量检查默认关闭，避免日常迭代变慢。需要输出 QC 报告时设置：

```powershell
$env:CIM_ROAD_RUN_QC = "1"
```

然后重新执行道路生成。报告输出位置为：

```text
output/qc_report/
```

常见报告包括：

- 道路模型评分
- 路口评分
- 标线对齐检查
- 地下管线检查

### 4.7 生成轨道区间隧道模型

轨道区间隧道与道路分开生成。生成 CIM4 轨道区间隧道：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_subway_tunnels.py --source full --level cim4
```

按线路过滤生成：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_subway_tunnels.py --source full --level cim4 --line "线路名称"
```

导出轨道区间隧道 FBX：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\03_export_cim_subway_tunnels_fbx.py --level cim4 --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

检查隧道空间分离：

```powershell
D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\check_cim_subway_tunnels.py --source full
```

### 4.8 Blender 检查方法

道路整体检查：

1. 打开 Blender。
2. 导入 `output/fbx/modules/cim4/city_roads.fbx`。
3. 检查道路面、路口面、人行道、绿化带、设施带、路缘石、标线、路灯、树木是否完整。
4. 对于轻量成果，导入 `output/fbx/modules/cim3/city_roads.fbx`。

路口问题检查：

1. 如果用户指出具体路口编号，如 `J0006`，优先导入 `output/fbx/junctions/J0006.fbx`。
2. 同时打开 `output/semantic/cim4/city_junctions_semantic.json`，查看该路口连接道路、路口类型和质量标记。
3. 若存在缝隙或重叠，回查道路断面、路口裁剪距离、侧向构件连接和路口 debug manifest。

### 4.9 常见问题处理

模型没有生成：

- 检查 `--source` 指定的数据源是否存在。
- 检查 `--roads-file` 指向的 SHP/GeoJSON 是否存在。
- 检查 Python 环境是否为 `cim-road`。

FBX 没有导出：

- 检查 Blender 路径是否正确。
- 检查 OBJ 是否已经生成。
- 检查 `--level` 是否和已生成的 OBJ 等级一致。

路口缺失或错位：

- 优先查看 `city_junctions_semantic.json`。
- 若已启用 debug，加载对应 `output/fbx/junctions/Jxxxx.fbx`。
- 检查道路中心线是否实际相交或端点是否距离过远。
- 检查道路等级、宽度、断面字段是否异常。

人行道、绿化带、设施带进入路口核心：

- 检查路口 arms 与道路等级。
- 检查侧向构件连接逻辑。
- 对高速或匝道合流路口，重点检查 one-sided sidewalk protection 是否只作用于合理的道路 arm。

模型太大或导入慢：

- 优先使用 `--level cim3` 生成轻量模型。
- 只针对问题区域使用样例数据源或裁剪数据源。
- 不需要时关闭 `CIM_ROAD_RUN_QC` 和 `CIM_ROAD_EXPORT_JUNCTION_DEBUG`。

## 5. 交付成果清单

一次完整道路 CIM 建模交付建议包含：

1. CIM3 OBJ：`output/obj/modules/cim3/city_roads.obj`
2. CIM3 FBX：`output/fbx/modules/cim3/city_roads.fbx`
3. CIM4 OBJ：`output/obj/modules/cim4/city_roads.obj`
4. CIM4 FBX：`output/fbx/modules/cim4/city_roads.fbx`
5. 道路语义 JSON：`output/semantic/cim3/` 与 `output/semantic/cim4/`
6. 路口语义 JSON：`city_junctions_semantic.json`
7. 源属性关联 JSON：`city_roads_source_attributes.json`
8. 模型对象属性 JSON：`city_roads_mesh_attributes.json`
9. 必要时提供单路口 OBJ/FBX debug 模型
10. 必要时提供 QC 报告

## 6. 后续优化建议

后续可继续完善以下方向：

1. 建立正式的道路断面配置表版本管理机制，使断面变更可追踪。
2. 将路口类型、路口 arms、车道转向关系和标线规则进一步参数化。
3. 增加 Blender 自动截图检查流程，用于批量生成路口问题对照图。
4. 建立道路、轨道、管线之间的空间避让与冲突检查报告。
5. 将当前 Markdown 技术方案转成 Word/PDF 模板，作为正式交付附件。
