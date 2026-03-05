#!/usr/bin/env python3
"""
BEAM Memory Evaluation Script
用 MiniMax (OpenCode 免费模型) 测试 BEAM Case 0 的记忆能力

实验设计：
  1. 将 BEAM 对话 Session 1+2 喂给模型（作为"历史上下文"）
  2. 模拟 compaction：用 LLM 生成摘要，替换原始对话
  3. 在摘要后提出探测问题
  4. 对比有/无摘要情况下的记忆得分

Usage:
  source /tmp/beam_env/bin/activate
  pip install openai
  python3 beam_memory_eval.py
"""

import ast
import json
import time
import os
from datasets import load_dataset

# ─── Config ───
# MiniMax API (OpenCode 免费模型通过 opencode 代理)
# 你也可以换成本地 vLLM: base_url="http://localhost:8000/v1"
API_BASE = os.environ.get("API_BASE", "https://api.opencode.ai/v1")
API_KEY = os.environ.get("API_KEY", "public")
MODEL = os.environ.get("MODEL", "minimax-m2")

# 控制对话长度（Token 截断），避免超出模型上下文
MAX_CONTEXT_MSGS = 40  # 每个 session 最多取多少条消息

# ─── Load BEAM Case 0 ───
print("📦 Loading BEAM Case 0...")
dataset = load_dataset("Mohammadta/BEAM")
conv = dataset["100K"][0]

chat_sessions = conv["chat"]
pq_raw = conv["probing_questions"]
pq = ast.literal_eval(pq_raw) if isinstance(pq_raw, str) else pq_raw

# Parse all probing questions
questions = []
for qtype, qs in pq.items():
    for q in qs:
        answer = q.get("answer", q.get("expected_answer", ""))
        if answer:  # only use questions with standard answers
            questions.append({
                "type": qtype,
                "question": q["question"],
                "expected_answer": str(answer),
            })

print(f"✅ Loaded {len(chat_sessions)} sessions, {sum(len(s) for s in chat_sessions)} total messages")
print(f"✅ {len(questions)} scoreable probing questions (with standard answers)")

# ─── Build context messages ───
def build_context(sessions, max_msgs_per_session=MAX_CONTEXT_MSGS):
    """Flatten multi-session chat into a single message list."""
    messages = []
    for i, session in enumerate(sessions):
        msgs = session if isinstance(session, list) else [session]
        # Truncate to avoid context overflow
        for msg in msgs[:max_msgs_per_session]:
            if isinstance(msg, dict) and "role" in msg and "content" in msg:
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
    return messages

# ─── API call via requests (more robust than OpenAI SDK) ───
def call_model(messages, system_prompt=None):
    """Call the model via HTTP directly for maximum compatibility."""
    import requests as req
    
    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)
    
    # Truncate messages to avoid context overflow
    # Keep system + last N messages
    if len(full_messages) > 42:
        system_msgs = [m for m in full_messages if m["role"] == "system"]
        other_msgs = [m for m in full_messages if m["role"] != "system"]
        full_messages = system_msgs + other_msgs[-40:]
    
    try:
        resp = req.post(
            f"{API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": full_messages,
                "max_tokens": 1024,
                "temperature": 0.3,
            },
            timeout=60,
        )
        
        data = resp.json()
        
        # Handle different response formats
        if isinstance(data, str):
            return data
        if "choices" in data and len(data["choices"]) > 0:
            choice = data["choices"][0]
            if "message" in choice:
                return choice["message"].get("content", "").strip()
            if "text" in choice:
                return choice["text"].strip()
        if "content" in data:
            return data["content"].strip()
        if "error" in data:
            print(f"❌ API Error: {data['error']}")
            return f"[ERROR: {data['error']}]"
        
        return str(data)[:500]
        
    except Exception as e:
        print(f"❌ Request Error: {e}")
        return f"[ERROR: {e}]"

# ─── Compaction Simulation ───
COMPACTION_PROMPT = """You are a context compressor. Summarize the following conversation into a concise but comprehensive summary. 
Preserve ALL specific details including:
- Exact dates, numbers, and deadlines
- Technical decisions and tool choices
- User preferences and constraints  
- Progress metrics and statistics
- File paths, API endpoints, and identifiers

Output a structured summary in 5 parts:
1. Project Overview
2. Key Technical Decisions  
3. Important Dates & Numbers
4. Current Progress
5. User Preferences & Constraints"""

def simulate_compaction(messages):
    """Simulate OpenCode's compaction: summarize context."""
    # Calculate pre-compaction stats
    total_chars = sum(len(msg.get("content", "")) for msg in messages)
    est_tokens = total_chars // 4
    
    print(f"""
╔══════════════════════════════════════════════════════════╗
║  ⚡ COMPACTION TRIGGERED                                ║
║  ─────────────────────────────────────────────────────── ║
║  Reason:    Context approaching token limit              ║
║  Messages:  {len(messages):>5} messages                              ║
║  Est tokens: {est_tokens:>6,} tokens (before)                     ║
║  Action:    Generating summary → replacing context       ║
╚══════════════════════════════════════════════════════════╝
""")
    
    # Build a single string of the conversation
    conv_text = ""
    for msg in messages:
        role = msg["role"].upper()
        conv_text += f"\n[{role}]: {msg['content'][:500]}\n"  # truncate long msgs
    
    summary = call_model(
        [{"role": "user", "content": f"Please summarize this conversation:\n\n{conv_text[:15000]}"}],
        system_prompt=COMPACTION_PROMPT
    )
    
    summary_tokens = len(summary) // 4
    ratio = (1 - summary_tokens / est_tokens) * 100 if est_tokens > 0 else 0
    
    print(f"""
╔══════════════════════════════════════════════════════════╗
║  ✅ COMPACTION COMPLETE                                  ║
║  ─────────────────────────────────────────────────────── ║
║  Before:   {est_tokens:>6,} tokens ({len(messages)} messages)              ║
║  After:    {summary_tokens:>6,} tokens (1 summary)                   ║
║  Compression: {ratio:.0f}% reduced                                ║
║  ⚠️  All original messages DISCARDED                     ║
╚══════════════════════════════════════════════════════════╝
""")
    
    return summary

# ─── Scoring ───
def score_answer(model_answer, expected_answer):
    """Simple keyword-overlap scoring."""
    if not model_answer or not expected_answer:
        return 0.0
    
    # Normalize
    model_lower = model_answer.lower()
    expected_lower = expected_answer.lower()
    
    # Extract key tokens from expected answer
    import re
    expected_tokens = set(re.findall(r'\b\w+\b', expected_lower))
    # Remove common words
    stopwords = {'the', 'a', 'an', 'is', 'was', 'are', 'were', 'be', 'been', 
                 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
                 'could', 'should', 'may', 'might', 'shall', 'can', 'to', 'of',
                 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into',
                 'and', 'or', 'but', 'not', 'no', 'that', 'this', 'it', 'i',
                 'my', 'you', 'your', 'we', 'our', 'they', 'their', 'me'}
    key_tokens = expected_tokens - stopwords
    
    if not key_tokens:
        return 1.0 if expected_lower in model_lower else 0.0
    
    # Count how many key tokens appear in model answer
    matches = sum(1 for token in key_tokens if token in model_lower)
    return matches / len(key_tokens)

# ─── Main Experiment ───
def run_experiment():
    print("\n" + "="*60)
    print("🔬 BEAM Memory Evaluation Experiment")
    print("="*60)
    
    # Build context from sessions
    context_msgs = build_context(chat_sessions)
    print(f"\n📊 Context: {len(context_msgs)} messages")
    
    # ─── Experiment A: Full Context (No Compaction) ───
    print("\n" + "-"*60)
    print("📋 Experiment A: Full Context (no compaction)")
    print("-"*60)
    
    results_full = []
    for i, q in enumerate(questions):
        print(f"\n  [{i+1}/{len(questions)}] ({q['type']}) {q['question'][:80]}...")
        
        # Ask probing question with full context
        probe_msgs = context_msgs.copy()
        probe_msgs.append({"role": "user", "content": q["question"]})
        
        answer = call_model(probe_msgs)
        score = score_answer(answer, q["expected_answer"])
        
        results_full.append({
            "type": q["type"],
            "question": q["question"],
            "expected": q["expected_answer"][:150],
            "model_answer": answer[:200] if answer else "",
            "score": score,
        })
        
        emoji = "✅" if score >= 0.5 else "⚠️" if score >= 0.3 else "❌"
        print(f"  {emoji} Score: {score:.2f}")
        print(f"     Expected: {q['expected_answer'][:100]}")
        print(f"     Got:      {answer[:100] if answer else '[empty]'}")
        
        time.sleep(1)  # Rate limiting
    
    # ─── Experiment B: After Compaction ───
    print("\n" + "-"*60)
    print("📋 Experiment B: After Compaction (compressed context)")
    print("-"*60)
    
    print("\n⚡ Running compaction...")
    summary = simulate_compaction(context_msgs)
    print(f"📝 Summary length: {len(summary)} chars")
    print(f"📝 Preview: {summary[:300]}...")
    
    # Create compacted context: just the summary
    compacted_msgs = [
        {"role": "assistant", "content": f"[Previous conversation summary]\n{summary}"}
    ]
    
    results_compacted = []
    for i, q in enumerate(questions):
        print(f"\n  [{i+1}/{len(questions)}] ({q['type']}) {q['question'][:80]}...")
        
        probe_msgs = compacted_msgs.copy()
        probe_msgs.append({"role": "user", "content": q["question"]})
        
        answer = call_model(probe_msgs)
        score = score_answer(answer, q["expected_answer"])
        
        results_compacted.append({
            "type": q["type"],
            "question": q["question"],
            "expected": q["expected_answer"][:150],
            "model_answer": answer[:200] if answer else "",
            "score": score,
        })
        
        emoji = "✅" if score >= 0.5 else "⚠️" if score >= 0.3 else "❌"
        print(f"  {emoji} Score: {score:.2f}")
        print(f"     Expected: {q['expected_answer'][:100]}")
        print(f"     Got:      {answer[:100] if answer else '[empty]'}")
        
        time.sleep(1)
    
    # ─── Results Summary ───
    print("\n" + "="*60)
    print("📊 RESULTS SUMMARY")
    print("="*60)
    
    # Overall scores
    avg_full = sum(r["score"] for r in results_full) / len(results_full) if results_full else 0
    avg_comp = sum(r["score"] for r in results_compacted) / len(results_compacted) if results_compacted else 0
    
    print(f"\n{'Metric':<35} {'Full Context':>15} {'After Compaction':>18} {'Delta':>8}")
    print("-" * 80)
    print(f"{'Overall Memory Score':<35} {avg_full:>14.1%} {avg_comp:>17.1%} {avg_comp-avg_full:>+7.1%}")
    
    # Per-type breakdown
    types = sorted(set(r["type"] for r in results_full))
    for qtype in types:
        full_scores = [r["score"] for r in results_full if r["type"] == qtype]
        comp_scores = [r["score"] for r in results_compacted if r["type"] == qtype]
        avg_f = sum(full_scores) / len(full_scores) if full_scores else 0
        avg_c = sum(comp_scores) / len(comp_scores) if comp_scores else 0
        print(f"  {qtype:<33} {avg_f:>14.1%} {avg_c:>17.1%} {avg_c-avg_f:>+7.1%}")
    
    # Save raw results
    results = {
        "model": MODEL,
        "case": "BEAM Case 0 - Flask Budget Tracker",
        "full_context": {
            "avg_score": avg_full,
            "results": results_full,
        },
        "after_compaction": {
            "avg_score": avg_comp,
            "results": results_compacted,
        },
        "compaction_summary": summary,
    }
    
    output_path = "/tmp/beam_eval_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n📁 Detailed results saved to: {output_path}")
    
    print(f"""
{'='*60}
🎯 INTERPRETATION GUIDE:
  Full Context Score  = 模型在完整对话下能记住多少
  Compaction Score    = 压缩后还能记住多少
  Delta              = 信息损失量（负数越大=丢失越多）
  
  你的改进目标：让 Delta 尽可能接近 0
{'='*60}
""")

if __name__ == "__main__":
    run_experiment()
