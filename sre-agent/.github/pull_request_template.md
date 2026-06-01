<!--
Thanks for the PR. Fill in the sections below — they help reviewers,
on-call engineers, and the next person debugging from `git blame`.

Keep it short. A PR that fits on one screen is almost always easier to
review than one that doesn't.
-->

## What

<!-- One sentence: what behavior changes, end-user-visible? -->

## Why

<!-- The motivation. Link the issue / discussion / runbook section that
prompted this. If this is a security fix, reference the advisory but do
NOT paste the vulnerability details until after disclosure. -->

Fixes #
Closes #

## How

<!-- A short architecture note: what's the new shape? What did you NOT
do that you considered? Reviewers especially value the "considered + ruled
out" entries — they save the next round of "why didn't you just..." -->

## Test plan

<!-- Tick what applies. Add the actual commands you ran. -->

- [ ] `make lint` clean
- [ ] `make typecheck` clean
- [ ] `make test` clean
- [ ] `make test-policy` clean (only if you touched `policies/`)
- [ ] `helm lint deploy/helm/pocteo --set-string auth.apiToken=t --set-string auth.hmacKey=t` clean
- [ ] Manual smoke test described below

### Manual smoke

<!-- Steps + expected behavior. Skip if pure refactor or doc change. -->

## Screenshots / recordings

<!-- For UI changes: before / after. For backend: skip unless the
`/metrics` shape or a log line is part of the contract. -->

## Breaking changes

<!-- If anything is breaking: list it here AND in the commit body as a
`BREAKING CHANGE: <description>` footer. CI's release.yml uses that to
bump the major version. -->

- [ ] None
- [ ] Yes — described below

## Operator impact

<!-- What does a Keos operator need to do when they upgrade past this PR?
- New env var to set? Listed in `docs/CONFIG_REFERENCE.md`?
- New Helm value? Default chosen so existing installs are unaffected?
- New permission needed? Added to `manifests/agent-rbac.yaml` + Helm chart?
- New runbook scenario? Linked in `docs/USE_CASES.md`?
- Anything to call out in `CHANGELOG.md`? -->

## DCO

By submitting this PR I certify that my contributions are made under the
DCO ([Developer Certificate of Origin](https://developercertificate.org/)),
i.e. every commit carries a `Signed-off-by:` line.

- [ ] All my commits are signed off (`git commit -s`)
