#!/usr/bin/env bash
# README lesson 1, part 1: a token, init, doctor. Needs, in the current folder, a fresh clone of a
# GitHub repo with no .briar.toml, and `gh` logged in (its token is the GitHub token of the demo).
source "$(dirname "$0")/typed.sh"

note "lesson 1: give briar a token, let it set itself up, check it"
run 'briar --version'
run 'export BRIAR_MYTEAM_GITHUB_TOKEN=$(gh auth token)'
run 'briar init --company myteam'
run 'briar doctor'
