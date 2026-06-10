# Runbook 003 — Privilege escalation / sensitive mount

**Tags** privilege-escalation, sensitive-mount, container-escape, hostpath, falco

## Symptom

A container attempts to gain capabilities beyond its sandbox. Common shapes:

- A **sensitive mount**: `/var/run/docker.sock`, the containerd socket, the
  host root `/`, `/etc`, `/proc/sys`, or a hostPath into a node directory.
  Mounting the Docker/containerd socket is a direct container-escape primitive.
- **Capability abuse**: a process gains `CAP_SYS_ADMIN`, `CAP_SYS_PTRACE`,
  or runs `setuid`/`setgid` to escalate to root.
- **Writing to a sensitive host path** from inside the container.

Falco rules: "Launch privileged container", "Sensitive mount launched",
"Write below /etc", "Change thread namespace". Telltale fields:
`container.mounts` references a host socket or `/`, `proc.cmdline` runs a
privilege tool (`nsenter`, `unshare`, `mount`), the container is privileged.

## Diagnosis

Privilege escalation is the bridge from "code execution in a container" to
"control of the node". The Docker/containerd socket mount is especially
severe: a process with that socket can launch a new privileged container on
the host and escape the sandbox entirely. Distinguish:

- **Genuine attack**: the mount/capability is unexpected for this workload
  and correlates with a prior exec/shell event.
- **Misconfiguration**: some legitimate tooling (CI runners, monitoring
  agents) does mount the socket. Check whether this is the workload's
  designed behaviour (it's in the chart) vs. a runtime surprise.

Confidence is high when the mount is the docker/containerd socket or host `/`
AND it's unexpected for the workload. Lower for a workload that documents the
mount in its spec.

## Containment

1. **isolate_pod** to cut network access while you assess — even if the
   escape primitive is local (the socket), isolating stops any C2 + exfil.
2. **capture_forensics** — record what was mounted and what ran. The mount
   list + process tree are the evidence the IR team needs to judge whether
   the node was reached.
3. For a confirmed escape attempt on a replicated workload, **kill_pod** at
   critical severity after capture — but be aware that if the node is already
   compromised, killing the pod is not sufficient; the escalation must flag
   the node for isolation/reimage (a human decision).

## Escalation

Always **page_security**, and flag the **node** as potentially compromised in
the page reason. Unlike an in-container threat, a privilege-escalation /
escape attempt puts the host in scope: the IR team must decide whether to
cordon + drain + reimage the node, not just recycle the pod.
