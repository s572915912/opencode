import path from "path"
import z from "zod"
import { Tool } from "./tool"
import { Instance } from "../project/instance"

export const MemoryRecallTool = Tool.define("recall", async () => {
  const dir = path.join(Instance.worktree, ".opencode", "knowledge")

  // Read topic index to build available topics list
  const topics: { name: string; file: string }[] = []
  try {
    const idx = Bun.file(path.join(dir, "index.md"))
    if (await idx.exists()) {
      const text = await idx.text()
      for (const line of text.split("\n")) {
        const m = line.match(/^\d+\.\s+(.+?)\s+\((\d+)\s+items?\)/)
        if (m) {
          const slug = m[1].replace(/[^a-z0-9]/gi, "_").toLowerCase()
          topics.push({ name: m[1], file: `${slug}.md` })
        }
      }
    }
  } catch {}

  const hint = topics.length > 0
    ? topics.map(t => `'${t.name}'`).slice(0, 5).join(", ")
    : ""

  const description = topics.length === 0
    ? "Recall detailed knowledge from memory on a specific topic. No knowledge topics are currently available."
    : [
        "Recall detailed knowledge from memory on a specific topic.",
        "Use this when you need more detail than what's in the conversation summary.",
        "",
        "<available_topics>",
        ...topics.map(t => `  <topic>${t.name}</topic>`),
        "</available_topics>",
      ].join("\n")

  const parameters = z.object({
    topic: z.string().describe(`Topic name from available_topics${hint ? ` (e.g., ${hint})` : ""}`),
  })

  return {
    description,
    parameters,
    async execute(params: z.infer<typeof parameters>, ctx: Tool.Context) {
      // Find matching topic (case-insensitive, fuzzy)
      const q = params.topic.toLowerCase()
      const match = topics.find(t => t.name.toLowerCase() === q)
        ?? topics.find(t => t.name.toLowerCase().includes(q))
        ?? topics.find(t => q.includes(t.name.toLowerCase()))

      if (!match) {
        const list = topics.map(t => t.name).join(", ")
        return {
          title: "No matching topic",
          output: `Topic "${params.topic}" not found. Available: ${list || "none"}`,
          metadata: {} as Record<string, any>,
        }
      }

      // Read the topic file
      const file = Bun.file(path.join(dir, match.file))
      if (!(await file.exists())) {
        return {
          title: `Topic: ${match.name}`,
          output: `Topic file not found: ${match.file}`,
          metadata: {} as Record<string, any>,
        }
      }

      const content = await file.text()
      return {
        title: `Recalled: ${match.name}`,
        output: [
          `<memory_topic name="${match.name}">`,
          content.trim(),
          "</memory_topic>",
        ].join("\n"),
        metadata: { topic: match.name } as Record<string, any>,
      }
    },
  }
})
