"""评分卡：对生成解读做 5 维度结构化评分。

用法:
    python3 evaluate.py output/synthesis_test_huajing_v8.md -q "事业和感情"
    python3 evaluate.py output/synthesis_test_huajing_v8.md --compare output/real_case_01.md
"""

import argparse, json, sys
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent

DIMENSIONS = {
    "accuracy": {
        "label": "准确性",
        "weight": 1.0,
        "prompt": "星盘事实有没有说错？星座/宫位/相位是否正确对应？",
        "anchors": {
            1: "多处硬伤（星座/宫位/相位写错）",
            3: "基本正确，偶有小瑕疵",
            5: "全部准确，宫位链/飞星/相位描述无误",
        },
    },
    "relevance": {
        "label": "相关性",
        "weight": 1.0,
        "prompt": "有没有回应客户的实际问题？还是跑题了？",
        "anchors": {
            1: "客户问A，解读主要在说B",
            3: "部分回应了客户问题，但有偏离",
            5: "每个板块都紧扣客户问题，回答直接有力",
        },
    },
    "concreteness": {
        "label": "落地性",
        "weight": 1.0,
        "prompt": "解法是否具体可行动？还是停留在抽象建议？",
        "anchors": {
            1: "解法全是抽象概念（'你需要平衡''学会爱自己'）",
            3: "有具体场景，但不够完整或不够可操作",
            5: "解法场景化、有具体行为引导，读者知道明天能做什么",
        },
    },
    "karma": {
        "label": "业力完整",
        "weight": 0.8,
        "prompt": "命主星/Sect Light 是否追溯了家庭/家族来源？",
        "anchors": {
            1: "完全没提家庭/业力来源",
            3: "提了但不深入，缺少4宫/10宫/土星/月亮的关联",
            5: "四层完整，业力追溯有具体人物（父/母）和具体模式",
        },
    },
    "language": {
        "label": "语言质感",
        "weight": 0.7,
        "prompt": "像不像人在说话？有没有抽象术语或模板感？",
        "anchors": {
            1: "堆砌术语，像教科书或AI模板",
            3: "基本流畅，但有抽象词或模板痕迹",
            5: "像真人在说话，有温度、有比喻、有节奏",
        },
    },
}


def score_output(text, question=""):
    """交互式评分。返回 (scores_dict, notes)."""
    scores = {}
    notes = {}

    print("\n" + "=" * 60)
    print("评分卡 — 5 维度评估")
    print("=" * 60)
    if question:
        print(f"客户问题: {question}")
    print(f"解读长度: {len(text)} 字\n")

    for key, dim in DIMENSIONS.items():
        print(f"\n--- {dim['label']} ({key}) ---")
        print(f"标准: {dim['prompt']}")
        print(f"  1 = {dim['anchors'][1]}")
        print(f"  3 = {dim['anchors'][3]}")
        print(f"  5 = {dim['anchors'][5]}")

        while True:
            try:
                s = input(f"  [{dim['label']}] 评分 (1-5, 支持.5): ").strip()
                if s == "":
                    s = "3"
                val = float(s)
                if 1 <= val <= 5:
                    scores[key] = val
                    break
            except ValueError:
                pass

        note = input(f"  [{dim['label']}] 备注 (可选): ").strip()
        if note:
            notes[key] = note

    # Summary
    weighted = sum(scores[k] * DIMENSIONS[k]["weight"] for k in scores)
    max_weighted = sum(DIMENSIONS[k]["weight"] for k in scores)
    overall = round(weighted / max_weighted * 20)  # scale to 0-100

    return scores, notes, overall


def print_report(scores, notes, overall, version=""):
    print("\n" + "=" * 60)
    print(f"评估报告 {version}")
    print("=" * 60)

    for key, dim in DIMENSIONS.items():
        s = scores.get(key, "-")
        bar = "█" * s if isinstance(s, int) else ""
        note_str = f" — {notes[key]}" if key in notes else ""
        print(f"  {dim['label']:6s}  {s}/5  {bar} {note_str}")

    print(f"\n  综合得分: {overall}/100")
    print()


def save_record(version, question, scores, notes, overall, path=None):
    """保存评分记录到 evaluation_log.jsonl."""
    if path is None:
        path = BASE / "output" / "evaluation_log.jsonl"

    record = {
        "version": version,
        "timestamp": datetime.now().isoformat(),
        "question": question,
        "scores": scores,
        "notes": notes,
        "overall": overall,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"评分已保存到 {path}")


def load_history(path=None):
    if path is None:
        path = BASE / "output" / "evaluation_log.jsonl"
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def trend_report(records):
    """打印各版本趋势对比."""
    if not records:
        print("暂无历史评分记录")
        return

    print("\n" + "=" * 60)
    print("趋势对比")
    print("=" * 60)

    # Header
    versions = [r["version"] for r in records]
    print(f"  {'维度':8s}", end="")
    for v in versions:
        print(f"  {v:12s}", end="")
    print()

    for key, dim in DIMENSIONS.items():
        print(f"  {dim['label']:8s}", end="")
        for r in records:
            s = r["scores"].get(key, "-")
            print(f"  {s}/5         ", end="")
        print()

    print(f"  {'综合':8s}", end="")
    for r in records:
        print(f"  {r['overall']}/100      ", end="")
    print("\n")


def compare_outputs(path_a, path_b):
    """对比两份解读（AI vs 真实咨询），打印差异分析框架."""
    a_text = Path(path_a).read_text(encoding="utf-8") if Path(path_a).exists() else ""
    b_text = Path(path_b).read_text(encoding="utf-8") if Path(path_b).exists() else ""

    print("\n" + "=" * 60)
    print("对比分析框架")
    print("=" * 60)
    print(f"A: {path_a} ({len(a_text)} 字)")
    print(f"B: {path_b} ({len(b_text)} 字)")
    print()
    print("逐项对比（手动填写）：")
    print()
    print("1. A 说了但 B 没说的事情（AI 生成的多余内容）:")
    print("   ")
    print("2. B 说了但 A 没说的事情（AI 漏掉的关键洞察）:")
    print("   ")
    print("3. 同一件事，表述差异最大的一处:")
    print("   A: ")
    print("   B: ")
    print("4. B 中你认为 A 永远学不会的东西（如果有）:")
    print("   ")
    print()


def parse_scores_arg(scores_str):
    """Parse comma-separated scores into dict. Order: accuracy, relevance, concreteness, karma, language."""
    keys = ["accuracy", "relevance", "concreteness", "karma", "language"]
    parts = [p.strip() for p in scores_str.split(",")]
    result = {}
    for i, p in enumerate(parts):
        if i < len(keys) and p:
            result[keys[i]] = float(p)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="解读评分卡")
    parser.add_argument("target", help="要评分的解读文件路径")
    parser.add_argument("--question", "-q", default="", help="客户问题")
    parser.add_argument("--compare", default="", help="对比文件路径（真实咨询案例）")
    parser.add_argument("--history", action="store_true", help="查看历史评分趋势")
    parser.add_argument("--version", default="", help="版本标签")
    parser.add_argument("--scores", default="", help="直接传分: 准确性,相关性,落地性,业力完整,语言质感 (逗号分隔, 1-5)")
    parser.add_argument("--notes", default="", help="备注，逗号分隔，对应5个维度")
    args = parser.parse_args()

    if args.history:
        records = load_history()
        trend_report(records)
        sys.exit(0)

    target_path = Path(args.target)
    if not target_path.is_absolute():
        target_path = BASE / target_path

    if not target_path.exists():
        print(f"文件不存在: {target_path}")
        sys.exit(1)

    text = target_path.read_text(encoding="utf-8")

    if args.compare:
        compare_path = Path(args.compare)
        if not compare_path.is_absolute():
            compare_path = BASE / compare_path
        compare_outputs(str(target_path), str(compare_path))
        sys.exit(0)

    version = args.version or target_path.stem

    if args.scores:
        scores = parse_scores_arg(args.scores)
        notes = {}
        if args.notes:
            note_parts = [n.strip() for n in args.notes.split(",")]
            for i, n in enumerate(note_parts):
                if n:
                    notes[["accuracy", "relevance", "concreteness", "karma", "language"][i]] = n
        weighted = sum(scores.get(k, 0) * DIMENSIONS[k]["weight"] for k in scores)
        max_weighted = sum(DIMENSIONS[k]["weight"] for k in scores)
        overall = round(weighted / max_weighted * 20) if max_weighted > 0 else 0
    else:
        scores, notes, overall = score_output(text, args.question)

    print_report(scores, notes, overall, version)
    save_record(version, args.question, scores, notes, overall)

    records = load_history()
    if len(records) > 1:
        trend_report(records)
