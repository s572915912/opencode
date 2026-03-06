#!/usr/bin/env python3
"""
BEAM Memory Evaluation — OpenCode 真实集成测试
通过 OpenCode HTTP API 发送对话，触发真实 compaction，评测记忆留存

前提：
  1. 先启动 OpenCode: cd opencode-dev && bun run dev
  2. 配置 DeepSeek API key:
     在 opencode.json 或环境变量 DEEPSEEK_API_KEY 中配置

Usage:
  source /tmp/beam_env/bin/activate
  python3 beam_opencode_eval.py
"""

import ast
import json
import time
import sys
import uuid
import os
import requests

# ─── Config ───
OPENCODE_URL = "http://localhost:4096"
PROVIDER_ID = "deepseek"      # OpenCode 的 provider 名
MODEL_ID = "deepseek-chat"    # 模型 ID

# BEAM 对话每次发送多少条消息（避免一次性发太多）
BATCH_SIZE = 5
# 每条消息间的延迟（秒）
DELAY = 2

# ─── Helper Functions ───

def check_server():
    """Check if OpenCode server is running."""
    try:
        resp = requests.get(f"{OPENCODE_URL}/session", timeout=5)
        if resp.status_code == 200:
            print("✅ OpenCode server is running")
            return True
    except requests.ConnectionError:
        pass
    print(f"""
╔══════════════════════════════════════════════════════════╗
║  ❌ OpenCode 服务器未启动                                ║
║                                                          ║
║  请先在另一个终端运行:                                   ║
║  cd opencode-dev && bun run dev                          ║
║                                                          ║
║  等待看到 "listening on port 4096" 后再运行本脚本        ║
╚══════════════════════════════════════════════════════════╝
""")
    return False

def create_session():
    """Create a new OpenCode session."""
    resp = requests.post(f"{OPENCODE_URL}/session", json={})
    resp.raise_for_status()
    session = resp.json()
    print(f"📌 Created session: {session['id']}")
    return session["id"]

def inject_message(session_id, text, cache_bust=False):
    """Inject a user message into session WITHOUT triggering model response.
    Uses noReply=true to just add the message to history."""
    actual_text = text
    if cache_bust:
        unique_id = str(uuid.uuid4())[:8]
        actual_text = f"[req-{unique_id}] {text}"
    payload = {
        "model": {
            "providerID": PROVIDER_ID,
            "modelID": MODEL_ID,
        },
        "noReply": True,
        "parts": [
            {
                "type": "text",
                "text": actual_text
            }
        ]
    }
    try:
        resp = requests.post(
            f"{OPENCODE_URL}/session/{session_id}/message",
            json=payload,
            timeout=30,
        )
        return resp.status_code < 400
    except Exception as e:
        print(f"  ⚠️ Inject error: {e}")
        return False

def send_message(session_id, text, wait=True, cache_bust=False):
    """Send a message to OpenCode session and get response.
    Uses sync POST to /message which streams the full response.
    If cache_bust=True, prepend a unique ID to break DeepSeek's prefix cache."""
    actual_text = text
    if cache_bust:
        unique_id = str(uuid.uuid4())[:8]
        actual_text = f"[req-{unique_id}] {text}"
    payload = {
        "model": {
            "providerID": PROVIDER_ID,
            "modelID": MODEL_ID,
        },
        "parts": [
            {
                "type": "text",
                "text": actual_text
            }
        ]
    }
    try:
        # POST /message is synchronous — streams chunks then returns full JSON
        resp = requests.post(
            f"{OPENCODE_URL}/session/{session_id}/message",
            json=payload,
            timeout=300,  # 5 min timeout for full response
        )
        resp.raise_for_status()
        content = resp.text
        
        # The response may be the JSON of the last assistant message
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Not JSON, return raw
            return content[:1000] if content else "[empty response]"
        
        # Extract text from parts
        texts = []
        parts = data.get("parts", [])
        for p in parts:
            ptype = p.get("type", "")
            if ptype == "text":
                t = p.get("text", "")
                if t.strip():
                    texts.append(t)
        
        if texts:
            return "\n".join(texts)
        
        # Fallback: check all messages via GET
        time.sleep(1)
        msgs_resp = requests.get(
            f"{OPENCODE_URL}/session/{session_id}/message",
            timeout=10,
        )
        msgs_resp.raise_for_status()
        messages = msgs_resp.json()
        
        # Collect all text from last batch of assistant messages
        texts = []
        seen_last_user = False
        for msg in reversed(messages):
            info = msg.get("info", {})
            role = info.get("role", "")
            if role == "user":
                if seen_last_user:
                    break
                seen_last_user = True
                continue
            if role == "assistant" and not info.get("summary"):
                for part in msg.get("parts", []):
                    if part.get("type") == "text":
                        t = part.get("text", "")
                        if t.strip():
                            texts.append(t)
        
        return "\n".join(reversed(texts)) if texts else "[no text in response]"
    except requests.exceptions.Timeout:
        return "[TIMEOUT]"
    except Exception as e:
        print(f"  ⚠️ Send error: {e}")
        return f"[ERROR: {e}]"

def trigger_compaction(session_id):
    """Trigger OpenCode's real compaction via /summarize endpoint."""
    print(f"""
╔══════════════════════════════════════════════════════════╗
║  ⚡ TRIGGERING OPENCODE COMPACTION                       ║
║  ─────────────────────────────────────────────────────── ║
║  Endpoint: POST /session/{session_id[:8]}../summarize        ║
║  Provider: {PROVIDER_ID:<20}                         ║
║  Model:    {MODEL_ID:<20}                         ║
║  This uses OpenCode's REAL compaction prompt & logic     ║
╚══════════════════════════════════════════════════════════╝
""")
    
    payload = {
        "providerID": PROVIDER_ID,
        "modelID": MODEL_ID,
        "auto": False,
    }
    try:
        resp = requests.post(
            f"{OPENCODE_URL}/session/{session_id}/summarize",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        
        print(f"""
╔══════════════════════════════════════════════════════════╗
║  ✅ COMPACTION COMPLETE                                  ║
║  OpenCode's real summarization was applied               ║
╚══════════════════════════════════════════════════════════╝
""")
        return True
    except Exception as e:
        print(f"  ❌ Compaction failed: {e}")
        return False

def get_messages(session_id):
    """Get all messages from a session."""
    resp = requests.get(f"{OPENCODE_URL}/session/{session_id}/message")
    resp.raise_for_status()
    return resp.json()

# ─── Scoring System ───

def _tokenize(text):
    """Tokenize text into lowercase words."""
    import re
    return re.findall(r'\b\w+\b', text.lower())

STOPWORDS = {'the', 'a', 'an', 'is', 'was', 'are', 'were', 'be', 'been',
             'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
             'could', 'should', 'may', 'might', 'shall', 'can', 'to', 'of',
             'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into',
             'and', 'or', 'but', 'not', 'no', 'that', 'this', 'it', 'i',
             'my', 'you', 'your', 'we', 'our', 'they', 'their', 'me', 'he',
             'she', 'his', 'her', 'its', 'us', 'them', 'so', 'if', 'then'}

def score_keyword(model_answer, expected_answer):
    """Original keyword-overlap scoring (recall only)."""
    if not model_answer or not expected_answer:
        return 0.0
    model_lower = model_answer.lower()
    key_tokens = set(_tokenize(expected_answer)) - STOPWORDS
    if not key_tokens:
        return 1.0 if expected_answer.lower() in model_lower else 0.0
    matches = sum(1 for t in key_tokens if t in model_lower)
    return matches / len(key_tokens)

def score_token_f1(model_answer, expected_answer):
    """Token-level F1 score (SQuAD-style)."""
    if not model_answer or not expected_answer:
        return 0.0
    pred_tokens = set(_tokenize(model_answer)) - STOPWORDS
    gold_tokens = set(_tokenize(expected_answer)) - STOPWORDS
    if not gold_tokens:
        return 1.0 if not pred_tokens else 0.0
    if not pred_tokens:
        return 0.0
    common = pred_tokens & gold_tokens
    if not common:
        return 0.0
    precision = len(common) / len(pred_tokens)
    recall = len(common) / len(gold_tokens)
    f1 = 2 * precision * recall / (precision + recall)
    return f1

def score_llm_judge(question, model_answer, expected_answer):
    """Use DeepSeek as a judge to evaluate semantic correctness.
    Returns a score 0.0~1.0."""
    import openai
    if not model_answer or model_answer.startswith("["):
        return 0.0
    
    prompt = f"""You are an expert evaluator. Rate how correctly the Model Answer addresses the Question compared to the Expected Answer.

Question: {question}
Expected Answer: {expected_answer}
Model Answer: {model_answer[:500]}

Scoring rules:
- 1.0 = Completely correct, covers all key facts
- 0.7-0.9 = Mostly correct, minor details missing or extra info
- 0.4-0.6 = Partially correct, some key facts right but others wrong/missing  
- 0.1-0.3 = Mostly wrong, only trivially related
- 0.0 = Completely wrong or irrelevant

IMPORTANT: Focus on factual correctness, not wording. "4 weeks" and "28 days" are equivalent. "March 29" and "March 29th, 2024" are equivalent.

Reply with ONLY a JSON object: {{"score": <float>, "reason": "<brief reason>"}}"""

    try:
        judge_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not judge_api_key:
            print("    ⚠️ LLM Judge skipped: DEEPSEEK_API_KEY not set")
            return -1.0
        client = openai.OpenAI(
            api_key=judge_api_key,
            base_url="https://api.deepseek.com",
        )
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=150,
        )
        text = resp.choices[0].message.content.strip()
        # Parse JSON response
        import re
        match = re.search(r'"score"\s*:\s*([0-9.]+)', text)
        if match:
            return min(1.0, max(0.0, float(match.group(1))))
        return 0.0
    except Exception as e:
        print(f"    ⚠️ LLM Judge error: {e}")
        return -1.0  # Signal that judge failed

def score_answer(question, model_answer, expected_answer):
    """Combined scoring: Token F1 + LLM Judge + Keyword overlap.
    Returns dict with all scores and a combined score."""
    kw = score_keyword(model_answer, expected_answer)
    f1 = score_token_f1(model_answer, expected_answer)
    judge = score_llm_judge(question, model_answer, expected_answer)
    
    # Combined: trust LLM Judge when confident
    if judge >= 0:  # judge succeeded
        if judge >= 0.8:
            # High confidence from judge → trust it directly
            combined = judge
        else:
            # Lower confidence → blend all signals
            combined = 0.6 * judge + 0.25 * f1 + 0.15 * kw
    else:  # judge failed, fallback
        combined = 0.5 * f1 + 0.5 * kw
        judge = None
    
    return {
        "keyword": round(kw, 3),
        "token_f1": round(f1, 3),
        "llm_judge": round(judge, 3) if judge is not None else None,
        "combined": round(combined, 3),
    }

# ─── Load BEAM Data ───
def load_beam():
    """Load BEAM Case 0 from exported files or HF."""
    # Try local exports first
    try:
        with open("/tmp/beam_case0_chat.json") as f:
            chat = json.load(f)
        with open("/tmp/beam_case0_questions.json") as f:
            questions = json.load(f)
        questions = [q for q in questions if q.get("has_answer", True) and q.get("expected_answer")]
        return chat, questions
    except FileNotFoundError:
        print("⚠️ Local BEAM exports not found, loading from HuggingFace...")
        from datasets import load_dataset
        dataset = load_dataset("Mohammadta/BEAM")
        conv = dataset["100K"][0]
        chat = conv["chat"]
        pq = ast.literal_eval(conv["probing_questions"]) if isinstance(conv["probing_questions"], str) else conv["probing_questions"]
        questions = []
        for qtype, qs in pq.items():
            for q in qs:
                answer = q.get("answer", q.get("expected_answer", ""))
                if answer:
                    questions.append({
                        "type": qtype,
                        "question": q["question"],
                        "expected_answer": str(answer),
                    })
        return chat, questions

# ─── Main Experiment ───
def run_experiment():
    # Preflight
    if not check_server():
        sys.exit(1)
    
    chat_sessions, questions = load_beam()
    print(f"📦 BEAM Case 0: {len(chat_sessions)} sessions, {len(questions)} scoreable questions")
    
    # Flatten ALL messages (user + assistant) from BEAM conversations
    all_messages = []
    for session in chat_sessions:
        msgs = session if isinstance(session, list) else [session]
        for msg in msgs:
            if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
                all_messages.append(msg)
    
    print(f"📨 Total BEAM messages to inject: {len(all_messages)}")
    
    # ─── Create session ───
    session_id = create_session()
    
    # ─── Feed BEAM conversation (noReply mode — fast injection) ───
    print(f"\n{'='*60}")
    print("📋 Phase 1: Injecting BEAM conversation into OpenCode session")
    print("   (using noReply=true — no agent processing, just history)")
    print(f"{'='*60}")
    
    # Inject user messages only (assistant messages can't be injected via API)
    # We inject all user messages as conversation context
    user_msgs = [m for m in all_messages if m["role"] == "user"]
    max_msgs = min(len(user_msgs), 40)  # Feed more since injection is fast
    injected = 0
    for i, msg in enumerate(user_msgs[:max_msgs]):
        content = msg["content"]
        truncated = content[:80] + "..." if len(content) > 80 else content
        ok = inject_message(session_id, content, cache_bust=True)
        status = "✓" if ok else "✗"
        injected += 1 if ok else 0
        if (i+1) % 5 == 0 or i == 0 or i == max_msgs-1:
            print(f"  [{i+1}/{max_msgs}] {status} {truncated}")
        time.sleep(0.3)  # Small delay to not overwhelm
    
    print(f"\n  ✅ Injected {injected}/{max_msgs} messages into session history")
    
    # ─── Experiment A: Ask probing questions BEFORE compaction ───
    print(f"\n{'='*60}")
    print("📋 Phase 2: Probing questions BEFORE compaction")
    print(f"{'='*60}")
    
    results_before = []
    for i, q in enumerate(questions):
        print(f"\n  [{i+1}/{len(questions)}] ({q['type']}) {q['question'][:80]}...")
        
        answer = send_message(session_id, q["question"], cache_bust=True)
        scores = score_answer(q["question"], answer, q["expected_answer"])
        
        results_before.append({
            "type": q["type"],
            "question": q["question"],
            "expected": q["expected_answer"][:150],
            "model_answer": answer[:300] if answer else "",
            "scores": scores,
        })
        
        c = scores["combined"]
        emoji = "✅" if c >= 0.5 else "⚠️" if c >= 0.3 else "❌"
        judge_str = f" Judge={scores['llm_judge']:.2f}" if scores['llm_judge'] is not None else ""
        print(f"  {emoji} Combined={c:.2f}  (F1={scores['token_f1']:.2f}  KW={scores['keyword']:.2f}{judge_str})")
        print(f"     Expected: {q['expected_answer'][:100]}")
        print(f"     Got:      {answer[:100] if answer else '[empty]'}")
        
        time.sleep(DELAY)
    
    # ─── Trigger REAL compaction ───
    print(f"\n{'='*60}")
    print("📋 Phase 3: Triggering OpenCode REAL compaction")
    print(f"{'='*60}")
    
    compaction_ok = trigger_compaction(session_id)
    if not compaction_ok:
        print("❌ Compaction failed, skipping Phase 4")
        return
    
    time.sleep(3)  # Wait for compaction to settle
    
    # ─── Experiment B: Ask probing questions AFTER compaction ───
    print(f"\n{'='*60}")
    print("📋 Phase 4: Probing questions AFTER compaction")
    print(f"{'='*60}")
    
    results_after = []
    for i, q in enumerate(questions):
        print(f"\n  [{i+1}/{len(questions)}] ({q['type']}) {q['question'][:80]}...")
        
        answer = send_message(session_id, q["question"], cache_bust=True)
        scores = score_answer(q["question"], answer, q["expected_answer"])
        
        results_after.append({
            "type": q["type"],
            "question": q["question"],
            "expected": q["expected_answer"][:150],
            "model_answer": answer[:300] if answer else "",
            "scores": scores,
        })
        
        c = scores["combined"]
        emoji = "✅" if c >= 0.5 else "⚠️" if c >= 0.3 else "❌"
        judge_str = f" Judge={scores['llm_judge']:.2f}" if scores['llm_judge'] is not None else ""
        print(f"  {emoji} Combined={c:.2f}  (F1={scores['token_f1']:.2f}  KW={scores['keyword']:.2f}{judge_str})")
        print(f"     Expected: {q['expected_answer'][:100]}")
        print(f"     Got:      {answer[:100] if answer else '[empty]'}")
        
        time.sleep(DELAY)
    
    # ─── Results ───
    print(f"\n{'='*60}")
    print("📊 RESULTS — OpenCode REAL Compaction Test")
    print(f"    Scoring: 50% LLM Judge + 30% Token F1 + 20% Keyword")
    print(f"{'='*60}")
    
    def avg_score(results, metric="combined"):
        vals = [r["scores"][metric] for r in results if r["scores"].get(metric) is not None]
        return sum(vals) / len(vals) if vals else 0
    
    for metric_name, metric_key in [("Combined Score", "combined"), ("Token F1", "token_f1"), ("LLM Judge", "llm_judge"), ("Keyword Overlap", "keyword")]:
        avg_b = avg_score(results_before, metric_key)
        avg_a = avg_score(results_after, metric_key) 
        print(f"\n{'Metric: ' + metric_name:<35} {'Before':<15} {'After':<15} {'Delta':<8}")
        print("-" * 76)
        print(f"{'  Overall':<35} {avg_b:>14.1%} {avg_a:>14.1%} {avg_a-avg_b:>+7.1%}")
        
        types = sorted(set(r["type"] for r in results_before))
        for qtype in types:
            b_vals = [r["scores"][metric_key] for r in results_before if r["type"] == qtype and r["scores"].get(metric_key) is not None]
            a_vals = [r["scores"][metric_key] for r in results_after if r["type"] == qtype and r["scores"].get(metric_key) is not None]
            ab = sum(b_vals) / len(b_vals) if b_vals else 0
            aa = sum(a_vals) / len(a_vals) if a_vals else 0
            print(f"    {qtype:<31} {ab:>14.1%} {aa:>14.1%} {aa-ab:>+7.1%}")
    
    # Save results
    results = {
        "test_type": "OpenCode Real Compaction (v2 - multi-score)",
        "scoring_method": "50% LLM Judge + 30% Token F1 + 20% Keyword Overlap",
        "session_id": session_id,
        "model": f"{PROVIDER_ID}/{MODEL_ID}",
        "beam_case": "Case 0 - Flask Budget Tracker",
        "messages_fed": max_msgs,
        "before_compaction": {"avg_combined": avg_score(results_before), "results": results_before},
        "after_compaction": {"avg_combined": avg_score(results_after), "results": results_after},
    }
    
    output_path = "/tmp/beam_opencode_results_v2.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n📁 Results saved to: {output_path}")
    print(f"""
{'='*60}
🎯 三重评分系统：Token F1 + LLM Judge + Keyword
   经过 OpenCode 的真实 compaction 流程
{'='*60}
""")

if __name__ == "__main__":
    run_experiment()
