import { initDecoder } from "./decoder";
import { connectWebSocket } from "./websocket";
import { RelayManager } from "./webrtc";
import { startAuroraWorker, stopAuroraWorker } from "./aurora";

const canvas = document.getElementById("input-canvas") as HTMLCanvasElement;
const outputVideo = document.getElementById("output-video") as HTMLVideoElement;
const auroraCanvas = document.getElementById("aurora") as HTMLCanvasElement;
const statusEl = document.getElementById("status") as HTMLDivElement;
const statusText = document.getElementById("status-text") as HTMLDivElement;

function log(msg: string): void {
  console.log("[Relay]", msg);
  statusText.textContent = msg;
}

function hideStatus(): void {
  auroraCanvas.classList.add("hidden");
  statusEl.classList.add("hidden");
  setTimeout(stopAuroraWorker, 300);
}

const relay = new RelayManager({
  inputCanvas: canvas,
  outputVideo: outputVideo,
  onVideoStarted: hideStatus,
  onLog: log,
  frameRate: 30,
});

function init(): void {
  log("Starting...");

  initDecoder(canvas);
  startAuroraWorker(auroraCanvas);

  connectWebSocket();
  setTimeout(() => relay.warmup(), 100);
  relay.start();
}

init();
