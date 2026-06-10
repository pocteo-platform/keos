# Runbook 001 — Interactive / reverse shell in container

**Tags** reverse-shell, terminal, lateral-movement, falco, tetragon

## Symptom

A shell process (`/bin/sh`, `bash`, `/bin/sh -i`, `bash -i`) is spawned
**inside an application container** whose parent process is the app itself
(not an init system, not a debug sidecar). Falco's "Terminal shell in
container" rule or Tetragon's process-exec tracing fires. Telltale fields:
`proc.cmdline` contains `-i` (interactive), `proc.pname` is the app binary,
the container was not started with a shell entrypoint.

## Diagnosis

This is the single strongest "we are popped" signal in runtime security.
An interactive shell inside a production container almost always means one of:

- A remote attacker dropped a reverse shell via an exploited dependency
  (deserialization, SSRF→RCE, a vulnerable web framework).
- A supply-chain compromise executing at runtime.
- (False positive) An operator running `kubectl exec -it … -- sh` for
  debugging — check whether a human session is active at this exact time.

Confidence is high when: the shell is interactive (`-i`), the parent is the
app process, and no operator `kubectl exec` is recorded. Confidence drops if
the container's normal entrypoint is a shell, or an operator session lines up.

## Containment

1. **isolate_pod** — apply a deny-all NetworkPolicy + quarantine label. This
   cuts the reverse-shell's command-and-control channel and stops lateral
   movement immediately. Reversible.
2. If forensics matter, prefer **quarantine_pod** (isolate + evidence-hold
   annotation) so the pod isn't immediately rescheduled over.
3. **capture_forensics** to pull the recent process/log trail into the audit
   record before the evidence rotates away.

Do **not** `kill_pod` first — killing the pod destroys the live process state
the IR team needs. Isolate, capture, then escalate.

## Escalation

Always **page_security**. A confirmed reverse shell is an incident, not an
SRE event — a human owns the forensics, the blast-radius assessment (did the
attacker reach secrets? other pods? the node?), and the rotate-credentials
decision. The agent's job is to contain fast and hand off with a clean trail.
