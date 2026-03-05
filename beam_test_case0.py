#!/usr/bin/env python3
"""
BEAM Case 0 — Memory Test Script for OpenCode
预算追踪 Flask 应用开发对话（~140K tokens, 3 sessions, 188 messages）

Usage:
  source /tmp/beam_env/bin/activate
  python3 beam_test_case0.py

This script:
1. Loads BEAM Case 0 (coding: Flask budget tracker)  
2. Prints the conversation structure
3. Prints all 20 probing questions with expected answers
4. Can be adapted to feed into OpenCode's API for testing
"""
import ast
import json
from datasets import load_dataset

# ─── Load Dataset ───
print("📦 Loading BEAM dataset...")
dataset = load_dataset("Mohammadta/BEAM")
conv = dataset["100K"][0]

# ─── Case Overview ───
seed = conv["conversation_seed"]
profile = conv["user_profile"]

print(f"""
{'='*60}
📌 BEAM Case 0: {seed['title']}
{'='*60}
Category:  {seed['category']}
Theme:     {seed['theme']}
Subtopics: {json.dumps(seed['subtopics'], indent=2)}

User:      {profile['user_info'][:200]}...

Sessions:  {len(conv['chat'])} sessions
""")

# ─── Chat Overview ───
chat = conv["chat"]
total_msgs = 0
for i, session in enumerate(chat):
    msgs = session if isinstance(session, list) else [session]
    total_msgs += len(msgs)
    # Show first user message as session summary
    first_user_msg = ""
    for msg in msgs:
        if isinstance(msg, dict) and msg.get("role") == "user":
            first_user_msg = msg["content"][:120]
            break
    print(f"Session {i+1}: {len(msgs)} messages")
    print(f"  First user msg: {first_user_msg}...")
    print()

print(f"Total messages: {total_msgs}")
total_chars = sum(len(str(msg)) for msg in chat)
print(f"Estimated tokens: ~{total_chars//4:,}")

# ─── Probing Questions ───
print(f"\n{'='*60}")
print("🔍 PROBING QUESTIONS (20 total, 10 types)")
print(f"{'='*60}")

pq = ast.literal_eval(conv["probing_questions"]) if isinstance(conv["probing_questions"], str) else conv["probing_questions"]

all_questions = []
for qtype, questions in pq.items():
    for qi, q in enumerate(questions):
        question_text = q.get("question", str(q))
        answer_text = q.get("answer", q.get("expected_answer", ""))
        all_questions.append({
            "id": f"{qtype}_{qi}",
            "type": qtype,
            "question": question_text,
            "expected_answer": str(answer_text),
            "has_answer": bool(answer_text),  # some types have empty answers (by design)
        })

# Print overview
for q in all_questions:
    marker = "✅" if q["has_answer"] else "⬜"  # ⬜ = no standard answer (open-ended)
    print(f"\n{marker} [{q['type']}]")
    print(f"  Q: {q['question'][:200]}")
    if q["has_answer"]:
        print(f"  A: {q['expected_answer'][:200]}")
    else:
        print(f"  A: (open-ended, judge by quality)")

# ─── Export for downstream use ───
export_path = "/tmp/beam_case0_questions.json"
with open(export_path, "w", encoding="utf-8") as f:
    json.dump(all_questions, f, indent=2, ensure_ascii=False)
print(f"\n📁 Questions exported to: {export_path}")

# Export full chat for feeding to model
chat_export_path = "/tmp/beam_case0_chat.json"
with open(chat_export_path, "w", encoding="utf-8") as f:
    json.dump(chat, f, indent=2, ensure_ascii=False)
print(f"📁 Full chat exported to: {chat_export_path}")

print(f"""
{'='*60}
✅ Done! Next steps:
  1. Feed {chat_export_path} to OpenCode (original)
  2. After compaction, ask probing questions from {export_path}
  3. Score answers against expected_answer
  4. Repeat with enhanced OpenCode → compare scores
{'='*60}
""")
