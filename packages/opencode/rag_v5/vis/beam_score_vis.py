#!/usr/bin/env python3
"""已评分结果可视化程序。

本程序用于读取一个或多个由 `beam_rubric_scorer.py` 产出的已评分 JSON 文件，
把其中的 case 结果按 `(case_id, split)` 聚合后，输出一张与参考程序风格一致
的总览图。程序不会重新调用模型评分，而是直接复用已有分数字段进行统计和绘图。

程序主要使用的方法如下：
1. 使用 `json.load()` 读取多个测评结果文件。
2. 使用题型维度的均值聚合，把重复 case 在多个文件中的结果合成为一份。
3. 使用 `matplotlib.table` 绘制左侧彩色表格，展示每个 case 在各题型上的得分。
4. 使用 `matplotlib` 的水平柱状图绘制右侧题型平均分，保持与参考脚本相近的布局。
5. 使用 `argparse` 提供命令行接口，支持一次传入多个 JSON 文件，并可选指定输出路径。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt


DIR = Path(__file__).resolve().parent
DEFAULT_PNG = DIR / "beam_score_vis.png"
DEFAULT_JSON = DIR / "beam_score_vis_merged.json"


def mean(nums):
    """计算数值列表均值并保留四位小数。"""
    if not nums:
        return 0.0
    return round(sum(nums) / len(nums), 4)


def mean_rubric(qs):
    """按 rubric 文本对齐并计算多份问题结果的均值。"""
    base = qs[0]
    items = base.get("rubric_scores")
    if not items:
        return None

    out = []
    for item in items:
        rub = item.get("rubric", "")
        vals = []
        for q in qs:
            for cur in q.get("rubric_scores", []):
                if cur.get("rubric", "") == rub:
                    vals.append(cur.get("score", 0.0))
                    break
        out.append({"rubric": rub, "score": mean(vals)})
    return out


def mean_questions(cases):
    """按题目文本对齐并计算同一 case 的问题维度均值。"""
    base = cases[0].get("questions", [])
    out = []
    for i, q in enumerate(base):
        text = q.get("question", "")
        group = []
        for case in cases:
            qs = case.get("questions", [])
            hit = next((cur for cur in qs if cur.get("question", "") == text), None)
            if hit is None and i < len(qs):
                hit = qs[i]
            if hit is not None:
                group.append(hit)

        if not group:
            continue

        avg = {
            "question": text,
            "type": q.get("type", ""),
            "score": mean([cur.get("score", 0.0) for cur in group]),
            "method": q.get("method", ""),
        }
        rub = mean_rubric(group)
        if rub is not None:
            avg["rubric_scores"] = rub
        out.append(avg)
    return out


def mean_case(cases):
    """把同一 `(case_id, split)` 的多份结果聚合为一份。"""
    base = cases[0]
    types = sorted({t for case in cases for t in case.get("per_type", {})})
    per_type = {}
    for t in types:
        vals = [
            case.get("per_type", {}).get(t, 0.0)
            for case in cases
            if t in case.get("per_type", {})
        ]
        per_type[t] = mean(vals)

    out = {
        "case_id": base.get("case_id"),
        "split": base.get("split", ""),
        "official_avg": mean([case.get("official_avg", 0.0) for case in cases]),
        "combined_avg": mean([case.get("combined_avg", 0.0) for case in cases]),
        "per_type": per_type,
        "sources": [case.get("_source", "") for case in cases if case.get("_source", "")],
        "rounds": len(cases),
    }
    questions = mean_questions(cases)
    if questions:
        out["questions"] = questions
    return out


def load_cases(path):
    """读取单个已评分 JSON，并为每条 case 附加来源文件名。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        items = data
    else:
        raise ValueError(f"文件内容不是 case 列表: {path}")

    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # 这里复制一份字典，避免后续附加来源字段时污染原始对象引用。
        case = dict(item)
        case["_source"] = os.path.basename(path)
        out.append(case)
    return out


def case_key(key):
    """生成稳定排序键，保证不同 split 下的 case 展示顺序可预测。"""
    cid, split = key
    try:
        idx = int(cid)
    except (TypeError, ValueError):
        idx = str(cid)
    return (str(split), idx)


def merge(files):
    """读取多个文件并按 `(case_id, split)` 聚合。"""
    groups = {}
    for path in files:
        for case in load_cases(path):
            # 聚合的唯一标识保持与计划一致：同一个 case_id 且同一个 split 才合并。
            key = (case.get("case_id"), case.get("split", ""))
            if key not in groups:
                groups[key] = []
            groups[key].append(case)

    out = [mean_case(groups[key]) for key in sorted(groups, key=case_key)]
    return out


def score_color(v):
    """根据分数 0~1 返回背景色，红到绿渐变。"""
    if v >= 0.8:
        return "#27AE60"
    if v >= 0.6:
        return "#58D68D"
    if v >= 0.4:
        return "#F9E79F"
    if v >= 0.2:
        return "#F5B041"
    return "#E74C3C"


def text_color(v):
    """根据底色亮度返回更清晰的前景色。"""
    return "white" if v >= 0.8 or v < 0.2 else "black"


def visualize(results, path, title):
    """生成与参考脚本一致风格的总览图。"""
    types = sorted({t for r in results for t in r.get("per_type", {})})
    if not types:
        print("⚠️ 无 per_type 数据，跳过可视化")
        return

    labels = [f"Case {r['case_id']}\n({r['split']})" for r in results]
    n_types = len(types)
    n_cases = len(results)

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(max(8 + n_cases * 1.8, 14), max(n_types * 0.55 + 2, 6)),
        gridspec_kw={"width_ratios": [max(4, n_cases + 2), 3]},
    )
    fig.suptitle(title, fontsize=14, fontweight="bold", y=0.995)

    # 这里保留与参考脚本相同的双栏布局，方便视觉对照已有图表。
    ax = axes[0]
    ax.axis("off")
    ax.set_title("Per-Type Official Scores", fontsize=13, fontweight="bold", pad=12)

    col_labels = ["Question Type"] + labels + ["Avg"]
    rows = []
    for t in types:
        row = [t.replace("_", " ")]
        vals = []
        for r in results:
            v = r.get("per_type", {}).get(t, 0.0)
            vals.append(v)
            row.append(f"{v * 100:.1f}%")
        row.append(f"{np.mean(vals) * 100:.1f}%" if vals else "0.0%")
        rows.append(row)

    # 最后一行保留参考脚本的 OVERALL 语义，用于快速查看每个 case 的总分。
    total = ["OVERALL"]
    for r in results:
        total.append(f"{r.get('official_avg', 0.0) * 100:.1f}%")
    total.append(f"{np.mean([r.get('official_avg', 0.0) for r in results]) * 100:.1f}%")
    rows.append(total)

    tbl = ax.table(cellText=rows, colLabels=col_labels, loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.auto_set_column_width(list(range(len(col_labels))))
    tbl.scale(1.1, 1.5)

    for j in range(len(col_labels)):
        cell = tbl[0, j]
        cell.set_facecolor("#2C3E50")
        cell.set_text_props(color="white", fontweight="bold", fontsize=9)

    for i, t in enumerate(types):
        tbl[i + 1, 0].set_facecolor("#ECF0F1")
        tbl[i + 1, 0].set_text_props(fontweight="bold", fontsize=8)
        for j, r in enumerate(results):
            v = r.get("per_type", {}).get(t, 0.0)
            cell = tbl[i + 1, j + 1]
            cell.set_facecolor(score_color(v))
            cell.set_text_props(color=text_color(v), fontweight="bold")
        avg = np.mean([r.get("per_type", {}).get(t, 0.0) for r in results])
        cell = tbl[i + 1, n_cases + 1]
        cell.set_facecolor(score_color(avg))
        cell.set_text_props(color=text_color(avg), fontweight="bold")

    row = n_types + 1
    tbl[row, 0].set_facecolor("#2C3E50")
    tbl[row, 0].set_text_props(color="white", fontweight="bold", fontsize=9)
    for j in range(n_cases + 1):
        cell = tbl[row, j + 1]
        cell.set_facecolor("#34495E")
        cell.set_text_props(color="white", fontweight="bold")

    ax2 = axes[1]
    avg = [np.mean([r.get("per_type", {}).get(t, 0.0) for r in results]) for t in types]
    y = np.arange(n_types)
    bars = ax2.barh(
        y,
        [v * 100 for v in avg],
        color=[score_color(v) for v in avg],
        edgecolor="white",
        height=0.6,
    )

    ax2.set_yticks(y)
    ax2.set_yticklabels([t.replace("_", " ") for t in types], fontsize=8)
    ax2.set_xlabel("Score (%)", fontsize=10)
    ax2.set_title("Average by Type", fontsize=13, fontweight="bold", pad=12)
    ax2.set_xlim(0, 105)
    ax2.invert_yaxis()
    ax2.grid(axis="x", alpha=0.3)

    for bar, v in zip(bars, avg):
        ax2.text(
            bar.get_width() + 1,
            bar.get_y() + bar.get_height() / 2,
            f"{v * 100:.1f}%",
            va="center",
            fontsize=8,
            fontweight="bold",
        )

    plt.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"[chart] saved: {path}")


def dump(results, path):
    """保存聚合后的 JSON，便于复查聚合结果。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"[json] saved: {path}")


def parse_args():
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="读取多个已评分 JSON 并生成与 beam_rubric_scorer 一致风格的可视化"
    )
    parser.add_argument("files", nargs="+", help="一个或多个已评分 JSON 文件路径")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_PNG),
        help=f"输出图片路径，默认: {DEFAULT_PNG}",
    )
    parser.add_argument(
        "--json-out",
        default=str(DEFAULT_JSON),
        help=f"聚合后 JSON 输出路径，默认: {DEFAULT_JSON}",
    )
    parser.add_argument(
        "--title",
        default="BEAM Official Scores Overview",
        help="图片总标题",
    )
    return parser.parse_args()


def main():
    """程序入口：读取、聚合、导出 JSON 并绘图。"""
    args = parse_args()
    files = [str(Path(path).expanduser()) for path in args.files]

    miss = [path for path in files if not os.path.exists(path)]
    if miss:
        raise FileNotFoundError(f"以下文件不存在: {miss}")

    results = merge(files)
    if not results:
        raise ValueError("没有读取到可用于可视化的 case 数据")

    # 先输出聚合信息，再落盘 JSON 和图片，方便在命令行里快速确认输入是否正确。
    print("已读取文件:")
    for path in files:
        print(f"  - {path}")
    print(f"聚合后 case 数量: {len(results)}")

    dump(results, args.json_out)
    visualize(results, args.output, args.title)


if __name__ == "__main__":
    main()
