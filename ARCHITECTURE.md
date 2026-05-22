# Architecture — class + function map, with SOLID findings

Reference diagram + the design-pattern violations found during the
2026-05-22 audit. Follow-up commits fix violations 1–6.

---

## Module + abstraction map

Twelve Strategy + Registry families, plus one-off helpers. Adapter
files live under `_<plural>/` subpackages; registries are dicts in
the package `__init__.py`.

```mermaid
flowchart LR
  classDef abc fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#000
  classDef cls fill:#dbeafe,stroke:#2563eb,color:#000
  classDef reg fill:#d1fae5,stroke:#059669,color:#000
  classDef cli fill:#e0e7ff,stroke:#4338ca,color:#000
  classDef bad fill:#fecaca,stroke:#dc2626,stroke-width:3px,color:#000

  subgraph cli["briar (CLI entrypoints)"]
    CLI[cli.py main]:::cli
    CMD[CommandRegistry]:::reg
    CmdExtract[CommandExtract]:::cls
    CmdRunbook[CommandRunbook]:::cls
    CmdAgent[CommandAgent]:::cls
    CmdScaffold[CommandScaffold]:::cls
    CmdContext[CommandContext]:::cls
    CmdDashboard[CommandDashboard]:::cls
    CmdSecrets[CommandSecrets]:::cls
    CmdVersion[CommandVersion]:::cls
    CLI --> CMD
    CMD --> CmdExtract & CmdRunbook & CmdAgent & CmdScaffold & CmdContext & CmdDashboard & CmdSecrets & CmdVersion
  end

  subgraph extract["extract/ — KnowledgeExtractor family"]
    KE([KnowledgeExtractor]):::abc
    RBE([RepoBackedExtractor]):::abc
    TBE([TrackerBackedExtractor]):::abc
    CBE([CloudBackedExtractor]):::abc
    TSE([TaskScopedExtractor]):::abc
    KE --> RBE
    KE --> TBE
    KE --> CBE
    EXTREG[/EXTRACTORS dict/]:::reg
    TSEREG[/TASK_SCOPED_EXTRACTORS dict/]:::reg

    EAW[ExtractActiveWork]:::cls
    EPA[ExtractPrArchaeology]:::cls
    EGD[ExtractGithubDeployments]:::cls
    ECC[ExtractCodebaseConventions]:::cls
    EAT[ExtractActiveTickets]:::cls
    ETA[ExtractTicketArchaeology]:::cls
    ERP[ExtractReviewerProfile]:::cls
    ECH[ExtractCodeHotspots]:::cls
    EAI[ExtractAwsInfra]:::bad
    RBE --> EAW & EPA & EGD & ECC & ERP & ECH
    TBE --> EAT & ETA
    CBE --> EAI

    FTC[FetchTicketContext]:::cls
    FPR[FetchPrReviewContext]:::cls
    TSE --> FTC
    TSE --> FPR

    EAW & EPA & EGD & ECC & EAI & EAT & ETA & ERP & ECH -.-> EXTREG
    FTC & FPR -.-> TSEREG
  end

  subgraph providers["extract/_provider* — vendor adapters"]
    RP([RepositoryProvider]):::abc
    GHP[GithubProvider]:::cls
    BBP[BitbucketProvider]:::cls
    RP --> GHP & BBP

    TP([TrackerProvider]):::abc
    JT[JiraTracker]:::cls
    GIT[GithubIssuesTracker]:::cls
    BIT[BitbucketIssuesTracker]:::cls
    LT[LinearTracker]:::cls
    TP --> JT & GIT & BIT & LT

    CP([CloudProvider]):::abc
    ACP[AwsCloudProvider]:::cls
    GCP[GcpCloudProvider]:::cls
    AZP[AzureCloudProvider]:::cls
    CP --> ACP & GCP & AZP

    PR[/PROVIDERS dict/]:::reg
    TR[/TRACKERS dict/]:::reg
    CR[/CLOUDS dict/]:::reg
    GHP & BBP -.-> PR
    JT & GIT & BIT & LT -.-> TR
    ACP & GCP & AZP -.-> CR
  end

  subgraph llms["agent/_llm* — LLM adapters"]
    LLM([LLMProvider]):::abc
    AntL[AnthropicLLM]:::cls
    OAIL[OpenAILLM]:::cls
    GemL[GeminiLLM]:::cls
    BedL[BedrockLLM]:::cls
    LLM --> AntL & OAIL & GemL & BedL
    LLMREG[/LLMS dict/]:::reg
    AntL & OAIL & GemL & BedL -.-> LLMREG
  end

  subgraph sinks["notify/ — NotificationSink"]
    NS([NotificationSink]):::abc
    TG[TelegramSink]:::cls
    SL[SlackSink]:::cls
    EM[EmailSink]:::cls
    PD[PagerDutySink]:::cls
    NS --> TG & SL & EM & PD
    NSREG[/SINKS dict/]:::reg
  end

  subgraph creds["credentials/ — CredentialStore"]
    CS([CredentialStore]):::abc
    EF[EnvFileStore]:::cls
    ASM[AwsSecretsManagerStore]:::cls
    SSM[SsmParameterStore]:::cls
    VL[VaultStore]:::cls
    CS --> EF & ASM & SSM & VL
    CSREG[/STORES dict/]:::reg
  end

  subgraph storage["storage/ — KnowledgeStore"]
    KS([KnowledgeStore]):::abc
    SF[StoreFile]:::cls
    SP[StorePostgres]:::cls
    KS --> SF & SP
    KSREG[/STORES dict/]:::reg
  end

  subgraph scaffold["iac/scaffold/ — generator plumbing"]
    ST([SourceTemplate]):::abc
    TT([TriggerTemplate]):::abc
    WS([WorkflowShape]):::abc
    AA([AgentArchetype]):::abc
    Rul([Rule]):::abc
    ST -.4 concretes.- ST
    TT -.4 concretes.- TT
    WS -.3 concretes.- WS
    AA -.5 concretes.- AA
    Rul -.7 markdown files.- Rul
  end

  subgraph runbook["iac/runbook/"]
    RBM[RunbookFile pydantic]:::bad
    RBE_Exec[RunbookExtractor]:::cls
    RBM --> RBE_Exec
  end

  CmdAgent --> KE
  CmdAgent --> TSE
  CmdAgent --> LLM
  CmdAgent --> KS
  CmdRunbook --> RBM
  CmdRunbook --> EXTREG
  CmdSecrets --> CS
  CmdSecrets --> EXTREG

  RBE -. uses .-> RP
  TBE -. uses .-> TP
  CBE -. uses .-> CP
  FTC -. uses .-> TP
  FPR -. uses .-> RP
  ECC -. uses .-> RP
```

Red-bordered nodes are the SOLID violation hotspots (annotated below).

---

## Violations found

### Diagram zoom — the if-chain hotspots

```mermaid
flowchart LR
  classDef bad fill:#fecaca,stroke:#dc2626,stroke-width:3px,color:#000
  classDef ok fill:#bbf7d0,stroke:#16a34a,stroke-width:2px,color:#000

  subgraph agent["commands/agent.py"]
    Run[CommandAgent.run]:::bad
    Run -- "if op == 'prfix' if op == 'implement'" --> RunPrfix[_run_prfix]
    Run --> RunImpl[_run_implement]

    Clone[_clone_default]:::bad
    Clone -- "if provider == 'bitbucket' else" --> GhClone[github branch]
    Clone --> BbClone[bitbucket branch]

    Instr[_implement_specific_instructions]:::bad
    Instr -- "if provider == 'bitbucket'" --> GhInstr[gh CLI recipe]
    Instr --> BbInstr[bitbucket curl recipe]
  end

  subgraph awsinfra["extract/aws_infra.py"]
    AwsExtract[ExtractAwsInfra.extract]:::bad
    AwsExtract -- "if cloud_kind == 'aws'" --> AwsLegacy[_extract_aws_legacy]
    AwsExtract --> AwsGeneric[_extract_via_cloud_provider]
  end

  subgraph runbookmodels["iac/runbook/models.py"]
    Lit1[ExtractEntry.name: Literal of N names]:::bad
    Lit1 -. duplicates .-> EXT[/EXTRACTORS dict/]
    Lit2[KnowledgeBinding.store: Literal of file,postgres]:::bad
    Lit2 -. duplicates .-> KS[/KnowledgeStoreRegistry.STORES/]
  end
```

### Findings table

| # | File:Line | Pattern | Why it's wrong | Fix |
|---|---|---|---|---|
| 1 | `commands/agent.py:112,114` | `if op == "prfix": ... if op == "implement": ...` | OCP — adding a new op requires editing the dispatch site, not just adding a class | `AgentOp` ABC + `AGENT_OPS` registry, mirroring every other plugin family |
| 2 | `commands/agent.py:302` | `if provider == "bitbucket": ... else (github)` | OCP — adding GitLab/Gitea cloner means a third `elif`, not a third class | `RepoCloner` ABC + `GithubRepoCloner`, `BitbucketRepoCloner` registered in `REPO_CLONERS` |
| 3 | `commands/agent.py:382` | Same `if provider == "bitbucket"` for instruction template | Same — provider-specific PR-creation recipe is data per-vendor, not branching | Method on `RepoCloner` (`pr_creation_recipe(owner, repo, branch, company)`) |
| 4 | `extract/aws_infra.py:62` | `if cloud_kind == "aws": ... else (cloud provider)` | The whole point of `CloudProvider` was to unify the AWS path with the generic path. The `if` re-introduces the coupling | Collapse to one path. `AwsCloudProvider` already exists; the legacy section-shape is the only blocker. Either accept the shape change or move the legacy rendering into `AwsCloudProvider` itself |
| 5 | `iac/runbook/models.py:24` | `ExtractEntry.name: Literal["pr-archaeology", ..., "code-hotspots"]` (9 names) | OCP — adding a new extractor requires editing the Literal even though the runtime registry already has the answer (`EXTRACTORS.keys()`). I edited this 3× this session | `name: str` + `@field_validator("name")` that checks against `EXTRACTORS.keys()` at validation time |
| 6 | `iac/runbook/models.py:53` | `KnowledgeBinding.store: Literal["file", "postgres"]` | Same shape — `KnowledgeStoreRegistry.STORES.keys()` is the source of truth | Same fix |
| 7 | `commands/secrets.py:22` | Hand-maintained `_EXTRACTOR_REQUIREMENTS: Dict[(extractor_name, provider_kind), List[CredEnv]]` | Each new (extractor, provider) pair requires editing the table. Should live on the extractor/provider as a `required_credentials()` method | DEFERRED — bigger refactor. Files a follow-up. |

The Literal[...] forms in `iac/models.py` (workflow-graph node `kind:
agent|human_checkpoint|branch|...`) are NOT violations — those are
tagged-union discriminators where the closed set is intentional (the
orchestrator switches on them). Keep.

### Why the if-chains specifically are the worst smell here

Every plugin family in the codebase uses Strategy + Registry. The
if-chains are inconsistent with that — they look like ad-hoc branching
when the surrounding architecture made the registry pattern the
default. A reviewer scanning `commands/agent.py:run` against
`commands/__init__.py:CommandRegistry.build` sees two different
philosophies and reasonably wonders which one wins. Convergence is
the cheaper outcome.

### Out-of-scope finds (not violations, noted for future)

- **`extract/_trackers/jira.py:_adf_walk`** has `if kind == "text"` — this is
  a single-decision branch inside a format walker, not a dispatcher. Not a
  violation.
- **Agent runner's `dry_run`** flag is a boolean parameter — could be a separate
  `DryRunRunner` class, but the if-check is one place and the runner is otherwise
  fine. Not worth the refactor.
- **`commands/agent.py::_pr_specific_instructions`** has a long format string. Long
  but readable. Not a SOLID issue.

---

## Follow-up commits

- **B (this branch):** fix violation 1 — `AgentOp` ABC + `AGENT_OPS` registry
- **C:** fix violations 2 + 3 — `RepoCloner` ABC for clone + PR-creation
- **D:** fix violation 4 — collapse `aws_infra` if-chain
- **E:** fix violations 5 + 6 — runbook Literal[] → field_validator against registries

Each commit stays independently revertable.
