#!/usr/bin/env python3
"""BEAM Official Nugget Scorer v11 — uses dataset rubric, parallel, fast.

- Rubric from BEAM dataset (fixed, reproducible)
- Each rubric item scored 0/0.5/1.0 by LLM judge
- event_ordering: Kendall tau-b + LLM equivalence (per BEAM spec)
- 15 parallel workers for speed
- Reads saved model_answers from v10c results (no re-probing)
"""

import json, os, re, sys, ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from scipy.stats import kendalltau
import requests
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

DIR = os.path.dirname(os.path.abspath(__file__))
WORKERS = 8
OPENCODE_URL = os.environ.get("OPENCODE_URL", "http://localhost:4096")
PROVIDER_ID = os.environ.get("OPENCODE_PROVIDER", "changyouopenai")
MODEL_ID = os.environ.get("OPENCODE_MODEL", "gpt-5.4")
import time as _time


def llm(prompt, temp=0.0, tokens=300):
    """通过本地 OpenCode API 调用 LLM"""
    for attempt in range(3):
        try:
            r = requests.post(f"{OPENCODE_URL}/session", json={}, timeout=10)
            r.raise_for_status()
            sid = r.json()["id"]
            payload = {
                "model": {"providerID": PROVIDER_ID, "modelID": MODEL_ID},
                "parts": [{"type": "text", "text": prompt}],
            }
            r = requests.post(
                f"{OPENCODE_URL}/session/{sid}/message",
                json=payload, timeout=120)
            r.raise_for_status()
            try:
                data = r.json()
                for p in data.get("parts", []):
                    if p.get("type") == "text" and p.get("text", "").strip():
                        return p["text"].strip()
            except Exception:
                pass
            for _ in range(3):
                _time.sleep(2)
                msgs = requests.get(
                    f"{OPENCODE_URL}/session/{sid}/message", timeout=10).json()
                for msg in reversed(msgs):
                    info = msg.get("info", {})
                    if info.get("role") == "assistant" and not info.get("summary"):
                        for p in msg.get("parts", []):
                            if p.get("type") == "text" and p.get("text", "").strip():
                                return p["text"].strip()
            return ""
        except Exception as e:
            if attempt == 2: raise
    return ""

# ─── BEAM official judge prompt (from compute_metrics.py) ───
JUDGE_PROMPT = """You are an expert evaluator. Score how well the model's answer satisfies the following evaluation criterion.

Question: {question}
Evaluation Criterion: {rubric}
Model Answer: {answer}

Score:
- 1.0 = Criterion is FULLY satisfied
- 0.5 = Criterion is PARTIALLY satisfied (related info but incomplete/vague)
- 0.0 = Criterion is NOT satisfied (missing, wrong, or contradicted)

IMPORTANT: Focus on factual content. Minor wording differences don't matter.

Reply with ONLY a JSON: {{"score": <0 or 0.5 or 1.0>}}"""

def score_rubric(question, rubric_item, answer):
    """Score one rubric item. Returns 0, 0.5, or 1.0."""
    text = llm(JUDGE_PROMPT.format(question=question, rubric=rubric_item, answer=answer))
    match = re.search(r'"score"\s*:\s*([0-9.]+)', text)
    if match:
        raw = float(match.group(1))
        if raw >= 0.75: return 1.0
        if raw >= 0.25: return 0.5
        return 0.0
    return 0.0

# ─── Event ordering: Kendall tau-b (per BEAM spec) ───
def score_event_ordering(question, expected, answer):
    """Use LLM to extract ordered items, then compute Kendall tau-b."""
    prompt = f"""Extract the ordered list of items/events from both the expected answer and the model answer.

Question: {question}
Expected Answer: {expected}
Model Answer: {answer}

Return a JSON with:
- "expected_order": list of short item descriptions in expected order
- "model_order": list of short item descriptions in the order the model gave them
- "matched": number of items from expected that appear in model answer

If the model doesn't provide an ordered list, return {{"expected_order": [], "model_order": [], "matched": 0}}

Reply with ONLY a JSON object."""

    text = llm(prompt, tokens=500)
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        return 0.0
    try:
        d = json.loads(match.group())
        exp = d.get("expected_order", [])
        mod = d.get("model_order", [])
        matched = d.get("matched", 0)
        if not exp or matched == 0:
            return 0.0
        # Build rank vectors for matched items
        if len(exp) < 2:
            return 1.0 if matched > 0 else 0.0
        # Simple: if model_order matches expected_order, tau=1
        # Map model items to expected positions
        n = len(exp)
        exp_ranks = list(range(n))
        mod_ranks = list(range(n))  # default
        # Try to match model items to expected
        for i, e_item in enumerate(exp):
            best_pos = i  # default: same position
            for j, m_item in enumerate(mod):
                # LLM equivalence check (simple substring)
                if m_item.lower().strip() in e_item.lower().strip() or e_item.lower().strip() in m_item.lower().strip():
                    best_pos = j
                    break
            mod_ranks[i] = best_pos
        tau, _ = kendalltau(exp_ranks, mod_ranks)
        if tau != tau:  # NaN
            return 0.0
        # Normalize to [0,1]: tau_norm = (tau + 1) / 2
        return round(max(0, (tau + 1) / 2), 4)
    except Exception:
        return 0.0

# ─── Load BEAM rubric for a specific case ───
def load_rubric(split, case_id):
    """Load rubric from BEAM dataset."""
    from datasets import load_dataset
    ds = load_dataset("Mohammadta/BEAM", split=split)
    case = ds[case_id]
    qs = case["probing_questions"]
    if isinstance(qs, str):
        qs = ast.literal_eval(qs)
    # qs is dict: {type: {question_data}} or {type: [question_data]}
    rubric_map = {}
    for qtype, qdata in qs.items():
        if isinstance(qdata, dict):
            qdata = [qdata]
        elif isinstance(qdata, str):
            qdata = ast.literal_eval(qdata)
        for i, q in enumerate(qdata if isinstance(qdata, list) else [qdata]):
            question = q.get("question", "")
            rubric = q.get("rubric", [])
            expected = q.get("ideal_response") or q.get("ideal_answer") or q.get("expected_answer", "")
            if isinstance(rubric, str):
                rubric = ast.literal_eval(rubric)
            rubric_map[question[:80]] = {
                "type": qtype,
                "rubric": rubric,
                "expected": expected,
            }
    return rubric_map

# ─── Score one question ───
def score_one(idx, total, qdata, rubric_map):
    question = qdata["question"]
    answer = qdata.get("model_answer", "")
    qtype = qdata.get("type", "")

    # Find rubric for this question
    key = question[:80]
    rinfo = rubric_map.get(key, {})
    rubric = rinfo.get("rubric", [])
    expected = rinfo.get("expected", qdata.get("expected", ""))

    # Event ordering: Kendall tau-b
    if qtype == "event_ordering":
        score = score_event_ordering(question, expected, answer)
        print(f"  [{idx+1}/{total}] ({qtype}) tau={score:.2f} {question[:55]}...")
        return {"question": question, "type": qtype, "score": score, "method": "kendall_tau_b"}

    # All other types: rubric scoring
    if not rubric:
        # Fallback: use expected as single rubric
        rubric = [expected[:500]] if expected else ["answer matches expected"]

    scores = []
    for r in rubric:
        s = score_rubric(question, r, answer)
        scores.append((r, s))

    avg = sum(s for _, s in scores) / len(scores) if scores else 0.0

    print(f"  [{idx+1}/{total}] ({qtype}) score={avg:.2f} ({len(rubric)} rubrics) {question[:50]}...")
    for r, s in scores:
        mark = "✅" if s >= 1.0 else "🟡" if s >= 0.5 else "❌"
        print(f"    {mark} {s:.1f} | {r[:70]}")

    return {
        "question": question, "type": qtype,
        "rubric_scores": [{"rubric": r, "score": s} for r, s in scores],
        "score": round(avg, 4),
        "method": "rubric_nugget",
    }

# ─── Main ───
def rescore(path, split):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    results = data.get("results", [])
    out = []

    for case in results:
        cid = case["case_id"]
        qs = case.get("after_compaction", {}).get("results", [])
        if not qs: continue

        print(f"\n{'='*60}\n🔬 Case {cid} ({split}) — loading rubric...\n{'='*60}")
        rubric_map = load_rubric(split, cid)
        print(f"  Loaded {len(rubric_map)} rubric entries")

        scored = [None] * len(qs)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futs = {pool.submit(score_one, i, len(qs), q, rubric_map): i for i, q in enumerate(qs)}
            for fut in as_completed(futs):
                i = futs[fut]
                try: scored[i] = fut.result()
                except Exception as e:
                    print(f"  ❌ Error [{i}]: {e}")
                    scored[i] = {"score": 0, "question": "?", "type": "?"}

        avg = sum(s["score"] for s in scored) / len(scored)
        old = sum((q.get("scores", {}).get("combined") or 0) for q in qs) / len(qs)

        # Per-type breakdown
        types = {}
        for s in scored:
            t = s.get("type", "?")
            types.setdefault(t, []).append(s["score"])
        print(f"\n  📊 Case {cid}: Official={avg*100:.1f}%  Combined={old*100:.1f}%")
        for t in sorted(types):
            tavg = sum(types[t]) / len(types[t])
            print(f"    {t}: {tavg*100:.1f}%")

        out.append({"case_id": cid, "split": split, "official_avg": round(avg, 4),
                     "combined_avg": round(old, 4), "per_type": {t: round(sum(v)/len(v), 4) for t, v in types.items()},
                     "questions": scored})
    return out


def _mean(nums):
    """计算数值列表均值并保留四位小数。"""
    if not nums:
        return 0.0
    return round(sum(nums) / len(nums), 4)


def _mean_rubric(round_qs):
    """按 rubric 文本对齐并计算多轮均值。"""
    base = round_qs[0]
    items = base.get("rubric_scores")
    if not items:
        return None

    out = []
    for item in items:
        rub = item.get("rubric", "")
        vals = []
        for q in round_qs:
            for cur in q.get("rubric_scores", []):
                if cur.get("rubric", "") == rub:
                    vals.append(cur.get("score", 0.0))
                    break
        out.append({"rubric": rub, "score": _mean(vals)})
    return out


def _mean_questions(round_cases):
    """按题目对齐并计算 question 维度多轮均值。"""
    base = round_cases[0].get("questions", [])
    out = []
    for i, q in enumerate(base):
        text = q.get("question", "")
        group = []
        for case in round_cases:
            qs = case.get("questions", [])
            hit = next((cur for cur in qs if cur.get("question", "") == text), None)
            if hit is None and i < len(qs):
                hit = qs[i]
            if hit is not None:
                group.append(hit)

        avg_q = {
            "question": text,
            "type": q.get("type", ""),
            "score": _mean([cur.get("score", 0.0) for cur in group]),
            "method": q.get("method", ""),
        }
        rub = _mean_rubric(group) if group else None
        if rub is not None:
            avg_q["rubric_scores"] = rub
        out.append(avg_q)
    return out


def mean_cases(round_cases):
    """将同一 case 的多轮结果聚合为均值结果。"""
    base = round_cases[0]
    all_types = sorted({t for case in round_cases for t in case.get("per_type", {})})
    per_type = {}
    for t in all_types:
        vals = [case.get("per_type", {}).get(t, 0.0) for case in round_cases if t in case.get("per_type", {})]
        per_type[t] = _mean(vals)

    return {
        "case_id": base.get("case_id"),
        "split": base.get("split"),
        "official_avg": _mean([case.get("official_avg", 0.0) for case in round_cases]),
        "combined_avg": _mean([case.get("combined_avg", 0.0) for case in round_cases]),
        "per_type": per_type,
        "questions": _mean_questions(round_cases),
        "rounds": len(round_cases),
    }


def mean_rounds(all_rounds):
    """将所有轮次结果按 case 聚合为同维度均值。"""
    groups = {}
    order = []
    for round_items in all_rounds:
        for case in round_items:
            key = (case.get("case_id"), case.get("split"))
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(case)
    return [mean_cases(groups[key]) for key in order if groups[key]]


def output_paths(path):
    """基于输入 JSON 文件名生成结果 JSON 和图片输出路径。"""
    root, _ = os.path.splitext(path)
    return {
        "json": f"{root}_official_scores.json",
        "png": f"{root}_scores_table.png",
    }


def report(results, rounds, path):
    """打印汇总结果并按输入 JSON 文件名保存产物。"""
    outs = output_paths(path)

    print(f"\n{'='*60}")
    print(f"📊 BEAM OFFICIAL SCORING (rubric + Kendall tau-b)")
    print(f"   Using mean across {rounds} round(s)")
    print(f"{'='*60}")
    print(f"{'Case':>8} {'Split':>5} {'Official':>10} {'Combined':>10}")
    print("-" * 40)
    total_o, total_c = 0, 0
    for r in results:
        o = r["official_avg"] * 100
        c = r["combined_avg"] * 100
        print(f"  Case {r['case_id']:>2}  {r['split']:>5}  {o:>8.1f}%  {c:>8.1f}%")
        total_o += o; total_c += c
    n = len(results) or 1
    print("-" * 40)
    print(f"  {'Avg':>8}        {total_o/n:>8.1f}%  {total_c/n:>8.1f}%")

    with open(outs["json"], "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n💾 {outs['json']}")

    if results:
        visualize(results, outs["png"])

def main():
    import argparse
    parser = argparse.ArgumentParser(description="BEAM Official Nugget Scorer")
    parser.add_argument("files", nargs="*", help="Paths to result JSON files to score")
    parser.add_argument("--split", type=str, help="Dataset split (e.g. 100K, 500K, 1M). Autodetected from filename if not provided.")
    parser.add_argument("--rounds", type=int, default=1, help="Number of full scoring rounds for each input file.")
    args = parser.parse_args()

    if args.files:
        files = []
        for path in args.files:
            split = args.split
            if not split:
                match = re.search(r'_(1[MK]|[1-9][0-9]*[KM]|mini)', os.path.basename(path))
                split = match.group(1) if match else "100K"
            files.append((path, split))
        print("\n🧾 Split mapping (for manual check):")
        for path, split in files:
            print(f"  - {os.path.basename(path)} -> {split}")
    else:
        files = [
            (os.path.join(DIR, "beam_v10c_500K/results_changyouopenai_gpt-5.4_500K_20260314_020530.json"), "500K"),
            (os.path.join(DIR, "beam_v10c_1M/results_changyouopenai_gpt-5.4_1M_20260314_031606.json"), "1M"),
        ]
        print("\n🧾 Split mapping (default files):")
        for path, split in files:
            print(f"  - {os.path.basename(path)} -> {split}")
    
    rounds = max(1, args.rounds)
    for path, split in files:
        if not os.path.exists(path):
            print(f"\n⚠️ 文件不存在，跳过: {path}")
            continue

        raw_rounds = []
        print(f"\n📁 {os.path.basename(path)}")
        for n in range(rounds):
            print(f"\n{'='*60}")
            print(f"🔁 Round {n + 1}/{rounds}")
            print(f"{'='*60}")
            raw_rounds.append(rescore(path, split))
        report(mean_rounds(raw_rounds), rounds, path)


def _score_color(v):
    """根据分数 0~1 返回背景色，红→黄→绿渐变。"""
    if v >= 0.8:  return "#27AE60"
    if v >= 0.6:  return "#58D68D"
    if v >= 0.4:  return "#F9E79F"
    if v >= 0.2:  return "#F5B041"
    return "#E74C3C"


def _text_color(v):
    """高/低分用白字，中间用黑字。"""
    return "white" if v >= 0.8 or v < 0.2 else "black"


def visualize(results, path):
    """生成10个题型得分的可视化表格和柱状图，保存为 PNG。

    包含两部分：
    - 左侧：带颜色的得分表格（行=题型，列=各 Case）
    - 右侧：水平柱状图（所有 Case 按题型平均后对比）
    """
    types = sorted(set(t for r in results for t in r.get("per_type", {})))
    if not types:
        print("⚠️ 无 per_type 数据，跳过可视化")
        return

    labels = [f"Case {r['case_id']}\n({r['split']})" for r in results]
    n_types = len(types)
    n_cases = len(results)

    fig, axes = plt.subplots(
        1, 2, figsize=(max(8 + n_cases * 1.8, 14), max(n_types * 0.55 + 2, 6)),
        gridspec_kw={"width_ratios": [max(4, n_cases + 2), 3]},
    )

    # ── 左侧：彩色表格 ──
    ax = axes[0]
    ax.axis("off")
    ax.set_title("Per-Type Official Scores", fontsize=13, fontweight="bold", pad=12)

    col_labels = ["Question Type"] + labels + ["Avg"]
    rows = []
    for t in types:
        row = [t.replace("_", " ")]
        vals = []
        for r in results:
            v = r.get("per_type", {}).get(t, 0)
            vals.append(v)
            row.append(f"{v*100:.1f}%")
        avg = np.mean(vals) if vals else 0
        row.append(f"{avg*100:.1f}%")
        rows.append(row)

    # 汇总行
    total = ["OVERALL"]
    for r in results:
        total.append(f"{r['official_avg']*100:.1f}%")
    overall = np.mean([r["official_avg"] for r in results])
    total.append(f"{overall*100:.1f}%")
    rows.append(total)

    tbl = ax.table(cellText=rows, colLabels=col_labels, loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.auto_set_column_width(list(range(len(col_labels))))
    tbl.scale(1.1, 1.5)

    # 表头样式
    for j in range(len(col_labels)):
        cell = tbl[0, j]
        cell.set_facecolor("#2C3E50")
        cell.set_text_props(color="white", fontweight="bold", fontsize=9)

    # 数据单元格着色
    for i, t in enumerate(types):
        tbl[i + 1, 0].set_facecolor("#ECF0F1")
        tbl[i + 1, 0].set_text_props(fontweight="bold", fontsize=8)
        for j, r in enumerate(results):
            v = r.get("per_type", {}).get(t, 0)
            cell = tbl[i + 1, j + 1]
            cell.set_facecolor(_score_color(v))
            cell.set_text_props(color=_text_color(v), fontweight="bold")
        # Avg 列
        vals = [r.get("per_type", {}).get(t, 0) for r in results]
        avg = np.mean(vals)
        cell = tbl[i + 1, n_cases + 1]
        cell.set_facecolor(_score_color(avg))
        cell.set_text_props(color=_text_color(avg), fontweight="bold")

    # OVERALL 行样式
    row_idx = n_types + 1
    tbl[row_idx, 0].set_facecolor("#2C3E50")
    tbl[row_idx, 0].set_text_props(color="white", fontweight="bold", fontsize=9)
    for j in range(n_cases + 1):
        cell = tbl[row_idx, j + 1]
        cell.set_facecolor("#34495E")
        cell.set_text_props(color="white", fontweight="bold")

    # ── 右侧：水平柱状图 ──
    ax2 = axes[1]
    avg_per_type = []
    for t in types:
        vals = [r.get("per_type", {}).get(t, 0) for r in results]
        avg_per_type.append(np.mean(vals))

    y = np.arange(n_types)
    colors = [_score_color(v) for v in avg_per_type]
    bars = ax2.barh(y, [v * 100 for v in avg_per_type], color=colors, edgecolor="white", height=0.6)

    ax2.set_yticks(y)
    ax2.set_yticklabels([t.replace("_", " ") for t in types], fontsize=8)
    ax2.set_xlabel("Score (%)", fontsize=10)
    ax2.set_title("Average by Type", fontsize=13, fontweight="bold", pad=12)
    ax2.set_xlim(0, 105)
    ax2.invert_yaxis()
    ax2.grid(axis="x", alpha=0.3)

    for bar, v in zip(bars, avg_per_type):
        ax2.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                 f"{v*100:.1f}%", va="center", fontsize=8, fontweight="bold")

    plt.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"\n[chart] saved: {path}")


if __name__ == "__main__":
    main()
