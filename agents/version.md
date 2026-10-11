# `briar version`

## What it does

Prints the installed briar version as one line, such as
`briar-cli 1.1.72`.

## Use it when

- You want to check that `briar` is installed and on your `PATH`.
- You want to confirm a deploy shipped the version you expect.
- You file a bug report. Always include the version.

## Examples

```bash
# Print the version.
briar version
```

You get `briar-cli 1.1.72`.

```bash
# The same line, with no startup work at all.
briar --version
```

You get `briar-cli 1.1.72`. `-V` is a short form of `--version`.

```bash
# Check the version inside the Docker image.
docker run --rm iklob1/briar version
```

You get the version of the briar in the image.

## All parameters

`briar version` has no flags of its own. `--format` does not change its
output.

## `version` or `--version`?

Both print the same line. The difference is what happens around it:

| | `briar version` | `briar --version` |
|---|---|---|
| Loads `secrets.env` | yes | no |
| Sends a telemetry event (if on) | yes | no |
| Checks PyPI for a newer version (at most once a day) | yes | no |
| Reads `.briar.toml` | yes | no |

Use `briar --version` when you need a check with no network calls and
no side effects, for example in a health probe.

## Good to know

- `command not found: briar` means briar is not installed in the active
  Python, or its folder is not on your `PATH`. Install it with
  `pip install briar-cli`, or run `python -m briar version`.
- A wrong version usually means another install comes first on your
  `PATH`. Run `which briar` to see which one runs.
