import type { LogEntry } from "./types";

// WebSocket 日志流客户端：自动重连，收到消息回调。
export class LogSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private onMsg: (e: LogEntry) => void;
  private onStatus: (connected: boolean) => void;
  private closed = false;
  private retry = 0;

  constructor(onMsg: (e: LogEntry) => void, onStatus: (connected: boolean) => void) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    this.url = `${proto}//${location.host}/api/ws`;
    this.onMsg = onMsg;
    this.onStatus = onStatus;
  }

  connect() {
    this.closed = false;
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.retry = 0;
      this.onStatus(true);
    };
    this.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data && typeof data.message === "string") this.onMsg(data as LogEntry);
      } catch { /* ignore */ }
    };
    this.ws.onclose = () => {
      this.onStatus(false);
      this.scheduleReconnect();
    };
    this.ws.onerror = () => {
      try { this.ws?.close(); } catch { /* noop */ }
    };
  }

  private scheduleReconnect() {
    if (this.closed) return;
    this.retry = Math.min(this.retry + 1, 8);
    const delay = Math.min(1000 * this.retry, 8000);
    setTimeout(() => this.connect(), delay);
  }

  close() {
    this.closed = true;
    try { this.ws?.close(); } catch { /* noop */ }
    this.ws = null;
  }
}
