# cheetahclaws — Instructions for Claude Code

## Persistent memory vault (import)
The shared Obsidian vault holds cross-session memory and defines the `/resume`
and `/save` session commands. Load its instructions:

@~/vault/CLAUDE.md

## Context Navigation (Graphify)

### 3-Layer Query Rule
1. **First:** query `graphify-out/graph.json` (use `graphify query "<question>"`)
   to understand code structure and connections — do NOT re-read the whole tree.
2. **Second:** query the Obsidian vault (`~/vault/cheetahclaws/` for decisions &
   progress, `~/vault/graphify/cheetahclaws/` for per-node maps).
3. **Third:** only read raw code files when editing, or when the first two
   layers don't have the answer.

### When to rebuild the graph
- After structural changes (new modules, major refactors).
- Skill (inside Claude Code): `/graphify . --update` — incremental, only changed files.
- Headless (0 tokens, AST only): `python .graphify_ast_build.py`
- The graph is persistent — NO need to rebuild every session.

### Do NOT
- Don't manually edit files inside `graphify-out/` or `~/vault/graphify/cheetahclaws/`.
- Don't re-read the entire codebase if the graph already has the information.

## Persistent Memory (Obsidian vault)

- `~/vault/cheetahclaws/architecture/` → decisions, conventions
- `~/vault/cheetahclaws/features/`     → planned / implemented features
- `~/vault/cheetahclaws/logs/`         → session logs (written by `/save`)

Start a session with `/resume` to load recent context; end with `/save` to log it.
