# Scripts 技术索引

本目录保留当前城市级建模流程需要的脚本，以及少量结果检查/渲染工具。主入口在项目根目录：

```text
run_city_workflow.bat
run_city_workflow.sh
```

## 城市级主流程

```text
05_generate_cim_city.py
  -> 06_export_cim_city_fbx_blender.py
  -> 14_export_cim_city_fbx.py
```

### `05_generate_cim_city.py`

职责：读取 `data/Data` 下的工程数据，生成完整 CIM 城市 OBJ 与分模块 OBJ。
也可以通过环境变量 `CIM_ROAD_DATA_DIR` 指向同结构数据目录，例如裁剪后的
`data/Data_clip_1_10`。

主要输入：

```text
data/Data/road50kms/*.shp
data/Data/公交站*/公交站*.shp
data/Data/轨道线和站点转坐标2000/*.shp
data/Data/供水+污水/*.shp
data/Data/rq规划试验区校核/*.shp
```

主要输出：

```text
output/obj/cim_city.obj
output/obj/modules/*.obj
output/semantic/*.json
output/qc_report/*.json
```

### `12_clip_raw_data_sample.py`

职责：从 `data/Data` 裁剪出同目录结构的 1/10 快速测试数据，默认输出到
`data/Data_clip_1_10`。

```bash
python scripts/12_clip_raw_data_sample.py --overwrite
```

### `13_generate_cim_city_test_data.py`

职责：直接使用 `data/Data_clip_1_10` 运行城市级主流程，适合快速验证道路、
路口、管线和 QC 报告。

```bash
python scripts/13_generate_cim_city_test_data.py
```

### `06_export_cim_city_fbx_blender.py`

职责：将 `cim_city.obj` 和各模块 OBJ 导入 Blender，按对象名前缀分配材质，并导出 FBX。

主要输出：

```text
output/fbx/cim_city.fbx
output/fbx/modules/*.fbx
```

### `14_export_cim_city_fbx.py`

职责：用普通 Python 查找并启动 Blender，调用 `06_export_cim_city_fbx_blender.py`
导出带材质的总 FBX 和分模块 FBX。适合在 `cim-road` 环境或命令行中直接运行。

```bash
python scripts/14_export_cim_city_fbx.py
```

如果 Blender 不在 PATH 中，可以通过环境变量或参数指定：

```bash
python scripts/14_export_cim_city_fbx.py --blender "C:/Program Files/Blender Foundation/Blender 5.1/blender.exe"
```

### `01_generate_cim3_road.py`

职责：历史兼容入口。道路几何库位于 `src/road/generator.py`，`05_generate_cim_city.py` 会导入它复用道路规则、断面、路口和资产生成函数。

旧 road-only `road_test.*` 输出已经退役；运行该脚本会提示改用城市级主流程。

## 检查与渲染工具

详细说明见 [docs/cim_city_technical_documentation.md](../docs/cim_city_technical_documentation.md)。

当前主要脚本已经重构为薄入口，核心逻辑位于：

```text
src/road/generator.py
src/city/pipeline.py
src/blender/fbx_export.py
```

检查与渲染工具核心逻辑位于：

```text
src/blender/fbx_inspect.py
src/blender/road_quality_render.py
src/blender/road_fbx_preview.py
src/render/cross_section_svg.py
src/junction/stack_check.py
```

### `07_inspect_fbx_materials_blender.py`

反向导入 `output/fbx/cim_city.fbx`，统计 Mesh 数量与材质名称。

```bash
blender --background --python scripts/07_inspect_fbx_materials_blender.py
```

### `08_render_road_quality_views_blender.py`

从 `output/fbx/modules/cim_city_roads.fbx` 渲染道路质量检查视图。

```bash
blender --background --python scripts/08_render_road_quality_views_blender.py
```

### `09_render_cross_section_diagrams.py`

根据 `output/semantic/cim_city_roads_semantic.json` 生成道路横断面 SVG 示意图。

```bash
python scripts/09_render_cross_section_diagrams.py
```

### `10_render_road_fbx_preview_blender.py`

渲染道路 FBX 俯视预览图。

```bash
blender --background --python scripts/10_render_road_fbx_preview_blender.py
```

### `11_check_junction_stack.py`

检查路口面、道路组件、标线和道路资产之间的平面叠压与连通性。

```bash
python scripts/11_check_junction_stack.py
```
