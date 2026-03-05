# Task Tracker - OpenCode Project

## Daily Progress Template

Copy this template for each day of work:

```markdown
## [YYYY-MM-DD] - Day [X] of [10]

### Phase: [Current Phase Name]

**Completed Tasks:**

- [ ] [Task description] - [Time spent]
- [ ] [Task description] - [Time spent]

**In Progress:**

- [ ] [Task description] - [Started at time]

**Blockers:**

- [None / List any blockers with details]

**Next Steps:**

- [ ] [Task to do next]
- [ ] [Task to do next]

**Notes:**
[Any additional notes, observations, or decisions]
```

## Weekly Summary Template

```markdown
## Week [X] Summary (YYYY-MM-DD to YYYY-MM-DD)

### Goals Achieved:

- [ ] [Major accomplishment 1]
- [ ] [Major accomplishment 2]

### Key Metrics:

- Files migrated: [X]
- Tests passing: [X]/[Y]
- Issues resolved: [X]
- New issues identified: [X]

### Challenges Faced:

- [Challenge 1]
- [Challenge 2]

### Lessons Learned:

- [Lesson 1]
- [Lesson 2]

### Next Week's Focus:

- [ ] [Priority 1]
- [ ] [Priority 2]
```

## Milestone Checklist

### Phase 0: Foundation (Target: March 5-6)

- [ ] Implement Process wrappers in `src/util/process.ts`
- [ ] Refactor `src/util/git.ts` to use Process only
- [ ] Add tests for exit handling, timeout, abort, and output capture

### Phase 1: High-impact Hotspots (Target: March 7-8)

- [ ] Migrate `src/cli/cmd/github.ts` (33 commands)
- [ ] Migrate `src/worktree/index.ts` (22 commands)
- [ ] Migrate `src/lsp/server.ts` (21 commands)
- [ ] Migrate `src/installation/index.ts` (20 commands)
- [ ] Migrate `src/snapshot/index.ts` (18 commands)

### Phase 2: Remaining Git-heavy Files (Target: March 11-12)

- [ ] Migrate `src/file/index.ts`
- [ ] Migrate `src/project/vcs.ts`
- [ ] Migrate `src/file/watcher.ts`
- [ ] Migrate `src/storage/storage.ts`
- [ ] Migrate `src/cli/cmd/pr.ts`

### Phase 3: Remaining Non-git Files (Target: March 13-14)

- [ ] Migrate `src/cli/cmd/tui/util/clipboard.ts`
- [ ] Migrate `src/util/archive.ts`
- [ ] Migrate `src/file/ripgrep.ts`
- [ ] Migrate `src/tool/bash.ts`
- [ ] Migrate `src/cli/cmd/uninstall.ts`

### Phase 4: Stabilize (Target: March 15)

- [ ] Remove dead wrappers and one-off patterns
- [ ] Keep plugin `$` compatibility isolated and documented
- [ ] Create linked 2.0 task for plugin `$` removal
- [ ] Update all documentation
- [ ] Run final validation tests

## Quick Status Commands

Use these commands to check progress:

```bash
# Count remaining Bun $ usage
grep -r "\$`" src/ | wc -l

# Check test status
bun test --timeout 30000

# Run type checking
bun run typecheck

# Check git status for changes
git status

# View recent commits
git log --oneline -5
```

## Emergency Contact/Help

If stuck on any task for more than 2 hours:

1. Document the issue clearly
2. Check existing tests for similar patterns
3. Review the Bun shell migration plan
4. Ask for help if needed
