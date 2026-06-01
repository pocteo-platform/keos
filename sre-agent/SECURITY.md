# Security policy

The Keos SRE Agent is a policy-gated agent that takes actions against real
Kubernetes clusters. A bug here can have operational consequences. We take
security reports seriously and ask reporters to use the private channel
below.

## Supported versions

Security fixes are backported to the latest minor release in each major
line. Older releases get fixes only for critical issues.

| Version | Status | Notes |
|---|---|---|
| `1.x` | Supported | Receives all security fixes |
| `0.x` | EOL — upgrade to 1.x | Pre-1.0 release candidates; no security guarantees |

The current latest version is in `pyproject.toml` (`version =`).

## Reporting a vulnerability

**Do NOT open a public GitHub issue for a security report.** Public issues
go straight into the search index of every scraper on the internet.

Use one of:

1. **Email**: `security@keos.platform` — encrypted with our PGP key (below)
   strongly preferred for high-severity reports.
2. **GitHub Security Advisory**: <https://github.com/your-org/self-healing-poc/security/advisories/new>
   (private; only the maintainers and you can see it until publication).

Please include:

- A description of the vulnerability and its impact (what an attacker can do).
- A proof-of-concept or minimal reproduction.
- Affected version(s) — git SHA preferred over tag.
- Your name + contact for the credit / disclosure timeline negotiation. If
  you want to stay anonymous, that's fine — say so explicitly.

We will:

| Step | Target |
|---|---|
| Acknowledge receipt | Within **5 business days** |
| Initial assessment (severity, scope, plan) | Within **10 business days** |
| Fix shipped for high/critical | Within **30 days** of the initial assessment |
| Public advisory + CVE | Within **90 days** of the initial report, unless mutually agreed otherwise |

If a fix needs longer than 90 days, we will say so in writing and propose
a new timeline.

## PGP key

Our security PGP key fingerprint:

```
0000 0000 0000 0000 0000 0000 0000 0000 0000 0000
```

(Replace this placeholder with your real fingerprint before publishing.)
Public key blob: <https://keos.platform/security.asc>

## Disclosure policy

We follow coordinated disclosure:

- Reporter and maintainers agree on a publication date.
- Fix lands in a release on the agreed date.
- Public advisory + CVE published the same day.
- Reporter credited unless they ask to stay anonymous.

If a vulnerability is already publicly known (e.g., a CVE in an upstream
dep that affects us), we publish our advisory as soon as we have a fix —
no embargo, the cat is already out of the bag.

## Scope

In scope:

- Anything in this repository (`agent/`, `mcp_server/`, `frontend/`,
  `deploy/`, `policies/`).
- The published container images at `ghcr.io/<owner>/pocteo-*`.
- The Helm chart at `oci://ghcr.io/<owner>/charts/pocteo`.

Out of scope:

- Issues in upstream dependencies — report those directly to the upstream
  project. We'll bump our pin once their fix lands.
- Misconfigurations on the operator's side (e.g., running the agent in
  `active` mode without OPA reachable, or with `networkPolicy.enabled=false`
  on a hostile network).
- Theoretical attacks that require pre-existing cluster-admin access. The
  agent runs WITH cluster permissions; the OPA gate + AgentMode kill-switch
  are its safety boundary, not absolute defenses.

## Bug bounty

We do not currently run a paid bounty program. Reporters who follow this
policy get public credit (if desired) and our gratitude.

## Hardening checklist for operators

If you're deploying Keos and want to verify the security posture before
production:

1. **`networkPolicy.enabled=true`** + your CNI actually enforces NetworkPolicy
   (vanilla k3s/flannel does NOT — switch to Calico or kube-router).
2. **`AUDIT_HMAC_KEY` rotated** away from the `.env.example` placeholder.
3. **`SHP_API_TOKEN`** sourced from a K8s `Secret` projected-volume
   (`SHP_API_TOKEN_FILE`), not the env var.
4. **TLS termination** at Caddy with a real cert (cert-manager + Let's
   Encrypt, or your internal PKI). Don't expose `:8000` raw.
5. **Pod Security Standard** set to `restricted` on the namespace. The
   chart's security contexts are designed to pass it.
6. **`monitoring.enabled=true`** with the alerts from
   [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) wired to a pager.
7. **Regular restore drills** per [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md) §"Restore drill".

If any of these are not in place, you're running with knowingly reduced
security guarantees. Document it.
