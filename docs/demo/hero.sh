#!/usr/bin/env bash
# README hero GIF: one command reads a repo, one file holds what it learned. Needs, in the
# current folder, a clone of a GitHub repo with releases and CI runs, and `gh` logged in.
source "$(dirname "$0")/typed.sh"

note "briar reads your repo and writes what it learns into one plain file"
run 'export BRIAR_MYTEAM_GITHUB_TOKEN=$(gh auth token)'
PAUSE=2 run 'briar extract --company myteam --repo iklobato/briar --include release-cadence --include ci-health'
run 'head -n 22 knowledge/knowledge/myteam.md | cut -c1-100'
