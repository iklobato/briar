#!/usr/bin/env bash
# README lesson 2: briar serve works one small GitHub issue. A REAL run: it spends model money and opens
# a real PR. Needs, in the current folder, a clone of the repo (default branch checked out), a
# runbooks/myteam.yaml like the README one that lists ISSUE under `only`, `gh` logged in, a Claude
# login in the environment, ISSUE (the number of an issue assigned to you) and REPO (owner/name).
source "$(dirname "$0")/typed.sh"
: "${ISSUE:?set ISSUE to the number of the issue}"
: "${REPO:?set REPO to owner/name}"

# briar serve runs until it is stopped: stop it after the task ends, as Ctrl+C would.
serve_until_done() {
  local pid_file line
  pid_file=$(mktemp)
  sh -c 'echo $$ > "$0"; exec briar serve runbooks' "$pid_file" 2>&1 | grep --line-buffered 'serve:' | while IFS= read -r line; do
    # briar logs a date and a module name in front of each line; show the time and the rest.
    printf '%s\n' "$line" | sed -E 's/^[0-9-]+T([0-9:]+)Z \[INFO +\] [^ ]+ serve: /\1 /' | cut -c1-100
    case "$line" in *"is done"* | *"is blocked"*) stop_serve "$pid_file" ;; esac
  done
  printf '^C\n'
  rm -f "$pid_file"
}

note "a real run on a small issue: briar plans, codes, opens the PR. The run takes minutes; silent waits are cut in this GIF"
run 'cat runbooks/myteam.yaml'
run 'export BRIAR_MYTEAM_GITHUB_TOKEN=$(gh auth token)'
run 'briar doctor --runbooks runbooks'
typed 'briar serve runbooks'
serve_until_done
sleep 1
run "gh pr list --head briar/issue-$ISSUE" "GH_PAGER=cat gh pr list -R $REPO --head briar/issue-$ISSUE"
