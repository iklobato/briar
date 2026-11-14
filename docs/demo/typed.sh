#!/usr/bin/env bash
# Helpers for the README demos: type a command like a person, then run it for real.
# The output in the GIF is always the real output; only the typing is staged.

TYPE_DELAY=${TYPE_DELAY:-0.03}

prompt() {
  printf '\033[1;32m$\033[0m '
}

note() {
  printf '\033[2m# %s\033[0m\n' "$1"
}

typed() {
  local text="$1" index
  prompt
  for ((index = 0; index < ${#text}; index++)); do
    printf '%s' "${text:index:1}"
    sleep "$TYPE_DELAY"
  done
  sleep 0.4
  printf '\n'
}

# run "<what the GIF shows>" ["<what really runs>"]: the second argument is only for a
# command that must differ from what is shown (a log filter, a stop condition).
run() {
  typed "$1"
  eval "${2:-$1}"
  sleep "${PAUSE:-1.2}"
}

# Ctrl+C (SIGINT) first; if serve is still there 15 seconds later, SIGTERM, and say so in SIGINT_LOG.
stop_serve() {
  local pid
  pid=$(cat "$1")
  kill -INT "$pid"
  for _ in $(seq 1 15); do kill -0 "$pid" 2> /dev/null || return 0; sleep 1; done
  echo "serve did not stop on SIGINT within 15 s" >> "${SIGINT_LOG:-/dev/null}"
  kill -TERM "$pid"
}
