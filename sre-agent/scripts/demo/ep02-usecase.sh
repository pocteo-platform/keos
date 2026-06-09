#!/usr/bin/env bash
# Episode 2 — Run a use case: trigger an incident, watch the agent react.
source "$(dirname "$0")/cast-lib.sh"

intro "Episode 2 · Run a use case" "usecases list · run · events"

say_comment "The library of ready-made incident scenarios"
play_cmd "keosctl usecases list"

say_comment "Fire one — a synthetic memory leak hits the event bus"
play_cmd "keosctl run lib-memory-leak"

pause 3
say_comment "The agent picked it up — incidents stream in colored by outcome"
play_cmd "keosctl events -n 5"

outro
