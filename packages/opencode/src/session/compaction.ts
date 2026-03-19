import { BusEvent } from "@/bus/bus-event"
import { Bus } from "@/bus"
import { Session } from "."
import { Identifier } from "../id/id"
import { Instance } from "../project/instance"
import { Provider } from "../provider/provider"
import { MessageV2 } from "./message-v2"
import z from "zod"
import { Token } from "../util/token"
import { Log } from "../util/log"
import { SessionProcessor } from "./processor"
import { fn } from "@/util/fn"
import { Agent } from "@/agent/agent"
import { Plugin } from "@/plugin"
import { Config } from "@/config/config"
import { ProviderTransform } from "@/provider/transform"
import path from "path"
import { mkdir, appendFile, writeFile } from "fs/promises"

export namespace SessionCompaction {
  const log = Log.create({ service: "session.compaction" })

  export const Event = {
    Compacted: BusEvent.define(
      "session.compacted",
      z.object({
        sessionID: z.string(),
      }),
    ),
  }

  const COMPACTION_BUFFER = 20_000

  const PK_MARKER = "## Persistent Knowledge"
  const SK_MARKER = "## Knowledge Gap Analysis"

  function pkPath(): string {
    return path.join(Instance.worktree, ".opencode", "memory.md")
  }

  function skPath(): string {
    return path.join(Instance.worktree, ".opencode", "knowledge.jsonl")
  }

  function eventsPath(): string {
    return path.join(Instance.worktree, ".opencode", "events.jsonl")
  }

  function contradictionsPath(): string {
    return path.join(Instance.worktree, ".opencode", "contradictions.jsonl")
  }

  async function readPK(): Promise<string | null> {
    try {
      const file = Bun.file(pkPath())
      if (!(await file.exists())) return null
      const text = await file.text()
      return text.trim() || null
    } catch {
      return null
    }
  }

  // --- Append-only PK merge: preserve critical entries across compaction rounds ---

  const EVENT_CAP = 200 // max event log entries to keep

  /** Parse a PK section into {header → lines[]} map */
  function parseSections(text: string): Map<string, string[]> {
    const result = new Map<string, string[]>()
    let current = "__preamble__"
    result.set(current, [])
    for (const line of text.split("\n")) {
      if (line.startsWith("### ")) {
        current = line.slice(4).trim()
        result.set(current, [])
      } else {
        result.get(current)!.push(line)
      }
    }
    return result
  }

  /** Extract key from a "- key: value" or "- key text" line */
  function entryKey(line: string): string | null {
    const m = line.match(/^\s*-\s*(.+)/)
    if (!m) return null
    const raw = m[1].trim()
    // For "key: value" format, use key
    const colon = raw.indexOf(":")
    if (colon > 0 && colon < 60) return raw.slice(0, colon).trim().toLowerCase()
    // For plain text entries, use first 60 chars as key
    return raw.slice(0, 60).toLowerCase()
  }

  /** Merge list entries: old entries preserved, new entries added/updated by key */
  function mergeEntries(old: string[], fresh: string[]): string[] {
    const merged = new Map<string, string>()
    // Load old entries first
    for (const line of old) {
      const key = entryKey(line)
      if (key) merged.set(key, line)
    }
    // New entries update or add
    for (const line of fresh) {
      const key = entryKey(line)
      if (key) merged.set(key, line)
    }
    return [...merged.values()]
  }

  /** Merge event log: keep numbered events, dedup, cap at EVENT_CAP */
  function mergeEvents(old: string[], fresh: string[]): string[] {
    const events = new Map<number, string>()
    const re = /^\s*(\d+)\.\s/
    for (const line of [...old, ...fresh]) {
      const m = line.match(re)
      if (m) events.set(parseInt(m[1]), line)
    }
    const sorted = [...events.entries()].sort((a, b) => a[0] - b[0])
    // Keep last EVENT_CAP entries
    return sorted.slice(-EVENT_CAP).map(e => e[1])
  }

  // Sections where entries are append-only (never lose old data)
  const MERGE_SECTIONS = new Set([
    "Value Registry (MOST CRITICAL — exact values only)",
    "Technical Specifications",
    "User Preferences & Constraints",
    "Contradiction & Update Log",
    "Causal Decisions",
  ])

  function mergePK(existing: string, fresh: string): string {
    const oldSections = parseSections(existing)
    const newSections = parseSections(fresh)

    // Start with the fresh PK structure
    const result: string[] = []
    const handled = new Set<string>()

    // Use the fresh PK's preamble (## header)
    const preamble = newSections.get("__preamble__") ?? oldSections.get("__preamble__") ?? []
    result.push(...preamble)

    // Process sections from the fresh PK first, then any old-only sections
    const allHeaders = new Set([...newSections.keys(), ...oldSections.keys()])
    allHeaders.delete("__preamble__")

    for (const header of allHeaders) {
      const oldLines = (oldSections.get(header) ?? []).filter(l => l.trim())
      const newLines = (newSections.get(header) ?? []).filter(l => l.trim())
      handled.add(header)

      result.push(`### ${header}`)

      if (header.includes("Chronological Event Log") || header.includes("Event Log")) {
        // Event log: merge and cap
        const merged = mergeEvents(oldLines, newLines)
        result.push(...merged)
      } else if ([...MERGE_SECTIONS].some(s => header.includes(s) || s.includes(header))) {
        // Critical sections: append-only merge
        const merged = mergeEntries(oldLines, newLines)
        result.push(...merged)
      } else {
        // Other sections: use fresh if available, otherwise keep old
        result.push(...(newLines.length > 0 ? newLines : oldLines))
      }
      result.push("")
    }

    return result.join("\n").trim()
  }

  async function writePK(pk: string): Promise<void> {
    if (!pk.trim()) return
    try {
      const existing = await readPK()
      if (existing) {
        const merged = mergePK(existing, pk)
        await Bun.write(pkPath(), merged + "\n")
        log.info("merged PK", {
          old: existing.length,
          fresh: pk.length,
          merged: merged.length,
        })
      } else {
        await Bun.write(pkPath(), pk + "\n")
        log.info("wrote PK to file", { length: pk.length })
      }
    } catch (err) {
      log.error("failed to write PK", { error: err })
    }
  }

  async function extractPKFromSummary(summary: string): Promise<void> {
    const idx = summary.indexOf(PK_MARKER)
    if (idx >= 0) {
      await writePK(summary.slice(idx).trim())
      return
    }
    // No PK section in output — existing memory.md is preserved
    log.info("no PK marker in compaction output, keeping existing file")
  }

  interface SKItem {
    type: string
    content: string
    session?: string
    turn?: string
    order?: string
    supersedes?: string
    ts: string
  }

  async function writeSK(items: SKItem[]): Promise<void> {
    if (!items.length) return
    try {
      const dir = path.dirname(skPath())
      const { mkdir } = await import("fs/promises")
      await mkdir(dir, { recursive: true })
      const lines = items.map(i => JSON.stringify(i)).join("\n") + "\n"
      const file = Bun.file(skPath())
      const existing = (await file.exists()) ? await file.text() : ""
      await Bun.write(skPath(), existing + lines)
      log.info("wrote SK items", { count: items.length })
    } catch (err) {
      log.error("failed to write SK", { error: err })
    }
  }

  async function readSK(): Promise<SKItem[]> {
    try {
      const file = Bun.file(skPath())
      if (!(await file.exists())) return []
      const text = await file.text()
      return text.trim().split("\n").filter(Boolean).map(l => JSON.parse(l))
    } catch {
      return []
    }
  }

  // --- Theme-Indexed SK: cluster items by topic keywords, expand only relevant themes ---

  const STOPWORDS = new Set([
    "the","a","an","is","are","was","were","be","been","being","have","has","had","do","does","did",
    "will","would","could","should","may","might","shall","can","need","must","to","of","in","for",
    "on","with","at","by","from","as","into","through","during","before","after","above","below",
    "between","out","off","over","under","again","further","then","once","and","but","or","nor",
    "not","no","so","if","that","this","these","those","it","its","i","my","me","we","our","you",
    "your","he","his","she","her","they","their","them","what","which","who","whom","how","when",
    "where","why","all","each","every","both","few","more","most","other","some","such","only",
    "same","than","too","very","just","about","also","here","there","up","new","set","get","use",
    "used","using","make","made","like","still","try","see","well","way","even","because","any",
    "work","first","also","take","come","know","since","much","own","now","old","one","two","per",
  ])

  function keywords(text: string): string[] {
    return text
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, " ")
      .split(/\s+/)
      .filter(w => w.length > 2 && !STOPWORDS.has(w))
  }

  interface Theme {
    title: string
    words: string[]
    items: SKItem[]
  }

  function clusterSK(items: SKItem[]): Theme[] {
    if (items.length === 0) return []
    // extract keywords per item
    const tagged = items.map(item => ({
      item,
      words: keywords(item.content).slice(0, 5),
    }))
    // group items by their top keyword (simple 1-pass clustering)
    const groups = new Map<string, { words: Set<string>; items: SKItem[] }>()
    for (const { item, words } of tagged) {
      if (words.length === 0) continue
      // find existing group with most keyword overlap
      let best = ""
      let overlap = 0
      for (const [key, group] of groups) {
        const shared = words.filter(w => group.words.has(w)).length
        if (shared > overlap) {
          overlap = shared
          best = key
        }
      }
      if (overlap >= 2 && best) {
        const g = groups.get(best)!
        for (const w of words) g.words.add(w)
        g.items.push(item)
      } else {
        // new group
        const key = words.slice(0, 2).join("_")
        const existing = groups.get(key)
        if (existing) {
          for (const w of words) existing.words.add(w)
          existing.items.push(item)
        } else {
          groups.set(key, { words: new Set(words), items: [item] })
        }
      }
    }
    // convert to Theme[] with readable titles
    const themes: Theme[] = []
    for (const [key, group] of groups) {
      // title = top 3 most common words across all items in this group
      const freq = new Map<string, number>()
      for (const w of group.words) freq.set(w, (freq.get(w) ?? 0) + 1)
      const top = [...freq.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(e => e[0])
      themes.push({
        title: top.map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" / "),
        words: [...group.words],
        items: group.items,
      })
    }
    return themes.sort((a, b) => b.items.length - a.items.length)
  }



  async function writeThemes(items: SKItem[]): Promise<void> {
    if (items.length === 0) return
    const themes = clusterSK(items)
    const dir = path.join(Instance.worktree, ".opencode", "knowledge")
    await mkdir(dir, { recursive: true })
    const index = themes.map((t, i) =>
      `${i + 1}. ${t.title} (${t.items.length} items)`
    ).join("\n")
    await Bun.write(path.join(dir, "index.md"), index)
    for (const t of themes) {
      const slug = t.title.replace(/[^a-z0-9]/gi, "_").toLowerCase()
      const body = t.items.map(i =>
        `- [${i.type.toUpperCase()}] ${i.content}${i.order ? ` | order:${i.order}` : ""}${i.supersedes ? ` | supersedes:${i.supersedes}` : ""}`
      ).join("\n")
      await Bun.write(path.join(dir, `${slug}.md`), `# ${t.title}\n\n${body}\n`)
    }
    log.info("wrote themes", { themes: themes.length, items: items.length })
  }



  function formatExpanded(themes: Theme[]): string {
    return themes.map(t => {
      const header = `### ${t.title}`
      const body = t.items
        .map(i => `- [${i.type.toUpperCase()}] ${i.content}${i.order ? ` | order:${i.order}` : ""}${i.supersedes ? ` | supersedes:${i.supersedes}` : ""}`)
        .join("\n")
      return `${header}\n${body}`
    }).join("\n\n")
  }

  function parseSKFromSummary(summary: string): SKItem[] {
    const idx = summary.indexOf(SK_MARKER)
    if (idx < 0) return []
    // find next ## heading or end of string
    const rest = summary.slice(idx + SK_MARKER.length)
    const end = rest.search(/^## /m)
    const block = end >= 0 ? rest.slice(0, end) : rest
    const items: SKItem[] = []
    const ts = new Date().toISOString().split("T")[0]
    for (const line of block.split("\n")) {
      const m = line.match(/^\s*-\s*\[(\w+)\]\s*(.+)/)
      if (!m) continue
      const tag = m[1].toLowerCase()
      const raw = m[2].trim()
      const item: SKItem = { type: tag, content: raw, ts }
      // parse optional metadata: | key:value
      const parts = raw.split("|")
      if (parts.length > 1) {
        item.content = parts[0].trim()
        for (const p of parts.slice(1)) {
          const kv = p.trim().split(":")
          if (kv.length >= 2) {
            const k = kv[0].trim().toLowerCase()
            const v = kv.slice(1).join(":").trim()
            if (k === "session") item.session = v
            if (k === "turn") item.turn = v
            if (k === "order") item.order = v
            if (k === "supersedes") item.supersedes = v
          }
        }
      }
      items.push(item)
    }
    return items
  }

  async function extractSKFromSummary(summary: string): Promise<void> {
    const items = parseSKFromSummary(summary)
    if (items.length > 0) {
      await writeSK(items)
    } else {
      log.info("no SK marker in compaction output")
    }
  }

  // --- V16: Append-only event ledger (immutable, never rewritten) ---

  async function extractEventsFromPK(pk: string): Promise<void> {
    const re = /###\s*(?:Chronological\s+)?Event\s+Log[^\n]*\n([\s\S]*?)(?=\n###|\n##|$)/i
    const m = pk.match(re)
    if (!m) return
    const events: Array<{seq: number; event: string; ts?: string}> = []
    for (const line of m[1].split("\n")) {
      const em = line.match(/^\s*(?:-\s*)?(\d+)[.)]\s*(?:\[([^\]]+)\]\s*)?(.+)/)
      if (em) events.push({ seq: parseInt(em[1]), ts: em[2] || undefined, event: em[3].trim() })
    }
    if (events.length === 0) return
    const file = Bun.file(eventsPath())
    const existing = new Set<string>()
    const prev = (await file.exists()) ? await file.text() : ""
    for (const line of prev.split("\n").filter(l => l.trim())) {
      try { existing.add(JSON.parse(line).event) } catch {}
    }
    const fresh = events.filter(e => !existing.has(e.event)).map(e => JSON.stringify(e))
    if (fresh.length > 0) {
      await Bun.write(eventsPath(), prev + fresh.join("\n") + "\n")
      log.info("appended events", { added: fresh.length, total: existing.size + fresh.length })
    }
  }

  // --- V16: Append-only contradiction pairs (immutable) ---

  async function extractContradictionsFromPK(pk: string): Promise<void> {
    const re = /###\s*Contradiction[^\n]*\n([\s\S]*?)(?=\n###|\n##|$)/i
    const m = pk.match(re)
    if (!m) return
    const pairs: Array<{topic: string; said: string; then: string}> = []
    for (const line of m[1].split("\n")) {
      // Match: "X" → "Y" or stated X → Y patterns
      const cm = line.match(/["\u201c]([^"\u201d]+)["\u201d].*(?:\u2192|->|changed to|corrected to|clarified to).*["\u201c]([^"\u201d]+)["\u201d]/i)
      if (cm) {
        pairs.push({ topic: cm[1].split(/\s+/).slice(0, 3).join("_").toLowerCase(), said: cm[1].trim(), then: cm[2].trim() })
        continue
      }
      const cm2 = line.match(/(?:said|stated|mentioned)\s+(.+?)\s*(?:\u2192|->|vs|versus)\s*(.+?)(?:\s*\(|$)/i)
      if (cm2) pairs.push({ topic: cm2[1].split(/\s+/).slice(0, 3).join("_").toLowerCase(), said: cm2[1].trim(), then: cm2[2].trim() })
    }
    if (pairs.length === 0) return
    const file = Bun.file(contradictionsPath())
    const existing = new Set<string>()
    const prev = (await file.exists()) ? await file.text() : ""
    for (const line of prev.split("\n").filter(l => l.trim())) {
      try { const c = JSON.parse(line); existing.add(c.said + "|" + c.then) } catch {}
    }
    const fresh = pairs.filter(p => !existing.has(p.said + "|" + p.then)).map(p => JSON.stringify(p))
    if (fresh.length > 0) {
      await Bun.write(contradictionsPath(), prev + fresh.join("\n") + "\n")
      log.info("appended contradictions", { added: fresh.length, total: existing.size + fresh.length })
    }
  }

  function extractPK(msgs: MessageV2.WithParts[]): string | null {
    for (let i = msgs.length - 1; i >= 0; i--) {
      const msg = msgs[i]
      if (msg.info.role !== "assistant") continue
      if (!(msg.info as MessageV2.Assistant).summary) continue
      const text = msg.parts
        .filter((p): p is MessageV2.TextPart => p.type === "text")
        .map((p) => p.text)
        .join("")
      const idx = text.indexOf(PK_MARKER)
      if (idx >= 0) return text.slice(idx)
    }
    return null
  }

  export async function isOverflow(input: { tokens: MessageV2.Assistant["tokens"]; model: Provider.Model }) {
    const config = await Config.get()
    if (config.compaction?.auto === false) return false
    const context = input.model.limit.context
    if (context === 0) return false

    const count =
      input.tokens.total ||
      input.tokens.input + input.tokens.output + input.tokens.cache.read + input.tokens.cache.write

    const reserved =
      config.compaction?.reserved ?? Math.min(COMPACTION_BUFFER, ProviderTransform.maxOutputTokens(input.model))
    const usable = input.model.limit.input
      ? input.model.limit.input - reserved
      : context - ProviderTransform.maxOutputTokens(input.model)
    return count >= usable
  }

  export const PRUNE_MINIMUM = 20_000
  export const PRUNE_PROTECT = 40_000

  const PRUNE_PROTECTED_TOOLS = ["skill"]

  // V17: Archive tool output to .opencode/archive/ before pruning
  async function archivePart(part: MessageV2.ToolPart) {
    try {
      const dir = path.join(Instance.worktree, ".opencode", "archive")
      await mkdir(dir, { recursive: true })
      const entry = {
        id: part.id,
        tool: part.tool,
        ts: part.state.status === "completed" ? part.state.time.start : Date.now(),
        input: JSON.stringify(part.state.status === "completed" ? part.state.input : {}).slice(0, 300),
        output: part.state.status === "completed" ? part.state.output : "",
      }
      await writeFile(path.join(dir, `${part.id}.json`), JSON.stringify(entry))
      const idx = { id: part.id, tool: part.tool, ts: entry.ts, chars: entry.output?.length ?? 0 }
      await appendFile(path.join(dir, "index.jsonl"), JSON.stringify(idx) + "\n")
    } catch (e) {
      log.info("archive failed", { id: part.id, error: e })
    }
  }

  // goes backwards through parts until there are 40_000 tokens worth of tool
  // calls. then erases output of previous tool calls. idea is to throw away old
  // tool calls that are no longer relevant.
  export async function prune(input: { sessionID: string }) {
    const config = await Config.get()
    if (config.compaction?.prune === false) return
    log.info("pruning")
    const msgs = await Session.messages({ sessionID: input.sessionID })
    let total = 0
    let pruned = 0
    const toPrune = []
    let turns = 0

    loop: for (let msgIndex = msgs.length - 1; msgIndex >= 0; msgIndex--) {
      const msg = msgs[msgIndex]
      if (msg.info.role === "user") turns++
      if (turns < 2) continue
      if (msg.info.role === "assistant" && msg.info.summary) break loop
      for (let partIndex = msg.parts.length - 1; partIndex >= 0; partIndex--) {
        const part = msg.parts[partIndex]
        if (part.type === "tool")
          if (part.state.status === "completed") {
            if (PRUNE_PROTECTED_TOOLS.includes(part.tool)) continue

            if (part.state.time.compacted) break loop
            const estimate = Token.estimate(part.state.output)
            total += estimate
            if (total > PRUNE_PROTECT) {
              pruned += estimate
              toPrune.push(part)
            }
          }
      }
    }
    log.info("found", { pruned, total })
    if (pruned > PRUNE_MINIMUM) {
      for (const part of toPrune) {
        if (part.state.status === "completed") {
          await archivePart(part)
          part.state.time.compacted = Date.now()
          await Session.updatePart(part)
        }
      }
      log.info("pruned", { count: toPrune.length, archived: true })
    }
  }

  export async function process(input: {
    parentID: string
    messages: MessageV2.WithParts[]
    sessionID: string
    abort: AbortSignal
    auto: boolean
    overflow?: boolean
  }) {
    // V17: prune before LLM compaction so LLM processes smaller context
    await prune({ sessionID: input.sessionID })
    const userMessage = input.messages.findLast((m) => m.info.id === input.parentID)!.info as MessageV2.User

    let messages = input.messages
    let replay: MessageV2.WithParts | undefined
    if (input.overflow) {
      const idx = input.messages.findIndex((m) => m.info.id === input.parentID)
      for (let i = idx - 1; i >= 0; i--) {
        const msg = input.messages[i]
        if (msg.info.role === "user" && !msg.parts.some((p) => p.type === "compaction")) {
          replay = msg
          messages = input.messages.slice(0, i)
          break
        }
      }
      const hasContent =
        replay && messages.some((m) => m.info.role === "user" && !m.parts.some((p) => p.type === "compaction"))
      if (!hasContent) {
        replay = undefined
        messages = input.messages
      }
    }

    const agent = await Agent.get("compaction")
    const model = agent.model
      ? await Provider.getModel(agent.model.providerID, agent.model.modelID)
      : await Provider.getModel(userMessage.model.providerID, userMessage.model.modelID)
    const msg = (await Session.updateMessage({
      id: Identifier.ascending("message"),
      role: "assistant",
      parentID: input.parentID,
      sessionID: input.sessionID,
      mode: "compaction",
      agent: "compaction",
      variant: userMessage.variant,
      summary: true,
      path: {
        cwd: Instance.directory,
        root: Instance.worktree,
      },
      cost: 0,
      tokens: {
        output: 0,
        input: 0,
        reasoning: 0,
        cache: { read: 0, write: 0 },
      },
      modelID: model.id,
      providerID: model.providerID,
      time: {
        created: Date.now(),
      },
    })) as MessageV2.Assistant
    const processor = SessionProcessor.create({
      assistantMessage: msg,
      sessionID: input.sessionID,
      model,
      abort: input.abort,
    })
    // Allow plugins to inject context or replace compaction prompt
    const compacting = await Plugin.trigger(
      "experimental.session.compacting",
      { sessionID: input.sessionID },
      { context: [], prompt: undefined },
    )
    const defaultPrompt = `Provide a detailed prompt for continuing our conversation above.
Focus on information that would be helpful for continuing the conversation, including what we did, what we're doing, which files we're working on, and what we're going to do next.
The summary that you construct will be used so that another agent can read it and continue the work.

CRITICAL PRESERVATION RULES:
- Preserve ALL numbers, metrics, and measurements EXACTLY (e.g., "45ms", "128KB", "port 3000")
- Preserve ALL dates, deadlines, and time durations EXACTLY (e.g., "March 15, 2024", "14 days", "sprint 3")
- Preserve ALL version numbers and configurations EXACTLY (e.g., "v4.7.0", "SDK 3.1.0", "Python 3.12")
- Preserve ALL identifiers EXACTLY: file paths, UUIDs, API endpoints, error codes, package names
- Preserve ALL user-stated preferences and constraints VERBATIM (e.g., "prefers functional style", "must use PostgreSQL")
- When information was UPDATED during the conversation, record BOTH old and new values (e.g., "deadline changed from March 20 to April 5")
- NEVER paraphrase numbers, approximate dates, or reword technical identifiers

When constructing the summary, try to stick to this template:
---
## Goal

[What goal(s) is the user trying to accomplish?]

## Instructions

- [What important instructions did the user give you that are relevant]
- [If there is a plan or spec, include information about it so next agent can continue using it]

## Key Facts & Values

- [List ALL specific numbers, dates, configurations, and metrics mentioned in the conversation]
- [List ALL version numbers and technical specifications]
- [List any values that were UPDATED, showing: "X changed from OLD to NEW"]
- [List user preferences and constraints verbatim]

## Discoveries

[What notable things were learned during this conversation that would be useful for the next agent to know when continuing the work]

## Accomplished

[What work has been completed, what work is still in progress, and what work is left?]

## Relevant files / directories

[Construct a structured list of relevant files that have been read, edited, or created that pertain to the task at hand. If all the files in a directory are relevant, include the path to the directory.]

## Persistent Knowledge (CRITICAL — accumulates across rounds, never discard)

Below is a structured knowledge store. Extract ALL of the following from the conversation and MERGE with any existing persistent knowledge provided. Do NOT include user biographical info (name, location, occupation).

### QUALITY FILTER — apply to EVERY entry below:
Before adding any item, it must pass ALL 4 tests:
1. **Persistence**: Will this still be true/relevant after 6 months?
2. **Specificity**: Does it contain concrete, searchable information (names, numbers, paths, versions)?
3. **Utility**: Can this help predict or serve future user needs?
4. **Independence**: Can it be understood WITHOUT the original conversation context?
Skip entries that fail ANY test. Prefer fewer HIGH-VALUE entries over many vague ones.

### Value Registry (MOST CRITICAL — exact values only)
For EVERY specific number, metric, measurement, version, config value, date, or deadline mentioned:
- key: EXACT value (e.g., "inference_time: 45ms/frame", "python_version: 3.10", "deadline: March 15, 2024")
- If a value was UPDATED during conversation: key: NEW_VALUE (was: OLD_VALUE)
- Include ALL of: ports, URLs, file paths, error codes, batch sizes, thresholds, latencies, dimensions, counts
- NEVER approximate — "~45ms" must stay "~45ms", "45.2ms" must stay "45.2ms"

### Chronological Event Log (preserve temporal order — CRITICAL for event_ordering)
Number each event sequentially. Include ALL significant actions, decisions, and milestones:
1. [date/context if known] First thing that happened
2. [date/context if known] Second thing that happened
... continue numbering ...
- Events include: deployments, config changes, bug discoveries, decisions made, features added
- NEVER reorder or merge events — append new events at the end with increasing numbers
- When updating: keep old events, add new ones with next number
- CRITICAL temporal preservation:
  - Record the EXACT timestamp or relative position for each event ("before X", "after Y", "during Z")
  - Record causal dependencies ("X happened because Y completed first")
  - Use sequence markers ("first", "then", "next", "finally", "meanwhile")
  - If two events happened in the SAME session, preserve their original order
  - For multi-step processes, number sub-steps (e.g., "3a. ...", "3b. ...")

### Contradiction & Update Log
For EVERY piece of information that changed or was contradicted:
- "X" was originally stated → later changed to "Y" (context: when/why)
- User first said "A" → then clarified/corrected to "B"
- Include the BEFORE and AFTER values explicitly

### Technical Specifications
- [identifier/category] exact value (versions, ports, endpoints, configs)

### Causal Decisions
- Because [cause] → chose [action] (outcome: [result if known])

### User Preferences & Constraints (CRITICAL — easy to lose during compression)
- [verbatim preference or constraint stated by user]
- Extract ALL of: coding style preferences, tool preferences, framework choices, workflow preferences
- Extract stated constraints: "must use X", "prefer Y over Z", "always do X before Y"
- If user expressed a STRONG opinion, mark it: [STRONG] "exact quote"

RULES:
1. NEVER remove existing persistent knowledge items unless explicitly superseded
2. MERGE duplicates — keep the most specific version with the latest context
3. If existing persistent knowledge is provided below, UPDATE it — do not start from scratch
4. Value Registry and Event Log are the HIGHEST PRIORITY sections — be exhaustive
5. Be comprehensive — include ALL relevant knowledge without artificial length limits

## Knowledge Gap Analysis (Prediction-Correction)

Use the Prediction-Correction method to extract ONLY genuinely missing knowledge:

STEP 1 — PREDICT: Read the Persistent Knowledge above. This is your "knowledge model" — what a future agent would know.
STEP 2 — COMPARE: Re-read the ORIGINAL conversation carefully. Find concrete facts, events, values, and decisions that exist in the conversation but are MISSING, WRONG, or IMPRECISE in the PK above.
STEP 3 — EXTRACT SURPRISES ONLY: Output ONLY the gaps — information that would be LOST if someone relied solely on the PK. If the PK already captures something accurately, DO NOT repeat it here.

ALWAYS extract these (they represent critical state changes):
- [UPDATE] new_value (was: old_value) — EVERY value change must be listed atomically | supersedes:previous_ref
- [DECISION] chose X because Y — every decision with its full reasoning chain

Extract these ONLY if genuinely missing from PK:
- [FACT] exact value that the PK approximated, omitted, or got wrong | session:N | turn:N
- [EVENT] event whose precise timing/ordering is lost in the PK | order:N
- [TEMPORAL] "X happened before/after Y" — temporal relationships not captured in Event Log | order:N

Quality check: for each item, ask "Would someone reading ONLY the PK above miss this?" If no, skip it.
If existing SK items are provided below, PRESERVE existing items and ADD only NEW gaps.
---`

    // Inject existing PK as context — model should update and include in output
    const filePK = await readPK()
    const pk = filePK ?? extractPK(messages)
    const pkContext = pk
      ? `\n\n---\nExisting Persistent Knowledge from previous compaction (PRESERVE, UPDATE, and include in your ## Persistent Knowledge output):\n\n${pk}`
      : ""
    log.info("persistent knowledge", { source: filePK ? "file" : pk ? "message" : "none", length: pk?.length ?? 0 })

    // V15a: Theme-structured SK injection — ALL items, organized by topic
    // (filtering only in recall tool for conversation-time retrieval)
    const sk = await readSK()
    let skContext = ""
    if (sk.length > 0) {
      const themes = clusterSK(sk)
      if (themes.length > 0) {
        const expanded = formatExpanded(themes)
        skContext = `\n\n---\nKnowledge gaps from previous compactions (${sk.length} items, ${themes.length} topics):\n${expanded}\n\nPRESERVE all existing items and ADD only NEW gaps in your ## Knowledge Gap Analysis output.`
      }
      log.info("structured knowledge", { total: sk.length, themes: themes.length })
    }

    const promptText = compacting.prompt ?? [defaultPrompt + pkContext + skContext, ...compacting.context].join("\n\n")
    const result = await processor.process({
      user: userMessage,
      agent,
      abort: input.abort,
      sessionID: input.sessionID,
      tools: {},
      system: [],
      messages: [
        ...MessageV2.toModelMessages(messages, model, { stripMedia: true }),
        {
          role: "user",
          content: [
            {
              type: "text",
              text: promptText,
            },
          ],
        },
      ],
      model,
    })

    // Extract PK and SK from compaction output and write to files
    if (result === "continue") {
      const msgs = await Session.messages({ sessionID: input.sessionID, limit: 5 })
      const compacted = msgs.find(m => m.info.id === processor.message.id)
      if (compacted) {
        const text = compacted.parts
          .filter((p): p is MessageV2.TextPart => p.type === "text")
          .map(p => p.text)
          .join("")
        await extractPKFromSummary(text)
        await extractSKFromSummary(text)
        // Write theme files for read-path retrieval
        const allSK = await readSK()
        await writeThemes(allSK)
        // V16: Append to immutable stores (events + contradictions)
        const freshPK = await readPK()
        if (freshPK) {
          await extractEventsFromPK(freshPK)
          await extractContradictionsFromPK(freshPK)
        }
      }
    }

    if (result === "compact") {
      processor.message.error = new MessageV2.ContextOverflowError({
        message: replay
          ? "Conversation history too large to compact - exceeds model context limit"
          : "Session too large to compact - context exceeds model limit even after stripping media",
      }).toObject()
      processor.message.finish = "error"
      await Session.updateMessage(processor.message)
      return "stop"
    }

    if (result === "continue" && input.auto) {
      if (replay) {
        const original = replay.info as MessageV2.User
        const replayMsg = await Session.updateMessage({
          id: Identifier.ascending("message"),
          role: "user",
          sessionID: input.sessionID,
          time: { created: Date.now() },
          agent: original.agent,
          model: original.model,
          format: original.format,
          tools: original.tools,
          system: original.system,
          variant: original.variant,
        })
        for (const part of replay.parts) {
          if (part.type === "compaction") continue
          const replayPart =
            part.type === "file" && MessageV2.isMedia(part.mime)
              ? { type: "text" as const, text: `[Attached ${part.mime}: ${part.filename ?? "file"}]` }
              : part
          await Session.updatePart({
            ...replayPart,
            id: Identifier.ascending("part"),
            messageID: replayMsg.id,
            sessionID: input.sessionID,
          })
        }
      } else {
        const continueMsg = await Session.updateMessage({
          id: Identifier.ascending("message"),
          role: "user",
          sessionID: input.sessionID,
          time: { created: Date.now() },
          agent: userMessage.agent,
          model: userMessage.model,
        })
        const text =
          (input.overflow
            ? "The previous request exceeded the provider's size limit due to large media attachments. The conversation was compacted and media files were removed from context. If the user was asking about attached images or files, explain that the attachments were too large to process and suggest they try again with smaller or fewer files.\n\n"
            : "") +
          "Continue if you have next steps, or stop and ask for clarification if you are unsure how to proceed."
        await Session.updatePart({
          id: Identifier.ascending("part"),
          messageID: continueMsg.id,
          sessionID: input.sessionID,
          type: "text",
          synthetic: true,
          text,
          time: {
            start: Date.now(),
            end: Date.now(),
          },
        })
      }
    }
    if (processor.message.error) return "stop"
    Bus.publish(Event.Compacted, { sessionID: input.sessionID })
    return "continue"
  }

  export const create = fn(
    z.object({
      sessionID: Identifier.schema("session"),
      agent: z.string(),
      model: z.object({
        providerID: z.string(),
        modelID: z.string(),
      }),
      auto: z.boolean(),
      overflow: z.boolean().optional(),
    }),
    async (input) => {
      const msg = await Session.updateMessage({
        id: Identifier.ascending("message"),
        role: "user",
        model: input.model,
        sessionID: input.sessionID,
        agent: input.agent,
        time: {
          created: Date.now(),
        },
      })
      await Session.updatePart({
        id: Identifier.ascending("part"),
        messageID: msg.id,
        sessionID: msg.sessionID,
        type: "compaction",
        auto: input.auto,
        overflow: input.overflow,
      })
    },
  )
}
