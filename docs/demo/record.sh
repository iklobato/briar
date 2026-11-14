#!/usr/bin/env bash
# Record one README demo GIF from its script, with asciinema and agg.
#
#   DEMO_DIR=/path/to/a/fresh/clone docs/demo/record.sh lesson1-setup
#
# DEMO_DIR is a clean clone the demo runs in (the scripts say what they need in it).
# `briar` and `gh` must be on the PATH, and the logins in the environment. Waits with no
# output are cut to 1.5 seconds in the GIF, so a minutes-long run fits in half a minute.
set -euo pipefail

name="${1:?usage: record.sh <name of a script in docs/demo, without .sh>}"
here="$(cd "$(dirname "$0")" && pwd)"
cast="$(mktemp -t briar-demo-XXXXXX).cast"

asciinema rec --overwrite --quiet --window-size 100x30 --idle-time-limit 1.5 \
  --command "cd '${DEMO_DIR:?set DEMO_DIR}' && PS1='$ ' bash --norc '${here}/${name}.sh'" "$cast"
agg --idle-time-limit 1.5 --font-size 18 --theme github-dark --last-frame-duration 5 "$cast" "${here}/${name}.gif" >/dev/null 2>&1
rm -f "$cast"
ls -l "${here}/${name}.gif" | awk '{print $5, $9}'
