# Road Iteration Scripts

`scripts/` 只保留当前道路模型快速迭代需要的四个入口。核心逻辑位于 `src/`，
脚本仅负责组织命令行调用。

## Recommended Workflow

```bash
python scripts/02_generate_cim_roads.py
python scripts/03_export_cim_roads_fbx.py
```

如需重新裁剪快速测试数据，先运行：

```bash
python scripts/01_clip_raw_data_sample.py --overwrite
```

## Scripts

### `01_clip_raw_data_sample.py`

从 `data/Data` 裁剪快速测试数据，默认写入 `data/Data_clip_1_10`。

### `02_generate_cim_roads.py`

使用 `data/Data_clip_1_10` 生成道路 OBJ、道路语义、道路分类和独立路口调试模型。
默认跳过耗时 QC。需要时设置环境变量 `CIM_ROAD_RUN_QC=1`。

### `03_export_cim_roads_fbx.py`

查找并启动 Blender，导出道路 FBX 和路口调试 FBX。可显式指定 Blender：

```bash
python scripts/03_export_cim_roads_fbx.py --blender "C:/Program Files/Blender Foundation/Blender 5.1/blender.exe"
```

### `04_export_cim_roads_fbx_blender.py`

由 `03_export_cim_roads_fbx.py` 调用的 Blender 内部脚本，通常不直接运行。

## Main Outputs

```text
output/obj/modules/cim_city_roads.obj
output/obj/junctions/J0000.obj
output/fbx/modules/cim_city_roads.fbx
output/fbx/junctions/J0000.fbx
output/semantic/cim_city_junctions_debug_manifest.json
```

## Junction Issue Location

每个调试路口都有稳定编号，例如 `J0055`。在 Blender 中直接加载
`output/fbx/junctions/J0055.fbx`；反馈问题时提供该编号即可。文件内的对象名称也会
保留同样的编号后缀，例如 `Sidewalk_J0055`。
