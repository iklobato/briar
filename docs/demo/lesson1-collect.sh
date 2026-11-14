#!/usr/bin/env bash
# README lesson 1, part 2: collect facts and read the file. Run it in the same folder as
# lesson1-setup.sh, after it (it needs the .briar.toml that init wrote).
source "$(dirname "$0")/typed.sh"

note "lesson 1: collect three kinds of facts, then read the file"
run 'export BRIAR_MYTEAM_GITHUB_TOKEN=$(gh auth token)'
PAUSE=2 run 'briar extract --include pr-archaeology --include release-cadence --include ci-health'
run 'grep "^## " knowledge/knowledge/myteam.md'
run 'head -n 12 knowledge/knowledge/myteam.md | cut -c1-100'
