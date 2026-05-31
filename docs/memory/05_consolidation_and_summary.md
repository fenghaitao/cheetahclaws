# Memory System - Part 5: AI Consolidation & Summary

## Module: `consolidator.py`

The consolidator automatically extracts long-term insights from completed sessions using AI.

---

## Purpose

**Problem:** Users forget to save important context during conversations.

**Solution:** After a session ends, analyze the conversation and extract:
- New user preferences revealed
- Project decisions made explicit
- Behavioral feedback given to the AI

---

## Design Principles

### 1. Hard Cap of 3 Memories

```python
MIN_MESSAGES_TO_CONSOLIDATE = 8  # Skip trivial sessions
```

**Rationale:**
- Prevents noise accumulation
- Forces quality over quantity
- Matches Claude Code's approach

### 2. Lower Confidence (0.8)

Auto-extracted memories start at **0.8 confidence** (vs 1.0 for explicit saves).

**Rationale:**
- Acknowledges inference uncertainty
- Won't overwrite higher-confidence memories
- User can review and adjust

### 3. Source Tracking

All consolidated memories have `source: "consolidator"`.

**Rationale:**
- Clear audit trail
- Helps resolve conflicts
- User knows which memories were auto-extracted

### 4. Conflict Avoidance

Won't overwrite a higher-confidence existing memory.

```python
conflict = check_conflict(entry, scope="user")
if conflict and conflict["existing_confidence"] >= entry.confidence:
    continue  # Skip this memory
```

---

## Implementation

### Main Function

```python
def consolidate_session(messages: list, config: dict) -> list[str]:
    """Analyze a session's messages and extract memories worth keeping long-term.
    
    Args:
        messages: the conversation message list (neutral format)
        config:   the active config dict (must contain a "model" key)
    
    Returns:
        List of memory names that were saved. Empty list on skip or error.
    """
    # Skip short sessions
    if len(messages) < MIN_MESSAGES_TO_CONSOLIDATE:
        return []

    try:
        from providers import stream, AssistantTurn
        from .store import MemoryEntry, save_memory, check_conflict
        import json

        # Build condensed transcript from the last 40 messages (≈ 20 turns)
        recent = messages[-40:]
        parts: list[str] = []
        for m in recent:
            role = m.get("role", "")
            content = m.get("content", "")
            if isinstance(content, str) and content.strip():
                prefix = "User" if role == "user" else "Assistant"
                snippet = content[:600].replace("\n", " ")
                parts.append(f"{prefix}: {snippet}")

        if not parts:
            return []

        transcript = "\n".join(parts)

        # Call AI to extract memories
        result_text = ""
        for event in stream(
            model=config.get("model", ""),
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Conversation:\n\n{transcript}"}],
            tool_schemas=[],
            config={**config, "max_tokens": 1024, "no_tools": True},
        ):
            if isinstance(event, AssistantTurn):
                result_text = event.text
                break

        if not result_text:
            return []

        # Parse JSON response
        parsed = json.loads(result_text)
        memories_data = parsed.get("memories", [])
        if not isinstance(memories_data, list):
            return []

        # Save extracted memories (max 3)
        saved: list[str] = []
        for m in memories_data[:3]:  # hard cap
            required = ("name", "type", "description", "content")
            if not all(k in m for k in required):
                continue

            entry = MemoryEntry(
                name=str(m["name"]),
                description=str(m["description"]),
                type=str(m.get("type", "user")),
                content=str(m["content"]),
                created=datetime.now().strftime("%Y-%m-%d"),
                confidence=float(m.get("confidence", 0.8)),
                source="consolidator",
            )

            # Don't overwrite a more confident existing memory
            conflict = check_conflict(entry, scope="user")
            if conflict and conflict["existing_confidence"] >= entry.confidence:
                continue

            save_memory(entry, scope="user")
            saved.append(entry.name)

        return saved

    except Exception:
        return []  # Silent failure
```

---

## System Prompt

```python
_SYSTEM = """\
You are a memory consolidation assistant. Analyze the conversation below and extract
insights that are worth storing as persistent memories for future sessions.

Focus ONLY on:
1. New user preferences or working-style corrections revealed in this session
2. Project decisions or facts made explicit (NOT derivable from code/git)
3. Behavioral feedback given to the AI (what to do or avoid, and why)

Return a JSON object with key "memories" containing a list of objects, each with:
  "name":        short slug, e.g. "user_prefers_concise_responses"
  "type":        "user" | "feedback" | "project"
  "description": one-line description (used for search relevance)
  "content":     memory body; for feedback/project lead with the rule/fact then
                 **Why:** and **How to apply:** lines
  "confidence":  float 0.0–1.0 (use ~0.8 for inferred, ~0.9 for clearly stated)

Return {"memories": []} if nothing new or worth saving.

Do NOT extract:
- Code patterns, architecture, file paths — derivable from the codebase
- Git history or debugging fixes — already in commits
- Anything already obvious from CLAUDE.md
- Ephemeral task state or tool results

Keep to AT MOST 3 memories. Quality over quantity."""
```

---

## Example Session

### Input Conversation

```
User: I prefer tests for all new features
Assistant: Understood. I'll make sure to include tests when implementing features.

User: Also, don't mock the database in integration tests
Assistant: Got it. I'll use test containers or in-memory DB instead.

User: One more thing - always ask about error handling before implementing
Assistant: Will do. I'll check error handling strategy upfront.
```

### AI Response

```json
{
  "memories": [
    {
      "name": "user_prefers_tests",
      "type": "user",
      "description": "User wants comprehensive test coverage for new features",
      "content": "User prefers tests written for all new features to catch regressions early.",
      "confidence": 0.9
    },
    {
      "name": "feedback_no_db_mocks",
      "type": "feedback",
      "description": "Don't mock the database in integration tests",
      "content": "Don't mock the database in integration tests.\n\n**Why:** Mocks hide real DB behavior and connection issues.\n\n**How to apply:** Use test containers or in-memory DB for integration tests.",
      "confidence": 0.9
    },
    {
      "name": "feedback_ask_error_handling",
      "type": "feedback",
      "description": "Always ask about error handling strategy before implementing",
      "content": "Always ask about error handling strategy before implementing.\n\n**Why:** Ensures consistent error handling across the codebase.\n\n**How to apply:** Before starting implementation, ask user about error handling approach.",
      "confidence": 0.9
    }
  ]
}
```

### Result

3 memories saved to `~/.cheetahclaws/memory/`:
- `user_prefers_tests.md`
- `feedback_no_db_mocks.md`
- `feedback_ask_error_handling.md`

---

## Invocation

### Manual (via slash command)

```bash
/memory consolidate
```

**Output:**
```
Extracted 3 memories from this session:
- user_prefers_tests
- feedback_no_db_mocks
- feedback_ask_error_handling
```

### Automatic (on session end)

Can be wired into session cleanup:

```python
# In cheetahclaws.py or session_store.py
def save_session(state, config):
    # ... save conversation ...
    
    # Auto-consolidate if enabled
    if config.get("auto_consolidate", False):
        from memory.consolidator import consolidate_session
        saved = consolidate_session(state.messages, config)
        if saved:
            print(f"Auto-extracted {len(saved)} memories")
```

---

## Performance

**Typical operation:**
- Input: Last 40 messages (~10K tokens)
- Model: Same as active model (or fallback to Haiku)
- Output: ~500 tokens (JSON)
- Time: ~2-3 seconds
- Cost: ~$0.01 per consolidation

**Frequency:**
- Manual: User-triggered via `/memory consolidate`
- Automatic: Once per session (if enabled)

---

## Quality Control

### What Gets Extracted

✅ **Good candidates:**
- "I prefer X over Y"
- "Don't do X because Y"
- "Always ask about X before Y"
- "We decided to migrate to X by date Y"
- "The API endpoint is at X"

❌ **Bad candidates:**
- "The bug is in file.py line 42" (stale)
- "Use pattern X in this codebase" (derivable)
- "Fix the test" (ephemeral)
- "The PR is #123" (git history)

### Confidence Guidelines

- **0.9**: Clearly stated preference or decision
- **0.8**: Inferred from behavior (default)
- **0.7**: Uncertain or speculative
- **0.6**: Very uncertain

---

## Summary: Complete Memory System

### Architecture Layers

```
┌─────────────────────────────────────────────────────────┐
│ User / AI Interaction                                   │
│ - Explicit saves via MemorySave                         │
│ - Searches via MemorySearch                             │
│ - Auto-consolidation on session end                     │
└────────────────┬────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────┐
│ LLM Tools (tools.py)                                    │
│ - MemorySave: Create/update memories                    │
│ - MemorySearch: Find relevant memories                  │
│ - MemoryDelete: Remove memories                         │
│ - MemoryList: List all memories                         │
└────────────────┬────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────┐
│ Context Layer (context.py)                              │
│ - get_memory_context(): Inject into system prompt       │
│ - find_relevant_memories(): Keyword + AI ranking        │
│ - truncate_index_content(): Stay within limits          │
└────────────────┬────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────┐
│ Storage Layer (store.py)                                │
│ - save_memory(): Write .md file + rebuild index         │
│ - load_entries(): Scan directory                        │
│ - search_memory(): Keyword match                        │
│ - check_conflict(): Detect overwrites                   │
└────────────────┬────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────┐
│ Filesystem                                              │
│ ~/.cheetahclaws/memory/                                 │
│ ├── MEMORY.md (index)                                   │
│ ├── user_prefers_tests.md                               │
│ └── feedback_no_db_mocks.md                             │
│                                                          │
│ .cheetahclaws/memory/                                   │
│ ├── MEMORY.md (project index)                           │
│ └── project_api_migration.md                            │
└─────────────────────────────────────────────────────────┘
```

### Key Features Recap

1. **Dual-scope**: User (global) + Project (local)
2. **4 memory types**: user, feedback, project, reference
3. **Confidence scoring**: 0.0-1.0 reliability tracking
4. **Source tracking**: user/model/tool/consolidator
5. **Conflict detection**: Warns on overwrites
6. **Conflict groups**: Tag related memories
7. **Recency ranking**: confidence × exp(-age/30)
8. **Staleness warnings**: For memories >1 day old
9. **Last used tracking**: Updates on search
10. **AI consolidation**: Auto-extract 3 memories per session
11. **Index truncation**: 200 lines / 25KB limits
12. **System prompt injection**: Index visible to AI every turn

### File Format

```markdown
---
name: memory_name
description: One-line description
type: user | feedback | project | reference
created: 2026-05-31
confidence: 0.8
source: consolidator
last_used_at: 2026-05-31
conflict_group: testing_policy
---

Memory content here.

For feedback/project types:
**Why:** Reason
**How to apply:** When this matters
```

### Usage Patterns

**Explicit save:**
```
User: "Remember that I prefer tests for all features"
AI: [calls MemorySave]
```

**Search:**
```
AI: [calls MemorySearch with query="testing"]
→ Returns relevant memories with staleness warnings
```

**Auto-consolidation:**
```
Session ends
→ consolidate_session() analyzes last 40 messages
→ Extracts up to 3 memories
→ Saves with confidence=0.8, source=consolidator
```

**System prompt:**
```
Every turn:
→ get_memory_context() reads MEMORY.md
→ Injects into system prompt
→ AI sees all memories
```

---

## Best Practices

### For Users

1. **Be explicit** - Say "remember this" when sharing important context
2. **Review consolidations** - Check auto-extracted memories with `/memory list`
3. **Delete stale memories** - Use `/memory delete` for outdated info
4. **Use project scope** - For project-specific context
5. **Add conflict groups** - For related preferences

### For AI

1. **Save corrections** - When user corrects you, save as feedback
2. **Save confirmations** - When user confirms non-obvious approach
3. **Don't save code** - Architecture/patterns are derivable
4. **Structure feedback** - Use **Why:** and **How to apply:**
5. **Check staleness** - Verify old memories before asserting as fact

### For Developers

1. **Keep index small** - One line per memory, ~150 chars
2. **Use confidence** - Lower for inferred, higher for explicit
3. **Track source** - Always set source field
4. **Handle conflicts** - Check before overwriting
5. **Test consolidation** - Verify quality of extracted memories

---

## Future Enhancements

### Planned

- **Memory decay**: Auto-delete unused memories after N days
- **Conflict resolution UI**: Interactive merge tool
- **Memory analytics**: Usage stats, staleness report
- **Semantic search**: Vector embeddings for better relevance
- **Memory versioning**: Track changes over time

### Under Consideration

- **Shared team memories**: Sync across team members
- **Memory templates**: Pre-defined structures for common types
- **Memory validation**: Check for contradictions
- **Memory compression**: Merge similar memories
- **Memory export**: Backup to external storage

---

## Conclusion

The memory system provides **persistent, cross-session knowledge** that makes CheetahClaws feel like it "remembers" you. Key innovations over Claude Code:

1. **Confidence scoring** - Track reliability
2. **Source tracking** - Audit trail
3. **Conflict groups** - Link related memories
4. **Recency ranking** - Balance freshness with reliability
5. **Last used tracking** - Identify stale memories

The system is **file-based** (easy to inspect/edit), **dual-scoped** (user + project), and **AI-assisted** (auto-consolidation). It strikes a balance between automation and user control.

---

## Complete File Reference

```
memory/
├── __init__.py          # Package exports
├── types.py             # Memory types + system prompt guidance
├── store.py             # CRUD operations (save/delete/load/search)
├── tools.py             # LLM tools (Save/Search/Delete/List)
├── context.py           # System prompt injection + relevance
├── scan.py              # File scanning + freshness tracking
└── consolidator.py      # AI-powered session extraction
```

**Total:** ~2,300 lines of Python implementing a production-ready memory system.
