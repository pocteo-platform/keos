---
name: Support question
about: Help running, configuring, or understanding Keos. Not a bug.
title: "help: "
labels: ["question", "triage"]
assignees: []
---

<!-- BEFORE opening this issue, please check:
  - docs/USER_GUIDE.md — comprehensive operator guide
  - docs/INSTALL.md — install matrix
  - docs/TALOS.md — Talos-specific notes
  - docs/RUNBOOKS/ — paper trails for common failure modes
  - The doctor: `make doctor` rules out 90% of "is it broken or did I
    misconfigure it" questions in 5 seconds.

If you've read those and the question stands, this issue is the right
place. We answer in roughly priority order: bug > security > support. -->

## What are you trying to do?

<!-- The goal, not the specific step you're stuck on. Helps us suggest
the right path. -->

## What have you tried?

<!-- Commands you ran, configs you used, docs you read. -->

## Where you got stuck

<!-- Specific error message, unexpected output, or "I read X and it didn't
cover this case". -->

## Environment

- Keos version / commit SHA:
- Install path: [ ] laptop docker-compose [ ] Helm on k3s [ ] Helm on Talos [ ] other
- Kubernetes version:
- OS + arch:

## Doctor output

```
<paste `make doctor` output here>
```

## Anything else

<!-- Edge cases of your environment we should know about. Air-gapped
network? Restricted PSS? Custom CNI? -->
