# CIM3 道路自动化建模 POC

该项目提供基于 Python/Trimesh 的道路几何网格自动生成以及基于 Blender 的程序化/PBR 材质增强流程。

## 运行步骤

### 1. 基础模型网格生成
基于 Python/GeoPandas，读取道路网并将拓扑输出为附带高程的基础 3D OBJ / GLTF 模型。
```bash
python scripts/01_generate_cim3_road.py
```
> **输出**：`output/gltf/road_test.glb` (测试级纯色材质)、`output/obj/road_test.obj` 以及相关的语义和质检 Json 报告。

### 2. PBR / 程序化材质增强
借助 Blender 节点系统对 `road_test.obj` 模型进行自动展 UV 以及材质增强渲染工作（在后台服务模式运行）。
```bash
blender --background --python scripts/03_apply_materials_blender.py
```
> **工作逻辑**：若检测到 `assets/textures/<材质类型>/` 目录下含有 `basecolor.jpg`、`normal.jpg` 等 PBR 贴图，则采用贴图流；若不存在（或不存在完整贴图），脚本不会报错而是自动生成程序化噪波法线材质用于表现颗粒和磨损。
> **输出**：`output/gltf/road_test_realistic.glb` (支持真实光照折射的高质量模型)

### 说明
此分离架构设计中，Blender 被单纯作为 **无 UI 的后台渲染转换服务** 处理，核心的语义生成与数据流均封闭在 Python 工程体系中，以此规避和 Blender 生态之间的耦合问题。