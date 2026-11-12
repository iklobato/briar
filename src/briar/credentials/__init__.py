"""The secrets of briar: `secrets.env` (`_paths.secrets_path`), written by hand and
loaded into the environment at startup (`_bootstraps`). 1.x also had AWS Secrets
Manager, SSM and Vault; a `BRIAR_DEFAULT_STORE` that still names one is stopped by
`briar.credentials._removed_stores`. The writer of `briar auth login` left with the
command (spec 003 part 4, X4)."""
