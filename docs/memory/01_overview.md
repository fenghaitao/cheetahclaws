# Memory System - Part 1: Overview & Architecture

## What is the Memory System?

CheetahClaws' memory system provides **persistent, cross-session knowledge storage** that allows the AI to remember:
- User preferences and working style
- Feedback and corrections given to the AI
- Project-specific context and decisions
- References to external systems

Unlike conversation history (which is ephemeral), memories persist across sessions and can be searched, updated, and deleted.

---

## Design Philosophy

### Inspired by Claude Code

The memory system closely mirrors Anthropic's Claude Code implementation:

1. **File-based storage** - Each memory is a markdown file with YAML frontmatter
2. **Dual-scope** - User-level (global) and project-level (local) memories
3. **Type taxonomy** - 4 memory types with clear boundaries
4. **Index-based discovery** - `MEMORY.md` index file injected into system prompt
5. **Staleness tracking** - Age warnings for memories older than 1 day
6. **Conflict detection** - Warns when overwriting existing memories

### Key Differences from Claude Code

| Feature | Claude Code | CheetahClaws |
|---------|-------------|--------------|
| Storage | File-based | File-based (same) |
| Scopes | User + Project | User + Project (same) |
| Types | 4 types | 4 types (same) |
| Confidence scoring | No | **Yes** (0.0-1.0) |
| Source tracking | No | **Yes** (user/model/tool/consolidator) |
| Conflict groups | No | **Yes** (tag related memories) |
| Last used tracking | No | **Yes** (updates on search) |
| AI consolidation | Yes | **Yes** (3-memory cap) |
| Recency ranking | No | **Yes** (exponential decay) |

---

## Architecture

### Directory Structure

```
~/.cheetahclaws/memory/          # User-level (global)
├── MEMORY.md                    # Auto-generated index
├── user_prefers_tests.md        # Individual memory files
├── feedback_code_style.md
└── reference_jira.md

.cheetahclaws/memory/            # Project-level (local to cwd)
├── MEMORY.md                    # Project index
├── project_api_migration.md
└── project_freeze_dates.md
```

### Memory File Format

Each memory is a markdown file with YAML frontmatter:

```markdown
---
name: user_prefers_tests
description: User wants tests written for all new features
type: user
created: 2026-05-31
confidence: 1.0
source: user
last_used_at: 2026-05-31
conflict_group: testing_policy
---

User prefers comprehensive test coverage for all new features.

**Why:** Catches regressions early and documents expected behavior.

**How to apply:** When implementing a new feature, always include unit tests
and integration tests. Ask about test strategy before starting implementation.
```

### Index File (`MEMORY.md`)

Auto-generated list of all memories in a scope:

```markdown
- [user_prefers_tests](user_prefers_tests.md) — User wants tests for all features
- [feedback_code_style](feedback_code_style.md) — Use 4-space indentation
- [reference_jira](reference_jira.md) — JIRA board at https://company.atlassian.net
```

**Limits** (matches Claude Code):
- Max 200 lines
- Max 25,000 bytes
- Truncation warning appended if exceeded

---

## Module Structure

The memory system is organized in `memory/` package:

```
memory/
├── __init__.py          # Package exports
├── types.py             # Memory type taxonomy + system prompt guidance
├── store.py             # Core CRUD operations (save/delete/load/search)
├── tools.py             # LLM-callable tools (MemorySave/Search/Delete/List)
├── context.py           # System prompt injection + relevance filtering
├── scan.py              # File scanning + freshness tracking
└── consolidator.py      # AI-powered session extraction
```

### Data Flow

```
User/AI → MemorySave tool
    ↓
store.py: save_memory()
    ↓
Write .md file with frontmatter
    ↓
Rebuild MEMORY.md index
    ↓
context.py: get_memory_context()
    ↓
Inject into system prompt
    ↓
AI sees memories in next turn
```

---

## Memory Types

### 1. `user` - User Preferences

**What to save:**
- User's role, goals, responsibilities
- Knowledge level and expertise areas
- Working style preferences
- Communication preferences

**Examples:**
- "User is a senior backend engineer focused on API design"
- "User prefers concise explanations without excessive detail"
- "User works in Pacific timezone, references times in PT"

**What NOT to save:**
- Temporary task assignments
- Current conversation context
- Anything derivable from code/git

### 2. `feedback` - Behavioral Guidance

**What to save:**
- Corrections given to the AI
- Confirmations of non-obvious approaches
- Guidance on how to work together

**Structure:**
```
Rule/correction statement

**Why:** Reason given by user

**How to apply:** When this guidance kicks in
```

**Examples:**
- "Don't mock the database in integration tests"
- "Always ask about error handling strategy before implementing"
- "Prefer composition over inheritance in this codebase"

**What NOT to save:**
- Code patterns (derivable from codebase)
- Debugging solutions (in git history)
- One-off corrections for current task

### 3. `project` - Project Context

**What to save:**
- Ongoing work and goals
- Decisions made (with rationale)
- Deadlines and milestones
- Known bugs or incidents

**Structure:**
```
Fact/decision statement

**Why:** Context and reasoning

**How to apply:** When this matters
```

**Examples:**
- "API v2 migration in progress, v1 deprecated after 2026-06-30"
- "Feature freeze from 2026-05-20 to 2026-06-01 for security audit"
- "Database connection pool increased to 50 after load testing"

**What NOT to save:**
- Architecture (in CLAUDE.md or code)
- Recent changes (in git log)
- File paths or structure

### 4. `reference` - External Pointers

**What to save:**
- Links to issue trackers
- Dashboard URLs
- Slack channels
- Documentation sites
- API endpoints

**Examples:**
- "JIRA board: https://company.atlassian.net/browse/PROJ"
- "Grafana dashboard: https://grafana.company.com/d/api-metrics"
- "Team Slack: #backend-team"

---

## Key Features

### 1. Confidence Scoring (0.0-1.0)

Tracks reliability of each memory:

- **1.0** - Explicit user statement (default)
- **0.9** - Clearly stated preference
- **0.8** - Inferred from user behavior (consolidator default)
- **0.6** - Uncertain or speculative

**Usage:**
- Search results ranked by `confidence × recency`
- Consolidator won't overwrite higher-confidence memories
- Low-confidence memories shown with warning tag

### 2. Source Tracking

Records origin of each memory:

- **user** - Explicit user statement (default)
- **model** - Inferred by AI
- **tool** - Extracted from tool output
- **consolidator** - Auto-extracted via `/memory consolidate`

**Usage:**
- Displayed in search results
- Helps resolve conflicts
- Audit trail for memory provenance

### 3. Conflict Detection

When saving a memory with an existing name:
- Compares content (ignoring whitespace)
- If different, shows warning with old content preview
- Includes old confidence, source, and creation date
- User/AI can decide whether to overwrite

### 4. Conflict Groups

Optional tag linking related memories:

```yaml
conflict_group: testing_policy
```

**Use cases:**
- Group related preferences that might conflict
- Track evolution of decisions over time
- Help AI identify when to ask for clarification

**Examples:**
- `testing_policy` - Different test coverage preferences
- `code_style` - Formatting and style rules
- `api_design` - REST vs GraphQL preferences

### 5. Recency Tracking

**Last used tracking:**
- `last_used_at` field updated on every `MemorySearch` hit
- Helps identify stale/unused memories

**Recency ranking:**
- Search results ranked by `confidence × recency_score`
- Recency score: `exp(-age_days / 30)` (30-day half-life)
- Balances reliability with freshness

### 6. Staleness Warnings

Memories older than 1 day show warning:

```
⚠ This memory is 5 days old. Memories are point-in-time observations,
not live state — claims about code behavior or file:line citations
may be outdated. Verify against current code before asserting as fact.
```

**Motivation:** Prevents AI from asserting stale code-state facts as truth.

---

## Next Parts

- **Part 2: Core Operations** - CRUD, search, indexing
- **Part 3: LLM Tools** - MemorySave/Search/Delete/List schemas
- **Part 4: Context Injection** - System prompt integration
- **Part 5: AI Consolidation** - Auto-extraction from sessions
- **Part 6: Usage Patterns** - Best practices and examples
