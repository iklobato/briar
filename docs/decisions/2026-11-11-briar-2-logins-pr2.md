# briar 2.0: spec 005 PR 2, the AWS, GCP and Azure logins

Grill session of 2026-11-11 for the second PR of spec 005 (tasks T009 to
T013 of the local `specs/005-source-logins/tasks.md`). It settles how the
cloud logins work on top of `docs/decisions/2026-11-10-briar-2-logins-spec.md`
(G1 to G26) and `docs/decisions/2026-11-10-briar-2-logins-pr1.md` (H1 to
H10), which are not reopened.

Format: `ID. Decision: chosen answer. Why. (depends on)`

Facts checked first (2026-11-11): azure-identity `AzureCliCredential` runs
`az account get-access-token` in a subprocess
(`azure/identity/_credentials/azure_cli.py:258`), so it needs `az` at run
time; azure-identity `DeviceCodeCredential` can log in from Python with a
persistent cache (Keychain, libsecret, DPAPI;
`azure/identity/_persistent_cache.py`). boto3 reads a profile and its SSO
token cache without the AWS CLI; google-auth reads the ADC file of a
config folder without gcloud. Only the logins need the CLIs.

## Decisions

- I1. AWS: `briar auth login aws` runs `aws sso login --profile P` in the
  terminal (`os.system`); runs read the profile with boto3, which renews
  the SSO token by itself. Nothing is copied to `secrets.env`. (L14, L20,
  L3)
- I2. GCP: `briar auth login gcp` runs
  `CLOUDSDK_CONFIG=<config_dir> gcloud auth application-default login`;
  runs load the ADC file of that folder with google-auth. No service
  account key option. (G26, L14)
- I3. Azure: `briar auth login azure` runs `az login --tenant T`; runs use
  `AzureCliCredential(tenant_id=T)`, which needs `az` at run time too. The
  Python device flow of azure-identity is not used (L14 unchanged). (L14)
- I4. Each cloud keeps its scope in its own `[auth]` table:
  `[auth.aws] profile`, `[auth.gcp] project` (with `config_dir`),
  `[auth.azure] subscription` (with `tenant`). `profile` leaves
  `[extract.aws-infra]` (it held the AWS profile, the GCP project and the
  Azure subscription): a run that still has it stops with exit 1 and names
  the new place. `region` stays in `[extract.aws-infra]`. (G2, E1 to E16)
- I5. For an AWS profile with no SSO (static keys), `briar auth login aws`
  runs `aws configure --profile P` instead; briar tells the two apart by
  reading the profile with botocore. The keys stay in the AWS files, never
  in `secrets.env`. (I1, L3)
- I6. `briar auth login aws`, `gcp` and `azure` need their value
  (`profile`, `config_dir`, `tenant`): without it the login stops with exit
  1 and says what to add where, as H1 does. A run with no value still uses
  the default of the cloud (the boto3 default chain, `~/.config/gcloud`,
  the default az tenant), which serves CI with one company. (H1, G2)
- I7. Before PR 3, `briar auth status aws|gcp|azure` shows the value it
  uses, whether the CLI is on `PATH`, and whether the profile is in the AWS
  config or the ADC file is in the folder (offline); exit 1 when one is
  missing. (G14)
- I8. The Docker image gets no AWS, gcloud or az CLI: log in outside the
  container and mount `~/.aws` and the gcloud folder. Azure in Docker is
  not supported until someone asks. (L6)

## What follows from earlier decisions (no new choice)

- I9. The 1.x targets `aws-static` and `aws-sso` leave (G9), and with them
  the last acquirers: `briar.auth._acquirer`, `briar.auth._acquirers` and
  the acquirer registry. (G9, G16)
- I10. The 1.x names `AWS_<C>_ACCESS_KEY_ID`, `AWS_<C>_SECRET_ACCESS_KEY`,
  `AWS_<C>_SESSION_TOKEN` and `AWS_<C>_REGION` stop a run that has no AWS
  profile, with the new place (G8). `CredEnv` has no member left and goes.
  (G8)

## Open risks

- I3: Azure needs `az` on every machine that runs an Azure extract,
  Docker included (I8).
- I6: a login without a value stops, but a run without one falls back to
  the default of the cloud; the two behave differently on purpose.
