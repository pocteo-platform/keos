# Runbook 002 — Crypto-miner / suspicious process

**Tags** crypto-miner, xmrig, resource-abuse, suspicious-binary, falco

## Symptom

An unexpected binary is executing inside a container — classically a miner
(`xmrig`, `minerd`, `cpuminer`), but any process that doesn't belong to the
workload's image counts. Signals: Falco's "Launch suspicious tool" /
"Unexpected spawned process" rules, a process name not in the image's
expected set, sustained high CPU on a pod that should be idle, or an
outbound connection to a known mining pool. Telltale fields: `proc.name`
matches a miner signature, `proc.cmdline` references a wallet address or a
stratum URL, CPU usage is pinned.

## Diagnosis

Crypto-mining is the most common post-exploitation payload — once an attacker
has code execution, mining monetises it quietly. The presence of a miner
binary confirms the container is already compromised; the mining itself is a
symptom, not the root cause. Correlate with:

- Recent reverse-shell or exec events on the same pod (the entry vector).
- The image's expected process set (a miner is never in a legitimate image).
- Egress to mining-pool IPs / stratum ports.

Confidence is high when the process name matches a known miner or the cmdline
carries a wallet/stratum string. Lower if it's just "high CPU" with no binary
signature — that may be a legitimate batch job; weigh the σ-above-baseline.

## Containment

1. **isolate_pod** — deny-all NetworkPolicy + quarantine. Cuts the mining
   pool connection (mining stops the moment egress is severed) AND any C2.
2. **capture_forensics** to record the process tree + logs (the wallet
   address and pool URL are valuable IOCs for the IR team).
3. If the workload is replicated and the compromised pod must go,
   **kill_pod** — but only after isolate + capture, and only at critical
   severity. The owning controller respawns a clean pod.

## Escalation

**page_security** with the miner signature, wallet/pool IOCs, and the entry
vector if known. The IR team rotates any credentials the pod could reach and
hunts for the same miner across the rest of the fleet — a miner on one pod
usually means the same exploit landed on others.
