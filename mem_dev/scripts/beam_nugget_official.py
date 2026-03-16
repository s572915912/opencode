#!/usr/bin/env python3
"""Official BEAM Nugget Evaluation per AutoNuggetizer (TREC 2024 RAG Track).

Steps per question:
1. Extract ≤20 nuggets from expected answer, each labeled vital/okay
2. Assign each nugget: support / partial_support / not_support
3. Compute 6 scoring variants (primary = Vital Strict)

Scoring:
- support=1.0, partial=0.5, not_support=0.0 (lenient)
- support=1.0, partial=0.0, not_support=0.0 (strict)
- All: mean over all nuggets
- Vital: mean over vital nuggets only
- Weighted: vital weight=1.0, okay weight=0.5
"""

import json, os, re, sys

DIR = os.path.dirname(os.path.abspath(__file__))
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

WORKERS = 15
API_URL = "https://ai.changyou.club/v1/chat/completions"
MODEL = "gpt-5.4"

def _key():
    k = os.environ.get("CHANGYOU_API_KEY", "")
    if not k:
        try:
            with open(os.path.expanduser("~/.local/share/opencode/auth.json")) as f:
                k = json.load(f).get("changyouopenai", {}).get("key", "")
        except Exception:
            pass
    return k or os.environ.get("DEEPSEEK_API_KEY", "")

KEY = _key()

def llm(prompt, temp=0.0, tokens=800):
    resp = requests.post(API_URL,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
              "temperature": temp, "max_tokens": tokens, "stream": True},
        timeout=120)
    resp.raise_for_status()
    parts = []
    for line in resp.text.splitlines():
        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
            try:
                d = json.loads(line[5:].strip())
                c = d.get("choices", [{}])[0].get("delta", {}).get("content")
                if c: parts.append(c)
            except Exception: pass
    return "".join(parts).strip()

# ─── Step 1: Extract nuggets with vital/okay labels ───
def extract_nuggets(question, expected):
    prompt = f"""You are NuggetizeLLM. Decompose the expected answer into atomic evaluation nuggets.

Question: {question}
Expected Answer: {expected}

Rules:
- Each nugget = ONE atomic, independently verifiable fact
- Label each nugget as "vital" (must appear in a good answer) or "okay" (good to have but not required)
- For abstention questions (answer says "no information available"), create exactly ONE vital nugget: "correctly states information is not available"
- Order nuggets by importance (most important first)
- Return AT MOST 20 nuggets total
- Return as a JSON array of objects: [{{"nugget": "...", "importance": "vital"}}, ...]

Reply with ONLY a JSON array. No other text."""

    text = llm(prompt)
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            arr = json.loads(match.group())
            out = []
            for item in arr[:20]:
                if isinstance(item, dict):
                    out.append({
                        "nugget": item.get("nugget", str(item)),
                        "importance": "vital" if item.get("importance", "").lower().startswith("vital") else "okay"
                    })
                elif isinstance(item, str):
                    out.append({"nugget": item, "importance": "vital"})
            return out if out else [{"nugget": expected[:200], "importance": "vital"}]
        except Exception:
            pass
    return [{"nugget": expected[:200], "importance": "vital"}]

# ─── Step 2: Assign nuggets (support/partial/not_support) ───
def assign_nuggets(question, answer, nuggets):
    """Assign all nuggets at once (listwise, per AutoNuggetizer)."""
    labels = ["not_support"] * len(nuggets)
    if not answer:
        return labels

    nug_list = [n["nugget"] for n in nuggets]
    # Process in batches of 10 (per paper)
    for start in range(0, len(nug_list), 10):
        batch = nug_list[start:start+10]
        prompt = f"""You are NuggetAssignerLLM. Label each nugget as support, partial_support, or not_support.

- support: nugget is FULLY captured in the answer
- partial_support: nugget is PARTIALLY captured (related info exists but incomplete/vague)
- not_support: nugget is NOT captured at all

Question: {question}
Answer: {answer}
Nugget List: {json.dumps(batch)}

Return ONLY a JSON array of labels (List[str]) in the same order. Example: ["support", "not_support", "partial_support"]"""

        text = llm(prompt, tokens=200)
        match = re.search(r'\[.*?\]', text, re.DOTALL)
        if match:
            try:
                batch_labels = json.loads(match.group())
                for i, lbl in enumerate(batch_labels[:len(batch)]):
                    lbl = str(lbl).lower().strip().strip('"')
                    if "partial" in lbl:
                        labels[start + i] = "partial_support"
                    elif "support" in lbl and "not" not in lbl:
                        labels[start + i] = "support"
                    else:
                        labels[start + i] = "not_support"
            except Exception:
                pass
    return labels

# ─── Step 3: Compute scores ───
def compute_scores(nuggets, labels):
    """Compute all 6 TREC scoring variants."""
    def lenient(lbl):
        if lbl == "support": return 1.0
        if lbl == "partial_support": return 0.5
        return 0.0

    def strict(lbl):
        return 1.0 if lbl == "support" else 0.0

    vitals = [(n, l) for n, l in zip(nuggets, labels) if n["importance"] == "vital"]
    okays  = [(n, l) for n, l in zip(nuggets, labels) if n["importance"] == "okay"]
    all_pairs = list(zip(nuggets, labels))

    def avg(pairs, fn):
        if not pairs: return 0.0
        return sum(fn(l) for _, l in pairs) / len(pairs)

    def weighted(vp, op, fn):
        num = sum(1.0 * fn(l) for _, l in vp) + sum(0.5 * fn(l) for _, l in op)
        den = len(vp) * 1.0 + len(op) * 0.5
        return num / den if den > 0 else 0.0

    return {
        "all":           round(avg(all_pairs, lenient), 4),
        "all_strict":    round(avg(all_pairs, strict), 4),
        "vital":         round(avg(vitals, lenient), 4),
        "vital_strict":  round(avg(vitals, strict), 4),  # PRIMARY METRIC
        "weighted":      round(weighted(vitals, okays, lenient), 4),
        "weighted_strict": round(weighted(vitals, okays, strict), 4),
        "n_vital": len(vitals),
        "n_okay": len(okays),
    }

# ─── Score one question ───
def score_one(idx, total, qdata):
    question = qdata["question"]
    expected = qdata["expected"]
    answer = qdata.get("model_answer", "")
    qtype = qdata.get("type", "")

    nuggets = extract_nuggets(question, expected)
    labels = assign_nuggets(question, answer, nuggets)
    scores = compute_scores(nuggets, labels)

    nv = scores["n_vital"]
    no = scores["n_okay"]
    vs = scores["vital_strict"]
    a = scores["all"]
    emoji = "✅" if vs >= 0.5 else "⚠️" if vs >= 0.3 else "❌"
    print(f"  [{idx+1}/{total}] ({qtype}) VS={vs:.2f} All={a:.2f} ({nv}V+{no}O) {question[:55]}...")
    for nug, lbl in zip(nuggets, labels):
        mark = "✅" if lbl == "support" else "🟡" if lbl == "partial_support" else "❌"
        tag = "V" if nug["importance"] == "vital" else "O"
        print(f"    {mark} [{tag}] {lbl:16s} | {nug['nugget'][:70]}")

    return {
        "question": question, "type": qtype,
        "nuggets": [{"nugget": n["nugget"], "importance": n["importance"], "label": l}
                     for n, l in zip(nuggets, labels)],
        "scores": scores,
        "old_combined": qdata.get("scores", {}).get("combined"),
    }

# ─── Main ───
def rescore(path):
    with open(path) as f:
        data = json.load(f)
    split = data.get("split", "?")
    results = data.get("results", [])
    out = []
    for case in results:
        cid = case["case_id"]
        qs = case.get("after_compaction", {}).get("results", [])
        if not qs: continue
        print(f"\n{'='*60}\n🔬 Case {cid} ({split}) — {len(qs)} questions\n{'='*60}")
        scored = [None] * len(qs)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futs = {pool.submit(score_one, i, len(qs), q): i for i, q in enumerate(qs)}
            for fut in as_completed(futs):
                i = futs[fut]
                try: scored[i] = fut.result()
                except Exception as e:
                    print(f"  ❌ Error [{i}]: {e}")
                    scored[i] = {"scores": {"vital_strict": 0, "all": 0}, "question": "?"}
        # Averages
        keys = ["all", "all_strict", "vital", "vital_strict", "weighted", "weighted_strict"]
        avgs = {k: sum(s["scores"].get(k, 0) for s in scored) / len(scored) for k in keys}
        old = sum((s.get("old_combined") or 0) for s in scored) / len(scored)
        print(f"\n  📊 Case {cid}: VS={avgs['vital_strict']*100:.1f}%  All={avgs['all']*100:.1f}%  W={avgs['weighted']*100:.1f}%  (Combined={old*100:.1f}%)")
        out.append({"case_id": cid, "split": split, "avgs": {k: round(v, 4) for k, v in avgs.items()}, "old_combined": round(old, 4), "questions": scored})
    return out

def main():
    files = [
        os.path.join(DIR, "beam_v10c_500K/results_changyouopenai_gpt-5.4_500K_20260314_020530.json"),
        os.path.join(DIR, "beam_v10c_1M/results_changyouopenai_gpt-5.4_1M_20260314_031606.json"),
    ]
    all_r = []
    for f in files:
        if os.path.exists(f):
            print(f"\n📁 {os.path.basename(f)}")
            all_r.extend(rescore(f))
    # Summary
    print(f"\n{'='*70}")
    print(f"📊 OFFICIAL BEAM NUGGET EVALUATION (AutoNuggetizer method)")
    print(f"{'='*70}")
    print(f"{'Case':>8} {'Split':>5} {'VS(primary)':>12} {'All':>8} {'Weighted':>10} {'Combined':>10}")
    print("-" * 60)
    totals = {k: 0 for k in ["vital_strict", "all", "weighted", "old"]}
    for r in all_r:
        vs = r["avgs"]["vital_strict"] * 100
        a = r["avgs"]["all"] * 100
        w = r["avgs"]["weighted"] * 100
        c = r["old_combined"] * 100
        print(f"  Case {r['case_id']:>2}  {r['split']:>5}  {vs:>10.1f}%  {a:>6.1f}%  {w:>8.1f}%  {c:>8.1f}%")
        totals["vital_strict"] += vs; totals["all"] += a; totals["weighted"] += w; totals["old"] += c
    n = len(all_r) or 1
    print("-" * 60)
    print(f"  {'Avg':>8}        {totals['vital_strict']/n:>10.1f}%  {totals['all']/n:>6.1f}%  {totals['weighted']/n:>8.1f}%  {totals['old']/n:>8.1f}%")
    out = os.path.join(DIR, "beam_v10c_nugget_official.json")
    with open(out, "w") as f:
        json.dump(all_r, f, indent=2, ensure_ascii=False)
    print(f"\n💾 {out}")

if __name__ == "__main__":
    main()
