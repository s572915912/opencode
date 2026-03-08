#!/usr/bin/env python3
"""
BEAM Official Nugget Evaluation (v2 — exact official implementation)

Implements BEAM benchmark's official scoring methodology from:
  https://github.com/mohammadtavakoli78/BEAM/blob/main/src/evaluation/compute_metrics.py

Key details verified from official source code:
  1. Prompt: unified_llm_judge_base_prompt with <rubric_item> and <llm_response>
     (NOTE: <question> placeholder exists in prompt but is NEVER replaced by official code)
  2. Scoring: int(response['score']) — effectively binary 0 or 1
  3. Event ordering: Kendall tau-b + nugget judge (dual scoring)
  4. All other types: pure nugget scoring (iterate rubric, score each, average)

BEAM repo cloned to: /tmp/BEAM_repo/src/evaluation/compute_metrics.py
"""

import json
import os
import sys
import ast
import time
import re
import requests
from typing import List, Dict, Any
from scipy.stats import kendalltau

# ─── Config ───────────────────────────────────────────────────
JUDGE_API_URL = "https://ai.changyou.club/v1/chat/completions"


def _get_api_key():
    """Get API key using same logic as beam_opencode_eval.py."""
    key = os.environ.get("CHANGYOU_API_KEY", "")
    if not key:
        try:
            auth_path = os.path.expanduser("~/.local/share/opencode/auth.json")
            with open(auth_path) as f:
                auth = json.load(f)
            key = auth.get("changyouopenai", {}).get("key", "")
        except Exception:
            pass
    if not key:
        key = os.environ.get("DEEPSEEK_API_KEY", "")
    return key


JUDGE_API_KEY = _get_api_key()

# ─── EXACT Official Prompt (from /tmp/BEAM_repo/src/prompts.py line 11547) ────
# NOTE: <question> placeholder is in the prompt but the official code
# (compute_metrics.py line 347-349) NEVER replaces it — only <rubric_item> and <llm_response>
# We reproduce this exact behavior for faithful comparison.
UNIFIED_LLM_JUDGE_BASE_PROMPT = """
You are an expert evaluator tasked with judging whether the LLM's response demonstrates compliance with the specified RUBRIC CRITERION.

## EVALUATION INPUTS
- QUESTION (what the user asked): <question>
- RUBRIC CRITERION (what to check): <rubric_item>
- RESPONSE TO EVALUATE: <llm_response>

## EVALUATION RUBRIC:
The rubric defines a specific requirement, constraint, or expected behavior that the LLM response should demonstrate. 

**IMPORTANT**: Pay careful attention to whether the rubric specifies:
- **Positive requirements** (things the response SHOULD include/do)
- **Negative constraints** (things the response SHOULD NOT include/do, often indicated by "no", "not", "avoid", "absent")

## RESPONSIVENESS REQUIREMENT (anchored to the QUESTION)
A compliant response must be **on-topic with respect to the QUESTION** and attempt to answer it.
- If the response does not address the QUESTION, score **0.0** and stop.
- For negative constraints, both must hold: (a) the response is responsive to the QUESTION, and (b) the prohibited element is absent.

## SEMANTIC TOLERANCE RULES:
Judge by meaning, not exact wording.
- Accept **paraphrases** and **synonyms** that preserve intent.
- **Case/punctuation/whitespace** differences must be ignored.
- **Numbers/currencies/dates** may appear in equivalent forms (e.g., "$68,000", "68k", "68,000 USD", or "sixty-eight thousand dollars"). Treat them as equal when numerically equivalent.
- If the rubric expects a number or duration, prefer **normalized comparison** (extract and compare values) over string matching.

## STYLE NEUTRALITY (prevents style contamination):
Ignore tone, politeness, length, and flourish unless the rubric explicitly requires a format/structure (e.g., "itemized list", "no citations", "one sentence").
- Do **not** penalize hedging, voice, or verbosity if content satisfies the rubric.
- Only evaluate format when the rubric **explicitly** mandates it.

## SCORING SCALE:
- **1.0 (Complete Compliance)**: Fully complies with the rubric criterion.
  - Positive: required element present, accurate, properly executed (allowing semantic equivalents).
  - Negative: prohibited element **absent** AND response is **responsive**.
  
- **0.5 (Partial Compliance)**: Partially complies.
  - Positive: element present but minor inaccuracies/incomplete execution.
  - Negative: generally responsive and mostly avoids the prohibited element but with minor/edge violations.
  
- **0.0 (No Compliance)**: Fails to comply.
  - Positive: required element missing or incorrect.
  - Negative: prohibited element present **or** response is non-responsive/evasive even if the element is absent.

## EVALUATION INSTRUCTIONS:
1. **Understand the Requirement**: Determine if the rubric is asking for something to be present (positive) or absent (negative/constraint).

2. **Parse Compound Statements**: If the rubric contains multiple elements connected by "and" or commas, evaluate whether:
   - **All elements** must be present for full compliance (1.0)
   - **Some elements** present indicates partial compliance (0.5)
   - **No elements** present indicates no compliance (0.0)
   
3. **Check Compliance**: 
   - For positive requirements: Look for the presence and quality of the required element
   - For negative constraints: Look for the absence of the prohibited element

4. **Assign Score**: Based on compliance with the specific rubric criterion according to the scoring scale above.

5. **Provide Reasoning**: Explain whether the rubric criterion was satisfied and justify the score.

## OUTPUT FORMAT:
Return your evaluation in JSON format with two fields:

{
   "score": [your score: 1.0, 0.5, or 0.0],
   "reason": "[detailed explanation of whether the rubric criterion was satisfied and why this justified the assigned score]"
}

NOTE: ONLY output the json object, without any explanation before or after that
"""


# ─── API Call (matches beam_opencode_eval.py SSE pattern) ─────
def call_judge_api(prompt: str, max_retries: int = 3) -> dict:
    """Call LLM judge via changyou API with SSE streaming."""
    for attempt in range(max_retries):
        try:
            time.sleep(2)  # Rate limiting
            resp = requests.post(
                JUDGE_API_URL,
                headers={
                    "Authorization": f"Bearer {JUDGE_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-5.4",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 300,
                    "stream": True,
                },
                timeout=60,
            )
            resp.raise_for_status()

            # Parse SSE streaming response
            text_chunks = []
            for line in resp.text.splitlines():
                if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                    try:
                        chunk = json.loads(line[5:].strip())
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        if delta.get("content"):
                            text_chunks.append(delta["content"])
                    except Exception:
                        pass
            text = "".join(text_chunks).strip()

            if not text:
                if attempt < max_retries - 1:
                    continue
                return {"score": 0, "reason": "Empty API response"}

            # Parse JSON from response (matching official parse_json_response)
            clean = text
            if clean.startswith("```"):
                match = re.search(r'```(?:json)?\s*(\[.*\]|\{.*\})\s*```', clean, re.DOTALL)
                if match:
                    clean = match.group(1).strip()

            try:
                return json.loads(clean)
            except json.JSONDecodeError:
                pass

            # Fallback: extract JSON object
            match = re.search(r'(\{.*?\})', clean, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except Exception:
                    pass

            # Last resort: regex extract score
            score_match = re.search(r'"score"\s*:\s*([0-9.]+)', text)
            score = float(score_match.group(1)) if score_match else 0
            return {"score": score, "reason": text[:100]}

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return {"score": 0, "reason": f"API error: {e}"}


# ─── Nugget Evaluation (matches official compute_metrics.py) ──
def evaluate_nuggets_official(rubric: list, llm_response: str) -> dict:
    """
    Official BEAM nugget evaluation for most question types.
    Matches compute_metrics.py evaluate_*() functions:
      - For each rubric item, replace <rubric_item> and <llm_response>
      - int(response['score']) — truncates 0.5 to 0
      - Average across all nuggets
    """
    llm_judge_responses = []
    score = 0

    for item in rubric:
        prompt = UNIFIED_LLM_JUDGE_BASE_PROMPT \
            .replace("<rubric_item>", item) \
            .replace("<llm_response>", llm_response)

        result = call_judge_api(prompt)

        # Official code: int(response['score']) — truncates 0.5 to 0
        nugget_score = int(result.get("score", 0))
        score += nugget_score

        llm_judge_responses.append({
            "rubric_item": item,
            "score": nugget_score,
            "raw_score": result.get("score", 0),
            "reason": result.get("reason", ""),
        })

    llm_judge_score = score / len(rubric) if rubric else 0

    return {
        "llm_judge_score": llm_judge_score,
        "llm_judge_responses": llm_judge_responses,
    }


def evaluate_event_ordering_official(rubric: list, llm_response: str) -> dict:
    """
    Official BEAM event ordering evaluation.
    Matches compute_metrics.py evaluate_event_ordering() (lines 396-433)
    + event_ordering_score() (lines 270-308)
    + report_results.py (line 42-43): final score = tau_norm

    Steps:
      1. Split response into event list
      2. LLM-based alignment (llm_equivalence)
      3. Kendall tau-b → tau_norm = (tau_b + 1) / 2, final = tau_norm * f1
      4. Nugget llm_judge_score (for reference, but NOT used as final score)
    """
    # ── Step 1: Split response into events ──
    system_list = llm_response.split("\n")
    system_list = [s.strip() for s in system_list if s.strip()]

    # ── Step 2: LLM-based alignment (matches align_with_llm + llm_equivalence) ──
    reference = rubric  # rubric items are the reference events
    used = set()
    system_canon = []

    for s in system_list:
        matched_index = None
        for index, r in enumerate(reference):
            if index in used:
                continue
            # Call LLM to check equivalence (matching llm_equivalence function)
            equiv_prompt = (
                "You are a binary classifier.\n"
                "If the TWO snippets describe the SAME event/fact, reply **YES**\n"
                "Otherwise reply **NO**. No extra words.\n"
                "DO NOT provide any explanation.\n\n"
                f"First snippet: {r}\n"
                f"Second snippet: {s}"
            )
            equiv_result = call_judge_api(equiv_prompt)
            # Parse YES/NO from response
            reason = str(equiv_result.get("reason", "")).lower()
            score_val = equiv_result.get("score", 0)
            # The LLM might return a standard JSON or just YES/NO text
            is_match = ("yes" in reason) or (score_val == 1)

            if is_match:
                matched_index = index
                break

        if matched_index is not None:
            system_canon.append(reference[matched_index])
            used.add(matched_index)
        else:
            system_canon.append(s)

    # ── Step 3: Kendall tau-b (matches event_ordering_score) ──
    reference_canon = reference

    # Compute precision/recall/f1
    tp = len(set(reference_canon) & set(system_canon))
    fp = len([x for x in system_canon if x not in reference_canon])
    fn = len([x for x in reference_canon if x not in system_canon])

    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0

    # Kendall tau-b on union ordering
    union = list(dict.fromkeys(reference_canon + system_canon))
    tie_rank = len(union) + 1

    def to_rank(seq):
        r = {item: i + 1 for i, item in enumerate(seq)}
        return [r.get(u, tie_rank) for u in union]

    tau_b, p_value = kendalltau(to_rank(reference_canon),
                                to_rank(system_canon),
                                variant="b", method="auto")
    tau_norm = (tau_b + 1) / 2 if tau_b is not None and not (tau_b != tau_b) else 0
    final_score = tau_norm * f1

    # ── Step 4: Nugget judge (for reference) ──
    llm_judge_responses = []
    llm_judge_score = 0
    for item in rubric:
        prompt = UNIFIED_LLM_JUDGE_BASE_PROMPT \
            .replace("<rubric_item>", item) \
            .replace("<llm_response>", llm_response)

        result = call_judge_api(prompt)
        nugget_score = float(result.get("score", 0))
        llm_judge_score += nugget_score

        llm_judge_responses.append({
            "rubric_item": item,
            "score": nugget_score,
            "reason": result.get("reason", ""),
        })

    llm_judge_score = llm_judge_score / len(rubric) if rubric else 0

    return {
        # tau_norm is the OFFICIAL final score (report_results.py line 42-43)
        "tau_norm": final_score,
        "tau_b": float(tau_b) if tau_b is not None else 0,
        "tau_norm_raw": tau_norm,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "llm_judge_score": llm_judge_score,
        "llm_judge_responses": llm_judge_responses,
        "alignment": {
            "reference": reference_canon,
            "system_aligned": system_canon,
            "matched": tp,
            "total_ref": len(reference_canon),
            "total_sys": len(system_canon),
        },
    }


# ─── Data Loading ────────────────────────────────────────────
def load_beam_rubrics(split: str = "500K", case_id: int = 0) -> dict:
    """Load rubrics from cached BEAM HuggingFace dataset."""
    from datasets import Dataset
    arrow_path = (
        f"/Users/haruaini/.cache/huggingface/datasets/Mohammadta___beam/"
        f"default/0.0.0/3205395e897e7318c7b094ef4e6047b9b82dbb03/"
        f"beam-{split}.arrow"
    )
    if not os.path.exists(arrow_path):
        print(f"❌ Cached dataset not found: {arrow_path}")
        sys.exit(1)
    d = Dataset.from_file(arrow_path)
    pq_str = d[case_id]["probing_questions"]
    return ast.literal_eval(pq_str)


def load_checkpoint_answers(split: str = "500K", case_id: int = 0) -> dict:
    """Load model answers from existing checkpoint."""
    ckpt_path = f"/tmp/beam_results/checkpoint_changyouopenai_gpt-5.4_{split}_tok128K.json"
    if not os.path.exists(ckpt_path):
        print(f"❌ Checkpoint not found: {ckpt_path}")
        sys.exit(1)
    with open(ckpt_path) as f:
        results = json.load(f)
    for r in results:
        if r["case_id"] == case_id:
            return r
    print(f"❌ Case {case_id} not found in checkpoint")
    sys.exit(1)


# ─── Main ────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="BEAM Official Nugget Evaluation v2")
    parser.add_argument("--split", default="500K", choices=["100K", "500K", "1M"])
    parser.add_argument("--case", type=int, default=0)
    args = parser.parse_args()

    split, case_id = args.split, args.case
    print(f"📊 BEAM Official Nugget Evaluation v2 (exact official implementation)")
    print(f"   Split: {split}, Case: {case_id}")
    print(f"   Judge: gpt-5.4 via changyou API")
    print(f"   Source: /tmp/BEAM_repo/src/evaluation/compute_metrics.py")
    print()

    # Load data
    print("📥 Loading rubrics from BEAM dataset...")
    rubrics = load_beam_rubrics(split, case_id)
    print("📥 Loading model answers from checkpoint...")
    case_result = load_checkpoint_answers(split, case_id)

    answers = case_result["after_compaction"]["results"]
    print(f"\n{'='*70}")
    print(f"Evaluating {len(answers)} questions with official nugget scoring")
    print(f"{'='*70}")

    all_results = []
    type_scores = {}

    for q_data in answers:
        q_type = q_data["type"]
        q_text = q_data["question"]
        model_answer = q_data["model_answer"]
        our_score = q_data["scores"]["combined"]

        # Find matching rubric
        if q_type not in rubrics:
            print(f"\n  ⚠️ Type {q_type} not in rubrics, skipping")
            continue

        matching_rubric = None
        for pq in rubrics[q_type]:
            if pq["question"] == q_text or pq["question"][:50] == q_text[:50]:
                matching_rubric = pq["rubric"]
                break

        if matching_rubric is None:
            print(f"\n  ⚠️ No rubric match for {q_type}: {q_text[:60]}...")
            continue

        print(f"\n  [{q_type}] {q_text[:70]}...")
        print(f"    Rubric: {len(matching_rubric)} nuggets")

        # Dispatch to correct evaluator (matches official code)
        if q_type == "event_ordering":
            result = evaluate_event_ordering_official(matching_rubric, model_answer)
        else:
            result = evaluate_nuggets_official(matching_rubric, model_answer)

        # Official score: tau_norm for event_ordering, llm_judge_score for others
        # (matches report_results.py line 42-43)
        if q_type == "event_ordering":
            official_score = result["tau_norm"]
            print(f"    📐 Kendall tau-b={result['tau_b']:.3f}, tau_norm={result['tau_norm_raw']:.3f}, "
                  f"F1={result['f1']:.3f}, final={official_score:.3f}")
            align = result["alignment"]
            print(f"    🔗 Aligned {align['matched']}/{align['total_ref']} ref events "
                  f"from {align['total_sys']} system events")
        else:
            official_score = result["llm_judge_score"]

        # Print nugget details
        for nr in result["llm_judge_responses"]:
            s = nr["score"]
            icon = "✅" if s >= 1 else "⚠️" if s >= 0.5 else "❌"
            print(f"    {icon} [{s}] {nr['rubric_item'][:80]}")
            reason = nr.get("reason", "")
            if reason:
                print(f"        → {reason[:80]}")

        icon = "✅" if official_score >= 0.5 else "⚠️" if official_score > 0 else "❌"
        print(f"    {icon} Official={official_score:.2f} vs Ours={our_score:.2f}")

        all_results.append({
            "type": q_type, "question": q_text[:100],
            "official_score": official_score, "our_combined": our_score,
            "num_nuggets": len(matching_rubric),
            "nugget_details": result["llm_judge_responses"],
            **({"tau_b": result.get("tau_b"), "f1": result.get("f1"),
                "tau_norm": result.get("tau_norm")} if q_type == "event_ordering" else {}),
        })

        if q_type not in type_scores:
            type_scores[q_type] = {"official": [], "ours": []}
        type_scores[q_type]["official"].append(official_score)
        type_scores[q_type]["ours"].append(our_score)

    # Summary
    print(f"\n{'='*70}")
    print(f"📊 RESULTS: Official Nugget (BEAM) vs Our Combined Scoring")
    print(f"{'='*70}")
    print(f"\n{'Type':25s} {'Official':>10s} {'Ours':>10s} {'Diff':>10s}")
    print(f"{'-'*55}")

    all_off, all_our = [], []
    for t, scores in sorted(type_scores.items()):
        off = sum(scores["official"]) / len(scores["official"])
        our = sum(scores["ours"]) / len(scores["ours"])
        all_off.extend(scores["official"])
        all_our.extend(scores["ours"])
        print(f"  {t:25s} {off*100:8.1f}% {our*100:8.1f}% {(off-our)*100:+8.1f}%")

    overall_off = sum(all_off) / len(all_off) if all_off else 0
    overall_our = sum(all_our) / len(all_our) if all_our else 0
    print(f"{'-'*55}")
    print(f"  {'Overall':25s} {overall_off*100:8.1f}% {overall_our*100:8.1f}% {(overall_off-overall_our)*100:+8.1f}%")

    # Save
    output = f"/tmp/beam_results/official_eval_v2_{split}_case{case_id}.json"
    with open(output, "w", encoding="utf-8") as f:
        json.dump({
            "split": split, "case_id": case_id,
            "method": "BEAM Official Nugget (v2, exact prompt)",
            "results": all_results,
            "type_scores": {t: {
                "official": sum(s["official"]) / len(s["official"]),
                "ours": sum(s["ours"]) / len(s["ours"]),
            } for t, s in type_scores.items()},
            "overall_official": overall_off,
            "overall_ours": overall_our,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n📁 Results saved to: {output}")


if __name__ == "__main__":
    main()

