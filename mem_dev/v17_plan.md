# V17 改进方案：归档式 prune + prompt 增强

> 时间约束：**今天一天**完成实现 + 评估
> 基于 V16 评估（56.2%）、context-kit 归档理念、现有代码架构

## 一、现有压缩流程（V16）

```
prompt.ts loop() 主循环:

1. 正常对话 → processor.process() → LLM 生成回复
2. 每个 finish-step 检查 isOverflow() (processor.ts L282-287)
   → 如果超阈值 → 返回 "compact"
3. loop() 收到 "compact" → 创建 compaction task (prompt.ts L705-712)
4. 下一轮 loop → 检测到 compaction task → 调用 SessionCompaction.process()
   → process() 内部调用 LLM 做压缩 → 产出 summary + PK/SK
5. loop 结束后 → prune() 清除旧 tool output (prompt.ts L716)
```

**关键发现：`prune()` 在 loop 结束后才跑，而且清除的内容永久丢失。**

---

## 二、V17 改动点（替换，不是叠加）

> ⚠️ **核心原则**：V17 修改的是现有压缩链路内部的逻辑，不新增额外的压缩环节。
> 压缩完成后 token 计数刷新，不会触发二次压缩。

### 改动 1：增强 prune() → 归档式 prune

**改什么**：`compaction.ts` L500-541 的 `prune()` 函数

**现状**：把旧 tool output 的内容清空（`time.compacted = Date.now()`），内容永久丢失

**V17**：清空前，把内容写入 session 级归档文件

```typescript
// compaction.ts — prune() 内部改动
// 现有代码 L533-539:
if (pruned > PRUNE_MINIMUM) {
  for (const part of toPrune) {
    if (part.state.status === "completed") {
      // 🆕 V17: 归档到文件再清除
      await archive(input.sessionID, part)
      part.state.time.compacted = Date.now()
      await Session.updatePart(part)
    }
  }
}
```

**归档函数**（新增，很简单）：
```typescript
// compaction.ts 新增 ~20行
async function archive(sessionID: string, part: MessageV2.ToolPart) {
  const dir = path.join(Session.directory(sessionID), "archive")
  await fs.mkdir(dir, { recursive: true })
  const entry = {
    id: part.id,
    tool: part.tool,
    ts: part.state.time?.start ?? Date.now(),
    input: JSON.stringify(part.state.input).slice(0, 200),
    output: part.state.output,
  }
  await Bun.file(path.join(dir, `${part.id}.json`)).write(JSON.stringify(entry))
  // 追加索引
  const idx = { id: part.id, tool: part.tool, ts: entry.ts, chars: entry.output?.length ?? 0 }
  await fs.appendFile(path.join(dir, "index.jsonl"), JSON.stringify(idx) + "\n")
}
```

**工作量**：~30 行代码改动，1 小时

---

### 改动 2：增强 compaction prompt（针对薄弱指标）

**改什么**：`compaction.ts` L615-680 的 compaction prompt

**现状 prompt 已有**（V16 加的）：
- 数值保留规则 ✅
- PK/SK 结构 ✅
- 事件账本 + 矛盾对 ✅

**V17 新增 prompt 指令**：
```
TEMPORAL & ORDERING RULES:
- PRESERVE event sequences with timestamps: "[T1] A happened, then [T2] B happened"
- When values change over time, record as: "X: old_value → new_value (at T)"
- MAINTAIN user preferences as a dedicated section in PK

ARCHIVE AWARENESS:
- Detailed tool outputs have been archived to session/archive/
- In your summary, reference archived content: "See archive/<id> for full output"
- Focus summary on conclusions and decisions, not raw tool output
```

**工作量**：~15 行 prompt 文本，30 分钟

---

### 改动 3：prune 时机前移（可选，低风险）

**现状**：`prune()` 在 loop 结束后才跑（prompt.ts L716）

**V17 可选改动**：在 `SessionCompaction.process()` 开始前也调用 prune

```typescript
// compaction.ts process() 开头新增一行
export async function process(input) {
  await prune({ sessionID: input.sessionID })  // 🆕 先 prune 再 LLM 压缩
  // ... 现有 LLM 压缩逻辑
}
```

**好处**：LLM 压缩时输入更短 → 压缩更快、质量更高
**风险**：低，prune 本身是幂等的

**工作量**：1 行代码，5 分钟

---

## 三、改动前后流程对比

```
=== V16 流程 ===
对话进行 → token 超阈值 → 创建 compaction task
→ LLM 压缩全量消息（含大量 tool output）→ 产出 summary
→ loop 结束 → prune() 清除旧 tool output → 内容丢失

=== V17 流程（替换，不是叠加）===
对话进行 → token 超阈值 → 创建 compaction task
→ process() 开头先调 prune()：
  ├── 归档旧 tool output 到 archive/*.json
  └── 清除旧 tool output（token 减少 60-80%）
→ LLM 压缩已瘦身的消息 + 增强 prompt
  ├── 产出 summary（含时序、偏好、变更追踪）
  └── 可引用 archive 中的详细信息
→ 压缩完成，token 计数刷新 → ✅ 不会再次触发压缩
```

**关键点**：整个链路只有一次压缩入口（isOverflow → create compaction task → process）。V17 只改 process 内部逻辑，不新增触发点。

---

## 四、预期 BEAM 分数提升

| 指标 | V16 | V17 预期 | 提升来源 |
|:---|:---:|:---:|:---|
| abstention | 100% | 100% | 维持 |
| information_extraction | 87.5% | 90%+ | prune 前 LLM 能看到更多上下文 |
| instruction_following | 75% | 80% | prompt 强化代码片段保留 |
| knowledge_update | 75% | 85% | prompt "old→new (at T)" 格式 |
| multi_session_reasoning | 66.7% | 70% | 更完整的 summary |
| contradiction_resolution | 56.2% | 62% | 矛盾对 + 更好的 summary |
| preference_following | 50% | 62% | prompt 新增偏好专区 |
| temporal_reasoning | 25% | 45% | prompt "[T1]...[T2]..." 时序格式 |
| summarization | 27.1% | 40% | prune 后 LLM 更专注于内容 |
| event_ordering | 0% | 15% | prompt 时序保留指令 |
| **总分** | **56.2%** | **~65%** | **+9pp** |

---

## 五、今天的时间表

| 时间 | 任务 | 产出 |
|:---|:---|:---|
| 13:00-14:00 | 实现改动 1：归档式 prune | `compaction.ts` +30行 |
| 14:00-14:30 | 实现改动 2：增强 prompt | `compaction.ts` prompt 修改 |
| 14:30-14:45 | 实现改动 3：prune 前移 | `compaction.ts` +1行 |
| 14:45-15:00 | 本地验证：启动 server，手动测试 | 确认不 break |
| 15:00-17:00 | 跑 BEAM V17 评估（5 case） | 评分结果 |
| 17:00-18:00 | 对比 V14/V15a/V16/V17 | 最终报告 |

**总改动量**：~50 行代码 + 15 行 prompt
**风险**：极低，所有改动都在 `compaction.ts` 内部

---

## 六、不在 V17 范围内的改动（留给后续）

| 改动 | 原因 |
|:---|:---|
| 自适应阈值（50%/80%） | 不影响 BEAM 分数，需改 isOverflow + processor |
| 后台静默压缩 | UI 层改动，今天没时间 |
| 归档内容的按需恢复（recall 工具） | 需要新增工具注册，复杂度高 |
| 多阶段压缩（Stage 1 / Stage 2） | 需要改 prompt.ts 的触发逻辑，风险较高 |

这些在 V17 评分确认提升后，作为 V18 实现。
