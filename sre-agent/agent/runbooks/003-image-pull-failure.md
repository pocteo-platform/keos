# Runbook 003 — Container image pull failure (ErrImagePull / ImagePullBackOff)

**Tags:** availability, supply-chain

## Symptom

A pod's container fails to start with `ErrImagePull` or, after retries, `ImagePullBackOff`. The kubelet logs the offending image reference; `kubectl describe pod` shows `Failed to pull image "<ref>"` with one of: `manifest unknown`, `not found`, `denied`, `unauthorized`, or `no such host`. The agent sees this from the live Pod / Event analyzers — the kubelet emits a clear `Reason: Failed / Type: Warning` event with the image reference.

## Diagnosis

The dominant causes, in order of frequency: (1) **typo or missing tag** — somebody pushed without retagging, or the manifest references a tag that never existed (`:v999` in the demo scenario); (2) **registry unreachable** — DNS failure or the registry is down; (3) **authorization** — credentials revoked or `imagePullSecrets` removed; (4) **image deleted from registry** — possible after an upstream force-push that invalidated the digest. Crucially: the runtime cannot help here. No amount of restarting the pod will succeed against an image that doesn't exist. The diagnostic signal reliably reports the exact failure reason in its `error` field — read it before deciding.

## Remediation

If a recent rollout introduced the bad image reference (check the Deployment's `metadata.annotations` and `kubectl rollout history`), the cheapest fix is `rollout_undo` on the Deployment — the previous revision had a working image. If the failure is registry-level (network, auth) and you cannot validate quickly, `page_human` so a responder can fix the registry credentials or pin a known-good digest. Never `restart_pod` for ErrImagePull: the new pod will hit the same registry-side failure. `scale_deployment` does not help either — adding replicas just multiplies the failure.
