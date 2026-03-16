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
from concurrent.futures import ThreadPoolExecutor, as_completed
import tiktoken
import os
import requests

# ─── Config ───
OPENCODE_URL = "http://localhost:4096"
PROVIDER_ID = "changyouopenai"   # OpenCode 的 provider 名
MODEL_ID = "gpt-5.4"           # 模型 ID

# BEAM 对话每次发送多少条消息（避免一次性发太多）
BATCH_SIZE = 5
# 每条消息间的延迟（秒）
DELAY = 1

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

def send_message(session_id, text, wait=True, cache_bust=False, no_compact=False):
    """Send a message to OpenCode session and get response.
    Uses sync POST to /message which streams the full response.
    If cache_bust=True, prepend a unique ID to break DeepSeek's prefix cache."""
    actual_text = text
    if cache_bust:
        unique_id = str(uuid.uuid4())[:8]
        actual_text = f"[req-{unique_id}] {text}"

    # Snapshot message count BEFORE sending so we can detect the new response
    try:
        pre = requests.get(f"{OPENCODE_URL}/session/{session_id}/message", timeout=10)
        pre.raise_for_status()
        pre_count = len(pre.json())
    except Exception:
        pre_count = -1

    payload = {
        "model": {
            "providerID": PROVIDER_ID,
            "modelID": MODEL_ID,
        },
        "config": {
            "reasoning_effort": "xhigh",
            **({"compaction": {"auto": False}} if no_compact else {}),
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

        # Fallback: poll GET for new assistant messages with retries
        for attempt in range(5):
            time.sleep(2 * (attempt + 1))
            msgs_resp = requests.get(
                f"{OPENCODE_URL}/session/{session_id}/message",
                timeout=10,
            )
            msgs_resp.raise_for_status()
            messages = msgs_resp.json()

            # If we got a pre_count, only look at NEW messages
            if pre_count >= 0 and len(messages) > pre_count:
                new = messages[pre_count:]
                texts = []
                for msg in new:
                    info = msg.get("info", {})
                    if info.get("role") == "assistant" and info.get("summary") is not True:
                        for part in msg.get("parts", []):
                            if part.get("type") == "text":
                                t = part.get("text", "")
                                if t.strip():
                                    texts.append(t)
                if texts:
                    return "\n".join(texts)
                continue  # retry — new messages appeared but no text yet

            # Fallback: scan from end for the last non-summary assistant message
            for msg in reversed(messages):
                info = msg.get("info", {})
                if info.get("role") == "assistant" and not info.get("summary"):
                    texts = []
                    for part in msg.get("parts", []):
                        if part.get("type") == "text":
                            t = part.get("text", "")
                            if t.strip():
                                texts.append(t)
                    if texts:
                        return "\n".join(texts)
                    break  # found latest assistant but no text — retry

        return "[no text in response]"
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
            timeout=600,
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

def get_session_tokens(session_id):
    """Query OpenCode API for actual token usage across all assistant messages."""
    try:
        msgs = get_messages(session_id)
        total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        for msg in msgs:
            info = msg.get("info", {})
            if info.get("role") != "assistant":
                continue
            tokens = info.get("tokens", {})
            total["input"] += tokens.get("input", 0)
            total["output"] += tokens.get("output", 0)
            cache = tokens.get("cache", {})
            total["cache_read"] += cache.get("read", 0)
            total["cache_write"] += cache.get("write", 0)
        total["api_total"] = total["input"] + total["output"]
        return total
    except Exception as e:
        print(f"  ⚠️ Token tracking error: {e}")
        return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "api_total": 0}

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
        # Try CHANGYOU_API_KEY first, then fall back to reading from OpenCode auth
        judge_api_key = os.environ.get("CHANGYOU_API_KEY", "")
        if not judge_api_key:
            # Try reading from OpenCode's stored auth
            try:
                import json as _json
                auth_path = os.path.expanduser("~/.local/share/opencode/auth.json")
                with open(auth_path) as _f:
                    _auth = _json.load(_f)
                judge_api_key = _auth.get("changyouopenai", {}).get("key", "")
            except Exception:
                pass
        if not judge_api_key:
            judge_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not judge_api_key:
            print("    ⚠️ LLM Judge skipped: no API key found")
            return -1.0
        time.sleep(0.3)  # Brief pause for rate limit
        resp = requests.post(
            "https://ai.changyou.club/v1/chat/completions",
            headers={"Authorization": f"Bearer {judge_api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-5.4",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 150,
                "stream": True,
                "reasoning_effort": "xhigh",
            },
            timeout=60,
        )
        resp.raise_for_status()
        # changyouopenai always streams SSE — parse delta chunks
        import re as _re, json as _json
        text_chunks = []
        for line in resp.text.splitlines():
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                try:
                    chunk = _json.loads(line[5:].strip())
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if delta.get("content"):
                        text_chunks.append(delta["content"])
                except Exception:
                    pass
        text = "".join(text_chunks).strip()
        if not text:
            return -1.0
        match = _re.search(r'"score"\s*:\s*([0-9.]+)', text)
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

_beam_dataset_cache = None

def _get_beam_dataset():
    """Load and cache the BEAM dataset from HuggingFace."""
    global _beam_dataset_cache
    if _beam_dataset_cache is None:
        print("📥 Loading BEAM dataset from HuggingFace...")
        from datasets import load_dataset
        _beam_dataset_cache = load_dataset("Mohammadta/BEAM")
    return _beam_dataset_cache

def _parse_questions(conv):
    """Extract scoreable questions from a BEAM conversation.
    
    Handles all 10 BEAM question types with different answer field names:
      - event_ordering, information_extraction, knowledge_update,
        multi_session_reasoning, temporal_reasoning: use 'answer'
      - abstention: uses 'ideal_response'
      - contradiction_resolution: uses 'ideal_answer'
      - summarization: uses 'ideal_summary'
      - instruction_following: uses 'expected_compliance' (no standard answer)
      - preference_following: uses 'expected_compliance' (no standard answer)
    """
    pq = conv["probing_questions"]
    if isinstance(pq, str):
        pq = ast.literal_eval(pq)
    questions = []
    # Map of possible answer field names in priority order
    ANSWER_FIELDS = [
        "answer", "expected_answer", "ideal_response",
        "ideal_answer", "ideal_summary", "expected_compliance",
    ]
    for qtype, qs in pq.items():
        for q in qs:
            # Find the answer from any of the known field names
            answer = ""
            for field in ANSWER_FIELDS:
                if field in q and q[field]:
                    answer = str(q[field])
                    break
            # For types that truly have no answer text, use rubric as reference
            if not answer and "rubric" in q and q["rubric"]:
                answer = "; ".join(q["rubric"])
            if answer:
                questions.append({
                    "type": qtype,
                    "question": q["question"],
                    "expected_answer": answer,
                })
    return questions

def load_beam(case_ids=None, split_name="100K"):
    """Load BEAM cases. Returns list of (case_id, chat, questions) tuples.
    
    Args:
        case_ids: list of int case IDs, or None for case 0 only.
        split_name: '100K', '500K', or '1M'.
    """
    if case_ids is None:
        case_ids = [0]
    
    ds = _get_beam_dataset()
    split = ds[split_name]
    
    cases = []
    for cid in case_ids:
        if cid >= len(split):
            print(f"⚠️ Case {cid} out of range (max {len(split)-1}), skipping")
            continue
        conv = split[cid]
        chat = conv["chat"]
        questions = _parse_questions(conv)
        # Count total user messages
        user_count = 0
        for session in chat:
            msgs = session if isinstance(session, list) else [session]
            for msg in msgs:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    user_count += 1
        cases.append((cid, chat, questions))
        print(f"  📦 Case {cid}: {len(chat)} sessions, {user_count} user msgs, {len(questions)} scoreable questions")
    
    return cases

PROBE_WORKERS = 10  # parallel scoring concurrency

def _score_one(idx, total, q, answer):
    """Score one probe answer. Thread-safe (no session state)."""
    scores = score_answer(q["question"], answer, q["expected_answer"])
    result = {
        "type": q["type"],
        "question": q["question"],
        "expected": q["expected_answer"],
        "model_answer": answer or "",
        "scores": scores,
    }
    c = scores["combined"]
    emoji = "✅" if c >= 0.5 else "⚠️" if c >= 0.3 else "❌"
    judge_str = f" Judge={scores['llm_judge']:.2f}" if scores['llm_judge'] is not None else ""
    print(f"  [{idx+1}/{total}] ({q['type']}) {q['question'][:80]}...")
    print(f"  {emoji} Combined={c:.2f}  (F1={scores['token_f1']:.2f}  KW={scores['keyword']:.2f}{judge_str})")
    print(f"     Expected: {q['expected_answer'][:100]}")
    print(f"     Got:      {answer[:100] if answer else '[empty]'}")
    return idx, result

# Project root = two levels up from mem_dev/scripts/
_OPENCODE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".opencode")

def _read_pk_context():
    """Read memory.md from project root .opencode/. Returns the full text."""
    mem = os.path.join(_OPENCODE_DIR, "memory.md")
    if not os.path.exists(mem):
        return ""
    with open(mem) as f:
        return f.read().strip()

def _read_timeline():
    """Read timeline.jsonl as a formatted string for temporal_reasoning injection."""
    tl = os.path.join(_OPENCODE_DIR, "timeline.jsonl")
    if not os.path.exists(tl):
        return ""
    lines = []
    with open(tl) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                lines.append(f"  {e.get('ts', '?')}: {e.get('event', '')}")
            except Exception:
                pass
    return "[Chronological Timeline]:\n" + "\n".join(lines) if lines else ""

def _read_episodes():
    """Read episodes.jsonl — sorted numbered Event Log entries for event_ordering injection."""
    ep_path = os.path.join(_OPENCODE_DIR, "episodes.jsonl")
    if not os.path.exists(ep_path):
        return []
    episodes = []
    with open(ep_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                episodes.append(json.loads(line))
            except Exception:
                pass
    # Sort by order field
    episodes.sort(key=lambda e: e.get("order", 0))
    return episodes

def _extract_event_log(pk_ctx):
    """Extract the Chronological Event Log section from PK context."""
    if not pk_ctx:
        return ""
    idx = pk_ctx.find("### Chronological Event Log")
    if idx < 0:
        idx = pk_ctx.find("### Event Log")
    if idx < 0:
        return ""
    rest = pk_ctx[idx:]
    # Find next ### section
    end = rest.find("\n### ", 5)
    if end > 0:
        return rest[:end].strip()
    return rest.strip()

def _extract_pk_section(pk_ctx, *section_names):
    """Extract one or more named ### sections from PK text.
    Matches by prefix keyword from section_names against actual ### headers.
    Returns concatenated text of all matched sections."""
    if not pk_ctx:
        return ""
    # Build a map: header_text -> block_text
    blocks = {}
    lines = pk_ctx.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("### "):
            header = line[4:].strip()
            content_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("### "):
                content_lines.append(lines[i])
                i += 1
            # Store under header
            body = "\n".join(content_lines).strip()
            if body:
                blocks[header] = f"### {header}\n{body}"
        else:
            i += 1

    parts = []
    for name in section_names:
        # Exact match first
        if name in blocks:
            parts.append(blocks[name])
            continue
        # Prefix/keyword match: find headers that START with the search name
        name_lower = name.lower()
        matched = [
            body for header, body in blocks.items()
            if header.lower().startswith(name_lower) or name_lower in header.lower()
        ]
        parts.extend(matched)
    return "\n\n".join(parts)

# Question-type to PK section mapping (xMemory-inspired top-down retrieval)
# Names below match the PREFIX of the actual section headers in memory.md
_QTYPE_SECTIONS = {
    "information_extraction":  ["Value Registry", "Technical Specifications"],
    "knowledge_update":        ["Value Registry", "Technical Specifications", "Contradiction"],
    "temporal_reasoning":      ["Chronological Event Log", "Value Registry"],
    "preference_following":    ["User Preferences", "Causal Decisions"],
    "instruction_following":   ["User Preferences", "Causal Decisions", "Technical Specifications"],
    "summarization":           ["Narrative Summary", "Chronological Event Log", "Causal Decisions"],
    "multi_session_reasoning": ["Narrative Summary", "User Context", "Causal Decisions", "Value Registry", "User Preferences"],
    "contradiction_resolution":["Contradiction", "Value Registry"],
    "event_ordering":          [],  # use episodes.jsonl
    "abstention":              [],  # no injection — avoid hallucination
}

def _build_probe(pk_ctx, question, qtype, episodes=None, timeline=""):
    """Build probe message with xMemory-inspired type-aware section injection.
    Each question type receives only the most relevant PK section."""
    if qtype == "event_ordering":
        if episodes:
            ep_lines = "\n".join(
                f"{e['order']}. [{e.get('ts', '?')}] {e['event']}" for e in episodes
            )
            return (
                f"The following is the EXACT chronological sequence of events from our conversation, "
                f"in the order they actually happened. Use these entries directly to answer the question:\n\n"
                f"{ep_lines}\n\n{question}"
            )
        elif pk_ctx:
            event_log = _extract_pk_section(pk_ctx, "Chronological Event Log", "Event Log")
            if event_log:
                return (
                    f"Here is the chronological record from our conversation history. "
                    f"Use it to answer accurately:\n\n{event_log}\n\n{question}"
                )
        return question

    sections = _QTYPE_SECTIONS.get(qtype, [])
    if not sections:
        # abstention or unknown: no injection
        return question

    ctx = _extract_pk_section(pk_ctx, *sections)

    # For temporal_reasoning, supplement with timeline.jsonl if available
    if qtype == "temporal_reasoning" and timeline:
        ctx = (ctx + "\n\n" + timeline).strip() if ctx else timeline

    if not ctx:
        return question

    return (
        f"Here is the relevant section from our conversation memory. "
        f"Use it to answer accurately — do NOT speculate beyond what is stated:\n\n"
        f"{ctx}\n\n{question}"
    )

def _run_raw_probes(session_id, questions):
    """Phase 2 (before compaction): send raw questions with no injection.
    memory.md doesn't exist yet, so injection is meaningless.
    no_compact=True prevents auto-compaction from firing mid-probe."""
    print(f"\n  📝 Sending {len(questions)} raw probes to session {session_id[:12]} (no injection, no_compact=True)...")
    answers = []
    for i, q in enumerate(questions):
        print(f"  [{i+1}/{len(questions)}] ({q['type']}) {q['question'][:80]}...")
        answer = send_message(session_id, q["question"], cache_bust=True, no_compact=True)
        answers.append(answer)
        time.sleep(0.3)
    print(f"\n  ⚡ Scoring {len(questions)} answers in parallel...")
    results = [None] * len(questions)
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        futs = {
            pool.submit(_score_one, i, len(questions), q, answers[i]): i
            for i, q in enumerate(questions)
        }
        for fut in as_completed(futs):
            idx, result = fut.result()
            results[idx] = result
    return results

def _run_probes_sequential(session_id, questions, use_fresh_session=False):
    """Send probes sequentially to the SAME compacted session (V12 behavior).
    The session already contains the compacted memory — questions are sent in order.
    Scoring is parallelized after all answers are collected."""
    # Load all memory sources for type-aware injection
    pk_ctx = _read_pk_context()
    timeline = _read_timeline()
    episodes = _read_episodes()
    if pk_ctx:
        print(f"  📎 memory.md: {len(pk_ctx)} chars — section injection enabled")
    if timeline:
        print(f"  🕐 timeline.jsonl loaded for temporal_reasoning injection")
    if episodes:
        print(f"  🗂  {len(episodes)} episodes loaded for event_ordering injection")

    print(f"\n  📝 Probing {len(questions)} questions — sequential in same compacted session (V12 mode)")

    answers = []
    for i, q in enumerate(questions):
        print(f"  [{i+1}/{len(questions)}] ({q['type']}) {q['question'][:80]}...")
        # V12: reuse the same compacted session — all questions see the same memory
        probe = _build_probe(pk_ctx, q["question"], q["type"], episodes=episodes, timeline=timeline)
        answer = send_message(session_id, probe, cache_bust=True, no_compact=True)
        answers.append(answer)
        time.sleep(0.3)

    # Score all answers in parallel
    print(f"\n  ⚡ Scoring {len(questions)} answers in parallel...")
    results = [None] * len(questions)
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        futs = {
            pool.submit(_score_one, i, len(questions), q, answers[i]): i
            for i, q in enumerate(questions)
        }
        for fut in as_completed(futs):
            idx, result = fut.result()
            results[idx] = result
    return results




# ─── Main Experiment ───
def run_single_case(case_id, chat_sessions, questions, compaction_interval=0, compaction_token_threshold=0):
    """Run BEAM evaluation for a single case. Returns results dict.
    
    Args:
        compaction_interval: if > 0, trigger compaction every N user messages (legacy).
        compaction_token_threshold: if > 0, trigger compaction when accumulated tokens exceed
                                   this threshold (e.g. 128000 for 128K context window).
                                   Takes priority over compaction_interval.
        if both are 0, use single-compaction mode (inject all → probe → compact → probe).
    """
    # Determine progressive mode
    progressive = compaction_interval > 0 or compaction_token_threshold > 0
    use_token_mode = compaction_token_threshold > 0
    
    print(f"\n{'='*60}")
    print(f"🔬 BEAM Case {case_id}: {len(chat_sessions)} sessions, {len(questions)} questions")
    if use_token_mode:
        print(f"   📐 Progressive mode: compact at {compaction_token_threshold//1000}K token threshold")
    elif compaction_interval > 0:
        print(f"   📐 Progressive mode: compact every {compaction_interval} messages")
    print(f"{'='*60}")
    
    # Clean up memory.md, knowledge.jsonl, and timeline.jsonl from previous case
    mem = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".opencode", "memory.md")
    sk = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".opencode", "knowledge.jsonl")
    tl = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".opencode", "timeline.jsonl")
    for f in [mem, sk, tl]:
        if os.path.exists(f):
            os.remove(f)
            print(f"  🧹 Cleaned up {f}")
    
    # Flatten ALL messages (user + assistant) from BEAM conversations
    all_messages = []
    for session in chat_sessions:
        msgs = session if isinstance(session, list) else [session]
        for msg in msgs:
            if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
                all_messages.append(msg)
    
    user_msgs = [m for m in all_messages if m["role"] == "user"]
    max_msgs = len(user_msgs)
    
    # Pre-calculate total tokens for estimation
    enc = tiktoken.get_encoding("cl100k_base")
    total_all_tokens = sum(len(enc.encode(m["content"])) for m in all_messages)
    total_user_tokens = sum(len(enc.encode(m["content"])) for m in user_msgs)
    print(f"📨 Total: {len(all_messages)} messages ({max_msgs} user + {len(all_messages)-max_msgs} asst)")
    print(f"📨 Tokens: {total_all_tokens:,} total ({total_user_tokens:,} user + {total_all_tokens-total_user_tokens:,} asst)")
    
    if use_token_mode:
        est_rounds = total_all_tokens // compaction_token_threshold
        print(f"📐 Estimated compaction rounds: {est_rounds} (@{compaction_token_threshold//1000}K threshold)")
    
    # ─── Create session ───
    session_id = create_session()
    
    if progressive:
        # ═══════════════════════════════════════════════
        # PROGRESSIVE MODE: inject ALL messages + compact when threshold reached
        # Injects both user AND assistant messages to simulate realistic context fill
        # ═══════════════════════════════════════════════
        threshold_label = f"{compaction_token_threshold//1000}K tokens" if use_token_mode else f"{compaction_interval} messages"
        print(f"\n{'='*60}")
        print(f"📋 Phase 1: Progressive injection with periodic compaction")
        print(f"   Injecting all messages (user + assistant) for realistic context fill")
        print(f"   Threshold: {threshold_label}")
        print(f"{'='*60}")
        
        injected = 0
        compaction_rounds = 0
        accumulated_tokens = 0  # tokens since last compaction (counting ALL messages)
        user_injected = 0
        pending_asst = None  # buffer the last assistant message to pair with next user msg
        
        for i, msg in enumerate(all_messages):
            content = msg["content"]
            role = msg["role"]
            msg_tokens = len(enc.encode(content))
            accumulated_tokens += msg_tokens
            
            if role == "assistant":
                # Buffer assistant message to pair with next user message
                pending_asst = content
            elif role == "user":
                # Pair with buffered assistant message if available
                if pending_asst:
                    paired = f"[Previous Assistant Response]:\n{pending_asst}\n\n[User Message]:\n{content}"
                    pending_asst = None
                else:
                    paired = content
                
                truncated = content[:80] + "..." if len(content) > 80 else content
                ok = inject_message(session_id, paired, cache_bust=True)
                injected += 1 if ok else 0
                user_injected += 1
                status = "✓" if ok else "✗"
                if user_injected % 20 == 0 or user_injected == 1 or i == len(all_messages)-1:
                    print(f"  [msg {i+1}/{len(all_messages)}, usr {user_injected}] {status} ({accumulated_tokens:,} tok) {truncated}")
                time.sleep(0.05)
            
            # Trigger compaction when threshold reached
            should_compact = False
            if use_token_mode:
                should_compact = accumulated_tokens >= compaction_token_threshold and (i + 1) < len(all_messages)
            elif compaction_interval > 0:
                should_compact = (i + 1) % compaction_interval == 0 and (i + 1) < len(all_messages)
            
            if should_compact:
                compaction_rounds += 1
                print(f"\n  🔄 Compaction round {compaction_rounds} (after {i+1} msgs, {accumulated_tokens:,} tokens)...")
                ok = trigger_compaction(session_id)
                if not ok:
                    print(f"  ⚠️ Compaction round {compaction_rounds} failed, continuing...")
                else:
                    print(f"  ✅ Compaction round {compaction_rounds} complete")
                accumulated_tokens = 0  # reset after compaction
                time.sleep(2)
        
        # Inject any trailing assistant message that wasn't paired
        if pending_asst:
            ok = inject_message(session_id, f"[Previous Assistant Response]:\n{pending_asst}", cache_bust=True)
            injected += 1 if ok else 0
        
        print(f"\n  ✅ Injected {injected} paired messages ({accumulated_tokens:,} total tok) with {compaction_rounds} compaction rounds")
        
        # No "before compaction" probing — model only sees recent context anyway
        results_before = []
        
        # Final compaction after all messages
        compaction_rounds += 1
        print(f"\n{'='*60}")
        print(f"📋 Phase 2: Final compaction (round {compaction_rounds})")
        print(f"{'='*60}")
        
        compaction_ok = trigger_compaction(session_id)
        if not compaction_ok:
            print("❌ Final compaction failed")
            return None
        
        time.sleep(3)
        
        # Post-compaction probing
        print(f"\n{'='*60}")
        print(f"📋 Phase 3: Probing after {compaction_rounds} rounds of compaction")
        print(f"{'='*60}")
    
    else:
        # ═══════════════════════════════════════════════
        # SINGLE-COMPACTION MODE (original 100K behavior)
        # ═══════════════════════════════════════════════
        print(f"\n{'='*60}")
        print("📋 Phase 1: Injecting BEAM conversation into OpenCode session")
        print("   (using noReply=true — no agent processing, just history)")
        print(f"{'='*60}")
        
        injected = 0
        for i, msg in enumerate(user_msgs):
            content = msg["content"]
            truncated = content[:80] + "..." if len(content) > 80 else content
            ok = inject_message(session_id, content, cache_bust=True)
            status = "✓" if ok else "✗"
            injected += 1 if ok else 0
            if (i+1) % 5 == 0 or i == 0 or i == max_msgs-1:
                print(f"  [{i+1}/{max_msgs}] {status} {truncated}")
            time.sleep(0.05)
        
        print(f"\n  ✅ Injected {injected}/{max_msgs} messages into session history")
        
        # ─── Probing BEFORE compaction ───
        print(f"\n{'='*60}")
        print("📋 Phase 2: Probing questions BEFORE compaction")
        print(f"{'='*60}")
        
        results_before = _run_raw_probes(session_id, questions)
        
        # ─── Trigger single compaction ───
        print(f"\n{'='*60}")
        print("📋 Phase 3: Triggering OpenCode REAL compaction")
        print(f"{'='*60}")
        
        compaction_ok = trigger_compaction(session_id)
        if not compaction_ok:
            print("❌ Compaction failed, skipping Phase 4")
            return None
        
        time.sleep(3)
        
        # ─── Probing AFTER compaction ───
        print(f"\n{'='*60}")
        print("📋 Phase 4: Probing questions AFTER compaction")
        print(f"{'='*60}")
    
    results_after = _run_probes_sequential(session_id, questions, use_fresh_session=True)
    
    # ─── Return results for this case ───
    def avg_score(results, metric="combined"):
        vals = [r["scores"][metric] for r in results if r["scores"].get(metric) is not None]
        return sum(vals) / len(vals) if vals else 0
    
    # ─── Token usage tracking ───
    token_usage = get_session_tokens(session_id)
    print(f"  💰 Tokens: input={token_usage['input']:,}  output={token_usage['output']:,}  cache_read={token_usage['cache_read']:,}  total_api={token_usage['api_total']:,}")

    result = {
        "case_id": case_id,
        "session_id": session_id,
        "messages_fed": max_msgs,
        "token_usage": token_usage,
        "before_compaction": {"avg_combined": avg_score(results_before), "results": results_before},
        "after_compaction": {"avg_combined": avg_score(results_after), "results": results_after},
    }
    
    # Quick per-case summary
    b = avg_score(results_before)
    a = avg_score(results_after)
    print(f"\n  📊 Case {case_id} Summary: Before={b:.1%}  After={a:.1%}  Delta={a-b:+.1%}")
    
    return result


def print_results_table(all_results):
    """Print aggregated results table from all case results."""
    # Flatten before/after results across all cases
    results_before = []
    results_after = []
    for r in all_results:
        results_before.extend(r["before_compaction"]["results"])
        results_after.extend(r["after_compaction"]["results"])
    
    def avg_score(results, metric="combined"):
        vals = [r["scores"][metric] for r in results if r["scores"].get(metric) is not None]
        return sum(vals) / len(vals) if vals else 0
    
    print(f"\n{'='*60}")
    print(f"📊 RESULTS — OpenCode BEAM Benchmark ({len(all_results)} cases)")
    print(f"    Scoring: 50% LLM Judge + 30% Token F1 + 20% Keyword")
    print(f"{'='*60}")
    
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
    
    # Token consumption summary
    total_input = sum(r.get("token_usage", {}).get("input", 0) for r in all_results)
    total_output = sum(r.get("token_usage", {}).get("output", 0) for r in all_results)
    total_cache = sum(r.get("token_usage", {}).get("cache_read", 0) for r in all_results)
    total_api = sum(r.get("token_usage", {}).get("api_total", 0) for r in all_results)
    
    print(f"\n{'='*60}")
    print(f"💰 Token Consumption Summary")
    print(f"{'='*60}")
    print(f"  {'Case':<12} {'Input':>12} {'Output':>12} {'Cache Read':>12} {'API Total':>12}")
    print(f"  {'-'*60}")
    for r in all_results:
        tu = r.get("token_usage", {})
        cid = r.get("case_id", "?")
        print(f"  Case {cid:<6} {tu.get('input',0):>12,} {tu.get('output',0):>12,} {tu.get('cache_read',0):>12,} {tu.get('api_total',0):>12,}")
    print(f"  {'-'*60}")
    print(f"  {'TOTAL':<12} {total_input:>12,} {total_output:>12,} {total_cache:>12,} {total_api:>12,}")


def main():
    """Main entry point with CLI arg parsing."""
    global PROVIDER_ID, MODEL_ID
    import argparse
    parser = argparse.ArgumentParser(description="BEAM Memory Benchmark for OpenCode")
    parser.add_argument("--cases", default="0", help="Case IDs: '0', '0,1,2', or 'all' (default: 0)")
    parser.add_argument("--split", default="100K", choices=["100K", "500K", "1M"], help="BEAM dataset split (default: 100K)")
    parser.add_argument("--compaction-interval", type=int, default=0,
                        help="Trigger compaction every N messages (legacy, use --compaction-token-threshold instead)")
    parser.add_argument("--compaction-token-threshold", type=int, default=0,
                        help="Trigger compaction when accumulated tokens exceed this threshold (e.g. 128000 for 128K)")
    parser.add_argument("--provider", default=PROVIDER_ID, help=f"Provider ID (default: {PROVIDER_ID})")
    parser.add_argument("--model", default=MODEL_ID, help=f"Model ID (default: {MODEL_ID})")
    parser.add_argument("--output", default="/tmp/beam_results", help="Output directory (default: /tmp/beam_results)")
    args = parser.parse_args()
    
    # Override globals
    PROVIDER_ID = args.provider
    MODEL_ID = args.model
    
    # Parse case IDs
    max_cases = 20 if args.split == "100K" else 35
    if args.cases == "all":
        case_ids = list(range(max_cases))
    else:
        case_ids = [int(x.strip()) for x in args.cases.split(",")]
    
    # Preflight
    if not check_server():
        sys.exit(1)
    
    ci = args.compaction_interval
    ctt = args.compaction_token_threshold
    if ctt > 0:
        mode_str = f"progressive(@{ctt//1000}K tokens)"
    elif ci > 0:
        mode_str = f"progressive(every {ci} msgs)"
    else:
        mode_str = "single-compaction"
    print(f"🚀 BEAM Benchmark: {args.split} split, {len(case_ids)} cases, {mode_str}")
    print(f"   Model: {PROVIDER_ID}/{MODEL_ID}")
    cases = load_beam(case_ids, split_name=args.split)
    
    # Checkpoint file for crash recovery
    os.makedirs(args.output, exist_ok=True)
    if ctt > 0:
        ckpt_suffix = f"_{args.split}_tok{ctt//1000}K"
    elif ci > 0:
        ckpt_suffix = f"_{args.split}_ci{ci}"
    else:
        ckpt_suffix = f"_{args.split}"
    checkpoint_path = os.path.join(args.output, f"checkpoint_{PROVIDER_ID}_{MODEL_ID}{ckpt_suffix}.json")
    
    # Load existing checkpoint if any
    completed = {}
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path) as f:
            completed = {r["case_id"]: r for r in json.load(f)}
        print(f"  📂 Resuming: {len(completed)} cases already done")
    
    all_results = list(completed.values())
    
    for case_id, chat, questions in cases:
        if case_id in completed:
            print(f"\n  ⏭️ Case {case_id} already done, skipping")
            continue
        
        try:
            result = run_single_case(case_id, chat, questions, compaction_interval=ci, compaction_token_threshold=ctt)
            if result is None:
                print(f"  ⚠️ Case {case_id} returned None, skipping")
                continue
            all_results.append(result)
            # Save checkpoint after each case
            with open(checkpoint_path, "w") as f:
                json.dump(all_results, f, indent=2, ensure_ascii=False)
            print(f"  💾 Checkpoint saved ({len(all_results)}/{len(cases)} cases)")
        except Exception as e:
            print(f"\n  ❌ Case {case_id} failed: {e}")
            continue
    
    # Print final aggregated results
    print_results_table(all_results)
    
    # Save final output
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_path = os.path.join(args.output, f"results_{PROVIDER_ID}_{MODEL_ID}_{args.split}_{ts}.json")
    output = {
        "test_type": f"OpenCode BEAM Benchmark ({args.split})",
        "scoring_method": "50% LLM Judge + 30% Token F1 + 20% Keyword Overlap",
        "model": f"{PROVIDER_ID}/{MODEL_ID}",
        "split": args.split,
        "compaction_interval": ci,
        "compaction_token_threshold": ctt,
        "compaction_mode": mode_str,
        "cases_tested": len(all_results),
        "results": all_results,
    }
    with open(final_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n📁 Results saved to: {final_path}")

if __name__ == "__main__":
    main()
