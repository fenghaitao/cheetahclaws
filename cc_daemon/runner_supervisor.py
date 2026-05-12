"""runner_supervisor.py — agent-runner subprocess supervision (RFC 0002 F-4).

Owns the lifecycle of one or more `python -m agent_runner --pipe` subprocesses
on behalf of the daemon. Each AgentRunner that today lives in a Python thread
becomes its own OS process so that:

  * a leak / hang / OOM in one runner doesn't take down the daemon,
  * `kill -9 <runner_pid>` is observable as an `agent_runner_crash` event,
  * `agent.stop` RPC delivers a graceful stop within 5 s.

Scope of this initial cut (F-4 skeleton, RFC 0002):

  * POSIX only — `subprocess.Popen` with stdin/stdout pipes, `JsonLineChannel`
    framing. Windows fallback is out of scope; callers must check `enabled()`.
  * Iteration log written via stdout dump (the runner side emits one
    `iteration_done` IPC message per iteration; supervisor persists to
    ``~/.cheetahclaws/agents/<name>/log.jsonl`` for parity with the in-thread
    path). SQLite persistence to the `agent_iterations` table is deferred.
  * Permission flow: when the runner sends ``permission_request``, the
    supervisor's reader thread auto-approves (matches today's
    ``auto_approve=True`` REPL default). Routing to a real PermissionStore
    is deferred to a follow-up.

Acceptance (RFC 0002 §F-4 "Acceptance"):

  ✓ Runner crash (kill -9): supervisor detects exit via proc.poll(), emits
    ``agent_runner_crash`` event with stderr tail.
  ✓ Runner OOM: same code path as kill -9 (process exits with non-zero
    code), supervisor stays up.
  ✓ Runner subprocess stops within 5 s of stop(): graceful "stop" IPC →
    SIGTERM at 2 s → SIGKILL at 5 s.
  ⚠ Iteration-log parity: jsonl format matches today's
    AgentRunner._persist_record. SQLite agent_iterations population is
    follow-up (see schema.py line 74-85 comment "populated in F-4").
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .runner_ipc import IpcReadTimeout, JsonLineChannel

# Lazy import — events module pulls in SQLite; tests that exercise the
# supervisor in isolation shouldn't trigger schema init.
def _get_event_bus():
    try:
        from . import events
        return events.get_bus()
    except Exception:
        return None


def _iso_now() -> str:
    """ISO 8601 UTC timestamp with microsecond precision and Z suffix.
    Same shape as cc_daemon.events._epoch_to_iso so the two columns sort
    consistently when joined on time."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


STDERR_TAIL_BYTES = 4 * 1024
HANDSHAKE_TIMEOUT_S = 5.0
GRACEFUL_STOP_TIMEOUT_S = 2.0      # IPC "stop" → SIGTERM after this
SIGTERM_GRACE_S = 3.0              # SIGTERM → SIGKILL after this
# Total upper bound on stop(): HANDSHAKE_TIMEOUT_S irrelevant here;
# 2 + 3 = 5 s matches the F-4 acceptance criterion.

_LOG_DIR = Path.home() / ".cheetahclaws" / "agents"


# ── Feature flag ──────────────────────────────────────────────────────────


def enabled() -> bool:
    """Return True iff F-4 subprocess-per-runner is active.

    Sources (any one truthy is enough):
      * ``CHEETAHCLAWS_ENABLE_F4`` env var
      * config key ``agent_runner_subprocess`` (callers pass via start())

    Defaults to False. Windows is unsupported regardless.
    """
    if sys.platform.startswith("win"):
        return False
    flag = os.environ.get("CHEETAHCLAWS_ENABLE_F4", "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


# ── Handle / status ───────────────────────────────────────────────────────


@dataclass
class RunnerHandle:
    name:        str
    run_id:      str
    pid:         int
    started_at:  float
    proc:        subprocess.Popen = field(repr=False)
    chan:        JsonLineChannel  = field(repr=False)
    stderr_tail: deque            = field(repr=False,
                                          default_factory=lambda: deque(maxlen=STDERR_TAIL_BYTES))
    _reader:        Optional[threading.Thread] = field(repr=False, default=None)
    _stderr_reader: Optional[threading.Thread] = field(repr=False, default=None)
    iteration:   int   = 0
    status:      str   = "starting"   # starting | running | stopping | stopped | crashed
    error:       str   = ""
    # RFC 0002 F-4 — kept for the agent.list RPC and SQLite agent_runs row.
    template_name: str = ""
    args:          str = ""
    auto_approve:  bool = True

    def is_alive(self) -> bool:
        return self.proc.poll() is None


# ── Registry ──────────────────────────────────────────────────────────────


_handles: dict[str, RunnerHandle] = {}
_handles_lock = threading.Lock()


def get(name: str) -> Optional[RunnerHandle]:
    with _handles_lock:
        h = _handles.get(name)
        if h and not h.is_alive() and h.status not in {"crashed", "stopped"}:
            # Process died before we noticed — reflect that.
            h.status = "crashed"
        return h


def list_all() -> list[RunnerHandle]:
    with _handles_lock:
        return list(_handles.values())


def _register(handle: RunnerHandle) -> None:
    with _handles_lock:
        # Stop any prior runner with the same name first.
        old = _handles.get(handle.name)
        if old and old.is_alive():
            # Caller is expected to have called stop() already; this is
            # just a safety net.
            try:
                old.proc.terminate()
            except ProcessLookupError:
                pass
        _handles[handle.name] = handle


def _unregister(name: str) -> None:
    with _handles_lock:
        _handles.pop(name, None)


# ── Spawn ─────────────────────────────────────────────────────────────────


def start(
    name: str,
    template_name: str,
    args: str,
    config: dict,
    *,
    interval: float = 2.0,
    auto_approve: bool = True,
    python: str = sys.executable,
) -> RunnerHandle:
    """Spawn `python -m agent_runner --pipe` as a child process and return
    its handle after the IPC handshake completes.

    Raises:
        RuntimeError on handshake failure or if F-4 is disabled.
    """
    if sys.platform.startswith("win"):
        raise RuntimeError("F-4 supervisor is POSIX-only in this skeleton")

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    log_dir = _LOG_DIR / name
    log_dir.mkdir(parents=True, exist_ok=True)

    cmd = [python, "-u", "-m", "agent_runner", "--pipe", "--name", name]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        # New session so a SIGTERM to the supervisor doesn't take the
        # runner with it; we manage the runner's lifetime explicitly.
        start_new_session=True,
        env={**os.environ, "CHEETAHCLAWS_F4_CHILD": "1"},
    )

    chan = JsonLineChannel(proc.stdout, proc.stdin)
    handle = RunnerHandle(
        name=name, run_id=run_id, pid=proc.pid,
        started_at=time.time(), proc=proc, chan=chan,
        template_name=template_name, args=args,
        auto_approve=bool(auto_approve),
    )

    # Capture stderr in a background thread so a chatty runner can't
    # block on a full stderr pipe. Tail kept for crash diagnostics.
    def _drain_stderr():
        try:
            for line in iter(proc.stderr.readline, b""):
                handle.stderr_tail.extend(line[-STDERR_TAIL_BYTES:])
        except Exception:
            pass

    t_err = threading.Thread(target=_drain_stderr, daemon=True,
                             name=f"f4-stderr-{name}")
    t_err.start()
    handle._stderr_reader = t_err

    # Send init; the runner must reply with {"op": "ready"} within
    # HANDSHAKE_TIMEOUT_S, else we kill it and raise.
    try:
        chan.send({
            "op": "init",
            "payload": {
                "name":         name,
                "run_id":       run_id,
                "template":     template_name,
                "args":         args,
                "config":       _strip_unserialisable(config),
                "interval":     float(interval),
                "auto_approve": bool(auto_approve),
                "log_dir":      str(log_dir),
            },
        })
        reply = chan.recv(timeout=HANDSHAKE_TIMEOUT_S)
    except (IpcReadTimeout, EOFError, ValueError, BrokenPipeError) as e:
        _hard_kill(proc)
        raise RuntimeError(
            f"agent runner handshake failed: {type(e).__name__}: {e}; "
            f"stderr tail: {bytes(handle.stderr_tail)[-512:]!r}"
        ) from e

    if reply.get("op") != "ready":
        _hard_kill(proc)
        raise RuntimeError(f"agent runner replied {reply!r}, expected 'ready'")

    handle.status = "running"

    # Register and insert the DB row BEFORE starting the reader thread.
    # Otherwise an immediate runner exit observed by the reader's `finally`
    # would race ahead of these calls — publishing `agent_runner_crash`
    # before `agent_runner_start`, and finalising a row that hadn't been
    # inserted yet.
    _register(handle)
    _db_insert_agent_run(handle)
    bus = _get_event_bus()
    if bus is not None:
        try:
            bus.publish("agent_runner_start", {
                "name": name, "run_id": run_id, "pid": proc.pid,
                "template": template_name,
            })
        except Exception:
            pass

    # Now safe to spawn the reader.
    t_read = threading.Thread(target=_reader_loop, args=(handle,),
                              daemon=True, name=f"f4-reader-{name}")
    t_read.start()
    handle._reader = t_read

    return handle


# ── Reader loop (one thread per runner) ───────────────────────────────────


def _reader_loop(handle: RunnerHandle) -> None:
    """Pump IPC messages from the runner. Auto-approves permission
    requests (matches today's default). On EOF, classify as graceful
    exit if proc.returncode == 0, else crash."""
    log_path = _LOG_DIR / handle.name / "log.jsonl"
    bus = _get_event_bus()

    try:
        while True:
            try:
                msg = handle.chan.recv(timeout=1.0)
            except IpcReadTimeout:
                # Periodic poll — keeps the loop responsive to proc death
                # even when the runner is mid-iteration and quiet.
                if not handle.is_alive():
                    break
                continue
            except EOFError:
                break
            except (ValueError, OSError) as e:
                handle.error = f"ipc parse error: {e}"
                break

            # Wrap message dispatch in its own try/except so a malformed
            # field from a buggy runner (e.g. non-int "iteration") can't
            # unwind the reader thread and leave the subprocess orphaned.
            try:
                op = msg.get("op", "")
                if op == "iteration_start":
                    try:
                        handle.iteration = int(msg.get("iteration", handle.iteration))
                    except (TypeError, ValueError):
                        pass    # ignore bad iteration counter, keep going
                elif op == "iteration_done":
                    try:
                        handle.iteration = int(msg.get("iteration", handle.iteration))
                    except (TypeError, ValueError):
                        pass
                    # Persist BEFORE the bus broadcast so any subscriber that
                    # immediately queries the DB sees the row.
                    _persist_iteration_jsonl(log_path, msg)
                    _db_insert_iteration(handle, msg)
                    if bus is not None:
                        try:
                            bus.publish("agent_iteration_done", {
                                "name":       handle.name,
                                "run_id":     handle.run_id,
                                "iteration":  msg.get("iteration"),
                                "status":     msg.get("status"),
                                "duration_s": msg.get("duration_s"),
                            })
                        except Exception:
                            pass
                elif op == "permission_request":
                    # Auto-approve — matches AgentRunner default. Full
                    # PermissionStore routing is a follow-up.
                    try:
                        handle.chan.send({
                            "op":         "permission_response",
                            "request_id": msg.get("request_id", ""),
                            "granted":    True,
                        })
                    except (BrokenPipeError, OSError):
                        break
                elif op == "exit":
                    handle.status = "stopping"   # waits for the proc to actually exit below
                    # Note: don't break here; proc.wait() will be observed.
                elif op == "notify":
                    # Send-fn output — currently dropped; bridge integration
                    # is RFC 0002 F-6/7/8 work.
                    pass
                elif op == "log":
                    # Forward through the daemon's logger when available.
                    # For the skeleton we just bus-publish at info level.
                    if bus is not None:
                        try:
                            bus.publish("agent_runner_log", {
                                "name":  handle.name,
                                "level": msg.get("level", "info"),
                                "msg":   msg.get("msg", ""),
                            })
                        except Exception:
                            pass
            except Exception as e:
                # Malformed payload, programmer error in dispatch, etc.
                # Don't propagate — record the most recent error on the
                # handle (visible via agent.status) and continue reading.
                handle.error = f"reader: {type(e).__name__}: {e}"[:512]

            if not handle.is_alive():
                break
    finally:
        # If the reader unwound while the subprocess is still alive
        # (e.g., an uncaught exception above), kill it so we don't leak
        # a runner that the supervisor no longer monitors.
        if handle.proc.poll() is None:
            _hard_kill(handle.proc)
        # Reap and classify.
        try:
            rc = handle.proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            rc = -1
        stderr_text = bytes(handle.stderr_tail)[-1024:].decode(
            "utf-8", errors="replace")
        prev_status = handle.status
        if rc == 0 or prev_status == "stopping":
            handle.status = "stopped"
            ev = "agent_runner_stopped"
            db_error = None
        else:
            handle.status = "crashed"
            ev = "agent_runner_crash"
            # Truncate to keep the agent_runs.error column reasonable.
            db_error = (f"exit_code={rc}; stderr_tail={stderr_text}"
                        if stderr_text else f"exit_code={rc}")
            db_error = db_error[:1024]
        # Finalize SQLite first so the bus subscribers' DB queries are
        # consistent with the event they just received.
        _db_finalize_run(handle, status=handle.status, error=db_error)
        if bus is not None:
            try:
                bus.publish(ev, {
                    "name":        handle.name,
                    "run_id":      handle.run_id,
                    "pid":         handle.pid,
                    "exit_code":   rc,
                    "iterations":  handle.iteration,
                    "stderr_tail": stderr_text,
                })
            except Exception:
                pass


# ── Stop ──────────────────────────────────────────────────────────────────


def stop(name: str, *, timeout_s: float = 5.0) -> bool:
    """Stop a runner. Returns True iff the process actually exited.

    Order:
      1. Send IPC "stop" (graceful — runner finishes its current iter and exits).
      2. After GRACEFUL_STOP_TIMEOUT_S: SIGTERM.
      3. After GRACEFUL_STOP_TIMEOUT_S + SIGTERM_GRACE_S: SIGKILL.

    Bounded by ``timeout_s`` (default 5 s — matches F-4 acceptance).
    """
    handle = get(name)
    if handle is None:
        return False
    if not handle.is_alive():
        _unregister(name)
        return True

    handle.status = "stopping"
    deadline = time.monotonic() + timeout_s

    # 1) Polite IPC ask.
    try:
        handle.chan.send({"op": "stop"})
    except (BrokenPipeError, OSError):
        pass

    if _wait_until(handle.proc, deadline=min(deadline,
                   time.monotonic() + GRACEFUL_STOP_TIMEOUT_S)):
        _unregister(name)
        return True

    # 2) SIGTERM.
    try:
        os.killpg(os.getpgid(handle.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            handle.proc.terminate()
        except (ProcessLookupError, OSError):
            pass

    if _wait_until(handle.proc, deadline=deadline):
        _unregister(name)
        return True

    # 3) SIGKILL.
    _hard_kill(handle.proc)
    handle.proc.wait(timeout=1.0)
    _unregister(name)
    return True


def _wait_until(proc: subprocess.Popen, *, deadline: float) -> bool:
    """Poll until proc exits or deadline reached. Returns True iff exited."""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return proc.poll() is not None


def _hard_kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass


def stop_all(*, timeout_s: float = 5.0) -> int:
    """Stop every registered runner. Returns the number that exited."""
    names = [h.name for h in list_all()]
    n = 0
    for name in names:
        if stop(name, timeout_s=timeout_s):
            n += 1
    return n


# ── SQLite persistence (agent_runs + agent_iterations) ───────────────────
#
# Every DB write is best-effort: a failed insert/update is logged via
# returning False but never raises, so the supervisor can keep going even
# if the daemon DB is missing or read-only. The schema lives in
# cc_daemon/schema.py (tables created by F-2's init_schema).


def _db_insert_agent_run(handle: "RunnerHandle") -> bool:
    """INSERT one row into agent_runs at start(). Idempotent: a UNIQUE
    PRIMARY KEY violation (caller retried with the same run_id) is
    swallowed because the existing row is already correct. Returns True
    iff the row was inserted (or already present)."""
    try:
        from .schema import get_conn
        conn = get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO agent_runs "
            "(id, name, template, args, status, auto_approve, "
            " started_at, last_iteration) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                handle.run_id,
                handle.name,
                handle.template_name,
                handle.args,
                "running",
                1 if handle.auto_approve else 0,
                _iso_now(),
                0,
            ),
        )
        conn.commit()
        return True
    except Exception:
        return False


def _db_insert_iteration(handle: "RunnerHandle", msg: dict) -> bool:
    """INSERT one row into agent_iterations and UPDATE agent_runs.last_iteration.

    Both writes happen inside one transaction so a duplicate iteration_done
    (PK violation) leaves last_iteration untouched.
    """
    iteration = int(msg.get("iteration", 0) or 0)
    if iteration <= 0:
        return False
    try:
        from .schema import get_conn
        conn = get_conn()
        # INSERT OR IGNORE: re-delivery of the same iteration_done shouldn't
        # double-count. UPDATE only fires when the row was newly inserted
        # to avoid clobbering on retry.
        cur = conn.execute(
            "INSERT OR IGNORE INTO agent_iterations "
            "(run_id, iteration, ts, status, duration_s, summary, "
            " in_tokens, out_tokens, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                handle.run_id,
                iteration,
                _iso_now(),
                str(msg.get("status", "ok")),
                float(msg.get("duration_s", 0.0) or 0.0),
                str(msg.get("summary", ""))[:400],
                int(msg.get("tokens_in", 0) or 0),
                int(msg.get("tokens_out", 0) or 0),
                float(msg.get("cost_usd", 0.0) or 0.0),
            ),
        )
        if cur.rowcount > 0:
            conn.execute(
                "UPDATE agent_runs SET last_iteration = ? "
                "WHERE id = ? AND last_iteration < ?",
                (iteration, handle.run_id, iteration),
            )
        conn.commit()
        return True
    except Exception:
        return False


def _db_finalize_run(handle: "RunnerHandle", *, status: str,
                     error: Optional[str] = None) -> bool:
    """UPDATE agent_runs at process exit. Idempotent — if the row is
    already in the terminal state we still bump ended_at, which is fine
    (a redundant finalize on the same handle is rare but harmless)."""
    if status not in {"stopped", "crashed"}:
        return False
    try:
        from .schema import get_conn
        conn = get_conn()
        conn.execute(
            "UPDATE agent_runs SET status = ?, ended_at = ?, error = ? "
            "WHERE id = ?",
            (status, _iso_now(), error, handle.run_id),
        )
        conn.commit()
        return True
    except Exception:
        return False


# ── Iteration-log persistence (jsonl parity) ──────────────────────────────


def _persist_iteration_jsonl(log_path: Path, msg: dict) -> None:
    """Mirror today's ``AgentRunner._persist_record`` so a runner under
    F-4 produces the same on-disk log as a runner under threads. Format
    locked by agent_runner.py:503-515."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "iteration":  int(msg.get("iteration", 0)),
                "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%S"),
                "status":     str(msg.get("status", "ok")),
                "duration_s": float(msg.get("duration_s", 0.0) or 0.0),
                "summary":    str(msg.get("summary", "")[:400]),
            }) + "\n")
    except Exception:
        # Persistence failure must not crash the supervisor — the
        # runner is still chugging.
        pass


# ── Helpers ───────────────────────────────────────────────────────────────


def _strip_unserialisable(cfg: dict) -> dict:
    """Remove dict entries that won't survive JSON round-trip. Callbacks,
    file handles, threading primitives all live in the parent's config
    today; child subprocess doesn't need them."""
    out: dict = {}
    for k, v in cfg.items():
        try:
            json.dumps(v)
        except (TypeError, ValueError):
            continue
        out[k] = v
    return out


__all__ = [
    "RunnerHandle",
    "enabled",
    "get",
    "list_all",
    "start",
    "stop",
    "stop_all",
]
