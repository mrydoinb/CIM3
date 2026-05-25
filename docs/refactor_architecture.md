# CIM Road POC 重构结构说明

本文档记录当前代码重构的目标结构、已完成内容和下一步拆分方向。

## 1. 目标

重构目标是把原先集中在 `scripts/` 中的大脚本逐步拆成可复用的 Python 包：

- `scripts/` 只保留命令行兼容入口。
- `src/` 保存核心业务逻辑。
- 道路、路口、城市对象、Blender 导出、质检和渲染分别归属不同模块。
- 主生成逻辑和检查逻辑复用同一套函数，减少复制和漂移。

## 2. 当前结构

当前已经完成第一轮包化迁移：

```text
src/
  cli/
    generate_city.py
    export_fbx.py
    inspect_fbx.py
    render_road_qc.py
    render_cross_sections.py
    check_junction_stack.py
  city/
    pipeline.py
  road/
    generator.py
  blender/
    fbx_export.py
    fbx_inspect.py
    road_quality_render.py
    road_fbx_preview.py
  render/
    cross_section_svg.py
  junction/
    stack_check.py
  config/
  geometry/
  data_io/
```

`config/`、`geometry/`、`data_io/` 当前先作为包边界保留，后续会从 `road.generator` 和 `city.pipeline` 中继续拆入。

## 3. 入口映射

| 兼容脚本入口 | 当前实现模块 |
|---|---|
| `scripts/01_generate_cim3_road.py` | `src/road/generator.py` |
| `scripts/05_generate_cim_city.py` | `src/city/pipeline.py` |
| `scripts/06_export_cim_city_fbx_blender.py` | `src/blender/fbx_export.py` |
| `scripts/07_inspect_fbx_materials_blender.py` | `src/blender/fbx_inspect.py` |
| `scripts/08_render_road_quality_views_blender.py` | `src/blender/road_quality_render.py` |
| `scripts/09_render_cross_section_diagrams.py` | `src/render/cross_section_svg.py` |
| `scripts/10_render_road_fbx_preview_blender.py` | `src/blender/road_fbx_preview.py` |
| `scripts/11_check_junction_stack.py` | `src/junction/stack_check.py` |

## 4. 运行兼容性

原有命令仍保持可用：

```bash
python scripts/05_generate_cim_city.py
python scripts/09_render_cross_section_diagrams.py
python scripts/11_check_junction_stack.py
blender --background --python scripts/06_export_cim_city_fbx_blender.py
blender --background --python scripts/07_inspect_fbx_materials_blender.py
```

也可以从包模块直接调用：

```bash
python -m cli.generate_city
python -m cli.render_cross_sections
python -m cli.check_junction_stack
```

直接 `python -m` 运行时需要确保 `src/` 在 `PYTHONPATH` 中，或先以 editable 方式安装项目。

## 5. 后续拆分计划

下一阶段建议从 `src/city/pipeline.py` 和 `src/road/generator.py` 中继续拆：

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

其中路口部分优先级最高，因为当前路口生成、裁剪、标线、语义和 QC 都围绕同一组几何函数展开。
