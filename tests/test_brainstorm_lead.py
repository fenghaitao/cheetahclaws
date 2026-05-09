"""Tests for the lead-moderator stages of /brainstorm.

The lead role replaces the previous "personas debate then main agent
reads the file and synthesizes" flow with three in-process stages:
  1. Opening — sets the agenda and what to REJECT
  2. Probe   — after each persona, asks one pointed follow-up if the
               contribution was vague (or NO_PROBE if it was concrete)
  3. Synthesis — produces the final dense master plan, no tools needed

Failure modes (LLM call returns "") are tested explicitly because a
flaky lead model must not break the brainstorm flow — we degrade to
the previous behavior, not crash.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import commands.advanced as adv
from commands.advanced import (
    _parse_lead_flag,
    _lead_opening,
    _lead_probe,
    _lead_synthesis,
)


# ── --lead flag parsing ──────────────────────────────────────────────────


@pytest.mark.parametrize("args,expected_lead,expected_remaining", [
    ("the topic",                                    None,                 "the topic"),
    ("",                                             None,                 ""),
    ("--lead claude-opus-4-7 the topic",             "claude-opus-4-7",    "the topic"),
    ("--lead=gpt-5 the topic",                       "gpt-5",              "the topic"),
    ("the topic --lead claude-opus-4-7",             "claude-opus-4-7",    "the topic"),
    ("--lead nim/meta/llama-3.3-70b-instruct topic", "nim/meta/llama-3.3-70b-instruct", "topic"),
])
def test_parse_lead_flag(args, expected_lead, expected_remaining):
    lead, remaining = _parse_lead_flag(args)
    assert lead == expected_lead
    assert remaining == expected_remaining


def test_parse_lead_and_models_compose():
    """--lead and --models can both be present and compose cleanly."""
    args = "--lead claude-opus-4-7 --models gpt-5,nim/deepseek-ai/deepseek-r1 redesign auth"
    lead, rest1 = _parse_lead_flag(args)
    assert lead == "claude-opus-4-7"
    models, rest2 = adv._parse_models_flag(rest1)
    assert models == ["gpt-5", "nim/deepseek-ai/deepseek-r1"]
    assert rest2 == "redesign auth"


# ── Lead helpers (with mocked LLM) ───────────────────────────────────────


def _patch_llm(monkeypatch, response: str):
    """Replace _llm_oneshot with a stub that returns `response`."""
    monkeypatch.setattr(adv, "_llm_oneshot",
                         lambda *_a, **_kw: response)


def test_lead_opening_returns_text(monkeypatch):
    _patch_llm(monkeypatch, "### Lead Opening\n- be specific")
    out = _lead_opening("Pick stocks", "snapshot...", "claude-opus-4-7", {})
    assert "Lead Opening" in out


def test_lead_opening_failure_returns_empty(monkeypatch):
    _patch_llm(monkeypatch, "")
    out = _lead_opening("Pick stocks", "snap", "claude-opus-4-7", {})
    assert out == ""


def test_lead_probe_no_probe_token_yields_empty(monkeypatch):
    """When the lead replies NO_PROBE, the probe call returns empty so
    cmd_brainstorm knows to skip the follow-up round."""
    _patch_llm(monkeypatch, "NO_PROBE")
    out = _lead_probe("topic", "Analyst", "A", "concrete reply", "lead-model", {})
    assert out == ""


def test_lead_probe_no_probe_with_trailing_text(monkeypatch):
    """Tolerate trailing whitespace / explanation after NO_PROBE."""
    _patch_llm(monkeypatch, "NO_PROBE — concrete enough")
    out = _lead_probe("topic", "Analyst", "A", "concrete reply", "lead-model", {})
    assert out == ""


def test_lead_probe_returns_question(monkeypatch):
    """Vague contribution → lead returns a follow-up question."""
    _patch_llm(monkeypatch,
                "> Lead to Agent A: Name the specific ticker, not 'consider tech'.")
    out = _lead_probe("topic", "Analyst", "A", "consider tech", "lead-model", {})
    assert "Lead to Agent A" in out
    assert "ticker" in out


def test_lead_probe_strips_code_fences(monkeypatch):
    """Some models wrap their reply in ```...``` — strip those."""
    _patch_llm(monkeypatch,
                "```\n> Lead to Agent B: which file specifically?\n```")
    out = _lead_probe("topic", "Engineer", "B", "improve modularity", "lead-model", {})
    assert out.startswith("> Lead to Agent B")
    assert "```" not in out


def test_lead_synthesis_returns_text(monkeypatch):
    _patch_llm(monkeypatch,
                "## Consensus\n- buy NVDA (backed by: A, B)\n## Dissents\nNo substantive dissents.\n## Concrete Action Plan\n1. ...\n")
    out = _lead_synthesis("topic", "transcript", "lead", {})
    assert "## Consensus" in out


def test_lead_synthesis_failure_returns_empty(monkeypatch):
    """Lead synthesis failure must be silent — caller falls back."""
    _patch_llm(monkeypatch, "")
    out = _lead_synthesis("topic", "transcript", "lead", {})
    assert out == ""
