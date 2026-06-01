// WebSocket client with reconnect-backoff and typed channel subscriptions.
//
// The server protocol (see agent/ws_bridge.py):
//    {op: "subscribe",   channels: [...]}
//    {op: "unsubscribe", channels: [...]}
//    {op: "ping"} -> server replies {op: "pong"}
//    server -> client : {channel: "...", event: {...}}
//
// React side: components call `useChannel("pipeline.<id>", handler)` to
// auto-subscribe-then-cleanup on unmount.

import { useEffect, useRef } from "react";
import { useApp } from "./store";

type ChannelEvent = { channel: string; event: Record<string, unknown> };
type Handler = (event: Record<string, unknown>) => void;

class WsClient {
  private ws: WebSocket | null = null;
  private url = "";
  private handlers = new Map<string, Set<Handler>>();
  private active = new Set<string>();
  private reconnectMs = 1000;
  private intentionalClose = false;
  private pingTimer: number | null = null;

  connect(token: string) {
    if (this.ws && this.ws.readyState <= 1) return;
    this.intentionalClose = false;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    this.url = `${proto}//${host}/ws/events?token=${encodeURIComponent(token)}`;
    try {
      this.ws = new WebSocket(this.url);
    } catch (err) {
      console.warn("ws constructor failed", err);
      return;
    }
    this.ws.addEventListener("open", () => {
      this.reconnectMs = 1000;
      // Resubscribe to anything that was active before the disconnect.
      if (this.active.size) this._send({ op: "subscribe", channels: [...this.active] });
      this.pingTimer = window.setInterval(() => this._send({ op: "ping" }), 30000);
    });
    this.ws.addEventListener("message", (e) => {
      try {
        const msg = JSON.parse(e.data) as ChannelEvent | { op: string };
        if ("op" in msg) return; // pong
        const set = this.handlers.get(msg.channel);
        if (set) for (const h of set) h(msg.event);
        // Also fire wildcard pipeline.* matches.
        if (msg.channel.startsWith("pipeline.") && this.handlers.has("pipeline.*")) {
          for (const h of this.handlers.get("pipeline.*")!)
            h({ ...msg.event, _channel: msg.channel });
        }
      } catch {
        /* malformed; ignore */
      }
    });
    this.ws.addEventListener("close", () => {
      if (this.pingTimer) {
        clearInterval(this.pingTimer);
        this.pingTimer = null;
      }
      if (this.intentionalClose) return;
      const backoff = Math.min(this.reconnectMs, 30000);
      window.setTimeout(() => this.connect(token), backoff);
      this.reconnectMs = Math.min(this.reconnectMs * 2, 30000);
    });
    this.ws.addEventListener("error", () => {
      // The close handler will trigger reconnect.
    });
  }

  disconnect() {
    this.intentionalClose = true;
    try {
      this.ws?.close();
    } catch {
      /* ignore */
    }
    this.ws = null;
  }

  subscribe(channel: string, handler: Handler): () => void {
    let set = this.handlers.get(channel);
    if (!set) {
      set = new Set();
      this.handlers.set(channel, set);
    }
    set.add(handler);
    if (!this.active.has(channel)) {
      this.active.add(channel);
      this._send({ op: "subscribe", channels: [channel] });
    }
    return () => this._unsubscribe(channel, handler);
  }

  private _unsubscribe(channel: string, handler: Handler) {
    const set = this.handlers.get(channel);
    if (set) {
      set.delete(handler);
      if (set.size === 0) {
        this.handlers.delete(channel);
        this.active.delete(channel);
        this._send({ op: "unsubscribe", channels: [channel] });
      }
    }
  }

  private _send(msg: unknown) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify(msg));
      } catch {
        /* ignore */
      }
    }
  }
}

export const wsClient = new WsClient();

/** Connect on token change; disconnect when no token. */
export function useWsLifecycle() {
  const token = useApp((s) => s.token);
  useEffect(() => {
    if (token) wsClient.connect(token);
    else wsClient.disconnect();
  }, [token]);
}

/** Subscribe to a channel for the lifetime of the calling component. */
export function useChannel(channel: string | null, handler: Handler) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;
  useEffect(() => {
    if (!channel) return;
    const cb: Handler = (ev) => handlerRef.current(ev);
    return wsClient.subscribe(channel, cb);
  }, [channel]);
}
