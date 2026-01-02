import { WS_URL } from "./config";
import { queueFrame } from "./decoder";

let ws: WebSocket | null = null;

export function connectWebSocket(): void {
  ws = new WebSocket(WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => console.log("[Relay] WebSocket connected");

  ws.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) {
      queueFrame(e.data);
    }
  };

  ws.onclose = () => {
    console.log("[Relay] WebSocket closed, reconnecting...");
    setTimeout(connectWebSocket, 1000);
  };
}
