---
name: Bug report
about: Something that should work doesn't.
title: "bug: "
labels: ["bug", "triage"]
assignees: []
---

<!-- For SECURITY bugs, DO NOT use this template. See SECURITY.md for the
private disclosure channel. -->

## Summary

<!-- One sentence describing what's wrong. -->

## Environment

- Keos version (or commit SHA):
- Install path: [ ] laptop docker-compose [ ] Helm on k3s [ ] Helm on Talos [ ] other (specify)
- Kubernetes version: `kubectl version --short` →
- Operating system + arch:
- Browser (UI bugs only):

## Steps to reproduce

1.
2.
3.

## Expected behavior

<!-- What did you think would happen? -->

## Actual behavior

<!-- What actually happened? Paste error messages verbatim. -->

## Doctor output

<!-- Run `make doctor` (or `.\make.ps1 doctor` on Windows) and paste below.
This rules out 90% of "it doesn't work" causes before triage. -->

```
<paste doctor output>
```

## Logs

<!-- Last ~50 lines of `docker compose logs api` (or `kubectl logs deploy/api`)
around the failure. Redact bearer tokens + webhook URLs. -->

```
<paste logs>
```

## Audit log verification

<!-- If the bug touches an action (something the agent executed/skipped),
also include the audit chain verification: -->

```bash
make verify-audit  # or run agent.audit.verify_audit_log() manually
```

```
<paste output>
```

## Additional context

<!-- Screenshots, related issues, recent changes (was this working before
a specific version?), anything else useful. -->
