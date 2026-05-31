# Memory System - Part 2: Core Operations

## Module: `store.py`

The `store.py` module provides all CRUD operations for memory files.

---

## Data Model

### `MemoryEntry` Dataclass

```python
@dataclass
class MemoryEntry:
    name: str               # Human-readable name (becomes filename slug)
    description: str        # One-line description for search relevance
    type: str              # "user" | "feedback" | "project" | "reference"
    content: str           # Body text of the memory
    file_path: str = ""    # Absolute path to .md file
    created: str = ""      # ISO date "2026-05-31"
    scope: str = "user"    # "user" | "project"
    confidence: float = 1.0  # 0.0-1.0 reliability score
    source: str = "user"   # "user" | "model" | "tool" | "consolidator"
    last_used_at: str = "" # ISO date of last retrieval
    conflict_group: str = "" # Tag for related memories
```

---

## Path Resolution

### Directory Functions

```python
USER_MEMORY_DIR = Path.home() / ".cheetahclaws" / "memory"

def get_project_memory_dir() -> Path:
    """Return .cheetahclaws/memory relative to cwd."""
    return Path.cwd() / ".cheetahclaws" / "memory"

def get_memory_dir(scope: str = "user") -> Path:
    """Return memory directory for given scope."""
    if scope == "project":
        return get_project_memory_dir()
    return USER_MEMORY_DIR
```

**Examples:**
- User scope: `~/.cheetahclaws/memory/`
- Project scope: `/home/user/myproject/.cheetahclaws/memory/`

---

## Core Operations

### 1. Save Memory

```python
def save_memory(entry: MemoryEntry, scope: str = "user") -> None:
    """Write/update a memory file and rebuild the index.
    
    If a memory with the same name (slug) already exists, it is overwritten.
    """
    mem_dir = get_memory_dir(scope)
    mem_dir.mkdir(parents=True, exist_ok=True)
    
    slug = _slugify(entry.name)  # "User Prefers Tests" → "user_prefers_tests"
    fp = mem_dir / f"{slug}.md"
    
    fp.write_text(_format_entry_md(entry))
    entry.file_path = str(fp)
    entry.scope = scope
    
    _rewrite_index(scope)  # Rebuild MEMORY.md
```

**Slugification:**
```python
def _slugify(name: str) -> str:
    """Convert name to filesystem-safe slug (max 60 chars)."""
    s = name.lower().strip().replace(" ", "_")
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s[:60]
```

**Examples:**
- `"User Prefers Tests"` → `"user_prefers_tests"`
- `"API v2 Migration Plan"` → `"api_v2_migration_plan"`
- `"Don't Mock DB in Tests"` → `"dont_mock_db_in_tests"`

### 2. Delete Memory

```python
def delete_memory(name: str, scope: str = "user") -> None:
    """Remove the memory file matching name and rebuild the index.
    
    No error if not found.
    """
    mem_dir = get_memory_dir(scope)
    slug = _slugify(name)
    fp = mem_dir / f"{slug}.md"
    
    if fp.exists():
        fp.unlink()
    
    _rewrite_index(scope)
```

### 3. Load Entries

```python
def load_entries(scope: str = "user") -> list[MemoryEntry]:
    """Scan all .md files (except MEMORY.md) in a scope and return entries.
    
    Returns:
        List of MemoryEntry sorted alphabetically by name.
    """
    mem_dir = get_memory_dir(scope)
    if not mem_dir.exists():
        return []
    
    entries: list[MemoryEntry] = []
    for fp in sorted(mem_dir.glob("*.md")):
        if fp.name == INDEX_FILENAME:  # Skip MEMORY.md
            continue
        
        try:
            text = fp.read_text()
        except Exception:
            continue
        
        meta, body = parse_frontmatter(text)
        entries.append(MemoryEntry(
            name=meta.get("name", fp.stem),
            description=meta.get("description", ""),
            type=meta.get("type", "user"),
            content=body,
            file_path=str(fp),
            created=meta.get("created", ""),
            scope=scope,
            confidence=float(meta.get("confidence", 1.0)),
            source=meta.get("source", "user"),
            last_used_at=meta.get("last_used_at", ""),
            conflict_group=meta.get("conflict_group", ""),
        ))
    
    return entries
```

### 4. Load Index

```python
def load_index(scope: str = "all") -> list[MemoryEntry]:
    """Load memory entries from one or both scopes.
    
    Args:
        scope: "user", "project", or "all" (both combined)
    
    Returns:
        List of MemoryEntry (user entries first, then project).
    """
    if scope == "all":
        return load_entries("user") + load_entries("project")
    return load_entries(scope)
```

### 5. Search Memory

```python
def search_memory(query: str, scope: str = "all") -> list[MemoryEntry]:
    """Case-insensitive keyword match on name + description + content.
    
    Returns:
        List of matching MemoryEntry objects.
    """
    q = query.lower()
    results = []
    
    for entry in load_index(scope):
        haystack = f"{entry.name} {entry.description} {entry.content}".lower()
        if q in haystack:
            results.append(entry)
    
    return results
```

**Search strategy:**
- Simple substring match (case-insensitive)
- Searches across name, description, and content
- No ranking at this layer (done in `context.py`)

---

## Frontmatter Parsing

### Parse Function

```python
def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse ---\\nkey: value\\n---\\nbody format.
    
    Returns:
        (meta_dict, body_str)
    """
    if not text.startswith("---"):
        return {}, text
    
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    
    meta: dict = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
    
    return meta, parts[2].strip()
```

**Example:**
```markdown
---
name: user_prefers_tests
description: User wants tests for all features
type: user
created: 2026-05-31
confidence: 1.0
---

User prefers comprehensive test coverage.
```

**Parsed result:**
```python
meta = {
    "name": "user_prefers_tests",
    "description": "User wants tests for all features",
    "type": "user",
    "created": "2026-05-31",
    "confidence": "1.0"
}
body = "User prefers comprehensive test coverage."
```

### Format Function

```python
def _format_entry_md(entry: MemoryEntry) -> str:
    """Render a MemoryEntry as a markdown file with YAML frontmatter."""
    lines = [
        "---",
        f"name: {entry.name}",
        f"description: {entry.description}",
        f"type: {entry.type}",
        f"created: {entry.created}",
    ]
    
    # Optional fields (only if non-default)
    if entry.confidence != 1.0:
        lines.append(f"confidence: {entry.confidence:.2f}")
    if entry.source and entry.source != "user":
        lines.append(f"source: {entry.source}")
    if entry.last_used_at:
        lines.append(f"last_used_at: {entry.last_used_at}")
    if entry.conflict_group:
        lines.append(f"conflict_group: {entry.conflict_group}")
    
    lines.append("---")
    lines.append(entry.content)
    
    return "\n".join(lines) + "\n"
```

---

## Index Management

### Rebuild Index

```python
def _rewrite_index(scope: str) -> None:
    """Rebuild MEMORY.md for the given scope from all .md files in that dir."""
    mem_dir = get_memory_dir(scope)
    if not mem_dir.exists():
        return
    
    index_path = mem_dir / INDEX_FILENAME
    entries = load_entries(scope)
    
    lines = [
        f"- [{e.name}]({Path(e.file_path).name}) — {e.description}"
        for e in entries
    ]
    
    index_path.write_text("\n".join(lines) + ("\n" if lines else ""))
```

**Generated index example:**
```markdown
- [user_prefers_tests](user_prefers_tests.md) — User wants tests for all features
- [feedback_code_style](feedback_code_style.md) — Use 4-space indentation
- [reference_jira](reference_jira.md) — JIRA board at https://company.atlassian.net
```

### Get Index Content

```python
def get_index_content(scope: str = "user") -> str:
    """Return raw MEMORY.md content for the given scope, or '' if absent."""
    mem_dir = get_memory_dir(scope)
    index_path = mem_dir / INDEX_FILENAME
    
    if not index_path.exists():
        return ""
    
    return index_path.read_text().strip()
```

---

## Conflict Detection

### Check Conflict

```python
def check_conflict(entry: MemoryEntry, scope: str = "user") -> dict | None:
    """Check whether a same-named memory already exists with different content.
    
    Returns a dict with the existing memory's key fields if a conflict is found,
    or None if no existing file or if the content is identical.
    """
    mem_dir = get_memory_dir(scope)
    slug = _slugify(entry.name)
    fp = mem_dir / f"{slug}.md"
    
    if not fp.exists():
        return None
    
    try:
        meta, existing_content = parse_frontmatter(fp.read_text())
    except Exception:
        return None
    
    # No conflict if content is identical
    if existing_content.strip() == entry.content.strip():
        return None
    
    return {
        "existing_content": existing_content.strip(),
        "existing_confidence": float(meta.get("confidence", 1.0)),
        "existing_created": meta.get("created", ""),
        "existing_source": meta.get("source", "user"),
    }
```

**Usage in `MemorySave` tool:**
```python
conflict = check_conflict(entry, scope=scope)
save_memory(entry, scope=scope)

if conflict:
    msg += (
        f"\n⚠ Replaced conflicting memory"
        f" (was {conflict['existing_source']}-sourced, "
        f"{conflict['existing_confidence']:.0%} confidence, "
        f"written {conflict['existing_created'] or 'unknown date'})."
    )
```

---

## Last Used Tracking

### Touch Last Used

```python
def touch_last_used(file_path: str) -> None:
    """Update the last_used_at frontmatter field of a memory file to today.
    
    Called by MemorySearch when a memory is returned so staleness/utility
    tracking stays current. Silent on any error.
    """
    from datetime import date
    
    fp = Path(file_path)
    if not fp.exists():
        return
    
    try:
        text = fp.read_text()
        meta, body = parse_frontmatter(text)
        today = date.today().isoformat()
        
        # Skip if already up to date
        if meta.get("last_used_at") == today:
            return
        
        meta["last_used_at"] = today
        
        # Rebuild frontmatter
        fm_lines = ["---"]
        for k in ("name", "description", "type", "created", "confidence",
                   "source", "last_used_at", "conflict_group"):
            v = meta.get(k)
            if v is not None and str(v):
                fm_lines.append(f"{k}: {v}")
        fm_lines.append("---")
        
        new_text = "\n".join(fm_lines) + "\n" + body + "\n"
        fp.write_text(new_text)
    
    except Exception:
        pass  # Silent failure
```

**When called:**
- Every time `MemorySearch` returns a memory
- Updates `last_used_at` to today's date
- Helps identify unused/stale memories

---

## Constants

```python
INDEX_FILENAME = "MEMORY.md"
MAX_INDEX_LINES = 200      # Matches Claude Code
MAX_INDEX_BYTES = 25_000   # Matches Claude Code
```

---

## Error Handling

All operations are **defensive**:
- `load_entries()` - Skips unreadable files silently
- `delete_memory()` - No error if file doesn't exist
- `touch_last_used()` - Silent on any error
- `parse_frontmatter()` - Returns empty dict on parse failure

**Rationale:** Memory operations should never crash the agent loop.

---

## Thread Safety

**Not thread-safe** - All operations assume single-threaded access.

**Why:** Memory operations are infrequent (user-initiated or consolidator-triggered), and the REPL is single-threaded.

**Future:** If daemon mode needs concurrent access, add file locking.

---

## Performance

**Typical operation times** (on SSD):
- `save_memory()`: ~5ms (write + index rebuild)
- `load_entries()`: ~10ms (scan 50 files)
- `search_memory()`: ~15ms (load + filter)
- `_rewrite_index()`: ~3ms (write single file)

**Scaling:**
- Linear with number of memory files
- Capped at 200 files per scope (MAX_MEMORY_FILES)
- Index truncation prevents unbounded growth

---

## Next: Part 3 - LLM Tools

The next part covers how the LLM interacts with memories via `MemorySave`, `MemorySearch`, `MemoryDelete`, and `MemoryList` tools.
