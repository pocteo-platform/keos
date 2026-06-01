---
id: nats-down
slug: /runbooks/nats-down
title: NATS / JetStream is down
sidebar_position: 4
---

# Runbook: NATS down / consumer stuck

## Symptom

- `keos_nats_consumer_pending{lane="observer"}` keeps growing past 50.
- Real cluster failures fire but no new incidents appear in the Incidents
  view.
- `kubectl logs deploy/api` shows `nats: connection refused` or
  `consumer info failed` on the queue-depth sampler tick (every 15 s).
- `kubectl get pod nats-0` shows the pod NotReady, CrashLoopBackOff, or
  in a tight restart loop.

## Triage (5 min)

```bash
# 1. Pod state.
kubectl -n pocteo get pod nats-0 -o wide
kubectl -n pocteo describe pod nats-0 | tail -30

# 2. NATS itself.
kubectl -n pocteo logs nats-0 --tail=80

# 3. JetStream state.
kubectl -n pocteo exec deploy/api -- python3 -c "
import asyncio, nats
async def main():
    nc = await nats.connect('nats://nats:4222', connect_timeout=5)
    js = nc.jetstream()
    info = await js.stream_info('INFRA')
    print(f'INFRA stream: messages={info.state.messages} last_seq={info.state.last_seq}')
    for d in ('pocteo-agent-user', 'pocteo-agent-obs'):
        c = await js.consumer_info('INFRA', d)
        print(f'  {d}: delivered={c.delivered.stream_seq} pending={c.num_pending} ack_pending={c.num_ack_pending}')
    await nc.drain()
asyncio.run(main())
"

# 4. Disk on the JetStream PVC.
kubectl -n pocteo exec nats-0 -- df -h /data
```

| Pattern | Cause | Jump to |
|---|---|---|
| Pod CrashLoopBackOff, log shows `EOF` reading store | JetStream store corrupted | Resolution §A |
| Pod Running, `INFRA` stream missing | Stream got deleted somehow (manual nats CLI?) | Resolution §B |
| `pending` huge + `ack_pending` stuck at 1 for >10 min | Graph hung on one message; ack_wait timed out and it redelivers in a loop | Resolution §C |
| Pod Pending, PVC stuck | Storage backend unhappy | Resolution §D |
| `consumer not found` errors in api log | Durables deleted; consumers will recreate on next subscribe | Resolution §E |

## Containment

If incidents are queueing because the graph can't keep up, **flip mode
to `frozen`**:

```bash
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"frozen","note":"runbook: nats-down — pause writes"}}'
```

This lets the consumer drain its backlog without producing new
side effects. If the underlying NATS is truly broken, this has no effect
(no consumer, no actions); it's a no-op for forensics.

## Resolution

### §A JetStream store corrupted

```bash
# Inspect the stream files directly.
kubectl -n pocteo exec nats-0 -- ls -la /data/jetstream

# Last resort: blow away the JetStream store + restart. Loses any
# UNPROCESSED events; processed incidents are already in the audit log
# and incident_store.
kubectl -n pocteo scale sts nats --replicas=0
kubectl -n pocteo exec --tty=false debug-pod -- rm -rf /data/jetstream/INFRA
# ^ requires a debug pod with the same PVC mounted. Easier:
kubectl -n pocteo delete pvc data-nats-0
kubectl -n pocteo scale sts nats --replicas=1

# The agent's serve.py recreates the stream on first publish.
kubectl -n pocteo wait --for=condition=ready pod/nats-0 --timeout=60s
```

### §B INFRA stream missing

```bash
# Trigger a synthetic publish — agent/serve.py calls js.add_stream on
# every consumer startup, but a fresh publish via usecase_runner forces
# the path quickly.
TOKEN=$(kubectl -n pocteo get secret pocteo-pocteo-auth -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d)
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost/api/usecases/lib-memory-leak/run

# OR, restart api so its `_nats_consumer()` reruns add_stream:
kubectl -n pocteo rollout restart deploy/api
```

### §C Hung graph causing redelivery loop

The most likely cause: a single message keeps redelivering because the
graph crashes on it deterministically.

```bash
# Identify the bad message.
kubectl -n pocteo logs deploy/api --tail=300 | grep -E "graph crashed|nak"

# Drop it from the consumer cursor. Be sure first — this loses the
# message permanently.
kubectl -n pocteo exec deploy/api -- python3 -c "
import asyncio, nats
async def main():
    nc = await nats.connect('nats://nats:4222')
    js = nc.jetstream()
    sub = await js.pull_subscribe('infra.events.>', durable='pocteo-agent-obs')
    msgs = await sub.fetch(1, timeout=2)
    for m in msgs:
        print('would-ack:', m.subject, m.data[:120])
        await m.ack()
    await nc.drain()
asyncio.run(main())
"
```

If many messages are jammed, **purge the stream entirely** (acceptable —
cluster events will fire again, and the audit log retains everything
that already ran):

```bash
kubectl -n pocteo exec deploy/api -- python3 -c "
import asyncio, nats
async def main():
    nc = await nats.connect('nats://nats:4222')
    js = nc.jetstream()
    info = await js.stream_info('INFRA')
    print(f'Purging {info.state.messages} messages')
    await js.purge_stream('INFRA')
    await nc.drain()
asyncio.run(main())
"
```

### §D PVC pending

```bash
kubectl -n pocteo get pvc data-nats-0
kubectl -n pocteo describe pvc data-nats-0

# Storage class issue — see USER_GUIDE.md §10 for the per-distro fixes.
```

### §E Durable consumers missing

```bash
# Restart api — the two-lane consumer setup in serve.py calls
# pull_subscribe(durable=...) which recreates the durables.
kubectl -n pocteo rollout restart deploy/api
```

## After the fix

```bash
# Drain check.
kubectl -n pocteo exec deploy/api -- python3 -c "
import asyncio, nats
async def main():
    nc = await nats.connect('nats://nats:4222')
    js = nc.jetstream()
    for d in ('pocteo-agent-user','pocteo-agent-obs'):
        c = await js.consumer_info('INFRA', d)
        print(f'{d}: pending={c.num_pending} ack_pending={c.num_ack_pending}')
    await nc.drain()
asyncio.run(main())
"
# Expect: small numbers, dropping.

# Back to active.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"active","note":"runbook: nats-down cleared"}}'
```

## Postmortem template

Same shape as [agent-api-down.md](agent-api-down.md#postmortem-template).
For the "what we'll change" section, consider:

- Did the alert fire on `keos_nats_consumer_pending` before the symptom
  was user-visible? If not, tune the threshold.
- Was the cause data (corruption) or capacity (graph too slow)? Capacity
  means [JETSON.md](/docs/platforms/jetson) GPU offload is worth a serious look.
