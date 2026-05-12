"""agent_methods.py — `agent.*` JSON-RPC methods (RFC 0002 F-4).

Thin wrappers over :mod:`cc_daemon.runner_supervisor` so external clients
(REPL `/agent` command, future Web UI, third-party tools) can manage
agent runners through the daemon's RPC channel instead of importing the
supervisor directly.

Exposed methods:

    agent.start(name, template, args="", interval=2.0, auto_approve=True)
        Spawn a runner subprocess and return its handle dict.

    agent.stop(name, timeout_s=5.0)
        Stop a runner. Returns {"name", "stopped": bool}.

    agent.list()
        Return all currently-tracked runners.

    agent.status(name)
        Return one runner's status dict. 404-equivalent (returns
        {"name", "found": False}) if no runner with that name.

F-4 keeps these methods open to any authenticated caller — same
single-user threat model as F-3's monitor.* methods. Per-method
authorisation arrives with the originator routing in a follow-up.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .rpc import RpcRegistry

if TYPE_CHECKING:
    from .server import DaemonState


def _handle_to_dict(handle) -> dict:
    """Serialise a RunnerHandle for the wire. Drops process / channel
    references (not JSON-serialisable) and exposes only the fields a
    caller can act on."""
    return {
        "name":          handle.name,
        "run_id":        handle.run_id,
        "pid":           handle.pid,
        "status":        handle.status,
        "iteration":     handle.iteration,
        "started_at":    handle.started_at,
        "template":      handle.template_name,
        "args":          handle.args,
        "auto_approve":  handle.auto_approve,
        "alive":         handle.is_alive(),
        "error":         handle.error,
    }


def register(registry: RpcRegistry, daemon_state: "DaemonState") -> None:

    def agent_start(params: dict, _ctx) -> dict:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise TypeError("agent.start requires non-empty 'name'")
        template = params.get("template")
        if not isinstance(template, str) or not template:
            raise TypeError("agent.start requires non-empty 'template'")
        args = str(params.get("args", "") or "")
        try:
            interval = float(params.get("interval", 2.0))
        except (TypeError, ValueError) as e:
            raise TypeError(f"agent.start: 'interval' must be numeric: {e}")
        auto_approve = bool(params.get("auto_approve", True))

        # Pull config from the daemon's own loaded config; the runner
        # subprocess inherits a JSON-safe subset (see
        # runner_supervisor._strip_unserialisable).
        config = dict(daemon_state.config or {})

        from . import runner_supervisor as rs
        handle = rs.start(
            name=name, template_name=template, args=args,
            config=config, interval=interval, auto_approve=auto_approve,
        )
        return _handle_to_dict(handle)

    def agent_stop(params: dict, _ctx) -> dict:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise TypeError("agent.stop requires non-empty 'name'")
        try:
            timeout_s = float(params.get("timeout_s", 5.0))
        except (TypeError, ValueError) as e:
            raise TypeError(f"agent.stop: 'timeout_s' must be numeric: {e}")
        from . import runner_supervisor as rs
        return {"name": name, "stopped": rs.stop(name, timeout_s=timeout_s)}

    def agent_list(_params: dict, _ctx) -> dict:
        from . import runner_supervisor as rs
        return {"runners": [_handle_to_dict(h) for h in rs.list_all()]}

    def agent_status(params: dict, _ctx) -> dict:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise TypeError("agent.status requires non-empty 'name'")
        from . import runner_supervisor as rs
        h = rs.get(name)
        if h is None:
            return {"name": name, "found": False}
        d = _handle_to_dict(h)
        d["found"] = True
        return d

    registry.register("agent.start",  agent_start)
    registry.register("agent.stop",   agent_stop)
    registry.register("agent.list",   agent_list)
    registry.register("agent.status", agent_status)
