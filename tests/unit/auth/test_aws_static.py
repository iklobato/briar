"""AwsStaticAcquirer — paste-an-IAM-key flow.

No network: the acquirer only talks to the operator through PromptIO.
We drive it with the in-repo MockPromptIO (scripted answers) and assert
the produced Credentials bundle + the env-var names it declares.

NB: the AccessKeyId / secret values below are obvious non-secret
PLACEHOLDERS — they are never validated, only flowed through. Real
``AKIA``+16 / 40-char-secret literals would trip secret scanners for no
reason; the *shape* (key-id / secret / region) is what's modelled.
"""

from __future__ import annotations

import pytest

from briar.auth._acquirers.aws_static import AwsStaticAcquirer
from briar.auth._prompt import MockPromptIO
from briar.env_vars import CredEnv

_KID = "AKIA-PLACEHOLDER-not-a-real-key"
_SECRET = "SECRET-PLACEHOLDER-not-a-real-secret"


class TestHappyPath:
    def test_returns_static_credentials_with_explicit_region(self) -> None:
        prompt = MockPromptIO(answers=[_KID, _SECRET, "eu-west-1"])
        creds = AwsStaticAcquirer().acquire(company="acme", prompt=prompt)

        assert creds.provider_kind == "aws-static"
        assert creds.metadata == {"auth_mode": "static-iam-user"}
        assert creds.entries[CredEnv.AWS_KEY_ID.for_company("acme")] == _KID
        assert creds.entries[CredEnv.AWS_SECRET.for_company("acme")] == _SECRET
        assert creds.entries[CredEnv.AWS_REGION.for_company("acme")] == "eu-west-1"
        # Static keys never carry a session token.
        assert CredEnv.AWS_SESSION.for_company("acme") not in creds.entries
        # No expiry on a static IAM user key.
        assert creds.expires_at is None

    def test_blank_region_defaults_to_us_east_1(self) -> None:
        # Empty region answer → the documented default, not "".
        prompt = MockPromptIO(answers=[_KID, _SECRET, "   "])
        creds = AwsStaticAcquirer().acquire(company="acme", prompt=prompt)
        assert creds.entries[CredEnv.AWS_REGION.for_company("acme")] == "us-east-1"

    def test_secret_is_read_with_echo_suppressed(self) -> None:
        # The secret prompt must request secret=True; the key-id must not.
        prompt = MockPromptIO(answers=[_KID, _SECRET, "us-east-1"])
        AwsStaticAcquirer().acquire(company="acme", prompt=prompt)
        kid_prompts = [s for m, s in prompt.prompts if "AccessKeyId" in m]
        secret_prompts = [s for m, s in prompt.prompts if "SecretAccessKey" in m]
        assert kid_prompts == [False]
        assert secret_prompts == [True]


class TestFailureModes:
    def test_no_company_raises_before_prompting(self) -> None:
        prompt = MockPromptIO(answers=[])
        with pytest.raises(ValueError, match="--company is required"):
            AwsStaticAcquirer().acquire(company="", prompt=prompt)
        # Must bail before touching the operator.
        assert prompt.prompts == []

    def test_empty_key_id_raises(self) -> None:
        # Operator hit Enter on the key-id prompt → both-required guard.
        prompt = MockPromptIO(answers=["   ", _SECRET, "us-east-1"])
        with pytest.raises(ValueError, match="key id and secret required"):
            AwsStaticAcquirer().acquire(company="acme", prompt=prompt)

    def test_empty_secret_raises(self) -> None:
        prompt = MockPromptIO(answers=[_KID, "  ", "us-east-1"])
        with pytest.raises(ValueError, match="key id and secret required"):
            AwsStaticAcquirer().acquire(company="acme", prompt=prompt)


class TestWrites:
    def test_writes_declares_the_three_static_vars(self) -> None:
        assert AwsStaticAcquirer.writes(company="acme") == [
            CredEnv.AWS_KEY_ID.for_company("acme"),
            CredEnv.AWS_SECRET.for_company("acme"),
            CredEnv.AWS_REGION.for_company("acme"),
        ]
        # No session-token var for static keys.
        assert CredEnv.AWS_SESSION.for_company("acme") not in AwsStaticAcquirer.writes(company="acme")

    def test_writes_with_empty_company_is_empty(self) -> None:
        # Empty company → nothing to write (avoids the templated-var
        # ValueError that for_company would raise on "").
        assert AwsStaticAcquirer.writes(company="") == []
