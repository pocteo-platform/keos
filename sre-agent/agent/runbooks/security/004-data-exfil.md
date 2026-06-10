# Runbook 004 — Data exfiltration / suspicious egress

**Tags** data-exfil, egress, c2, suspicious-network, tetragon, falco

## Symptom

A pod opens an outbound network connection to an unexpected destination —
an IP or domain outside the workload's known dependency set. Shapes:

- A connection to a raw external IP (no DNS), often on an odd port.
- A spike in egress bytes from a pod that normally talks only to in-cluster
  services.
- A connection to a known-bad / newly-registered domain, or a cloud metadata
  endpoint (`169.254.169.254`) being scraped for credentials.

Tetragon's network-tracing (`connect`/`sendmsg` events) and Falco's
"Unexpected outbound connection" / "Contact cloud metadata service" rules
fire here. Telltale fields: `net.dst.ip` outside the cluster CIDR + the
workload's allowlist, `net.dst.port`, egress byte counts well above baseline.

## Diagnosis

Suspicious egress is the **exfiltration** or **command-and-control** stage —
later in the kill chain than execution. By the time you see it, the attacker
likely already has code execution and is now either stealing data or phoning
home for instructions. Distinguish:

- **Exfil**: high outbound bytes to an external endpoint = data leaving.
- **C2 beacon**: small, periodic connections to a fixed external IP.
- **Credential theft**: connection to the cloud metadata IP from a workload
  that has no reason to query it.
- **(False positive)**: a legitimate new third-party integration nobody
  added to the egress allowlist. Weigh whether the destination is plausibly
  a real dependency.

Confidence is high for the metadata-endpoint case or a connection to a raw
external IP from an internal-only workload. Lower if the destination could be
a legitimate-but-undocumented SaaS dependency.

## Containment

1. **apply_network_policy** (deny-egress, DNS excepted) — the right primary
   response for exfil: it **cuts the leak channel immediately** while keeping
   the pod alive and observable (ingress intact) so the IR team can watch the
   attacker's next move and pull logs. Reversible.
2. **capture_forensics** to record the destination IOCs + the recent log/
   process trail (what data was being staged, what process opened the socket).
3. Escalate to **isolate_pod** (full cut) only if the threat is also actively
   executing (a live shell/miner on the same pod) — then containment of the
   whole pod outweighs keeping it observable.

## Escalation

**page_security** with the destination IOCs (IP/domain/port) and the egress
volume. The IR team assesses **what data could have left** (the pod's mounted
secrets, the data it can read) and whether the same beacon destination appears
from other pods — a shared C2 endpoint means a fleet-wide compromise.
