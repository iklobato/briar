"""Infisical machine-identity acquirer — "log into Infisical".

This is the bootstrap-the-bootstrap flow: every other store backend
authenticates with credentials the operator already has (AWS keys
in their ~/.aws, Vault token in their shell, GitHub PAT generated
on github.com). Infisical's machine identity is generated FROM
within Infisical's web UI, so we walk the operator through that.

The result is THREE values pasted into briar's local store (the
``envfile`` backend by default — usually ``~/.config/briar/secrets.env``
on a laptop). Subsequent processes that load that file then have
``INFISICAL_CLIENT_ID/_SECRET/_PROJECT_ID`` in env, so both
``InfisicalBootstrap`` (startup hydrate) and ``InfisicalStore``
(per-name read/write) can talk to Infisical.

Once these are set, ``briar auth login --provider github-pat
--store infisical`` writes the acquired GitHub PAT INTO Infisical,
not the local envfile. Same with every other acquirer."""

from __future__ import annotations

from typing import List

from briar.auth._acquirer import CredentialAcquirer, Credentials, DestinationPolicy
from briar.auth._prompt import PromptIO


_MACHINE_IDENTITY_URL = "https://app.infisical.com/personal-settings/machine-identities"


class InfisicalAcquirer(CredentialAcquirer):
    kind = "infisical"
    display_name = "Infisical machine identity (paste)"
    # The captured credentials are how briar talks to Infisical itself
    # — they cannot be stored INSIDE Infisical (chicken-and-egg). The
    # CLI forces --store=envfile when this policy is set.
    destination_policy = DestinationPolicy.BOOTSTRAP_LOCAL

    def acquire(self, *, company: str, prompt: PromptIO) -> Credentials:
        prompt.info("==> Infisical machine identity")
        prompt.info("    This is the bootstrap step — once these three values land in your")
        prompt.info("    local store, briar can subsequently use Infisical as a CredentialStore")
        prompt.info("    (`briar auth login ... --store infisical`).")
        prompt.info("")
        prompt.info(f"    1. Open {_MACHINE_IDENTITY_URL}")
        prompt.info("    2. Click 'Create Identity', name it `briar-cli`")
        prompt.info("    3. Select 'Universal Auth' as the auth method → create")
        prompt.info("    4. Open the new identity → 'Create Client Secret'")
        prompt.info("    5. Add the identity to your project: project Settings → Access Control →")
        prompt.info("       Machine Identities → Add identity → grant 'Secrets / Read' + 'Write'")
        prompt.info("    6. Copy: Client ID + Client Secret (you only see the secret once) + Project ID")
        prompt.open_url(_MACHINE_IDENTITY_URL)

        client_id = prompt.prompt("    INFISICAL_CLIENT_ID (UUID): ").strip()
        client_secret = prompt.prompt("    INFISICAL_CLIENT_SECRET: ", secret=True).strip()
        project_id = prompt.prompt("    INFISICAL_PROJECT_ID (UUID, project Settings → General): ").strip()
        env_slug = prompt.prompt("    INFISICAL_ENV slug [prod]: ").strip() or "prod"
        host = prompt.prompt("    INFISICAL_HOST [https://app.infisical.com]: ").strip() or "https://app.infisical.com"

        if not (client_id and client_secret and project_id):
            raise ValueError("infisical: client_id + client_secret + project_id all required")

        return Credentials(
            provider_kind=self.kind,
            entries={
                "INFISICAL_CLIENT_ID": client_id,
                "INFISICAL_CLIENT_SECRET": client_secret,
                "INFISICAL_PROJECT_ID": project_id,
                "INFISICAL_ENV": env_slug,
                "INFISICAL_HOST": host,
            },
            metadata={"auth_mode": "universal-auth-machine-identity"},
        )

    @classmethod
    def writes(cls, *, company: str) -> List[str]:
        # The three required vars (env + host are listed in acquire() but
        # not surfaced here — they're operational defaults the doctor
        # doesn't need to flag as missing).
        return ["INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID"]
