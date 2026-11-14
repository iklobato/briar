#!/usr/bin/env bash
# README demo 1: the first 10 minutes. Needs, in the current folder, a fresh clone of a GitHub
# repo with no .briar.toml, and `gh` logged in (its token is the GitHub token of the demo).
source "$(dirname "$0")/typed.sh"

note "the first 10 minutes: a token, init, doctor, extract"
run 'briar --version'
run 'export BRIAR_MYTEAM_GITHUB_TOKEN=$(gh auth token)'
run 'briar init --company myteam'
run 'briar doctor'
PAUSE=2 run 'briar extract --include pr-archaeology --include release-cadence --include ci-health'
run 'head -n 14 ./knowledge/knowledge/myteam.md'
