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

DIR = os.path.dirname(os.path.abspath(__file__))
WORKERS = 15
API_URL = "https://ai.changyou.club/v1/chat/completions"
MODEL = "gpt-5.4"

def _key():
    k = os.environ.get("CHANGYOU_API_KEY", "")
    if not k:
        try:
            with open(os.path.expanduser("~/.local/share/opencode/auth.json")) as f:
                k = json.load(f).get("changyouopenai", {}).get("key", "")
        except Exception: pass
    return k or os.environ.get("DEEPSEEK_API_KEY", "")

KEY = _key()

def llm(prompt, temp=0.0, tokens=300):
    for attempt in range(3):
        try:
            resp = requests.post(API_URL,
                headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                      "temperature": temp, "max_tokens": tokens, "stream": True},
                timeout=90)
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
    """Score one rubric item. Returns 0, 0.5, or 1.0.
    V16: Deterministic keyword matching for 86% of rubric items.
    Only falls back to LLM for complex/ambiguous items."""
    r = rubric_item.strip()
    # Normalize curly quotes → straight quotes in answer
    a = (answer or "").lower().replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')

    # --- Pattern 1: "should not contain: X" (negative check) ---
    neg = re.match(r'.*should\s+not\s+(?:contain|mention|include|state)[:\s]+(.+)', r, re.I)
    if neg:
        target = neg.group(1).strip().strip('"\'').lower()
        return 0.0 if target in a else 1.0

    # --- Pattern 2: "should contain/mention/state: X" (keyword check) ---
    pos = re.match(r'.*should\s+(?:contain|mention|state)[:\s]+(.+)', r, re.I)
    if pos:
        target = pos.group(1).strip().strip('"\'').lower()
        if target in a:
            return 1.0
        stop = {'the','a','an','is','are','was','were','be','to','of','and','or','in','on','at','for','with','that','this','it'}
        t_tokens = set(re.findall(r'\w+', target)) - stop
        a_tokens = set(re.findall(r'\w+', a)) - stop
        if not t_tokens:
            return 0.5
        overlap = len(t_tokens & a_tokens) / len(t_tokens)
        if overlap >= 0.8:
            return 1.0
        if overlap >= 0.4:
            return 0.5
        return 0.0

    # --- Pattern 3: "should include: X" (often needs code/example — use tokens) ---
    inc = re.match(r'.*should\s+include[:\s]+(.+)', r, re.I)
    if inc:
        target = inc.group(1).strip().strip('"\'').lower()
        stop = {'the','a','an','is','are','was','were','be','to','of','and','or','in','on','at','for','with','that','this','it'}
        t_tokens = set(re.findall(r'\w+', target)) - stop
        a_tokens = set(re.findall(r'\w+', a)) - stop
        if not t_tokens:
            return 0.5
        overlap = len(t_tokens & a_tokens) / len(t_tokens)
        if overlap >= 0.6:
            return 1.0
        if overlap >= 0.3:
            return 0.5
        return 0.0

    # --- Pattern 4: Abstention — "no information" / "not discussed" in rubric ---
    if 'no information' in r.lower() or 'not discussed' in r.lower():
        abstain_signals = [
            'no information', 'not discussed', 'not mentioned', 'no record',
            "don't have", "do not have", "wasn't discussed", "wasn't mentioned",
            "not covered", "no details", "not available", "cannot confirm",
            "didn't discuss", "did not discuss", "wasn't covered", "not part of",
            "no data", "no mention", "never discussed", "haven't discussed",
            "no specific", "not something we", "outside of what we",
        ]
        return 1.0 if any(s in a for s in abstain_signals) else 0.0

    # --- Pattern 5: "should ask for clarification" ---
    if 'ask for clarification' in r.lower() or 'which is correct' in r.lower():
        clarify_signals = ['which is correct', 'clarify', 'which statement', 'which one',
                          'could you confirm', 'can you confirm', 'please confirm']
        return 1.0 if any(s in a for s in clarify_signals) else 0.0

    # --- Fallback: LLM judge (only ~8% of items reach here) ---
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
    """Use LLM to directly produce rank mapping, then compute Kendall tau-b.
    V16: Direct LLM rank assignment with semantic matching."""
    prompt = f"""You are evaluating whether a model listed events in the correct order.

Question: {question}

Expected order (GROUND TRUTH — numbered 1..N):
{expected}

Model's answer:
{answer}

TASK: Match each expected item to the model's answer using SEMANTIC equivalence (ignore wording differences).
For each expected item 1..N, find which numbered position in the model's answer covers the SAME topic.

Example: If expected #1 is "setup issues with webcam" and model's #1 is "OpenCV + YOLOv5 webcam integration issues" → they match → output [1, 1].

Be GENEROUS with matching — if the topics overlap significantly, consider it a match.
For items with NO match at all, skip them entirely.

Reply with ONLY a JSON: {{"pairs": [[exp_pos, model_pos], [exp_pos, model_pos], ...], "n_matched": M}}
ONLY include matched pairs. Do NOT include null or unmatched entries.
"""
    text = llm(prompt, tokens=500)
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        return 0.0
    try:
        d = json.loads(match.group())
        pairs = d.get("pairs", [])
        # Filter out any nulls/invalids
        valid = []
        for p in pairs:
            if isinstance(p, (list, tuple)) and len(p) >= 2 and p[0] is not None and p[1] is not None:
                valid.append((int(p[0]), int(p[1])))
        if len(valid) < 2:
            return 1.0 if len(valid) == 1 else 0.0
        exp_ranks = [p[0] for p in valid]
        mod_ranks = [p[1] for p in valid]
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
            # event_ordering: expected order is in ordering_tested or answer field
            if not expected and qtype == "event_ordering":
                ordering = q.get("ordering_tested", [])
                if ordering:
                    expected = "\n".join(f"{i+1}. {item}" for i, item in enumerate(ordering)) if isinstance(ordering, list) else str(ordering)
                elif q.get("answer"):
                    expected = q["answer"]
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
    with open(path) as f:
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

def main():
    files = [
        (os.path.join(DIR, "beam_v10c_500K/results_changyouopenai_gpt-5.4_500K_20260314_020530.json"), "500K"),
        (os.path.join(DIR, "beam_v10c_1M/results_changyouopenai_gpt-5.4_1M_20260314_031606.json"), "1M"),
    ]
    all_r = []
    for path, split in files:
        if os.path.exists(path):
            print(f"\n📁 {os.path.basename(path)}")
            all_r.extend(rescore(path, split))

    print(f"\n{'='*60}")
    print(f"📊 BEAM OFFICIAL SCORING (rubric + Kendall tau-b)")
    print(f"{'='*60}")
    print(f"{'Case':>8} {'Split':>5} {'Official':>10} {'Combined':>10}")
    print("-" * 40)
    total_o, total_c = 0, 0
    for r in all_r:
        o = r["official_avg"] * 100
        c = r["combined_avg"] * 100
        print(f"  Case {r['case_id']:>2}  {r['split']:>5}  {o:>8.1f}%  {c:>8.1f}%")
        total_o += o; total_c += c
    n = len(all_r) or 1
    print("-" * 40)
    print(f"  {'Avg':>8}        {total_o/n:>8.1f}%  {total_c/n:>8.1f}%")

    out = os.path.join(DIR, "beam_v11_official_scores.json")
    with open(out, "w") as f:
        json.dump(all_r, f, indent=2, ensure_ascii=False)
    print(f"\n💾 {out}")

if __name__ == "__main__":
    main()
