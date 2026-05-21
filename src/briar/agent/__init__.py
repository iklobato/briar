"""Autonomous agent runtime.

`briar agent prfix --company X [--pr N]` invokes an Anthropic-API-driven
loop that reads PR review threads, applies minimum-correct fixes, and
commits + pushes follow-ups as the human GitHub identity. The system
prompt comes from the pr-fixer archetype + the spliced knowledge for
the company; the tools are git/gh/file-edit primitives behind a strict
allowlist.

The runner is deliberately separate from the scheduler — opt-in via the
CLI for now. Wiring it into the scheduled prfix task is a follow-up.
"""

from briar.agent.runner import AgentRunner, AgentRunResult

__all__ = ["AgentRunner", "AgentRunResult"]
