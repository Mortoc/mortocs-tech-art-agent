import { type ClientMessage, type ServerMessage, decodeServer, encode } from "@houdini-pi/protocol";

export type Status = "connecting" | "open" | "closed";

/** Reconnecting WebSocket to the agent core. The launch token rides on the page URL. */
export class PaneSocket {
  private socket: WebSocket | undefined;
  private attempt = 0;
  private closedByUs = false;
  onMessage: (message: ServerMessage) => void = () => {};
  onStatus: (status: Status) => void = () => {};

  constructor(private readonly url: string) {}

  static urlFromPage(location: Location): string {
    const params = new URLSearchParams(location.search);
    const token = params.get("token") ?? "";
    const client = params.get("client") ?? "";
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    return `${scheme}://${location.host}/ws?token=${encodeURIComponent(token)}&client=${encodeURIComponent(client)}`;
  }

  connect() {
    this.closedByUs = false;
    this.onStatus("connecting");
    const socket = new WebSocket(this.url);
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
      this.onStatus("open");
    };
    socket.onmessage = (event) => this.onMessage(decodeServer(String(event.data)));
    socket.onclose = () => {
      this.onStatus("closed");
      if (this.closedByUs) return;
      const delay = Math.min(10_000, 500 * 2 ** this.attempt++);
      setTimeout(() => this.connect(), delay);
    };
  }

  send(message: ClientMessage) {
    const ready = this.socket?.readyState === WebSocket.OPEN;
    if (!ready) throw new Error("not connected to the sidecar");
    this.socket!.send(encode(message));
  }

  close() {
    this.closedByUs = true;
    this.socket?.close();
  }
}
