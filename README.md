# CIM Road / City Modeling POC

本项目用于从 OpenStreetMap 与规则参数生成 CIM3/CIM4 级城市三维模型。当前实验区域已从安联球场切换为慕尼黑中央火车站周边 1 公里范围，以便覆盖地表道路、建筑、公交站、地铁站、地铁区间隧道和地下综合管线。

## 当前交付目标

生成一个可在 Blender、Unity、Unreal、CityEngine 或其他三维平台中查看的 CIM 城市模型：

- 地表道路系统：道路面、人行道、路缘石、车道标线。
- 地上城市实体：建筑体块、公交站点。
- 地下空间结构：地铁站体块、地铁区间隧道。
- 地下综合管线：给水、污水、电力、通信四类模拟管线。
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

1. 下载慕尼黑中央火车站周边 OSM 数据。
2. 生成 CIM 城市 OBJ。
3. 用 Blender 后台导出带材质的 FBX。

## 当前保留输出

```text
output/
  obj/
    cim_city.obj
  fbx/
    cim_city.fbx
```

- `output/obj/cim_city.obj`：几何交换模型，包含城市全部实体。
- `output/fbx/cim_city.fbx`：最终展示模型，包含按对象类型分配的材质。

旧的道路单体测试输出 `road_test.obj` / `road_test.fbx` 已清理，不再作为当前城市构建任务的必要成果。

## 详细技术文档

请阅读：

- [docs/cim_city_technical_documentation.md](docs/cim_city_technical_documentation.md)
