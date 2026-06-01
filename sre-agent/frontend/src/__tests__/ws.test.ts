// WebSocket client tests — specifically the wildcard-channel dispatch
// (`pipeline.*` matches every `pipeline.<id>`) which is the only piece of
// logic in ws.ts that isn't trivially "forward to a Map<string, Set<fn>>".
//
// Strategy: stub the global WebSocket so we can drive open/message events
// deterministically, then exercise the public subscribe + message path.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// We import the singleton — its private state must be cleared between
// tests so handlers from one test don't leak into the next.
import { wsClient } from "@/lib/ws";

// Minimal in-memory fake. Just enough to satisfy ws.ts's expectations:
// readyState, addEventListener, send, plus a way for the test to trigger
// open/message/close synchronously.
class FakeWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  url: string;
  readyState = 0;
  private listeners: Record<string, ((e: unknown) => void)[]> = {};
  sent: unknown[] = [];

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, fn: (e: unknown) => void) {
    (this.listeners[type] ??= []).push(fn);
  }

  send(data: string) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
    this._fire("close", {});
  }

  // Test helpers — drive the WebSocket from outside.
  _open() {
    this.readyState = FakeWebSocket.OPEN;
    this._fire("open", {});
  }

  _recvMessage(payload: unknown) {
    this._fire("message", { data: JSON.stringify(payload) });
  }

  private _fire(type: string, event: unknown) {
    for (const fn of this.listeners[type] ?? []) fn(event);
  }
}

describe("wsClient channel dispatch", () => {
  beforeEach(() => {
    // Stub the global WebSocket constructor so wsClient.connect uses our fake.
    FakeWebSocket.instances.length = 0;
    vi.stubGlobal("WebSocket", FakeWebSocket);

    // Reset the singleton's internal state. These are private fields, but
    // at runtime they're plain properties on the instance.
    const internal = wsClient as unknown as {
      ws: WebSocket | null;
      handlers: Map<string, Set<unknown>>;
      active: Set<string>;
      intentionalClose: boolean;
      pingTimer: number | null;
    };
    internal.ws = null;
    internal.handlers = new Map();
    internal.active = new Set();
    internal.intentionalClose = false;
    if (internal.pingTimer !== null) {
      clearInterval(internal.pingTimer);
      internal.pingTimer = null;
    }
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function getSocket(): FakeWebSocket {
    expect(FakeWebSocket.instances).toHaveLength(1);
    return FakeWebSocket.instances[0]!;
  }

  it("subscribe sends a subscribe op once OPEN", () => {
    wsClient.connect("tok-xyz");
    const sock = getSocket();

    const handler = vi.fn();
    wsClient.subscribe("pipeline.inc-1", handler);
    sock._open();

    expect(sock.sent[0]).toEqual({ op: "subscribe", channels: ["pipeline.inc-1"] });
  });

  it("dispatches an exact-channel message to its handler", () => {
    wsClient.connect("tok");
    const sock = getSocket();
    sock._open();

    const handler = vi.fn();
    wsClient.subscribe("pipeline.inc-A", handler);

    sock._recvMessage({ channel: "pipeline.inc-A", event: { node: "triage" } });

    expect(handler).toHaveBeenCalledOnce();
    expect(handler).toHaveBeenCalledWith({ node: "triage" });
  });

  it("wildcard 'pipeline.*' fires for every pipeline.<id> channel", () => {
    wsClient.connect("tok");
    const sock = getSocket();
    sock._open();

    const wildcard = vi.fn();
    wsClient.subscribe("pipeline.*", wildcard);

    sock._recvMessage({ channel: "pipeline.inc-A", event: { node: "triage" } });
    sock._recvMessage({ channel: "pipeline.inc-B", event: { node: "diagnose" } });

    expect(wildcard).toHaveBeenCalledTimes(2);
    // The wildcard handler receives the event WITH a `_channel` field
    // injected so the consumer can demux. This is the contract the
    // /pipeline/live view relies on.
    expect(wildcard).toHaveBeenNthCalledWith(1, {
      node: "triage",
      _channel: "pipeline.inc-A",
    });
    expect(wildcard).toHaveBeenNthCalledWith(2, {
      node: "diagnose",
      _channel: "pipeline.inc-B",
    });
  });

  it("non-pipeline channels do NOT match the wildcard", () => {
    wsClient.connect("tok");
    const sock = getSocket();
    sock._open();

    const wildcard = vi.fn();
    wsClient.subscribe("pipeline.*", wildcard);

    sock._recvMessage({ channel: "audit", event: { kind: "emit" } });
    sock._recvMessage({ channel: "mode", event: { mode: "active" } });

    expect(wildcard).not.toHaveBeenCalled();
  });

  it("ignores pong + malformed frames silently", () => {
    wsClient.connect("tok");
    const sock = getSocket();
    sock._open();

    const handler = vi.fn();
    wsClient.subscribe("pipeline.inc-A", handler);

    sock._recvMessage({ op: "pong" }); // ws.ts checks `"op" in msg` and returns.

    // Drive a raw non-JSON frame straight through the listener registry.
    // Same effect as _fire("message", {data}) but we keep `_fire` private
    // on FakeWebSocket — accessing private state from outside via cast.
    type WithListeners = { listeners: Record<string, ((e: unknown) => void)[]> };
    const internal = sock as unknown as WithListeners;
    for (const fn of internal.listeners["message"] ?? []) {
      fn({ data: "not json {" });
    }

    expect(handler).not.toHaveBeenCalled();
  });
});
