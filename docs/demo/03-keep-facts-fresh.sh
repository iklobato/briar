#!/usr/bin/env bash
# README demo 3: a runbook with a schedule keeps the knowledge file fresh. Needs, in the current
# folder, a clone of a GitHub repo and a runbooks/myteam.yaml like the README one (every minute),
# and `gh` logged in. It runs for real until the job has fired twice.
source "$(dirname "$0")/typed.sh"

# Show the lines that tell the story: the time and what happened. briar logs them with a date and
# a module name in front; the rest of each line is untouched.
story() {
  grep --line-buffered -E 'extractor-ok|result task=' \
    | sed -u -E 's/^[0-9-]+T([0-9:]+)Z \[INFO +\] [^ ]+ /\1 /; s/\[company=[^]]*extractor=([a-z-]+)\]/\1/' \
    | cut -c1-100
}

# briar serve runs until it is stopped: stop it after the second run, as Ctrl+C would.
serve_two_runs() {
  local pid_file line runs=0
  pid_file=$(mktemp)
  sh -c 'echo $$ > "$0"; exec briar serve runbooks' "$pid_file" 2>&1 | tee >(story > /dev/tty) | while IFS= read -r line; do
    case "$line" in *"result task="*) runs=$((runs + 1)); [ "$runs" -ge 2 ] && stop_serve "$pid_file" ;; esac
  done
  sleep 1
  printf '^C\n'
  rm -f "$pid_file"
}

note "keep the facts fresh: a schedule re-reads your tools by itself (every minute here, only for the demo)"
run 'cat runbooks/myteam.yaml'
run 'export BRIAR_MYTEAM_GITHUB_TOKEN=$(gh auth token)'
typed 'briar serve runbooks'
serve_two_runs
run 'head -n 2 knowledge/knowledge/myteam.md'
