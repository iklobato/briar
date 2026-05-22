"""Trigger kind registry. One file per kind; this module is the
assembly point."""

from __future__ import annotations

from typing import Dict

from briar._registry import build_registry
from briar.iac.scaffold.triggers.base import TriggerTemplate
from briar.iac.scaffold.triggers.bitbucket_webhook import TriggerBitbucketWebhook
from briar.iac.scaffold.triggers.github_webhook import TriggerGithubWebhook
from briar.iac.scaffold.triggers.manual import TriggerManual
from briar.iac.scaffold.triggers.schedule_cron import TriggerScheduleCron


TRIGGER_TEMPLATES: Dict[str, TriggerTemplate] = build_registry(
    (TriggerGithubWebhook(), TriggerBitbucketWebhook(), TriggerScheduleCron(), TriggerManual()),
    kind="scaffold trigger",
    name_attr="kind",
)


__all__ = ["TriggerTemplate", "TRIGGER_TEMPLATES"]
