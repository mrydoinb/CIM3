from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import sys


ROOT = Path(__file__).resolve().parents[1]
REPL = ROOT / "tmp" / "pptx_replacements"


def find_original_ppt() -> Path:
    downloads = Path.home() / "Downloads"
    candidates = [
        path
        for path in downloads.glob("*.pptx")
        if "CIM" in path.name
        and path.name.endswith("11.pptx")
        and "截图替换" not in path.stem
        and not path.name.startswith("~$")
    ]
    if not candidates:
        raise SystemExit("No original PPTX found in Downloads.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


MEDIA_MAPPING = {
    "ppt/media/image7.png": REPL / "subway_perspective.png",
    "ppt/media/image8.png": REPL / "road_obj_plan.png",
    "ppt/media/image9.png": REPL / "junction_j0038.png",
    "ppt/media/image10.png": REPL / "junction_j0006.png",
    "ppt/media/image11.png": REPL / "rule_semantics_16x9.png",
    "ppt/media/image12.png": REPL / "junction_j0006.png",
    "ppt/media/image13.png": REPL / "subway_perspective.png",
    "ppt/media/image14.png": REPL / "road_obj_plan.png",
    "ppt/media/image15.png": REPL / "rule_semantics_wide.png",
    "ppt/media/image16.png": REPL / "subway_perspective.png",
}


TEXT_REPLACEMENTS = {
    "复盘与下一步：从“能生成”走向“稳定交付”": "复盘与下一步：围绕 2026 绩效目标推进",
    "算法收敛": "自动化建模交付",
    "继续修正典型路口缝隙、重叠与错位，优先优化共享几何构造。": (
        "完善参数提取、模型生成与规则配置，完成 CIM3/CIM4 轨道交通、地下空间部件建模工具集成测试和课题交付。"
    ),
    "回归加固": "超融合数据库",
    "把典型路口编号固化为轻量回归集，覆盖不同道路等级与侧向构件。": (
        "基于 ClickHouse 完成不少于 6 类多模态时空数据接入、时空索引与组合检索，推进性能优化和软著成果。"
    ),
    "工程化推进": "时空基座大模型",
    "梳理参数表、输出命名、批量导出与检查入口，并控制 FBX 体量。": (
        "围绕 Qwen-VL 开展地理空间与时间约束微调，完成训练样本、评测验证和城市时空基座模型阶段成果。"
    ),
    "平台联调": "成果沉淀与支撑",
    "完善 Blender 展示场景，并验证 Unity / Unreal / CityEngine 衔接。": (
        "支撑重点项目、汇报材料和技术验证闭环，同步推进 1 篇 AI 相关 EI 论文、2 项专利及研发资料归档。"
    ),
}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    src = find_original_ppt()
    out = src.with_name(f"{src.stem}_截图替换_第12页更新.pptx")

    media_payloads = {name: path.read_bytes() for name, path in MEDIA_MAPPING.items()}
    with ZipFile(src, "r") as zin:
        slide12_xml = zin.read("ppt/slides/slide12.xml").decode("utf-8")
        for old, new in TEXT_REPLACEMENTS.items():
            if old not in slide12_xml:
                raise SystemExit(f"Missing expected text in slide12.xml: {old}")
            slide12_xml = slide12_xml.replace(old, new)

        existing = set(zin.namelist())
        missing = sorted(set(media_payloads) - existing)
        if missing:
            raise SystemExit(f"Missing media entries in source PPTX: {missing}")

        with ZipFile(out, "w", ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                if info.filename == "ppt/slides/slide12.xml":
                    zout.writestr(info, slide12_xml.encode("utf-8"))
                elif info.filename in media_payloads:
                    zout.writestr(info, media_payloads[info.filename])
                else:
                    zout.writestr(info, zin.read(info.filename))

    print(f"source={src}")
    print(f"output={out}")
    print(f"size={out.stat().st_size}")
    print("media replacements:")
    for name, path in MEDIA_MAPPING.items():
        print(f"  {name} <= {path.name}")
    print("slide12 updated")


if __name__ == "__main__":
    main()
