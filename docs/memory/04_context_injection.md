# Memory System - Part 4: Context Injection

## Module: `context.py`

The `context.py` module handles:
1. Building memory context for system prompt injection
2. Finding relevant memories for a query
3. Truncating index content to stay within limits

---

## System Prompt Injection

### Main Function

```python
def get_memory_context(include_guidance: bool = False) -> str:
    """Return memory context for injection into the system prompt.
    
    Combines user-level and project-level MEMORY.md content (if present).
    Returns empty string when no memories exist.
    
    Args:
        include_guidance: if True, prepend the full memory system guidance
                          (MEMORY_SYSTEM_PROMPT). Normally False since the
                          system prompt template already includes brief guidance.
    """
    parts: list[str] = []

    # User-level index
    user_content = get_index_content("user")
    if user_content:
        truncated = truncate_index_content(user_content)
        parts.append(truncated)

    # Project-level index (labelled separately)
    proj_content = get_index_content("project")
    if proj_content:
        truncated = truncate_index_content(proj_content)
        parts.append(f"[Project memories]\n{truncated}")

    if not parts:
        return ""

    body = "\n\n".join(parts)
    
    if include_guidance:
        return f"{MEMORY_SYSTEM_PROMPT}\n\n## MEMORY.md\n{body}"
    
    return body
```

### Integration with System Prompt

In `context.py` (the main context builder, not memory/context.py):

```python
def build_system_prompt(config: dict) -> str:
    """Build the full system prompt with all context blocks."""
    from memory.context import get_memory_context
    
    parts = [
        load_base_prompt(config),  # Base template
        get_env_block(),           # Environment info
        get_memory_context(),      # Memory index ← HERE
        get_tmux_block(),          # Tmux tools (if available)
        get_plan_block(),          # Plan mode (if active)
    ]
    
    return "\n\n".join(p for p in parts if p)
```

**Result:** The AI sees the memory index in every turn's system prompt.

---

## Index Truncation

### Limits (matches Claude Code)

```python
MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25_000
```

### Truncation Function

```python
def truncate_index_content(raw: str) -> str:
    """Truncate MEMORY.md content to line AND byte limits, appending a warning.
    
    Matches Claude Code's truncateEntrypointContent:
      - Line-truncates first (natural boundary)
      - Then byte-truncates at the last newline before the cap
      - Appends which limit fired
    """
    trimmed = raw.strip()
    content_lines = trimmed.split("\n")
    line_count = len(content_lines)
    byte_count = len(trimmed.encode())

    was_line_truncated = line_count > MAX_INDEX_LINES
    was_byte_truncated = byte_count > MAX_INDEX_BYTES

    if not was_line_truncated and not was_byte_truncated:
        return trimmed

    # Step 1: Line truncation
    truncated = "\n".join(content_lines[:MAX_INDEX_LINES]) if was_line_truncated else trimmed

    # Step 2: Byte truncation (cut at last newline before limit)
    if len(truncated.encode()) > MAX_INDEX_BYTES:
        raw_bytes = truncated.encode()
        cut = raw_bytes[:MAX_INDEX_BYTES].rfind(b"\n")
        truncated = raw_bytes[: cut if cut > 0 else MAX_INDEX_BYTES].decode(errors="replace")

    # Append warning
    if was_byte_truncated and not was_line_truncated:
        reason = f"{byte_count:,} bytes (limit: {MAX_INDEX_BYTES:,}) — index entries are too long"
    elif was_line_truncated and not was_byte_truncated:
        reason = f"{line_count} lines (limit: {MAX_INDEX_LINES})"
    else:
        reason = f"{line_count} lines and {byte_count:,} bytes"

    warning = (
        f"\n\n> WARNING: {INDEX_FILENAME} is {reason}. "
        "Only part of it was loaded. Keep index entries to one line under ~150 chars."
    )
    
    return truncated + warning
```

**Example warning:**
```
> WARNING: MEMORY.md is 250 lines (limit: 200). Only part of it was loaded.
Keep index entries to one line under ~150 chars.
```

---

## Relevant Memory Finder

### Main Function

```python
def find_relevant_memories(
    query: str,
    max_results: int = 5,
    use_ai: bool = False,
    config: dict | None = None,
) -> list[dict]:
    """Find memories relevant to a query.
    
    Strategy:
      1. Always: keyword match on name + description + content
      2. If use_ai=True and config has a model: use a small AI call to rank
    
    Returns:
        List of dicts with keys: name, description, type, scope, content,
        file_path, mtime_s, freshness_text, confidence, source
    """
    # Step 1: Keyword filter
    keyword_results = search_memory(query)
    if not keyword_results:
        return []

    if not use_ai or not config:
        # Return top max_results by recency (newest first)
        from .scan import scan_all_memories
        headers = scan_all_memories()
        path_to_mtime = {h.file_path: h.mtime_s for h in headers}

        results = []
        for entry in keyword_results[:max_results * 3]:
            mtime_s = path_to_mtime.get(entry.file_path, 0)
            results.append({
                "name": entry.name,
                "description": entry.description,
                "type": entry.type,
                "scope": entry.scope,
                "content": entry.content,
                "file_path": entry.file_path,
                "mtime_s": mtime_s,
                "freshness_text": memory_freshness_text(mtime_s),
                "confidence": entry.confidence,
                "source": entry.source,
            })
        
        results.sort(key=lambda r: r["mtime_s"], reverse=True)
        return results[:max_results]

    # Step 2: AI-powered relevance selection (optional, lightweight)
    return _ai_select_memories(query, keyword_results, max_results, config)
```

### Two-Stage Strategy

**Stage 1: Keyword filtering (always)**
- Case-insensitive substring match
- Searches name + description + content
- Fast, no API cost

**Stage 2: AI ranking (optional)**
- Builds manifest of candidates
- Sends to fast model (Haiku) with JSON schema
- Returns indices of most relevant memories
- Falls back to keyword results on error

---

## AI-Powered Relevance Selection

### Implementation

```python
def _ai_select_memories(
    query: str,
    candidates: list,
    max_results: int,
    config: dict,
) -> list[dict]:
    """Use a fast AI call to select the most relevant memories from candidates.
    
    Falls back to keyword results on any error.
    """
    try:
        from providers import stream, AssistantTurn
        from .scan import scan_all_memories

        headers = scan_all_memories()
        path_to_mtime = {h.file_path: h.mtime_s for h in headers}

        # Build manifest of candidates only
        manifest_lines = []
        for i, e in enumerate(candidates):
            manifest_lines.append(f"{i}: [{e.type}] {e.name} — {e.description}")
        manifest = "\n".join(manifest_lines)

        system = (
            "You select memories relevant to a query. "
            "Return a JSON object with key 'indices' containing a list of integer indices "
            f"(0-based) from the provided list. Select at most {max_results} entries. "
            "Only include indices clearly relevant to the query. Return {\"indices\": []} if none."
        )
        
        messages = [{"role": "user", "content": f"Query: {query}\n\nMemories:\n{manifest}"}]

        result_text = ""
        for event in stream(
            model=config.get("model", "claude-haiku-4-5-20251001"),
            system=system,
            messages=messages,
            tool_schemas=[],
            config={**config, "max_tokens": 256, "no_tools": True},
        ):
            if isinstance(event, AssistantTurn):
                result_text = event.text
                break

        import json as _json
        parsed = _json.loads(result_text)
        selected_indices = [int(i) for i in parsed.get("indices", []) if isinstance(i, int)]

    except Exception:
        # Fall back to keyword results
        selected_indices = list(range(min(max_results, len(candidates))))

    # Build result dicts
    results = []
    for i in selected_indices[:max_results]:
        if i < 0 or i >= len(candidates):
            continue
        entry = candidates[i]
        mtime_s = path_to_mtime.get(entry.file_path, 0)
        results.append({
            "name": entry.name,
            "description": entry.description,
            "type": entry.type,
            "scope": entry.scope,
            "content": entry.content,
            "file_path": entry.file_path,
            "mtime_s": mtime_s,
            "freshness_text": memory_freshness_text(mtime_s),
            "confidence": entry.confidence,
            "source": entry.source,
        })
    
    return results
```

### Example AI Call

**Input:**
```
Query: testing strategy

Memories:
0: [user] user_prefers_tests — User wants comprehensive test coverage
1: [feedback] feedback_no_db_mocks — Don't mock the database in integration tests
2: [project] project_api_migration — API v2 migration in progress
3: [reference] reference_jira — JIRA board at https://company.atlassian.net
```

**AI response:**
```json
{
  "indices": [0, 1]
}
```

**Result:** Returns memories 0 and 1 (both testing-related).

---

## Memory Freshness

### Age Calculation

```python
def memory_age_days(mtime_s: float) -> int:
    """Days since mtime_s (floor-rounded, clamped to 0 for future times)."""
    return max(0, math.floor((time.time() - mtime_s) / 86_400))
```

### Human-Readable Age

```python
def memory_age_str(mtime_s: float) -> str:
    """Human-readable age: 'today', 'yesterday', or 'N days ago'."""
    d = memory_age_days(mtime_s)
    if d == 0:
        return "today"
    if d == 1:
        return "yesterday"
    return f"{d} days ago"
```

### Staleness Warning

```python
def memory_freshness_text(mtime_s: float) -> str:
    """Staleness caveat for memories older than 1 day (empty string if fresh).
    
    Motivated by user reports of stale code-state memories (file:line
    citations to code that has since changed) being asserted as fact.
    """
    d = memory_age_days(mtime_s)
    if d <= 1:
        return ""
    
    return (
        f"This memory is {d} days old. "
        "Memories are point-in-time observations, not live state — "
        "claims about code behavior or file:line citations may be outdated. "
        "Verify against current code before asserting as fact."
    )
```

**Example:**
```
⚠ This memory is 5 days old. Memories are point-in-time observations,
not live state — claims about code behavior or file:line citations may
be outdated. Verify against current code before asserting as fact.
```

---

## System Prompt Guidance

### Memory Type Descriptions

From `types.py`:

```python
MEMORY_TYPE_DESCRIPTIONS: dict[str, str] = {
    "user": (
        "Information about the user's role, goals, responsibilities, and knowledge. "
        "Helps tailor future behavior to the user's preferences."
    ),
    "feedback": (
        "Guidance the user has given about how to approach work — both what to avoid "
        "and what to keep doing. Lead with the rule, then **Why:** and **How to apply:**."
    ),
    "project": (
        "Ongoing work, goals, bugs, or incidents not derivable from code or git history. "
        "Lead with the fact/decision, then **Why:** and **How to apply:**. "
        "Always convert relative dates to absolute dates."
    ),
    "reference": (
        "Pointers to external systems (issue trackers, dashboards, Slack channels, docs)."
    ),
}
```

### What NOT to Save

```python
WHAT_NOT_TO_SAVE = """\
## What NOT to save in memory
- Code patterns, conventions, architecture, file paths, or project structure — derivable from the codebase.
- Git history, recent changes, who-changed-what — use `git log` / `git blame`.
- Debugging solutions or fix recipes — the fix is in the code; the commit message has context.
- Anything already documented in CLAUDE.md files.
- Ephemeral task details: in-progress work, temporary state, current conversation context.

These exclusions apply even when explicitly asked. If asked to save a PR list or activity summary,
ask what was *surprising* or *non-obvious* — that is the part worth keeping."""
```

### Full System Prompt Block

```python
MEMORY_SYSTEM_PROMPT = """\
## Memory system

You have a persistent, file-based memory system. Memories are stored as markdown files with
YAML frontmatter. Build this up over time so future conversations have context about the user,
their preferences, and the work you're doing together.

**Types** (save only what cannot be derived from the codebase):
- **user** — role, goals, knowledge, preferences
- **feedback** — guidance on how to work (corrections AND confirmations of non-obvious approaches)
- **project** — ongoing work, decisions, deadlines not in git history
- **reference** — pointers to external systems (Linear, Grafana, Slack, etc.)

**When to save**: If the user corrects you, confirms an approach, or shares context that should
persist beyond this conversation. For feedback: save corrections AND quiet confirmations.

**Body structure for feedback/project**: Lead with the rule/fact, then:
  **Why:** (reason given) | **How to apply:** (when this guidance kicks in)

**Format**:
{format_example}

**Saving is two steps**:
1. Write the memory to its own file (e.g. `feedback_testing.md`) using MemorySave.
2. The index (MEMORY.md) is updated automatically.

**What NOT to save**: code patterns, architecture, git history, debugging fixes,
anything already in CLAUDE.md, or ephemeral task state.

**Before recommending from memory**: A memory naming a file, function, or flag may be stale.
Verify it still exists before acting on it. For current state, prefer `git log` or reading code.
"""
```

---

## Performance

**Typical operation times:**
- `get_memory_context()`: ~5ms (read 2 index files)
- `find_relevant_memories()` (keyword): ~15ms
- `find_relevant_memories()` (AI): ~500ms (API call)
- `truncate_index_content()`: <1ms

**API costs (AI relevance):**
- Model: claude-haiku-4-5-20251001
- Input: ~200 tokens (manifest)
- Output: ~50 tokens (JSON)
- Cost: ~$0.0001 per search

---

## Next: Part 5 - AI Consolidation

The next part covers how the consolidator automatically extracts memories from completed sessions.
