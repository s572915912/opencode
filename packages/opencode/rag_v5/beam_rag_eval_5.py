#!/usr/bin/env python3
"""
BEAM Memory Evaluation — Solution 5: Type-Aware Structured Memory RAG（方案 5）

程序作用：
  1. 在不修改现有方案 1-4 的前提下，新增一个独立的评测脚本。
  2. 保留方案 4 的外挂式混合检索外壳：Chroma 向量检索 + BM25 稀疏检索 + RRF 融合。
  3. 额外为每个 case 构建一套结构化记忆文件，模拟官方方案 C 的“按题型注入不同记忆源”。
  4. 回答问题时，不再只依赖检索 chunk，而是优先使用结构化记忆，再用方案 4 的检索结果作为补充证据。
  5. 对 `event_ordering` 单独使用事件序列上下文和编号输出格式，尽量贴近官方 Kendall tau-b 排序评分方式。

主要方法：
  - 数据层：先把 BEAM 对话展平，再切成带时序标签的 chunk，建立独立的 `rag_db_5` 检索库。
  - 记忆层：对每个 chunk 调用本地 OpenCode API 提取结构化信息，落盘为 `memory.md`、`episodes.jsonl`、`timeline.jsonl`。
  - 路由层：按照题型选择最相关的 memory section / 时间线 / 事件列表。
  - 证据层：把方案 4 的混合检索结果作为补充证据，帮助模型在结构化记忆不完整时回到原始对话片段。
  - 回答层：不同题型使用不同 prompt 模板，尤其是 `event_ordering` 强制输出编号时间序列。
  - 评分层：继续复用现有 Keyword / Token F1 / LLM Judge 三维评分，保持和方案 4 输出结构兼容。
"""

import argparse
import ast
import json
import math
import os
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import tiktoken
try:
    from rank_bm25 import BM25Okapi
except Exception:
    class BM25Okapi:
        """缺少三方依赖时使用的简化 BM25 实现。"""

        def __init__(self, corpus, k1=1.5, b=0.75):
            self.corpus = corpus
            self.k1 = k1
            self.b = b
            self.size = len(corpus)
            self.avg = sum(len(doc) for doc in corpus) / self.size if self.size else 0.0
            self.freqs = []
            self.df = {}
            for doc in corpus:
                freq = {}
                for tok in doc:
                    freq[tok] = freq.get(tok, 0) + 1
                self.freqs.append(freq)
                for tok in freq:
                    self.df[tok] = self.df.get(tok, 0) + 1
            self.idf = {
                tok: math.log(1 + (self.size - val + 0.5) / (val + 0.5))
                for tok, val in self.df.items()
            }

        def get_scores(self, query):
            scores = []
            for doc, freq in zip(self.corpus, self.freqs):
                score = 0.0
                size = len(doc)
                base = self.k1 * (1 - self.b + self.b * size / self.avg) if self.avg else self.k1
                for tok in query:
                    hits = freq.get(tok, 0)
                    if not hits:
                        continue
                    idf = self.idf.get(tok, 0.0)
                    score += idf * hits * (self.k1 + 1) / (hits + base)
                scores.append(score)
            return scores

# ─── 全局配置 ──────────────────────────────────────────────────────────────

OPENCODE_URL = "http://localhost:4096"
PROVIDER_ID = "changyouopenai"
MODEL_ID = "gpt-5.4"

SCORE_F1 = True
SCORE_JUDGE = True
SCORE_KEYWORD = True

PROBE_WORKERS = 10
MEM_WORKERS = 4
DB_ROOT = "./rag_db_5"
MEM_ROOT = "./beam_mem_5"


def get_api_key():
    """获取 API key。"""
    api_key = os.environ.get("CHANGYOU_API_KEY", "")
    if not api_key:
        try:
            auth_path = os.path.expanduser("~/.local/share/opencode/auth.json")
            with open(auth_path, encoding="utf-8") as file:
                auth = json.load(file)
            api_key = auth.get("changyouopenai", {}).get("key", "")
        except Exception:
            pass
    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    return api_key


def bm25_tokenize(text):
    """把文本切成 BM25 用的词项。"""
    return re.findall(r"\b\w+\b", text.lower())


def flatten_messages(chat_sessions):
    """把多 session 对话拍平成消息列表。"""
    rows = []
    for session in chat_sessions:
        msgs = session if isinstance(session, list) else [session]
        for msg in msgs:
            if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
                rows.append({"role": msg["role"], "content": msg["content"]})
    return rows


def parse_json_block(text, fallback=None):
    """从模型回复中提取 JSON 对象。"""
    if fallback is None:
        fallback = {}
    if not text:
        return fallback
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return fallback
    try:
        return json.loads(match.group(0))
    except Exception:
        return fallback


def dedupe(rows):
    """按归一化文本去重并保留原顺序。"""
    seen = set()
    out = []
    for row in rows:
        if not isinstance(row, str):
            continue
        text = row.strip()
        if not text:
            continue
        key = re.sub(r"\s+", " ", text).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def safe_name(text):
    """把题型或标签转成文件名友好的文本。"""
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", text).strip("_") or "item"


# ─── 方案 5 检索库构建 ──────────────────────────────────────────────────────

def build_solution5_db(case_id, chat_sessions, max_tokens_per_chunk=400):
    """构建程序 5 使用的独立混合检索库。"""
    import chromadb

    print(f"\n⚙️ 正在为 Case {case_id} 构建【方案5：题型感知混合检索库】...")

    db_client = chromadb.PersistentClient(path=DB_ROOT)
    collection_name = f"beam_case_sol5_{case_id}"
    try:
        db_client.delete_collection(name=collection_name)
    except Exception:
        pass

    collection = db_client.create_collection(name=collection_name)
    enc = tiktoken.get_encoding("cl100k_base")
    rows = flatten_messages(chat_sessions)

    bg = ""
    for row in rows:
        if row["role"] == "user":
            bg = row["content"][:300]
            break

    docs = []
    metas = []
    ids = []
    buf = []
    total = 0
    idx = 0

    for row in rows:
        text = f"[{row['role'].upper()}]: {row['content']}\n"
        size = len(enc.encode(text))
        if total + size > max_tokens_per_chunk and buf:
            raw = "".join(buf)
            doc = (
                f"[Project Context]: {bg}...\n"
                f"[Conversation Timeline: Segment {idx}]:\n"
                f"{raw}"
            )
            docs.append(doc)
            metas.append({"chunk_index": idx, "case_id": case_id})
            ids.append(f"chunk_{idx}")
            idx += 1
            buf = [text]
            total = size
            continue
        buf.append(text)
        total += size

    if buf:
        raw = "".join(buf)
        doc = (
            f"[Project Context]: {bg}...\n"
            f"[Conversation Timeline: Segment {idx}]:\n"
            f"{raw}"
        )
        docs.append(doc)
        metas.append({"chunk_index": idx, "case_id": case_id})
        ids.append(f"chunk_{idx}")

    print(f"   [+] 生成 {len(docs)} 个时序 Chunk，正在写入 ChromaDB...")
    if docs:
        collection.add(documents=docs, metadatas=metas, ids=ids)

    print("   [+] 正在构建 BM25 稀疏索引...")
    corpus = [bm25_tokenize(doc) for doc in docs]
    bm25 = BM25Okapi(corpus) if corpus else BM25Okapi([["empty"]])
    return db_client, collection, bm25, docs, metas, ids, rows


def hybrid_rank(question, collection, bm25_index, docs, metas, doc_ids, top_k=10):
    """返回混合检索后的 chunk id 排名。"""
    if not docs:
        return []
    recall = min(max(top_k * 2, 10), len(docs))
    dense = collection.query(query_texts=[question], n_results=recall)
    dense_ids = dense["ids"][0] if dense.get("ids") else []

    toks = bm25_tokenize(question)
    scores = bm25_index.get_scores(toks)
    sparse = sorted(
        [(doc_ids[i], scores[i]) for i in range(len(doc_ids))],
        key=lambda item: item[1],
        reverse=True,
    )
    sparse_ids = [item[0] for item in sparse[:recall]]

    rank = {doc_id: 0.0 for doc_id in doc_ids}
    for pos, doc_id in enumerate(dense_ids):
        rank[doc_id] += 1.0 / (60 + pos + 1)
    for pos, doc_id in enumerate(sparse_ids):
        rank[doc_id] += 1.0 / (60 + pos + 1)
    rows = sorted(rank.items(), key=lambda item: item[1], reverse=True)
    return [item[0] for item in rows[:top_k]]


def search_evidence_sol5(question, qtype, collection, bm25_index, docs, metas, doc_ids):
    """按题型检索补充证据。"""
    if not docs:
        return []

    top_map = {
        "event_ordering": 12,
        "temporal_reasoning": 12,
        "knowledge_update": 10,
        "summarization": 10,
        "multi_session_reasoning": 12,
        "abstention": 0,
    }
    top_k = top_map.get(qtype, 8)
    if top_k <= 0:
        return []

    top_ids = hybrid_rank(question, collection, bm25_index, docs, metas, doc_ids, top_k=top_k)
    id_to_doc = dict(zip(doc_ids, docs))
    id_to_meta = dict(zip(doc_ids, metas))

    if qtype == "event_ordering":
        idx_to_id = {meta["chunk_index"]: doc_id for doc_id, meta in zip(doc_ids, metas)}
        extra = set(top_ids)
        for doc_id in list(top_ids):
            pos = id_to_meta[doc_id]["chunk_index"]
            if pos - 1 in idx_to_id:
                extra.add(idx_to_id[pos - 1])
            if pos + 1 in idx_to_id:
                extra.add(idx_to_id[pos + 1])
        top_ids = sorted(extra, key=lambda doc_id: id_to_meta[doc_id]["chunk_index"])
    else:
        top_ids = sorted(top_ids, key=lambda doc_id: id_to_meta[doc_id]["chunk_index"])

    return [id_to_doc[doc_id] for doc_id in top_ids]


# ─── 结构化记忆构建 ────────────────────────────────────────────────────────

def extract_chunk_memory(doc, seg):
    """从单个 chunk 提取结构化记忆。"""
    prompt = f"""You are building structured memory for a long conversation.
Extract a compact JSON object from the conversation segment below.

Rules:
- Use ONLY information grounded in the segment.
- Preserve exact values for dates, versions, ports, file paths, counts, numbers, and names.
- Keep events in the same order as they appear.
- Keep each list item short and specific.
- If a field has nothing useful, return an empty list or empty string.
- Reply with ONLY valid JSON.

Required JSON schema:
{{
  "summary": "<1-2 sentence summary>",
  "events": [{{"ts": "<time hint or segment>", "event": "<short event>"}}],
  "values": ["<exact value or fact>"],
  "tech": ["<technical spec or identifier>"],
  "updates": ["<old -> new update>"],
  "causal": ["<because X -> chose Y>"],
  "prefs": ["<user preference or constraint>"],
  "context": ["<user context or project context>"]
}}

Segment label: Segment {seg}
Conversation segment:
{doc}
"""
    data = parse_json_block(_judge_via_opencode(prompt), fallback={})
    if not isinstance(data, dict):
        data = {}

    summary = data.get("summary", "")
    if not isinstance(summary, str):
        summary = ""

    events = data.get("events", [])
    clean = []
    if isinstance(events, list):
        for row in events:
            if not isinstance(row, dict):
                continue
            event = str(row.get("event", "")).strip()
            if not event:
                continue
            ts = str(row.get("ts", "")).strip() or f"segment {seg}"
            clean.append({"ts": ts, "event": event})

    if not clean and summary.strip():
        clean.append({"ts": f"segment {seg}", "event": summary.strip()})

    return {
        "summary": summary.strip(),
        "events": clean,
        "values": dedupe(data.get("values", []) if isinstance(data.get("values", []), list) else []),
        "tech": dedupe(data.get("tech", []) if isinstance(data.get("tech", []), list) else []),
        "updates": dedupe(data.get("updates", []) if isinstance(data.get("updates", []), list) else []),
        "causal": dedupe(data.get("causal", []) if isinstance(data.get("causal", []), list) else []),
        "prefs": dedupe(data.get("prefs", []) if isinstance(data.get("prefs", []), list) else []),
        "context": dedupe(data.get("context", []) if isinstance(data.get("context", []), list) else []),
    }


def merge_memory(rows, msgs):
    """把多段提取结果合并为程序 5 的结构化记忆。"""
    sums = []
    events = []
    vals = []
    tech = []
    updates = []
    causal = []
    prefs = []
    ctx = []
    order = 1

    for row in rows:
        if row.get("summary"):
            sums.append(row["summary"])
        vals.extend(row.get("values", []))
        tech.extend(row.get("tech", []))
        updates.extend(row.get("updates", []))
        causal.extend(row.get("causal", []))
        prefs.extend(row.get("prefs", []))
        ctx.extend(row.get("context", []))
        for item in row.get("events", []):
            event = str(item.get("event", "")).strip()
            if not event:
                continue
            events.append({
                "order": order,
                "ts": str(item.get("ts", "")).strip() or f"event {order}",
                "event": event,
            })
            order += 1

    if msgs:
        first = next((row["content"] for row in msgs if row["role"] == "user"), "")
        if first:
            ctx.insert(0, f"初始用户目标: {first[:300]}")
        ctx.append(f"总消息数: {len(msgs)}")
        ctx.append(f"用户消息数: {sum(1 for row in msgs if row['role'] == 'user')}")
        ctx.append(f"助手消息数: {sum(1 for row in msgs if row['role'] == 'assistant')}")

    return {
        "summary": dedupe(sums),
        "events": events,
        "values": dedupe(vals),
        "tech": dedupe(tech),
        "updates": dedupe(updates),
        "causal": dedupe(causal),
        "prefs": dedupe(prefs),
        "context": dedupe(ctx),
    }


def write_jsonl(path, rows):
    """写入 JSONL 文件。"""
    with open(path, "w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_memory_md(path, mem):
    """把结构化记忆写成 memory.md。"""
    lines = [
        "# Solution 5 Memory",
        "",
        "### Narrative Summary",
    ]
    if mem["summary"]:
        lines.extend(f"- {row}" for row in mem["summary"])
    else:
        lines.append("- 暂无可用摘要。")

    lines.extend(["", "### Chronological Event Log"])
    if mem["events"]:
        lines.extend(f"{row['order']}. [{row['ts']}] {row['event']}" for row in mem["events"])
    else:
        lines.append("- 暂无可用事件。")

    sections = [
        ("Value Registry", mem["values"], "- 暂无关键值。"),
        ("Technical Specifications", mem["tech"], "- 暂无技术规格。"),
        ("Contradiction & Update Log", mem["updates"], "- 暂无更新记录。"),
        ("Causal Decisions", mem["causal"], "- 暂无因果决策。"),
        ("User Preferences & Constraints", mem["prefs"], "- 暂无用户偏好。"),
        ("User Context", mem["context"], "- 暂无用户上下文。"),
    ]

    for name, rows, empty in sections:
        lines.extend(["", f"### {name}"])
        if rows:
            lines.extend(f"- {row}" for row in rows)
        else:
            lines.append(empty)

    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines).strip() + "\n")


def build_solution5_memory(case_id, docs, metas, msgs):
    """为当前 case 生成独立的结构化记忆文件。"""
    root = Path(MEM_ROOT) / f"case_{case_id}"
    root.mkdir(parents=True, exist_ok=True)

    print(f"   [+] 正在为 Case {case_id} 生成结构化记忆文件...")
    rows = [None] * len(docs)
    with ThreadPoolExecutor(max_workers=min(MEM_WORKERS, max(1, len(docs)))) as pool:
        futs = {
            pool.submit(extract_chunk_memory, doc, meta["chunk_index"]): pos
            for pos, (doc, meta) in enumerate(zip(docs, metas))
        }
        for fut in as_completed(futs):
            pos = futs[fut]
            try:
                rows[pos] = fut.result()
            except Exception as err:
                seg = metas[pos]["chunk_index"]
                print(f"   [!] Segment {seg} 记忆抽取失败，使用降级摘要: {err}")
                rows[pos] = {
                    "summary": docs[pos][:240],
                    "events": [{"ts": f"segment {seg}", "event": docs[pos][:180]}],
                    "values": [],
                    "tech": [],
                    "updates": [],
                    "causal": [],
                    "prefs": [],
                    "context": [],
                }

    mem = merge_memory(rows, msgs)
    mem_path = root / "memory.md"
    ep_path = root / "episodes.jsonl"
    tl_path = root / "timeline.jsonl"
    raw_path = root / "memory.json"

    write_memory_md(mem_path, mem)
    write_jsonl(ep_path, mem["events"])
    write_jsonl(tl_path, [{"ts": row["ts"], "event": row["event"]} for row in mem["events"]])
    with open(raw_path, "w", encoding="utf-8") as file:
        json.dump(mem, file, indent=2, ensure_ascii=False)

    print(
        "   [+] 结构化记忆已生成："
        f"events={len(mem['events'])}, "
        f"values={len(mem['values'])}, "
        f"updates={len(mem['updates'])}"
    )
    return {
        "root": str(root),
        "memory_path": str(mem_path),
        "episodes_path": str(ep_path),
        "timeline_path": str(tl_path),
        "raw_path": str(raw_path),
        "memory": mem,
    }


def read_file_text(path):
    """读取文本文件，缺失时返回空串。"""
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as file:
        return file.read()


def read_jsonl(path):
    """读取 JSONL 文件。"""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def extract_sections(text, *names):
    """从 memory.md 中提取一个或多个 section。"""
    if not text:
        return ""

    blocks = {}
    lines = text.split("\n")
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if not line.startswith("### "):
            idx += 1
            continue
        head = line[4:].strip()
        idx += 1
        buf = []
        while idx < len(lines) and not lines[idx].startswith("### "):
            buf.append(lines[idx])
            idx += 1
        body = "\n".join(buf).strip()
        if body:
            blocks[head] = f"### {head}\n{body}"

    out = []
    for name in names:
        if name in blocks:
            out.append(blocks[name])
            continue
        want = name.lower()
        hit = [
            body
            for head, body in blocks.items()
            if head.lower().startswith(want) or want in head.lower()
        ]
        out.extend(hit)
    return "\n\n".join(dedupe(out))


def read_timeline(path):
    """读取 timeline.jsonl 并格式化。"""
    rows = read_jsonl(path)
    if not rows:
        return ""
    lines = []
    for row in rows:
        ts = row.get("ts", "?")
        event = row.get("event", "")
        if not event:
            continue
        lines.append(f"  {ts}: {event}")
    return "[Chronological Timeline]:\n" + "\n".join(lines) if lines else ""


def read_episodes(path):
    """读取 episodes.jsonl 并按顺序排序。"""
    rows = read_jsonl(path)
    rows.sort(key=lambda row: row.get("order", 0))
    return rows


# ─── 题型路由与 Prompt 组装 ────────────────────────────────────────────────

QTYPE_SECTIONS = {
    "information_extraction": ["Value Registry", "Technical Specifications"],
    "knowledge_update": ["Value Registry", "Technical Specifications", "Contradiction"],
    "temporal_reasoning": ["Chronological Event Log", "Value Registry"],
    "preference_following": ["User Preferences", "Causal Decisions"],
    "instruction_following": ["User Preferences", "Causal Decisions", "Technical Specifications"],
    "summarization": ["Narrative Summary", "Chronological Event Log", "Causal Decisions"],
    "multi_session_reasoning": ["Narrative Summary", "User Context", "Causal Decisions", "Value Registry", "User Preferences"],
    "contradiction_resolution": ["Contradiction", "Value Registry"],
    "event_ordering": [],
    "abstention": [],
}


def format_evidence(chunks):
    """把检索证据整理成文本块。"""
    if not chunks:
        return ""
    parts = []
    for idx, chunk in enumerate(chunks, 1):
        parts.append(f"[Evidence Chunk {idx}]\n{chunk}")
    return "\n\n========================\n\n".join(parts)


def build_probe_sol5(question, qtype, mem_path, ep_path, tl_path, chunks):
    """根据题型构建程序 5 的最终问答 prompt。"""
    mem_text = read_file_text(mem_path)
    episodes = read_episodes(ep_path)
    timeline = read_timeline(tl_path)
    evidence = format_evidence(chunks)
    uid = str(uuid.uuid4())[:8]

    if qtype == "event_ordering":
        seq = ""
        if episodes:
            seq = "\n".join(
                f"{row['order']}. [{row.get('ts', '?')}] {row['event']}"
                for row in episodes
                if row.get("event")
            )
        if not seq and mem_text:
            seq = extract_sections(mem_text, "Chronological Event Log", "Event Log")

        parts = [
            f"[req-{uid}] You are a highly accurate QA assistant.",
            "Answer the event-ordering question using the chronological record below.",
            "Return ONLY a numbered chronological list.",
            "Each line must contain exactly one event.",
            "Do not merge multiple events into one line.",
            'If the available memory is insufficient, reply exactly with "Insufficient information".',
        ]
        if seq:
            parts.extend(["", "[Chronological Sequence]:", seq])
        if evidence:
            parts.extend(["", "[Supplementary Evidence]:", evidence])
        parts.extend(["", "[Question]:", question])
        return "\n".join(parts)

    names = QTYPE_SECTIONS.get(qtype, [])
    ctx = extract_sections(mem_text, *names) if names else ""

    if qtype == "temporal_reasoning" and timeline:
        ctx = (ctx + "\n\n" + timeline).strip() if ctx else timeline

    if evidence:
        ctx = (ctx + "\n\n[Supplementary Evidence]\n" + evidence).strip() if ctx else "[Supplementary Evidence]\n" + evidence

    if qtype == "knowledge_update":
        guide = (
            "Highlight changes explicitly. When possible, use the pattern "
            '"OLD" -> "NEW" and explain what changed over time.'
        )
    elif qtype == "summarization":
        guide = "Provide a concise summary grounded in the structured memory and evidence."
    elif qtype == "abstention":
        guide = 'If the memory does not support the answer, reply exactly with "Insufficient information".'
    else:
        guide = 'If the context does not contain the information to answer the question, reply exactly with "Insufficient information".'

    if not ctx:
        return (
            f"[req-{uid}] You are a highly accurate QA assistant.\n"
            f"{guide}\n\n"
            f"[Question]: {question}"
        )

    return (
        f"[req-{uid}] You are a highly accurate QA assistant.\n"
        f"Please answer the question STRICTLY based on the provided [Structured Memory Context].\n"
        f"{guide}\n\n"
        f"[Structured Memory Context]:\n"
        f"{ctx}\n\n"
        f"[Question]: {question}"
    )


def answer_sol5(question, qtype, mem_info, chunks, model_id):
    """根据题型生成程序 5 的回答。"""
    _ = model_id
    prompt = build_probe_sol5(
        question,
        qtype,
        mem_info["memory_path"],
        mem_info["episodes_path"],
        mem_info["timeline_path"],
        chunks,
    )
    result = _judge_via_opencode(prompt)
    return result if result else "[empty response]"


# ─── OpenCode 辅助函数 ─────────────────────────────────────────────────────

def check_server():
    """检查 OpenCode 服务是否运行。"""
    try:
        resp = requests.get(f"{OPENCODE_URL}/session", timeout=5)
        if resp.status_code == 200:
            print("✅ OpenCode server is running")
            return True
    except requests.ConnectionError:
        pass
    print(
        """
╔══════════════════════════════════════════════════════════╗
║  ❌ OpenCode 服务器未启动                                ║
║  请先在另一个终端运行: cd opencode-dev && bun run dev    ║
╚══════════════════════════════════════════════════════════╝
"""
    )
    return False


def _judge_via_opencode(prompt):
    """通过本地 OpenCode API 发送 prompt 并返回文本。"""
    resp = requests.post(f"{OPENCODE_URL}/session", json={}, timeout=10)
    resp.raise_for_status()
    sid = resp.json()["id"]

    payload = {
        "model": {"providerID": PROVIDER_ID, "modelID": MODEL_ID},
        "parts": [{"type": "text", "text": prompt}],
    }
    resp = requests.post(f"{OPENCODE_URL}/session/{sid}/message", json=payload, timeout=120)
    resp.raise_for_status()

    try:
        data = resp.json()
        for part in data.get("parts", []):
            if part.get("type") == "text" and part.get("text", "").strip():
                return part["text"].strip()
    except Exception:
        pass

    for _ in range(3):
        time.sleep(2)
        msgs = requests.get(f"{OPENCODE_URL}/session/{sid}/message", timeout=10).json()
        for msg in reversed(msgs):
            info = msg.get("info", {})
            if info.get("role") == "assistant" and not info.get("summary"):
                for part in msg.get("parts", []):
                    if part.get("type") == "text" and part.get("text", "").strip():
                        return part["text"].strip()
    return ""


# ─── 评分系统 ──────────────────────────────────────────────────────────────

def _tokenize(text):
    """把文本转成评分用词项。"""
    return re.findall(r"\b\w+\b", text.lower())


STOPWORDS = {
    "the", "a", "an", "is", "was", "are", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "to", "of",
    "in", "for", "on", "with", "at", "by", "from", "as", "into",
    "and", "or", "but", "not", "no", "that", "this", "it", "i",
    "my", "you", "your", "we", "our", "they", "their", "me", "he",
    "she", "his", "her", "its", "us", "them", "so", "if", "then",
}


def score_keyword(model_answer, expected_answer):
    """计算关键词重叠得分。"""
    if not model_answer or not expected_answer:
        return 0.0
    text = model_answer.lower()
    gold = set(_tokenize(expected_answer)) - STOPWORDS
    if not gold:
        return 1.0 if expected_answer.lower() in text else 0.0
    hits = sum(1 for item in gold if item in text)
    return hits / len(gold)


def score_token_f1(model_answer, expected_answer):
    """计算 token F1 得分。"""
    if not model_answer or not expected_answer:
        return 0.0
    pred = set(_tokenize(model_answer)) - STOPWORDS
    gold = set(_tokenize(expected_answer)) - STOPWORDS
    if not gold:
        return 1.0 if not pred else 0.0
    if not pred:
        return 0.0
    common = pred & gold
    if not common:
        return 0.0
    prec = len(common) / len(pred)
    rec = len(common) / len(gold)
    return 2 * prec * rec / (prec + rec)


def score_llm_judge(question, model_answer, expected_answer):
    """通过本地 OpenCode API 调用 LLM 评估语义正确性。"""
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

IMPORTANT: Focus on factual correctness, not wording.

Reply with ONLY a JSON object: {{"score": <float>, "reason": "<brief reason>"}}"""

    try:
        text = _judge_via_opencode(prompt)
        if not text:
            return -1.0
        match = re.search(r'"score"\s*:\s*([0-9.]+)', text)
        if match:
            return min(1.0, max(0.0, float(match.group(1))))
        return 0.0
    except Exception as err:
        print(f"    ⚠️ LLM Judge error: {err}")
        return -1.0


def score_answer(question, model_answer, expected_answer):
    """根据全局评分开关计算综合得分。"""
    kw = score_keyword(model_answer, expected_answer) if SCORE_KEYWORD else None
    f1 = score_token_f1(model_answer, expected_answer) if SCORE_F1 else None
    judge = score_llm_judge(question, model_answer, expected_answer) if SCORE_JUDGE else None

    if judge is not None and judge < 0:
        judge = None

    rows = []
    if judge is not None:
        if judge >= 0.8:
            return {
                "keyword": round(kw, 3) if kw is not None else None,
                "token_f1": round(f1, 3) if f1 is not None else None,
                "llm_judge": round(judge, 3),
                "combined": round(judge, 3),
            }
        rows.append((judge, 0.6))
    if f1 is not None:
        rows.append((f1, 0.25 if judge is not None else 0.5))
    if kw is not None:
        rows.append((kw, 0.15 if judge is not None else 0.5))

    if rows:
        total = sum(weight for _, weight in rows)
        combined = sum(value * weight for value, weight in rows) / total
    else:
        combined = 0.0

    return {
        "keyword": round(kw, 3) if kw is not None else None,
        "token_f1": round(f1, 3) if f1 is not None else None,
        "llm_judge": round(judge, 3) if judge is not None else None,
        "combined": round(combined, 3),
    }


# ─── 数据加载 ──────────────────────────────────────────────────────────────

BEAM_DATASET = None


def _get_beam_dataset():
    """延迟加载 BEAM 数据集。"""
    global BEAM_DATASET
    if BEAM_DATASET is None:
        print("📥 Loading BEAM dataset from HuggingFace...")
        from datasets import load_dataset

        BEAM_DATASET = load_dataset("Mohammadta/BEAM")
    return BEAM_DATASET


def _parse_questions(conv):
    """解析一个 case 的评分问题。"""
    pq = conv["probing_questions"]
    if isinstance(pq, str):
        pq = ast.literal_eval(pq)
    rows = []
    fields = [
        "answer",
        "expected_answer",
        "ideal_response",
        "ideal_answer",
        "ideal_summary",
        "expected_compliance",
    ]
    for qtype, items in pq.items():
        for item in items:
            answer = ""
            for field in fields:
                if field in item and item[field]:
                    answer = str(item[field])
                    break
            if not answer and "rubric" in item and item["rubric"]:
                answer = "; ".join(item["rubric"])
            if answer:
                rows.append({
                    "type": qtype,
                    "question": item["question"],
                    "expected_answer": answer,
                })
    return rows


def load_beam(case_ids=None, split_name="100K"):
    """按 split 和 case id 加载 BEAM 数据。"""
    if case_ids is None:
        case_ids = [0]
    ds = _get_beam_dataset()
    split = ds[split_name]
    rows = []
    for cid in case_ids:
        if cid >= len(split):
            print(f"⚠️ Case {cid} out of range, skipping")
            continue
        conv = split[cid]
        rows.append((cid, conv["chat"], _parse_questions(conv)))
        print(f"  📦 Case {cid}: {len(conv['chat'])} sessions, {len(rows[-1][2])} scoreable questions")
    return rows


def _score_one(idx, total, q, answer):
    """评分单条 probe 回答。"""
    no_score = not (SCORE_F1 or SCORE_JUDGE or SCORE_KEYWORD)
    scores = (
        {"keyword": None, "token_f1": None, "llm_judge": None, "combined": None}
        if no_score else score_answer(q["question"], answer, q["expected_answer"])
    )
    row = {
        "type": q["type"],
        "question": q["question"],
        "expected": q["expected_answer"],
        "model_answer": answer or "",
        "scores": scores,
    }
    print(f"  [{idx + 1}/{total}] ({q['type']}) {q['question'][:80]}...")
    if no_score:
        print("  ⏭️  Scoring disabled")
    else:
        score = scores["combined"]
        mark = "✅" if score >= 0.5 else "⚠️" if score >= 0.3 else "❌"
        parts = []
        if scores["token_f1"] is not None:
            parts.append(f"F1={scores['token_f1']:.2f}")
        if scores["keyword"] is not None:
            parts.append(f"KW={scores['keyword']:.2f}")
        if scores["llm_judge"] is not None:
            parts.append(f"Judge={scores['llm_judge']:.2f}")
        print(f"  {mark} Combined={score:.2f}  ({' '.join(parts)})")
    print(f"     Expected: {q['expected_answer'][:100]}")
    print(f"     Got:      {answer[:100] if answer else '[empty]'}")
    return idx, row


def print_results_table(all_results, save_path=None):
    """打印并可选保存汇总表。"""
    before = []
    after = []
    for row in all_results:
        before.extend(row["before_compaction"]["results"])
        after.extend(row["after_compaction"]["results"])

    def avg_score(rows, metric="combined"):
        vals = [row["scores"][metric] for row in rows if row["scores"].get(metric) is not None]
        return sum(vals) / len(vals) if vals else 0

    metrics = [("Combined Score", "combined")]
    if SCORE_F1:
        metrics.append(("Token F1", "token_f1"))
    if SCORE_JUDGE:
        metrics.append(("LLM Judge", "llm_judge"))
    if SCORE_KEYWORD:
        metrics.append(("Keyword Overlap", "keyword"))

    enabled = [name for name, on in [("F1", SCORE_F1), ("Judge", SCORE_JUDGE), ("Keyword", SCORE_KEYWORD)] if on]
    label = ", ".join(enabled) if enabled else "ALL DISABLED"
    lines = []

    def log(text=""):
        print(text)
        lines.append(text)

    log(f"\n{'=' * 76}")
    log(f"📊 RESULTS — Solution 5 Type-Aware RAG Benchmark ({len(all_results)} cases)")
    log(f"    Scoring: {label}")
    log(f"{'=' * 76}")

    for metric_name, metric_key in metrics:
        avg_b = avg_score(before, metric_key)
        avg_a = avg_score(after, metric_key)
        log(f"\nMetric: {metric_name:<30} {'Before':<15} {'After(RAG)':<15} {'Delta':<8}")
        log("-" * 76)
        log(f"{'  Overall':<35} {avg_b:>10.1%} {avg_a:>14.1%} {avg_a - avg_b:>+8.1%}")

        types = sorted(set(row["type"] for row in after))
        for qtype in types:
            b_vals = [row["scores"][metric_key] for row in before if row["type"] == qtype and row["scores"].get(metric_key) is not None]
            a_vals = [row["scores"][metric_key] for row in after if row["type"] == qtype and row["scores"].get(metric_key) is not None]
            avg1 = sum(b_vals) / len(b_vals) if b_vals else 0
            avg2 = sum(a_vals) / len(a_vals) if a_vals else 0
            log(f"    {qtype:<31} {avg1:>10.1%} {avg2:>14.1%} {avg2 - avg1:>+8.1%}")

    if save_path:
        try:
            with open(save_path, "w", encoding="utf-8") as file:
                file.write("\n".join(lines) + "\n")
            print(f"\n📄 表格已保存至: {save_path}")
        except Exception as err:
            print(f"\n❌ 保存表格文件失败: {err}")


def run_single_case_sol5(case_id, chat_sessions, questions, model_id):
    """运行单个 case 的程序 5 主流程。"""
    print(f"\n{'=' * 60}")
    print(f"🔬 Sol 5 RAG Case {case_id}: {len(chat_sessions)} sessions, {len(questions)} questions")
    print(f"{'=' * 60}")

    db_client, collection, bm25, docs, metas, doc_ids, msgs = build_solution5_db(case_id, chat_sessions)
    _ = db_client
    mem_info = build_solution5_memory(case_id, docs, metas, msgs)

    print(f"\n{'=' * 60}")
    print("📋 正在使用【题型感知结构化记忆 + 混合证据检索】回答问题...")
    print(f"{'=' * 60}")

    answers = []
    for idx, q in enumerate(questions):
        print(f"\n  [{idx + 1}/{len(questions)}] ({q['type']}) {q['question'][:80]}...")
        chunks = search_evidence_sol5(q["question"], q["type"], collection, bm25, docs, metas, doc_ids)
        answer = answer_sol5(q["question"], q["type"], mem_info, chunks, model_id)
        answers.append(answer)
        print(f"     Got(Sol5): {answer[:120] if answer else '[empty]'}")
        time.sleep(2)

    print(f"\n  ⚡ Scoring {len(questions)} answers in parallel...")
    rows = [None] * len(questions)
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        futs = {
            pool.submit(_score_one, idx, len(questions), q, answers[idx]): idx
            for idx, q in enumerate(questions)
        }
        for fut in as_completed(futs):
            idx, row = fut.result()
            rows[idx] = row

    vals = [row["scores"]["combined"] for row in rows if row["scores"].get("combined") is not None]
    avg = sum(vals) / len(vals) if vals else 0
    print(f"\n  📊 Case {case_id} Solution 5 Summary: Avg Score = {avg:.1%}")

    return {
        "case_id": case_id,
        "session_id": f"rag_case_{case_id}",
        "messages_fed": 0,
        "before_compaction": {"avg_combined": 0, "results": []},
        "after_compaction": {"avg_combined": avg, "results": rows},
    }


def main():
    """解析参数并执行程序 5。"""
    global PROVIDER_ID, MODEL_ID, SCORE_F1, SCORE_JUDGE, SCORE_KEYWORD

    parser = argparse.ArgumentParser(description="BEAM Benchmark with Sol 5: Type-Aware Structured Memory RAG")
    parser.add_argument("--cases", default="0", help="Case IDs")
    parser.add_argument("--split", default="100K", choices=["100K", "500K", "1M"])
    parser.add_argument("--provider", default=PROVIDER_ID)
    parser.add_argument("--model", default=MODEL_ID)
    parser.add_argument("--output", default="/tmp/beam_results")
    parser.add_argument("--no-f1", action="store_true", help="关闭 Token F1 评分")
    parser.add_argument("--no-judge", action="store_true", help="关闭 LLM Judge 评分")
    parser.add_argument("--no-keyword", action="store_true", help="关闭 Keyword Overlap 评分")
    parser.add_argument("--no-score", action="store_true", help="关闭全部评分")
    args = parser.parse_args()

    PROVIDER_ID = args.provider
    MODEL_ID = args.model

    if args.no_score:
        SCORE_F1 = SCORE_JUDGE = SCORE_KEYWORD = False
    else:
        if args.no_f1:
            SCORE_F1 = False
        if args.no_judge:
            SCORE_JUDGE = False
        if args.no_keyword:
            SCORE_KEYWORD = False

    enabled = [name for name, on in [("F1", SCORE_F1), ("Judge", SCORE_JUDGE), ("Keyword", SCORE_KEYWORD)] if on]
    print(f"📏 Scoring: {', '.join(enabled) if enabled else 'ALL DISABLED'}")

    max_cases = 20 if args.split == "100K" else 35
    case_ids = list(range(max_cases)) if args.cases == "all" else [int(item.strip()) for item in args.cases.split(",")]

    if not check_server():
        sys.exit(1)

    print(f"🚀 Sol 5 RAG Benchmark: {args.split} split, {len(case_ids)} cases")
    print(f"   Model: {PROVIDER_ID}/{MODEL_ID}")

    cases = load_beam(case_ids, split_name=args.split)
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(DB_ROOT, exist_ok=True)
    os.makedirs(MEM_ROOT, exist_ok=True)
    checkpoint_path = os.path.join(args.output, f"checkpoint_Scheme5_{safe_name(PROVIDER_ID)}_{safe_name(MODEL_ID)}_{args.split}.json")

    done = {}
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path, encoding="utf-8") as file:
            done = {row["case_id"]: row for row in json.load(file)}

    all_results = list(done.values())
    for case_id, chat, questions in cases:
        if case_id in done:
            continue
        try:
            row = run_single_case_sol5(case_id, chat, questions, MODEL_ID)
            if row is None:
                continue
            all_results.append(row)
            with open(checkpoint_path, "w", encoding="utf-8") as file:
                json.dump(all_results, file, indent=2, ensure_ascii=False)
            print(f"  💾 Checkpoint saved ({len(all_results)}/{len(cases)} cases)")
        except Exception as err:
            print(f"\n  ❌ Case {case_id} failed: {err}")
            continue

    print_results_table(all_results)

    from datetime import datetime

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    cases_text = args.cases.replace(",", "_")
    final_json_path = os.path.join(args.output, f"results_Scheme5_{safe_name(PROVIDER_ID)}_{safe_name(MODEL_ID)}_{args.split}_{ts}.json")
    table_txt_path = os.path.join(args.output, f"table_Scheme5_{args.split}_case{cases_text}.txt")

    print_results_table(all_results, save_path=table_txt_path)

    label = ", ".join(enabled) if enabled else "ALL DISABLED"
    output = {
        "test_type": f"Type-Aware Structured Memory RAG Benchmark ({args.split})",
        "scoring_method": label,
        "model": f"{PROVIDER_ID}/{MODEL_ID}",
        "split": args.split,
        "cases_tested": len(all_results),
        "results": all_results,
    }
    with open(final_json_path, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, ensure_ascii=False)
    print(f"📁 JSON 源文件已保存至: {final_json_path}")


if __name__ == "__main__":
    main()
