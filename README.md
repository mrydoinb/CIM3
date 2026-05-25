# CIM Road / City Modeling POC

本项目用于基于本地工程数据生成 CIM3/CIM4 级城市道路与地下管线三维模型。当前主流程以 `data/Data` 下的工程数据为输入，生成可在 Blender、Unity、Unreal、CityEngine 或其他三维平台中查看的 OBJ/FBX 模型。

## 当前交付目标

- 地表道路系统：路面、人行道、路缘石、车道标线、路口面、斑马线、停止线、绿化带、路灯、树木等。
- 地上/交通实体：公交站点、轨道站点体块。
- 地下空间结构：轨道区间隧道。
- 地下综合管线：给水、污水、燃气等工程管线。
- 最终输出：OBJ 与带材质的 FBX。

## 一键运行

Windows:

```bat
run_city_workflow.bat
```

Git Bash / Linux / macOS:

```bash
./run_city_workflow.sh
```

城市流程会依次执行：

1. 生成 CIM 城市 OBJ。
2. 由 Blender 后台导出带材质的 FBX。

## 当前保留输出

```text
output/
  obj/
    cim_city.obj
  fbx/
    cim_city.fbx
```

- `output/obj/cim_city.obj`：几何交换模型，包含当前城市实体。
- `output/fbx/cim_city.fbx`：最终展示模型，包含按对象类型分配的材质。

旧的 OSM 下载入口和 `road_test.obj` / `road_test.fbx` 专用 Blender 脚本已清理，不再作为当前城市构建任务的一部分。

## 详细技术文档

请阅读：

- [docs/cim_city_technical_documentation.md](docs/cim_city_technical_documentation.md)
