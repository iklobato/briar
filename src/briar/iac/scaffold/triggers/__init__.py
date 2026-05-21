"""Trigger kind registry. One file per kind; this module is the
assembly point."""

from __future__ import annotations

from typing import Dict

from briar.iac.scaffold.triggers.base import TriggerTemplate
from briar.iac.scaffold.triggers.bitbucket_webhook import TriggerBitbucketWebhook
from briar.iac.scaffold.triggers.github_webhook import TriggerGithubWebhook
from briar.iac.scaffold.triggers.manual import TriggerManual
from briar.iac.scaffold.triggers.schedule_cron import TriggerScheduleCron


TRIGGER_TEMPLATES: Dict[str, TriggerTemplate] = {
    t.kind: t
    for t in (
        TriggerGithubWebhook(),
        TriggerBitbucketWebhook(),
        TriggerScheduleCron(),
        TriggerManual(),
    )
}


__all__ = ["TriggerTemplate", "TRIGGER_TEMPLATES"]
