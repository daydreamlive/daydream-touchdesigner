import type {
  PeerConnectionFactory,
  FetchFn,
  TimerProvider,
} from "./dependencies";
import type { RedirectCache } from "./webrtc";

export const DEFAULT_ICE_SERVERS: RTCIceServer[] = [
  { urls: "stun:stun.l.google.com:19302" },
  { urls: "stun:stun1.l.google.com:19302" },
];

export const DEFAULT_VIDEO_BITRATE = 300_000;
export const DEFAULT_AUDIO_BITRATE = 64_000;

export interface WHIPResponseResult {
  whepUrl: string | null;
}

export interface WHIPClientConfig {
  url: string;
  iceServers?: RTCIceServer[];
  videoBitrate?: number;
  audioBitrate?: number;
  maxFramerate?: number;
  skipIceGathering?: boolean;
  onStats?: (report: RTCStatsReport) => void;
  statsIntervalMs?: number;
  onResponse?: (response: Response) => WHIPResponseResult | void;
  peerConnectionFactory?: PeerConnectionFactory;
  fetch?: FetchFn;
  timers?: TimerProvider;
  redirectCache?: RedirectCache;
}

export interface WHEPClientConfig {
  url: string;
  iceServers?: RTCIceServer[];
  skipIceGathering?: boolean;
  onTrack?: (event: RTCTrackEvent) => void;
  maxRetries?: number;
  retryDelayMs?: number;
}
