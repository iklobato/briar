# REFACTORING.md — final, ready to execute

Action runbook consolidating [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) §17 + §18 + §19 + §20 + §21. Supersedes all prior versions of this file.

**Plan in one paragraph.** Commit the meeting subsystem first (it's complete and waiting in the working tree). Then ~2.5 days of austere refactoring: 4 correctness fixes (T0.1–T0.4 — T0.5 is absorbed by Step 0b); `AgentOp.CONSUMES_EXTRACTORS` ClassVar + auto-attach in `CommandAgent.add_arguments` (eliminates ~25 LOC of duplicate flag registration); four StrEnum/IntEnum declarations (`StopReason`, `ExitCode`, `MeetingExtractMode`, `PlanCardStatus`) centralizing 3 magic strings × 10+ sites + 6 magic integers × 15+ sites; drop the three `_fetch_*_context` staticmethods and replace with a polymorphic `TaskScopedExtractor.fetch_or_skip` method on the base class; `AgentRunConfig` parameter object replacing 13 kwargs on `AgentRunner.__init__`; `Tool` Protocol + `Dict[str, Tool]` registry replacing the if-chain in `_dispatch_tool` and dropping the redundant `except Exception` clause; `MeetingExtractedData` TypedDict at the meeting boundary. Net: ≈ −210 LOC, **0 new module functions, 0 new context managers, 4 new enums + 1 new dataclass + 1 new Protocol + 1 new TypedDict + 1 new base-class method + 1 new ClassVar pattern**, 3 broad catches removed, 5 silent-fail paths surfaced, 0 behaviour changes for users.

---

## Table of contents

- [0. Pre-flight](#0-pre-flight)
- [1. Branch + commit strategy](#1-branch--commit-strategy)
- [2. Step-by-step](#2-step-by-step)
  - [Step −1 — Land meeting subsystem](#step-1--land-meeting-subsystem)
  - [Step 0a — Tier-0 correctness fixes](#step-0a--tier-0-correctness-fixes)
  - [Step 0b — `CONSUMES_EXTRACTORS` + auto-attach](#step-0b--consumes_extractors--auto-attach)
  - [Step 0c — Introduce 4 enums](#step-0c--introduce-4-enums)
  - [Step 1 — Drop `_fetch_*_context` + add `fetch_or_skip`](#step-1--drop-_fetch__context--add-fetch_or_skip)
  - [Step 2 — `AgentRunConfig` parameter object](#step-2--agentrunconfig-parameter-object)
  - [Step 3 — `Tool` Protocol + registry + drop `except Exception`](#step-3--tool-protocol--registry--drop-except-exception)
  - [Step 5 — `MeetingExtractedData` TypedDict](#step-5--meetingextracteddata-typeddict)
- [3. Verification gates](#3-verification-gates)
- [4. Rollback playbook](#4-rollback-playbook)
- [5. Final acceptance checklist](#5-final-acceptance-checklist)
- [6. PR templates](#6-pr-templates)

---

## 0. Pre-flight

```bash
git status                          # confirm what's modified vs untracked
git fetch origin
git log --oneline -5
pytest -x                           # MUST be green before any refactor work
ruff check src tests
black --check src tests
mypy src 2>&1 | tee /tmp/mypy-baseline.txt   # capture pre-existing errors

# Capture behaviour baseline (every step except Step 0a + Step 0b must NOT change these)
briar agent prfix --help > /tmp/before-prfix.txt 2>&1
briar agent implement --help > /tmp/before-implement.txt 2>&1
briar plan --help > /tmp/before-plan.txt 2>&1
```

---

## 1. Branch + commit strategy

Per global rules ([`~/.claude/CLAUDE.md`](file:///Users/iklo/.claude/CLAUDE.md)): **no direct commits to `main`**. One branch per logical step; commits within a branch may be split per sub-step for review-ability.

```
main
  feat/meeting-subsystem               ← Step −1, ships independently
  refactor/step-0a-t0-correctness      ← Step 0a, 4 small commits
  refactor/step-0b-consumes-extractors ← Step 0b
  refactor/step-0c-enums               ← Step 0c, 4 commits (one per enum)
  refactor/step-1-fetch-or-skip        ← Step 1
  refactor/step-2-agent-run-config     ← Step 2
  refactor/step-3-tool-registry        ← Step 3 (folds in Step 4)
  refactor/step-5-meeting-typeddict    ← Step 5
```

Each branch rebases on the previous one's merged commit. Squash-merge to `main`.

**Commit message style** (matches repo convention):

```
<type>(<scope>): <imperative summary under 70 chars>

<body — explain WHY, not WHAT>
```

Types: `feat` (Step −1), `fix` (Step 0a sub-commits), `refactor` (everything else).

---

## 2. Step-by-step

### Step −1 — Land meeting subsystem

**Goal.** The working tree contains a complete-but-uncommitted meeting subsystem (extractors, provider, tests, archetype wiring). Land it as its own PR before any refactor work starts. This unblocks Step 0c (`MeetingExtractMode` enum), Step 5 (TypedDict), and T0.1–T0.3.

**Branch.** `feat/meeting-subsystem`

**Files to commit:**

| Category | Paths |
|---|---|
| New (untracked) | `src/briar/extract/_meeting.py`, `src/briar/extract/_meetings/{__init__,fireflies}.py`, `src/briar/extract/meeting_context.py`, `src/briar/extract/meeting_digest.py`, `tests/test_extract_meetings.py` |
| Modified — meeting wiring | `src/briar/commands/agent.py` (meeting flags + fetch wiring), `src/briar/extract/__init__.py` (registry), `src/briar/extract/base.py` (`MeetingBackedExtractor`, `TaskScopedMeetingExtractor`), `src/briar/env_vars.py` (Fireflies env var), `examples/all_features.yaml` (example block), `src/briar/iac/scaffold/_knowledge.py`, `src/briar/iac/scaffold/archetypes/{engineer,pr_fixer}.py` (archetypes consume new extractors) |
| Modified — UNRELATED, exclude | `DEPLOY_EC2.md`, `Taskfile.yml` — split into separate commit if intentional, else stash |

**Verify before PR:**

```bash
pytest -x tests/test_extract_meetings.py
ruff check src/briar/extract/_meeting*.py src/briar/extract/_meetings \
            src/briar/extract/meeting_context.py src/briar/extract/meeting_digest.py
briar agent prfix --help        # parser builds; --meeting flags appear
briar agent implement --help
```

**Commit message:**

```
feat(extract): add meeting subsystem (Fireflies provider + extractors)

New plug-in family symmetric to RepositoryProvider / TrackerProvider:
- MeetingProvider ABC + MeetingBackedExtractor / TaskScopedMeetingExtractor
- FirefliesMeetingProvider as first concrete adapter
- ExtractMeetingDigest (scheduled last-N-days summaries + action items)
- FetchMeetingContext (JIT — by-id or top-K relevant transcripts)
- Wired into _run_prfix and _run_implement; engineer + pr-fixer archetypes
  consume meeting context automatically

Adding Otter / Granola / Read.ai = one module under _meetings/ + one
tuple entry in MEETINGS registry.
```

---

### Step 0a — Tier-0 correctness fixes

**Goal.** Surface three silent-failure paths. No structural changes. 4 sub-commits in one branch.

**Branch.** `refactor/step-0a-t0-correctness` (rebased on Step −1)

#### T0.1 — `MeetingProvider.get_meeting` becomes `@abstractmethod`

**File:** `src/briar/extract/_meeting.py:92`

```diff
-    def get_meeting(self, meeting_id: str) -> MeetingDetail:
-        """Fetch one meeting with the full transcript populated.
-        Default: returns an empty `MeetingDetail` — concrete providers
-        override to hit their single-meeting endpoint."""
-        return MeetingDetail(
-            meeting=Meeting(
-                meeting_id=meeting_id,
-                title="",
-                started_at="",
-                duration_sec=0,
-                organizer="",
-            )
-        )
+    @abstractmethod
+    def get_meeting(self, meeting_id: str) -> MeetingDetail:
+        """Fetch one meeting with the full transcript populated."""
```

**Verify:** `pytest -x tests/test_extract_meetings.py` — `FirefliesMeetingProvider` already implements `get_meeting`, so no test break.

**Commit:** `fix(extract): mark MeetingProvider.get_meeting abstract`

#### T0.2 — UTF-8 truncation `ignore` → `replace`

**File:** `src/briar/extract/meeting_context.py:175–179`

```diff
     if len(transcript.encode("utf-8")) > max_bytes:
         # Truncate to the byte budget on a UTF-8 boundary, then
         # mark the cut explicitly so the agent knows it's partial.
-        truncated = transcript.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
+        log.info("meeting %s transcript truncated: %d -> %d bytes",
+                 m.meeting_id, len(transcript.encode("utf-8")), max_bytes)
+        truncated = transcript.encode("utf-8")[:max_bytes].decode("utf-8", errors="replace")
         transcript = truncated + f"\n\n_…transcript truncated at {max_bytes} bytes; fetch full via `--meeting-key {m.meeting_id}`._"
```

**Commit:** `fix(extract): use errors=replace and log when transcript is truncated`

#### T0.3 — Narrow `except Exception` in `ExtractMeetingDigest.is_available`

**File:** `src/briar/extract/meeting_digest.py:56`

```diff
+from briar.errors import CliError
+
 class ExtractMeetingDigest(MeetingBackedExtractor):
     def is_available(self, args: argparse.Namespace) -> bool:
         try:
             provider = self._meeting(args)
-        except Exception:  # noqa: BLE001
+        except CliError:
             return False
         return provider.is_available()
```

**Commit:** `fix(extract): narrow ExtractMeetingDigest.is_available to CliError`

#### T0.4 — Drop silent github fallback in `_implement_specific_instructions`

**File:** `src/briar/commands/agent.py:638–641`

```diff
-        try:
-            cloner = _resolve_cloner(provider)
-        except RuntimeError:
-            # Unknown provider — degrade gracefully with the GitHub
-            # recipe rather than crashing the instruction build.
-            cloner = REPO_CLONERS["github"]
+        cloner = _resolve_cloner(provider)  # raises RuntimeError on unknown — desired
```

Note: Step 0b adds `choices=` to `--provider` at the argparse layer, so the CLI catches typos before they reach this method.

**Commit:** `fix(agent): remove silent github fallback in _implement_specific_instructions`

> **T0.5 is absorbed by Step 0b** — `CONSUMES_EXTRACTORS` + auto-attach is a structural fix that makes `choices=` arrive automatically. No separate work needed in `commands/agent.py`.

---

### Step 0b — `CONSUMES_EXTRACTORS` + auto-attach

**Goal.** Eliminate ~25 LOC of duplicate flag registration between `PrfixOp`, `ImplementOp`, and the task-scoped extractors. Each op declares which extractors it consumes; the dispatcher auto-attaches those extractors' `add_arguments`. As a side effect, the `choices=` validation already in `TaskScopedMeetingExtractor` / `TaskScopedTrackerExtractor` / `TaskScopedRepoExtractor` arrives on the prfix/implement subparser automatically (resolves T0.5).

**Branch.** `refactor/step-0b-consumes-extractors`

#### 0b.1 — Add `CONSUMES_EXTRACTORS` ClassVar to `AgentOp`

**File:** `src/briar/commands/agent.py` (around line 34, `class AgentOp`)

```python
class AgentOp(ABC):
    name: ClassVar[str]
    help: ClassVar[str]
    CONSUMES_EXTRACTORS: ClassVar[Tuple[str, ...]] = ()  # new

    @abstractmethod
    def add_arguments(self, parser: argparse.ArgumentParser) -> None: ...
    @abstractmethod
    def run(self, agent_cmd: "CommandAgent", args: argparse.Namespace) -> int: ...
```

#### 0b.2 — Declare the consumption per op

```python
class PrfixOp(AgentOp):
    name = "prfix"
    help = "Address open review comments on a PR (pr-fixer archetype)."
    CONSUMES_EXTRACTORS = ("pr-review-context", "meeting-context")

    def add_arguments(self, parser):
        # ONLY op-specific flags — meeting flags arrive via auto-attach
        parser.add_argument("--company", required=True, ...)
        parser.add_argument("--owner", required=True, ...)
        parser.add_argument("--repo", required=True, ...)
        parser.add_argument("--pr", type=int, required=True, ...)
        parser.add_argument("--store", default="postgres", choices=["file", "postgres"], ...)
        # … REMOVE all --meeting-* / --tracker / --provider registrations

class ImplementOp(AgentOp):
    name = "implement"
    help = "Implement one ticket end-to-end (engineer archetype)."
    CONSUMES_EXTRACTORS = ("ticket-context", "meeting-context")
    # … same pattern, only op-specific flags
```

#### 0b.3 — Update `CommandAgent.add_arguments` to auto-attach

```python
class CommandAgent(Command):
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        subparsers = parser.add_subparsers(dest="agent_op", required=True)
        for op in AGENT_OPS.values():
            sp = subparsers.add_parser(op.name, help=op.help)
            op.add_arguments(sp)
            for ext_name in op.CONSUMES_EXTRACTORS:
                ext = TASK_SCOPED_EXTRACTORS.get(ext_name)
                if ext is not None:
                    ext.add_arguments(sp)   # adds --meeting choices=, etc.
```

**Verify:**

```bash
briar agent prfix --help          # confirm --meeting choices= appears
briar agent prfix --meeting bogus # exit 2 with "invalid choice"
diff /tmp/before-prfix.txt <(briar agent prfix --help)
# Only diff should be choices= additions; no flag missing
```

**Tests.** Add `tests/test_commands_agent.py`:

```python
def test_prfix_meeting_flag_validates_choices(capsys):
    from briar.cli import build_parser
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["agent", "prfix", "--meeting", "no-such-provider",
                           "--company", "acme", "--owner", "o", "--repo", "r", "--pr", "1"])
    assert "invalid choice" in capsys.readouterr().err
```

**Estimated diff:** −30 / +15 LOC.

**Commit:** `refactor(agent): AgentOp declares CONSUMES_EXTRACTORS; CommandAgent auto-attaches extractor flags`

---

### Step 0c — Introduce 4 enums

**Goal.** Centralize 3 magic strings × 10+ sites and 6 magic integers × 15+ sites into named enum members. Per-domain location (one `_enums.py` per package).

**Branch.** `refactor/step-0c-enums` (4 commits — one per enum)

#### 0c.1 — `StopReason` (StrEnum)

**New file:** `src/briar/agent/_enums.py`

```python
"""Closed enumerations for the agent subsystem."""
from __future__ import annotations
from enum import StrEnum


class StopReason(StrEnum):
    """Canonical reasons an LLM turn ended.

    Each LLM provider translates its vendor-specific stop_reason to one
    of these values. Anything else is a bug in the provider adapter.
    """
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    DRY_RUN = "dry_run"
    MAX_ITERATIONS = "max_iterations"
    UNEXPECTED = "unexpected"
```

**Migrate call sites** (StrEnum equality with bare strings still works during rollout):

- `src/briar/agent/runner.py:164` — `if stop == StopReason.END_TURN:`
- `src/briar/agent/runner.py:168` — `if stop != StopReason.TOOL_USE:`
- `src/briar/agent/runner.py:313` — `stop_reason=StopReason.DRY_RUN,`
- `src/briar/agent/_llms/anthropic_llm.py:185` — `elif block_type == "tool_use":` stays (it's the SDK's block type, not our stop reason)
- `src/briar/agent/_llms/bedrock.py:143–146`:
  ```python
  if stop == "endTurn":
      stop = StopReason.END_TURN
  elif stop == "toolUse":
      stop = StopReason.TOOL_USE
  ```
- `src/briar/agent/_llms/openai_llm.py:116,118`:
  ```python
  stop = StopReason.END_TURN
  stop = StopReason.TOOL_USE
  ```
- `src/briar/agent/_llms/gemini.py:139` — same

**Commit:** `refactor(agent): add StopReason enum centralising LLM stop_reason magic strings`

#### 0c.2 — `ExitCode` (IntEnum)

**New file:** `src/briar/commands/_enums.py`

```python
"""Closed enumerations for the CLI command surface."""
from __future__ import annotations
from enum import IntEnum


class ExitCode(IntEnum):
    """CLI process exit codes returned by Command.run.

    Conventional: 0 = success, 2 = usage error (argparse-compatible),
    3-9 = pre-LLM failures, 10+ reserved for future LLM/agent failures.
    """
    OK = 0
    GENERAL_ERROR = 1
    USAGE_ERROR = 2
    STORE_OPEN_FAILED = 3
    CLONE_FAILED = 4
    GIT_CONFIG_FAILED = 5
    AGENT_ERROR = 6
```

**Migrate every `return <int>` in `commands/agent.py` + `commands/plan.py`** to use named members. IntEnum is wire-compatible (`return ExitCode.CLONE_FAILED` is identical to `return 4`).

**Commit:** `refactor(commands): add ExitCode enum replacing scattered integer returns`

#### 0c.3 — `MeetingExtractMode` (StrEnum)

**New file:** `src/briar/extract/_enums.py`

```python
"""Closed enumerations for the extract subsystem."""
from __future__ import annotations
from enum import StrEnum


class MeetingExtractMode(StrEnum):
    """Which fetch path produced a meeting ExtractedSection.data payload."""
    BY_ID = "by-id"
    SEARCH = "search"
    DIGEST = "digest"
```

**Migrate** the dict-literal mode keys in `meeting_context.py:_fetch_one`, `_fetch_by_query`, and `meeting_digest.py:_render_meeting`.

**Commit:** `refactor(extract): add MeetingExtractMode enum for meeting data mode field`

#### 0c.4 — `PlanCardStatus` (StrEnum)

**New file:** `src/briar/plan/_enums.py`

```python
"""Closed enumerations for the plan subsystem."""
from __future__ import annotations
from enum import StrEnum


class PlanCardStatus(StrEnum):
    """Lifecycle states for one card in an ImplementationPlan."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
```

**Migrate** `src/briar/plan/_models.py`:

```python
# line 39: status: str = "pending"  ← was
status: PlanCardStatus = PlanCardStatus.PENDING

# line 60: status=str(raw.get("status") or "pending"),  ← was
status=PlanCardStatus(raw.get("status") or "pending"),  # raises ValueError on unknown

# line 120: done = {c.key for c in self.cards if c.status == "done"}
done = {c.key for c in self.cards if c.status == PlanCardStatus.DONE}

# line 122: if card.status != "pending":
if card.status != PlanCardStatus.PENDING:
```

**Tests.** Add to `tests/test_plan.py`:

```python
def test_plan_card_status_rejects_unknown_value():
    from briar.plan._models import PlanCard
    with pytest.raises(ValueError):
        PlanCard.from_dict({"key": "X-1", "title": "t", "status": "in-progress"})  # typo
```

**Commit:** `refactor(plan): add PlanCardStatus enum replacing string status field`

**Estimated total for Step 0c:** 4 new files (~80 LOC), ~30 call site migrations.

---

### Step 1 — Drop `_fetch_*_context` + add `fetch_or_skip`

**Goal.** Remove `CommandAgent._fetch_ticket_context`, `_fetch_pr_context`, `_fetch_meeting_context` — they wrap `extractor.fetch(args)` in `try/except Exception: return []`, swallowing real bugs. Replace with `TaskScopedExtractor.fetch_or_skip` polymorphic method on the base class.

**Branch.** `refactor/step-1-fetch-or-skip`

#### 1.1 — Add `fetch_or_skip` to `TaskScopedExtractor`

**File:** `src/briar/extract/base.py`

```python
class TaskScopedExtractor(ABC):
    name: ClassVar[str]
    description: ClassVar[str]

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    @abstractmethod
    def fetch(self, args: argparse.Namespace) -> ExtractedSection: ...

    def fetch_or_skip(self, args: argparse.Namespace) -> List[ExtractedSection]:
        """Fetch and wrap as a list; empty list if the result is empty.
        Lets exceptions propagate — this is NOT a swallow."""
        section = self.fetch(args)
        if section.is_empty:
            return []
        log.info("fetched %s: %s (%d bytes)", self.name, section.title, len(section.body))
        return [section]
```

Add `import logging; log = logging.getLogger(__name__)` at top if not present.

#### 1.2 — Delete the three staticmethods

**File:** `src/briar/commands/agent.py`

Remove lines 593–609 (`_fetch_ticket_context`), 710–735 (`_fetch_pr_context`), 738–778 (`_fetch_meeting_context`). All three full removals.

#### 1.3 — Update the four call sites

| Call site (current) | Replace with |
|---|---|
| `commands/agent.py:330` `task_sections = self._fetch_pr_context(...)` | See pattern below |
| `commands/agent.py:340` `task_sections += self._fetch_meeting_context(...)` | Same pattern |
| `commands/agent.py:431` `task_sections = self._fetch_ticket_context(...)` | Same |
| `commands/agent.py:446` `task_sections += self._fetch_meeting_context(...)` | Same |

Pattern:

```python
ext = TASK_SCOPED_EXTRACTORS.get("pr-review-context")
if ext:
    task_sections += ext.fetch_or_skip(args)
```

**Tests.** Add to `tests/test_commands_agent.py`:

```python
def test_fetch_or_skip_propagates_extractor_exception():
    """Regression: previously _fetch_*_context swallowed extractor bugs as []."""
    from briar.extract.base import TaskScopedExtractor, ExtractedSection
    class Boom(TaskScopedExtractor):
        name = "boom"
        description = "test"
        def fetch(self, args):
            raise RuntimeError("upstream 500")
    with pytest.raises(RuntimeError, match="upstream 500"):
        Boom().fetch_or_skip(argparse.Namespace())

def test_fetch_or_skip_returns_empty_for_empty_section():
    from briar.extract.base import TaskScopedExtractor, EMPTY_SECTION
    class Quiet(TaskScopedExtractor):
        name = "quiet"
        description = "test"
        def fetch(self, args):
            return EMPTY_SECTION
    assert Quiet().fetch_or_skip(argparse.Namespace()) == []
```

**Estimated diff:** −80 / +25 LOC.

**Commit:** `refactor(extract): add fetch_or_skip method; drop _fetch_*_context staticmethods`

---

### Step 2 — `AgentRunConfig` parameter object

**Goal.** Replace `AgentRunner.__init__`'s 13 keyword-only parameters with one `AgentRunConfig` dataclass + the LLM dependency.

**Branch.** `refactor/step-2-agent-run-config`

#### 2.1 — Define `AgentRunConfig`

**File:** `src/briar/agent/runner.py` (near top, just below `AgentRunResult`)

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class AgentRunConfig:
    """Everything AgentRunner needs except its LLM."""
    company: str
    task: str
    archetype_name: str
    workdir: Path
    knowledge_store: Any                # KnowledgeStore (Any to avoid cycle)
    target: str
    oauth_token: str = ""
    model: str = ""
    max_iterations: int = 30
    extra_user_instructions: str = ""
    task_context_sections: Tuple[ExtractedSection, ...] = ()
    dry_run: bool = False
    messages: Mapping[str, Any] = field(default_factory=dict)
```

Note: `frozen=True, kw_only=True, slots=True` is the modern Python value-object form. No `__post_init__` validation unless a field has a real invariant beyond its type.

#### 2.2 — Rewrite `__init__`

```python
class AgentRunner:
    DEFAULT_MAX_TOKENS_PER_TURN = 8_000

    def __init__(self, config: AgentRunConfig, *,
                 llm: Optional[LLMProvider] = None,
                 llm_kind: str = "anthropic"):
        self._cfg = config
        self._llm = llm or make_llm(llm_kind, model=config.model)
        self._archetype = ARCHETYPES.get(config.archetype_name)
        if self._archetype is None:
            raise ValueError(f"unknown archetype: {config.archetype_name}")
        self._bash = BashTool(base_cwd=config.workdir)
        self._read = ReadFileTool(allowed_roots=[config.workdir])
        self._write = WriteFileTool(allowed_roots=[config.workdir])
        self._edit = EditFileTool(allowed_roots=[config.workdir])
        self._send = (SendMessageTool(messages=config.messages, company=config.company)
                      if config.messages else None)
```

#### 2.3 — Sed every `self._<field>` to `self._cfg.<field>` inside the class

Mechanical edit. `mypy src/briar/agent/runner.py` catches any miss.

#### 2.4 — Update call sites in `commands/agent.py` + `commands/plan.py`

Find every `AgentRunner(...)` and wrap kwargs in `AgentRunConfig(...)`:

```bash
grep -rn "AgentRunner(" src/briar tests
```

Each becomes:

```python
result = AgentRunner(AgentRunConfig(
    company=...,
    task=...,
    # … same 13 fields
)).run()
```

#### 2.5 — Tests

Add `tests/test_agent_runner.py`:

```python
from pathlib import Path
from briar.agent.runner import AgentRunner, AgentRunConfig, AgentRunResult
from briar.agent._enums import StopReason

def test_agent_dry_run_returns_dry_run_stop_reason(tmp_path):
    cfg = AgentRunConfig(
        company="acme", task="prfix", archetype_name="pr-fixer",
        workdir=tmp_path, knowledge_store=None, target="acme/repo",
        dry_run=True,
    )
    result = AgentRunner(cfg, llm=None).run()
    assert isinstance(result, AgentRunResult)
    assert result.stop_reason == StopReason.DRY_RUN
```

**Estimated diff:** −40 / +50 LOC.

**Commit:** `refactor(agent): replace AgentRunner's 13 kwargs with AgentRunConfig dataclass`

---

### Step 3 — `Tool` Protocol + registry + drop `except Exception`

**Goal.** Replace the sequential `if name == self._bash.name: ... if name == self._read.name: ...` chain in `_dispatch_tool` with a `Dict[str, Tool]` lookup. Drop the redundant `except Exception` clause that hides programmer errors as confused LLM-facing strings.

**Branch.** `refactor/step-3-tool-registry`

#### 3.1 — Declare `Tool` Protocol

**File:** `src/briar/agent/tools.py` (top of file)

```python
from typing import Any, ClassVar, Dict, Protocol, runtime_checkable

@runtime_checkable
class Tool(Protocol):
    """Structural type the 5 existing tools already conform to."""
    name: ClassVar[str]
    description: ClassVar[str]
    INPUT_SCHEMA: ClassVar[Dict[str, Any]]

    def run(self, **kwargs: Any) -> str: ...
```

#### 3.2 — Build the registry in `AgentRunner.__init__`

Replace the per-tool attribute assignments (set up by Step 2):

```python
tools: List[Tool] = [
    BashTool(base_cwd=config.workdir),
    ReadFileTool(allowed_roots=[config.workdir]),
    WriteFileTool(allowed_roots=[config.workdir]),
    EditFileTool(allowed_roots=[config.workdir]),
]
if config.messages:
    tools.append(SendMessageTool(messages=config.messages, company=config.company))
self._tools: Dict[str, Tool] = {t.name: t for t in tools}
```

#### 3.3 — Replace `_dispatch_tool`

**File:** `src/briar/agent/runner.py:334–355` (whole method body)

```python
def _dispatch_tool(self, name: str, raw_input: Any, result: AgentRunResult) -> Dict[str, Any]:
    tool = self._tools.get(name)
    if tool is None:
        return {"content": f"unknown tool {name!r}", "is_error": True}
    try:
        output = tool.run(**(raw_input or {}))
    except ToolError as exc:
        log.warning("tool %s error: %s", name, exc)
        return {"content": str(exc), "is_error": True}
    # NOTE: no `except Exception` — a tool raising non-ToolError is a
    # programmer error; let it propagate to the outer LLM-loop catch at
    # runner.py:144 which records result.error and returns cleanly.
    # See ARCHITECTURE_MAP.md §18 E2.
    if name == "bash":
        self._record_commit_if_any(output, result)
    result.tool_calls += 1
    return {"content": output, "is_error": False}
```

#### 3.4 — Tests

```python
def test_dispatch_tool_propagates_non_tool_error(tmp_path):
    """Step 4 (E2): non-ToolError exceptions are programmer errors;
    must NOT be caught locally."""
    cfg = AgentRunConfig(company="a", task="t", archetype_name="pr-fixer",
                         workdir=tmp_path, knowledge_store=None, target="t")
    runner = AgentRunner(cfg, llm=MagicMock())
    class BadTool:
        name = "bad"; description = ""; INPUT_SCHEMA = {}
        def run(self, **_): raise RuntimeError("programmer error")
    runner._tools["bad"] = BadTool()
    with pytest.raises(RuntimeError, match="programmer error"):
        runner._dispatch_tool("bad", {}, AgentRunResult(company="a", task="t"))
```

**Verify:**

```bash
grep -n "if name ==" src/briar/agent/runner.py     # must be empty
grep -c "except Exception" src/briar/agent/runner.py   # must equal 2 (was 3)
```

**Estimated diff:** −35 / +20 LOC.

**Commit:** `refactor(agent): tool dispatch via Dict[str, Tool]; drop redundant except Exception`

---

### Step 5 — `MeetingExtractedData` TypedDict

**Goal.** Type-only annotation for what meeting extractors put in `ExtractedSection.data`. Uses `MeetingExtractMode` enum from Step 0c.

**Branch.** `refactor/step-5-meeting-typeddict`

#### 5.1 — Define the TypedDict

**File:** `src/briar/extract/_types.py` (new — small file co-located with `_enums.py`)

```python
"""Type-only contracts for ExtractedSection.data payloads."""
from __future__ import annotations
from typing import List, TypedDict

from briar.extract._enums import MeetingExtractMode


class MeetingExtractedData(TypedDict, total=False):
    """Shape of ExtractedSection.data populated by meeting-* extractors.

    `total=False` because by-id, search, and digest modes populate
    different subsets. Consumers should use .get(...), not [...].
    """
    mode: MeetingExtractMode
    meeting_id: str
    started_at: str         # ISO-8601
    attendees: List[str]
    query: str              # search mode only
    match_count: int        # search mode only
    title: str              # digest mode subsection title
```

#### 5.2 — Annotate the producers

`src/briar/extract/meeting_context.py` `_fetch_one` and `_fetch_by_query`, plus `meeting_digest.py:_render_meeting` — annotate the data dict with `MeetingExtractedData`. `ExtractedSection.data` field type stays `Dict[str, Any]` for wire compatibility; the TypedDict is the internal contract.

**Estimated diff:** +40 / 0 LOC.

**Commit:** `refactor(extract): MeetingExtractedData TypedDict at meeting boundary`

---

## 3. Verification gates

These run at each step boundary. Merge condition: all green.

| Gate | Command | Required for |
|---|---|---|
| Unit tests | `pytest -x` | every step |
| Lint | `ruff check src tests` | every step |
| Format | `black --check src tests` | every step |
| Types | `mypy src` (no worse than `/tmp/mypy-baseline.txt`) | every step |
| CLI smoke | `briar agent prfix --help && briar agent implement --help && briar plan --help` | steps 0b, 1, 2, 3 |
| Help diff | `diff /tmp/before-prfix.txt <(briar agent prfix --help)` — should differ ONLY by `choices:` additions after Step 0b | step 0b |
| If-chain audit | `grep -n "if name ==" src/briar/agent/runner.py` returns nothing | step 3 |
| Broad-except audit | `grep -c "except Exception" src/briar/agent/runner.py` returns exactly 2 (lines 144 + 186 stay; 352 removed) | step 3 |

---

## 4. Rollback playbook

Every step is one squash-merged PR. To revert any single step:

```bash
git checkout main && git pull --ff-only
git log --oneline --grep="<commit summary>" -1
git revert <sha>
```

Order of safety to revert (least → most disruptive):

1. Step 5 (TypedDict) — type-only, never breaks runtime
2. Step 0c (enums) — StrEnum/IntEnum is wire-compatible; revertible value-by-value
3. Step 0a sub-commits — each independent
4. Step 2 (`AgentRunConfig`) — mechanical; revertable but cascades to call sites
5. Step 3 (`Tool` registry + drop E2) — touches `_dispatch_tool`; reverting brings back the if-chain
6. Step 1 (drop `_fetch_*_context`) — bigger behavioural change (silent-fail E1 removed)
7. Step 0b (`CONSUMES_EXTRACTORS`) — touches the dispatcher; reverting reinstates flag duplication
8. Step −1 (meeting subsystem) — biggest single landing; revert means re-stashing the WIP

If any PR's CI fails, **do not** force-merge — push a fix to the same branch. Per global rules: never `--no-verify` past a failing hook.

---

## 5. Final acceptance checklist

After all branches merge to `main`:

- [ ] `pytest -x` green
- [ ] `ruff check src tests` clean
- [ ] `black --check src tests` clean
- [ ] `mypy src` no worse than `/tmp/mypy-baseline.txt`
- [ ] `grep -c "except Exception" src/briar/agent/runner.py` returns **2** (was 3)
- [ ] `grep -n "if name ==" src/briar/agent/runner.py` returns nothing
- [ ] `grep -n "_fetch_ticket_context\|_fetch_pr_context\|_fetch_meeting_context" src/briar` returns nothing
- [ ] `grep -n "class AgentRunConfig" src/briar/agent/runner.py` returns 1 match
- [ ] `grep -n "class Tool" src/briar/agent/tools.py` returns 1 match (Protocol)
- [ ] `grep -rn "class StopReason\|class ExitCode\|class MeetingExtractMode\|class PlanCardStatus" src/briar` returns 4 matches (one per package's `_enums.py`)
- [ ] `grep -n "class MeetingExtractedData" src/briar/extract` returns 1 match
- [ ] `grep -n "CONSUMES_EXTRACTORS" src/briar/commands/agent.py` returns ≥ 3 (base + 2 ops)
- [ ] `briar agent prfix --meeting bogus` exits 2 with "invalid choice"
- [ ] `briar agent implement --provider bogus` exits 2 with "invalid choice"
- [ ] LOC delta on the touched files matches the per-step estimates within ±20%

**Net per [`ARCHITECTURE_MAP.md`](ARCHITECTURE_MAP.md) §21:**

| Metric | Target |
|---|---|
| LOC removed | ≈ −210 |
| New module functions | 0 |
| New private helpers | 0 |
| New context managers | 0 |
| New enums | 4 |
| New dataclasses | 1 |
| New Protocols | 1 |
| New TypedDicts | 1 |
| New base-class methods | 1 |
| New ClassVar patterns | 1 |
| Broad catches removed | 3 |
| Broad catches kept (documented) | 15+ |
| Silent-fail paths surfaced | 5 |
| Behaviour change for users | 0 (StrEnum/IntEnum wire-compatible; only Step 0b adds new `choices=` validation that rejects typos previously silent-defaulted) |

---

## 6. PR templates

Templates per step in `ARCHITECTURE_MAP.md` §16.7 / §21.9. Each PR description includes:

```markdown
## Summary
[1 line + link to ARCHITECTURE_MAP.md §X step Y]

## Behaviour changes
[none, OR specific list]

## Test plan
- [ ] pytest -x green
- [ ] ruff + black + mypy clean on touched files
- [ ] [step-specific smoke]
```

---

## Done

When the §5 checklist is fully ticked, the refactor matches the §21 target shape in `ARCHITECTURE_MAP.md`. Total effort: ~2.5 days across 8 PRs (Step −1 + Steps 0a, 0b, 0c, 1, 2, 3, 5).
