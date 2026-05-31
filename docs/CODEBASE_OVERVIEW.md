# CheetahClaws Codebase Overview

## What is CheetahClaws?

CheetahClaws is a **Python-native AI coding assistant** that reimplements Claude Code's functionality while supporting **any LLM model** (Claude, GPT, Gemini, Qwen, DeepSeek, local Ollama models, etc.). It's designed to be:

- **Hackable**: ~40K lines of readable Python vs Claude Code's 283K lines of compiled TypeScript
- **Multi-provider**: Switch between 8+ providers with a single flag
- **Extensible**: Runtime tool registration, MCP servers, plugins, skills
- **Production-ready**: Web UI, bridges (Telegram/WeChat/Slack), autonomous agents

---

## Architecture at a Glance

```
User Input (Terminal/Web/Telegram/WeChat/Slack)
    ↓
cheetahclaws.py (REPL + slash commands)
    ↓
agent.py (multi-turn generator loop)
    ├→ providers.py (Anthropic/OpenAI/Gemini/Ollama/...)
    ├→ tool_registry.py → tools/ (27+ built-in tools)
    ├→ compaction.py (context window management)
    ├→ memory/ (persistent cross-session memory)
    ├→ multi_agent/ (sub-agents with git worktree isolation)
    └→ bridges/ (Telegram/WeChat/Slack remote control)
```

---

## Core Components

### 1. Entry Points

| File | Purpose |
|------|---------|
| **cheetahclaws.py** | Main REPL, slash command dispatcher, permission UI, streaming renderer |
| **bootstrap.py** | Startup sequence: logging → tool registry → health server |
| **agent.py** | Core agent loop (174 lines) - yields typed events (TextChunk, ToolStart, ToolEnd, TurnDone) |

### 2. Provider System (`providers.py`)

Unified streaming interface for 8+ providers:
- **Anthropic** (Claude Opus/Sonnet/Haiku)
- **OpenAI** (GPT-4o, o1, o3)
- **Google Gemini** (2.5 Pro, 2.0 Flash)
- **Kimi** (Moonshot)
- **Qwen** (Alibaba DashScope)
- **Zhipu** (GLM)
- **DeepSeek** (Chat, Reasoner)
- **MiniMax**
- **Ollama** (local models)
- **LM Studio** (local GUI)
- **Custom** (any OpenAI-compatible endpoint)
- **LiteLLM** (AWS Bedrock, Azure, Vertex AI)

Each provider adapter:
1. Converts neutral message format → provider-specific format
2. Streams responses as typed events
3. Handles tool calling (function calling)
4. Tracks token usage and costs

### 3. Tool System

**Tool Registry** (`tool_registry.py`):
```python
@dataclass
class ToolDef:
    name: str              # "Read", "Write", "Bash", etc.
    schema: dict           # JSON schema for LLM
    func: Callable         # (params, config) -> str
    read_only: bool        # auto-approve in 'auto' mode
    concurrent_safe: bool  # can run in parallel
```

**27+ Built-in Tools** (`tools/`):
- **File ops**: Read, Write, Edit, Glob, Grep
- **Shell**: Bash (with denylist for dangerous commands)
- **Web**: WebFetch, WebSearch
- **Code**: NotebookEdit (Jupyter), GetDiagnostics (pyright/mypy/flake8)
- **Memory**: MemorySave, MemorySearch, MemoryList, MemoryDelete
- **Agents**: Agent, SendMessage, CheckAgentResult, ListAgentTasks
- **Tasks**: TaskCreate, TaskUpdate, TaskGet, TaskList
- **Skills**: Skill, SkillList
- **Interaction**: AskUserQuestion, SleepTimer
- **Plan mode**: EnterPlanMode, ExitPlanMode

**5 Registration Paths**:
1. Built-ins (`tools/__init__.py`)
2. Extension packages (`memory/`, `multi_agent/`, `skill/`, `cc_mcp/`, `task/`)
3. Plugins (`plugin/loader.py`)
4. Modular ecosystem (`modular/*/tools.py`)
5. Checkpoint hooks (monkey-patches Write/Edit)

### 4. Context Management (`compaction.py`)

**Two-layer compression**:
1. **Snip layer** (cheap): Truncate old tool outputs
2. **AI summarization**: LLM condenses older turns

**Auto-fanout** (`multi_agent/fanout.py`):
- When a single tool result > 0.4 × context window
- Splits into chunks → parallel sub-LLM calls → merge
- Critical for 32K local models reading large files

**Dynamic max_tokens cap**:
- `input + output + 1024 safety ≤ context_window`
- Per-model context registry (Qwen, Llama, Mistral, Phi, Gemma, DeepSeek)

### 5. Memory System (`memory/`)

**Dual-scope persistent memory**:
- **User scope**: `~/.cheetahclaws/memory/user/`
- **Project scope**: `.cheetahclaws/memory/project/`

**4 memory types**:
- `fact`: Objective information
- `preference`: User preferences
- `pattern`: Code patterns
- `context`: Project context

**Features**:
- Confidence scoring (0.0-1.0)
- Source tracking
- Recency-weighted search
- Conflict detection
- `/memory consolidate` - AI extracts insights from session

### 6. Multi-Agent System (`multi_agent/`)

**Sub-agent types**:
- `coder`: Code implementation
- `reviewer`: Code review
- `researcher`: Information gathering
- `analyst`: Data analysis
- `tester`: Test generation

**Features**:
- Git worktree isolation (each agent gets own branch)
- Background mode (runs in thread)
- Depth limiting (prevent infinite recursion)
- Message passing between agents

### 7. Bridges (`bridges/`)

**Remote control from messaging apps**:

**Telegram** (`telegram.py`):
- Bot API long-polling
- Slash command passthrough
- File upload/download
- Typing indicator
- Job queue with `!jobs`/`!cancel`

**WeChat** (`wechat.py`):
- iLink Bot API (QR code login)
- Smart reply with `/draft`
- Context token per peer
- Session auto-recovery

**Slack** (`slack.py`):
- Web API polling
- In-place message updates
- Channel threading
- Auto-start on launch

All bridges share:
- Interactive session management
- Permission prompt routing
- Tool step tracking
- Persistent job log

---

## Key Packages

### `commands/` - Slash Commands

**37+ slash commands** organized by file:

**core.py**: `/help`, `/clear`, `/history`, `/context`, `/cost`, `/status`, `/doctor`

**config_cmd.py**: `/model`, `/config`, `/permissions`, `/verbose`, `/thinking`

**session.py**: `/save`, `/load`, `/resume`, `/export`, `/copy`

**advanced.py**: 
- `/brainstorm` - Multi-persona adversarial debate
- `/worker` - Auto-implement tasks from todo_list.txt
- `/ssj` - Developer power menu (15 shortcuts)
- `/memory` - Memory management
- `/skills` - Skill templates
- `/agents` - Sub-agent control
- `/mcp` - MCP server management
- `/plugin` - Plugin system
- `/tasks` - Task management

**checkpoint_plan.py**: `/checkpoint`, `/rewind`, `/plan`

**agent_cmd.py**: `/agent` - Autonomous background agents

**monitor_cmd.py**: `/subscribe`, `/monitor` - AI-monitored topics

**research_cmd.py**: `/research` - Multi-source research (20 sources)

**lab_cmd.py**: `/lab` - Research lab (autonomous paper writing)

### `modular/` - Optional Features

**Auto-discovered modules** with `cmd.py` and/or `tools.py`:

**voice/** (`/voice`):
- Offline Whisper STT (local, no API key)
- Keyterm booster (git branch + project files)
- Multi-language support
- Auto-submit after transcription

**video/** (`/video`):
- AI video pipeline: story → TTS → images → subtitles → MP4
- 10 viral content niches
- Landscape or short format
- Zero-cost path (Gemini TTS + placeholders)

**trading/** (`/trading`):
- Multi-agent analysis (Bull/Bear → Judge → Risk → PM)
- 4 backtest strategies (dual_ma, RSI, Bollinger, MACD)
- Paper trading with calibration
- Anomaly detection + alerts
- US/HK/A-share stocks + 20+ cryptos

### `web/` - Web UI

**Production-ready browser interface**:
- Multi-user accounts (bcrypt + JWT)
- SQLite session persistence
- Chat UI with streaming, tool cards, permission prompts
- PTY terminal (xterm.js) with 100% CLI parity
- Settings panel (model picker, API keys, permissions)
- Light/dark/system theme
- Session CRUD + markdown export
- Ops endpoints (`/health`, `/metrics`)
- 21 pytest end-to-end tests

**No build step**: 9 vanilla JS modules, no React/Node.js

### `cc_mcp/` - MCP Integration

**Model Context Protocol client**:
- Stdio/SSE/HTTP transports
- OAuth 2.0 PKCE flow
- Auto-register remote tools as `mcp__<server>__<tool>`
- `.mcp.json` config file
- `/mcp` commands for server management

### `cc_kernel/` - Agent OS Layer

**Process-based agent runtime** (daemon foundation):
- Process table + capability model
- Quota ledger (token/cost budgets)
- Scheduler (cron-like for agents)
- Mailbox (inter-agent messaging)
- AgentFS (sandboxed filesystem)
- Observability (structured events)
- Tool inventory + dispatch
- Streaming IPC

**27 RFCs** (0003-0032) document the design.

### `cc_daemon/` - Daemon Mode

**Background server** (`cheetahclaws serve`):
- Unix socket + JWT auth
- RPC methods (agent.start, session.send, bridge.start, etc.)
- Bridge supervisor (Telegram/WeChat/Slack)
- Runner supervisor (subprocess agent isolation)
- Proactive scheduler (auto-wake agents)
- Event bus (SSE for web UI)

**CLI**: `cheetahclaws daemon {status, stop, logs, rotate-token}`

---

## Data Flow

### 1. User Message → Agent Loop

```
User types: "Fix the bug in utils.py"
    ↓
cheetahclaws.py: parse_input() → run_agent()
    ↓
agent.py: run(user_message, state, config, system_prompt)
    ↓
context.py: build_system_prompt() (base + memory + env)
    ↓
compaction.py: maybe_compact() (if approaching context limit)
    ↓
providers.py: stream(messages, tools, config)
    ↓
[API call to Claude/GPT/Gemini/Ollama/...]
    ↓
← TextChunk events (streamed back)
← ToolStart("Read", {"path": "utils.py"})
    ↓
tool_registry.py: execute_tool("Read", params, config)
    ↓
tools/fs.py: read_file()
    ↓
← ToolEnd("Read", result="<file contents>")
    ↓
[Loop continues until model says "done"]
    ↓
← TurnDone(input_tokens=1234, output_tokens=567)
    ↓
session_store.py: save_session() (auto-save)
```

### 2. Permission Flow

```
Model calls: Bash("rm -rf /")
    ↓
agent.py: check permission_mode
    ↓
If mode == "manual":
    ← PermissionRequest("Execute: rm -rf /")
    ↓
    cheetahclaws.py: show_permission_prompt()
    ↓
    User: [Deny]
    ↓
    ← ToolEnd("Bash", result="[Permission denied]", permitted=False)
```

### 3. Bridge Flow (Telegram)

```
User sends Telegram message: "/status"
    ↓
bridges/telegram.py: poll_updates()
    ↓
bridges/interactive_session.py: handle_message()
    ↓
Check if slash command → commands/core.py: handle_status()
    ↓
← Response text
    ↓
bridges/telegram.py: send_message()
    ↓
User sees reply in Telegram
```

---

## Configuration

**Config hierarchy**:
1. Defaults (`cc_config.py::DEFAULT_CONFIG`)
2. `~/.cheetahclaws/config.json` (user-wide)
3. `.cheetahclaws/config.json` (project-specific)
4. CLI flags (`--model`, `--accept-all`, etc.)
5. Runtime overrides (`/config key=value`)

**Key settings**:
```python
{
    "model": "claude-sonnet-4-6",
    "permission_mode": "auto",  # auto/accept-all/manual/plan
    "preserve_last_n_turns": 5,
    "auto_fanout_enabled": True,
    "auto_fanout_threshold": 0.4,
    "rich_live": True,
    "log_level": "INFO",
    "health_check_port": 8765,
    # Provider API keys
    "anthropic_api_key": "sk-ant-...",
    "openai_api_key": "sk-...",
    # ... etc
}
```

---

## Testing

**2347+ tests** across:
- Unit tests (`test_*.py`)
- Integration tests (`e2e_*.py`)
- Provider tests (skipif-gated on API keys)
- Web UI tests (21 HTTP end-to-end)
- Kernel tests (Agent OS layer)
- Trading tests (discovery, pipeline, advanced)

**Run tests**:
```bash
pytest tests/ -x -q                    # all tests
pytest tests/test_agent.py             # specific file
pytest tests/ -k "not e2e"             # skip e2e tests
```

---

## Extension Points

### 1. Add a New Tool

```python
# tools/my_tool.py
from tool_registry import register_tool

def my_tool_impl(params: dict, config: dict) -> str:
    # Your logic here
    return "result"

register_tool({
    "name": "MyTool",
    "schema": {
        "type": "object",
        "properties": {
            "input": {"type": "string"}
        },
        "required": ["input"]
    },
    "func": my_tool_impl,
    "read_only": False,
    "concurrent_safe": True
})
```

### 2. Add a Slash Command

```python
# commands/my_cmd.py
def handle_my_command(args: str, state, config) -> str:
    # Your logic here
    return "Command executed"

# In cheetahclaws.py COMMANDS dict:
COMMANDS["/mycommand"] = handle_my_command
```

### 3. Add a Provider

```python
# providers.py
def stream_my_provider(messages, tools, config):
    # Convert messages to provider format
    # Make API call
    # Yield TextChunk/ToolStart/ToolEnd events
    pass

PROVIDERS["myprovider"] = {
    "stream": stream_my_provider,
    "detect": lambda m: m.startswith("myprovider/"),
    "cost_per_1k": {"input": 0.01, "output": 0.03}
}
```

### 4. Create a Plugin

```
my-plugin/
├── plugin.json          # metadata
├── tools.py            # TOOL_DEFS list
├── cmd.py              # COMMAND_DEFS list
└── skills/             # optional skill templates
    └── my_skill.md
```

Install: `/plugin install my-plugin@https://github.com/user/my-plugin`

---

## Key Design Patterns

### 1. Generator-Based Agent Loop

`agent.py::run()` is a generator that yields typed events:
- **TextChunk**: Streaming text from model
- **ThinkingChunk**: Reasoning tokens (Claude Extended Thinking, DeepSeek-R1)
- **ToolStart**: Model called a tool
- **ToolEnd**: Tool execution finished
- **PermissionRequest**: Needs user approval
- **TurnDone**: Turn complete with token counts

This allows:
- Streaming UI updates
- Permission prompts mid-turn
- Bridge notifications
- Web UI SSE events

### 2. Neutral Message Format

Internal format is provider-agnostic:
```python
{
    "role": "user" | "assistant" | "tool",
    "content": str,
    "tool_calls": [...],  # optional
    "images": [...]       # optional
}
```

Each provider adapter converts to/from its native format.

### 3. Tool Registry Pattern

Single global registry, multiple registration paths:
- Built-ins register at import time
- Extensions register via `_EXTENSION_MODULES`
- Plugins register via loader
- MCP tools register dynamically

All tools have same interface: `(params: dict, config: dict) -> str`

### 4. Permission Gating

Three layers:
1. **Tool-level**: `read_only` flag
2. **Mode-level**: `auto`/`accept-all`/`manual`/`plan`
3. **Runtime**: `RuntimeContext.permission_callback`

Bridges can inject custom permission handlers.

### 5. Context Compression

Layered approach:
1. **Per-call cap**: `input + output + 1024 ≤ ctx`
2. **Truncation**: `execute_tool` caps at 32K chars
3. **Auto-fanout**: Split large results → parallel summarize
4. **Snip**: Truncate old tool outputs
5. **AI summarize**: Condense older turns

### 6. Error Resilience

Three mechanisms:
1. **Error classifier**: Categorize API errors
2. **Circuit breaker**: Trip after N failures
3. **Retry with backoff**: Exponential backoff for transient errors

---

## Performance Characteristics

**Startup time**: ~200ms (cold), ~50ms (warm)

**Memory usage**:
- Base: ~50MB
- With web UI: ~100MB
- With active agent: ~150MB

**Token efficiency**:
- Snip layer: 0 API cost
- AI summarization: ~1K tokens per compaction
- Auto-fanout: N+1 API calls (N chunks + 1 reduce)

**Concurrency**:
- Tools marked `concurrent_safe=True` run in parallel
- Sub-agents run in ThreadPoolExecutor
- Bridge polling in separate threads

---

## Security

**Sandboxing**:
- Bash tool has denylist (`rm -rf /`, fork bombs, `mkfs`, etc.)
- Read/Write/Edit have credential-path denylist (`~/.ssh/id_*`, `~/.aws`, etc.)
- Plugin loader confines to `install_dir`
- MCP servers can't inject `LD_PRELOAD`/`PYTHONPATH`

**Authentication**:
- Web UI: bcrypt + JWT (7-day cookie)
- Daemon: Unix socket + JWT
- Terminal: one-time 32-char password

**CSRF protection**:
- Web UI: double-submit cookie (`ccsrf`)
- All POST/PUT/PATCH/DELETE require `X-CSRF-Token` header

**Bot tokens**:
- Prefer env vars (`$TELEGRAM_BOT_TOKEN`, `$SLACK_BOT_TOKEN`)
- Legacy `/telegram <token>` syntax auto-scrubs from history

See [`docs/guides/security.md`](docs/guides/security.md) for full details.

---

## Roadmap

**Completed**:
- ✅ Multi-provider support (8+ providers)
- ✅ Web UI with auth + persistence
- ✅ Bridges (Telegram/WeChat/Slack)
- ✅ MCP integration
- ✅ Plugin system
- ✅ Memory system
- ✅ Multi-agent system
- ✅ Trading agent
- ✅ Research pipeline (20 sources)
- ✅ Daemon foundation (F-1 to F-9)

**In Progress**:
- 🚧 Agent OS layer (cc_kernel/)
- 🚧 Research Lab (autonomous paper writing)

**Planned**:
- 📋 Mobile app (iOS/Android)
- 📋 VSCode extension
- 📋 More bridges (Discord, Matrix, iMessage)
- 📋 More MCP servers
- 📋 More trading strategies

See [`docs/roadmap/ROADMAP.md`](docs/roadmap/ROADMAP.md) for details.

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for:
- Code style guide
- PR checklist
- Testing requirements
- Documentation standards

Quick start:
```bash
git clone https://github.com/SafeRL-Lab/cheetahclaws.git
cd cheetahclaws
pip install -r requirements.txt
pytest tests/ -x -q
python cheetahclaws.py
```

---

## Resources

**Documentation**:
- [README.md](README.md) - User guide
- [CONTRIBUTING.md](CONTRIBUTING.md) - Contributor guide
- [docs/architecture.md](docs/architecture.md) - Architecture deep dive
- [docs/guides/](docs/guides/) - Feature guides
- [docs/RFC/](docs/RFC/) - Design RFCs

**Community**:
- GitHub Issues: Bug reports + feature requests
- GitHub Discussions: Q&A + ideas
- Pull Requests: Code contributions

---

## License

Apache 2.0 - See [LICENSE](LICENSE)
