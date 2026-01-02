import { SDP_ORIGIN, WHIP_PROXY, WHEP_PROXY } from "./config";
import {
  DEFAULT_ICE_SERVERS,
  DEFAULT_VIDEO_BITRATE,
  DEFAULT_AUDIO_BITRATE,
  type WHIPClientConfig,
  type WHEPClientConfig,
  type WHIPResponseResult,
} from "./types";
import { ConnectionError, NetworkError } from "./errors";
import {
  type PeerConnectionFactory,
  type FetchFn,
  type TimerProvider,
  defaultPeerConnectionFactory,
  defaultFetch,
  defaultTimerProvider,
} from "./dependencies";

const PLAYBACK_ID_PATTERN = /([/+])([^/+?]+)$/;
const PLAYBACK_ID_PLACEHOLDER = "__PLAYBACK_ID__";

export interface RedirectCache {
  get(key: string): URL | undefined;
  set(key: string, value: URL): void;
}

class LRURedirectCache implements RedirectCache {
  private cache = new Map<string, URL>();
  private readonly maxSize: number;

  constructor(maxSize = 10) {
    this.maxSize = maxSize;
  }

  get(key: string): URL | undefined {
    const cached = this.cache.get(key);
    if (cached) {
      this.cache.delete(key);
      this.cache.set(key, cached);
    }
    return cached;
  }

  set(key: string, value: URL): void {
    if (this.cache.has(key)) {
      this.cache.delete(key);
    } else if (this.cache.size >= this.maxSize) {
      const oldestKey = this.cache.keys().next().value;
      if (oldestKey) this.cache.delete(oldestKey);
    }
    this.cache.set(key, value);
  }
}

const sharedRedirectCache = new LRURedirectCache();

function preferH264(sdp: string): string {
  const lines = sdp.split("\r\n");
  const mLineIndex = lines.findIndex((line) => line.startsWith("m=video"));
  if (mLineIndex === -1) return sdp;

  const codecRegex = /a=rtpmap:(\d+) H264(\/\d+)+/;
  const codecLine = lines.find((line) => codecRegex.test(line));
  if (!codecLine) return sdp;

  const match = codecRegex.exec(codecLine);
  const codecPayload = match?.[1];
  if (!codecPayload) return sdp;

  const mLine = lines[mLineIndex];
  if (!mLine) return sdp;

  const mLineElements = mLine.split(" ");
  const reorderedMLine = [
    ...mLineElements.slice(0, 3),
    codecPayload,
    ...mLineElements.slice(3).filter((payload) => payload !== codecPayload),
  ];
  lines[mLineIndex] = reorderedMLine.join(" ");
  return lines.join("\r\n");
}

export class WHIPClient {
  private readonly url: string;
  private readonly iceServers: RTCIceServer[];
  private readonly videoBitrate: number;
  private readonly audioBitrate: number;
  private readonly onStats?: (report: RTCStatsReport) => void;
  private readonly statsIntervalMs: number;
  private readonly onResponse?: (
    response: Response,
  ) => WHIPResponseResult | void;
  private readonly pcFactory: PeerConnectionFactory;
  private readonly fetch: FetchFn;
  private readonly timers: TimerProvider;
  private readonly redirectCache: RedirectCache;
  private readonly skipIceGathering: boolean;

  private maxFramerate?: number;
  private pc: RTCPeerConnection | null = null;
  private resourceUrl: string | null = null;
  private abortController: AbortController | null = null;
  private statsTimer: number | null = null;
  private videoSender: RTCRtpSender | null = null;
  private audioSender: RTCRtpSender | null = null;
  private videoTransceiver: RTCRtpTransceiver | null = null;
  private audioTransceiver: RTCRtpTransceiver | null = null;
  private iceGatheringTimer: number | null = null;

  constructor(config: WHIPClientConfig) {
    this.url = config.url;
    this.iceServers = config.iceServers ?? DEFAULT_ICE_SERVERS;
    this.videoBitrate = config.videoBitrate ?? DEFAULT_VIDEO_BITRATE;
    this.audioBitrate = config.audioBitrate ?? DEFAULT_AUDIO_BITRATE;
    this.maxFramerate = config.maxFramerate;
    this.onStats = config.onStats;
    this.statsIntervalMs = config.statsIntervalMs ?? 5000;
    this.onResponse = config.onResponse;
    this.pcFactory =
      config.peerConnectionFactory ?? defaultPeerConnectionFactory;
    this.fetch = config.fetch ?? defaultFetch;
    this.timers = config.timers ?? defaultTimerProvider;
    this.redirectCache = config.redirectCache ?? sharedRedirectCache;
    this.skipIceGathering = config.skipIceGathering ?? true;
  }

  async connect(stream: MediaStream): Promise<{ whepUrl: string | null }> {
    this.cleanup();

    this.pc = this.pcFactory.create({
      iceServers: this.iceServers,
      iceCandidatePoolSize: 10,
    });

    this.videoTransceiver = this.pc.addTransceiver("video", {
      direction: "sendonly",
    });
    this.audioTransceiver = this.pc.addTransceiver("audio", {
      direction: "sendonly",
    });
    this.videoSender = this.videoTransceiver.sender;
    this.audioSender = this.audioTransceiver.sender;

    const videoTrack = stream.getVideoTracks()[0];
    const audioTrack = stream.getAudioTracks()[0];

    if (videoTrack) {
      if (videoTrack.contentHint === "") {
        videoTrack.contentHint = "motion";
      }
      await this.videoSender.replaceTrack(videoTrack);
    }

    if (audioTrack) {
      await this.audioSender.replaceTrack(audioTrack);
    }

    this.setCodecPreferences();
    await this.applyBitrateConstraints();

    const offer = await this.pc.createOffer({
      offerToReceiveAudio: false,
      offerToReceiveVideo: false,
    });
    const enhancedSdp = preferH264(offer.sdp ?? "");
    await this.pc.setLocalDescription({ type: "offer", sdp: enhancedSdp });

    if (!this.skipIceGathering) {
      await this.waitForIceGathering();
    }

    this.abortController = new AbortController();
    const timeoutId = this.timers.setTimeout(
      () => this.abortController?.abort(),
      10000,
    );

    try {
      const fetchUrl = this.getUrlWithCachedRedirect();

      const response = await this.fetch(fetchUrl, {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: this.pc.localDescription!.sdp,
        signal: this.abortController.signal,
      });

      this.timers.clearTimeout(timeoutId);

      if (!response.ok) {
        const errorText = await response.text().catch(() => "");
        throw new ConnectionError(
          `WHIP connection failed: ${response.status} ${response.statusText} ${errorText}`,
        );
      }

      this.cacheRedirectIfNeeded(fetchUrl, response.url);

      const location = response.headers.get("location");
      if (location) {
        this.resourceUrl = new URL(location, this.url).toString();
      }

      const responseResult = this.onResponse?.(response);

      const answerSdp = await response.text();
      await this.pc.setRemoteDescription({ type: "answer", sdp: answerSdp });

      await this.applyBitrateConstraints();
      this.startStatsTimer();

      return { whepUrl: responseResult?.whepUrl ?? null };
    } catch (error) {
      this.timers.clearTimeout(timeoutId);
      if (error instanceof ConnectionError) {
        throw error;
      }
      if (error instanceof Error && error.name === "AbortError") {
        throw new NetworkError("Connection timeout");
      }
      throw new NetworkError("Failed to establish connection", error);
    }
  }

  private setCodecPreferences(): void {
    if (!this.videoTransceiver?.setCodecPreferences) return;

    try {
      const caps = RTCRtpSender.getCapabilities("video");
      if (!caps?.codecs?.length) return;

      const h264Codecs = caps.codecs.filter((c) =>
        c.mimeType.toLowerCase().includes("h264"),
      );
      if (h264Codecs.length) {
        this.videoTransceiver.setCodecPreferences(h264Codecs);
      }
    } catch {
      // Codec preferences not supported
    }
  }

  private async applyBitrateConstraints(): Promise<void> {
    if (!this.pc) return;

    const senders = this.pc.getSenders();
    for (const sender of senders) {
      if (!sender.track) continue;

      const params = sender.getParameters();
      if (!params.encodings) params.encodings = [{}];

      const encoding = params.encodings[0];
      if (!encoding) continue;

      if (sender.track.kind === "video") {
        encoding.maxBitrate = this.videoBitrate;
        if (this.maxFramerate && this.maxFramerate > 0) {
          encoding.maxFramerate = this.maxFramerate;
        }
        encoding.scaleResolutionDownBy = 1.0;
        encoding.priority = "high";
        encoding.networkPriority = "high";
        params.degradationPreference = "maintain-resolution";
      } else if (sender.track.kind === "audio") {
        encoding.maxBitrate = this.audioBitrate;
        encoding.priority = "medium";
        encoding.networkPriority = "medium";
      }

      try {
        await sender.setParameters(params);
      } catch {
        // Parameters not supported
      }
    }
  }

  private waitForIceGathering(): Promise<void> {
    return new Promise((resolve) => {
      if (!this.pc) {
        resolve();
        return;
      }

      if (this.pc.iceGatheringState === "complete") {
        resolve();
        return;
      }

      const onStateChange = () => {
        if (this.pc?.iceGatheringState === "complete") {
          this.pc.removeEventListener("icegatheringstatechange", onStateChange);
          if (this.iceGatheringTimer !== null) {
            this.timers.clearTimeout(this.iceGatheringTimer);
            this.iceGatheringTimer = null;
          }
          resolve();
        }
      };

      this.pc.addEventListener("icegatheringstatechange", onStateChange);

      this.iceGatheringTimer = this.timers.setTimeout(() => {
        this.pc?.removeEventListener("icegatheringstatechange", onStateChange);
        this.iceGatheringTimer = null;
        resolve();
      }, 1000);
    });
  }

  private startStatsTimer(): void {
    if (!this.onStats || !this.pc) return;

    this.stopStatsTimer();

    this.statsTimer = this.timers.setInterval(async () => {
      if (!this.pc) return;
      try {
        const report = await this.pc.getStats();
        this.onStats?.(report);
      } catch {
        // Stats collection failed
      }
    }, this.statsIntervalMs);
  }

  private stopStatsTimer(): void {
    if (this.statsTimer !== null) {
      this.timers.clearInterval(this.statsTimer);
      this.statsTimer = null;
    }
  }

  async replaceTrack(track: MediaStreamTrack): Promise<void> {
    if (!this.pc) {
      throw new ConnectionError("Not connected");
    }

    const sender = track.kind === "video" ? this.videoSender : this.audioSender;
    if (!sender) {
      throw new ConnectionError(
        `No sender found for track kind: ${track.kind}`,
      );
    }

    await sender.replaceTrack(track);
    await this.applyBitrateConstraints();
  }

  setMaxFramerate(fps?: number): void {
    this.maxFramerate = fps;
    void this.applyBitrateConstraints();
  }

  private cleanup(): void {
    this.stopStatsTimer();

    if (this.iceGatheringTimer !== null) {
      this.timers.clearTimeout(this.iceGatheringTimer);
      this.iceGatheringTimer = null;
    }

    if (this.abortController) {
      try {
        this.abortController.abort();
      } catch {
        // Ignore abort errors
      }
      this.abortController = null;
    }

    if (this.pc) {
      try {
        this.pc.getTransceivers().forEach((t) => {
          try {
            t.stop();
          } catch {
            // Ignore stop errors
          }
        });
      } catch {
        // Ignore transceiver errors
      }

      try {
        this.pc.close();
      } catch {
        // Ignore close errors
      }
      this.pc = null;
    }

    this.videoSender = null;
    this.audioSender = null;
    this.videoTransceiver = null;
    this.audioTransceiver = null;
  }

  async disconnect(): Promise<void> {
    if (this.resourceUrl) {
      try {
        await this.fetch(this.resourceUrl, { method: "DELETE" });
      } catch {
        // Ignore delete errors
      }
    }

    this.cleanup();
    this.resourceUrl = null;
  }

  getPeerConnection(): RTCPeerConnection | null {
    return this.pc;
  }

  restartIce(): void {
    if (this.pc) {
      try {
        this.pc.restartIce();
      } catch {
        // ICE restart not supported
      }
    }
  }

  isConnected(): boolean {
    return this.pc !== null && this.pc.connectionState === "connected";
  }

  private getUrlWithCachedRedirect(): string {
    const originalUrl = new URL(this.url);
    const playbackIdMatch = originalUrl.pathname.match(PLAYBACK_ID_PATTERN);
    const playbackId = playbackIdMatch?.[2];

    const cachedTemplate = this.redirectCache.get(this.url);
    if (!cachedTemplate || !playbackId) {
      return this.url;
    }

    const redirectedUrl = new URL(cachedTemplate);
    redirectedUrl.pathname = cachedTemplate.pathname.replace(
      PLAYBACK_ID_PLACEHOLDER,
      playbackId,
    );
    return redirectedUrl.toString();
  }

  private cacheRedirectIfNeeded(requestUrl: string, responseUrl: string): void {
    if (requestUrl === responseUrl) return;

    try {
      const actualRedirect = new URL(responseUrl);
      const template = new URL(actualRedirect);
      template.pathname = template.pathname.replace(
        PLAYBACK_ID_PATTERN,
        `$1${PLAYBACK_ID_PLACEHOLDER}`,
      );
      this.redirectCache.set(this.url, template);
    } catch {
      // Invalid URL, skip caching
    }
  }
}

export class WHEPClient {
  private readonly url: string;
  private readonly iceServers: RTCIceServer[];
  private readonly onTrack?: (event: RTCTrackEvent) => void;
  private readonly pcFactory: PeerConnectionFactory;
  private readonly fetch: FetchFn;
  private readonly timers: TimerProvider;
  private readonly skipIceGathering: boolean;
  private readonly maxRetries: number;
  private readonly retryDelayMs: number;

  private pc: RTCPeerConnection | null = null;
  private resourceUrl: string | null = null;
  private abortController: AbortController | null = null;
  private iceGatheringTimer: number | null = null;
  private retryCount = 0;
  private retryTimer: number | null = null;

  constructor(config: WHEPClientConfig) {
    this.url = config.url;
    this.iceServers = config.iceServers ?? DEFAULT_ICE_SERVERS;
    this.onTrack = config.onTrack;
    this.pcFactory = defaultPeerConnectionFactory;
    this.fetch = defaultFetch;
    this.timers = defaultTimerProvider;
    this.skipIceGathering = config.skipIceGathering ?? true;
    this.maxRetries = config.maxRetries ?? 30;
    this.retryDelayMs = config.retryDelayMs ?? 100;
  }

  async connect(): Promise<void> {
    this.cleanup();

    this.pc = this.pcFactory.create({
      iceServers: this.iceServers,
      iceCandidatePoolSize: 10,
    });

    if (this.onTrack) {
      this.pc.ontrack = this.onTrack;
    }

    this.pc.addTransceiver("video", { direction: "recvonly" });
    this.pc.addTransceiver("audio", { direction: "recvonly" });

    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);

    if (!this.skipIceGathering) {
      await this.waitForIceGathering();
    }

    this.abortController = new AbortController();
    const timeoutId = this.timers.setTimeout(
      () => this.abortController?.abort(),
      10000,
    );

    try {
      const response = await this.fetch(this.url, {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: this.pc.localDescription!.sdp,
        signal: this.abortController.signal,
      });

      this.timers.clearTimeout(timeoutId);

      if (!response.ok) {
        if (this.retryCount < this.maxRetries) {
          this.retryCount++;
          this.scheduleRetry();
          return;
        }
        throw new ConnectionError(
          `WHEP connection failed: ${response.status} ${response.statusText}`,
        );
      }

      const location = response.headers.get("location");
      if (location) {
        this.resourceUrl = new URL(location, this.url).toString();
      }

      const answerSdp = await response.text();
      await this.pc.setRemoteDescription({ type: "answer", sdp: answerSdp });

      this.retryCount = 0;
    } catch (error) {
      this.timers.clearTimeout(timeoutId);

      if (this.retryCount < this.maxRetries) {
        this.retryCount++;
        this.scheduleRetry();
        return;
      }

      if (error instanceof ConnectionError) {
        throw error;
      }
      if (error instanceof Error && error.name === "AbortError") {
        throw new NetworkError("Connection timeout");
      }
      throw new NetworkError("Failed to establish WHEP connection", error);
    }
  }

  private scheduleRetry(): void {
    this.retryTimer = this.timers.setTimeout(() => {
      this.retryTimer = null;
      void this.connect();
    }, this.retryDelayMs);
  }

  private waitForIceGathering(): Promise<void> {
    return new Promise((resolve) => {
      if (!this.pc) {
        resolve();
        return;
      }

      if (this.pc.iceGatheringState === "complete") {
        resolve();
        return;
      }

      const onStateChange = () => {
        if (this.pc?.iceGatheringState === "complete") {
          this.pc.removeEventListener("icegatheringstatechange", onStateChange);
          if (this.iceGatheringTimer !== null) {
            this.timers.clearTimeout(this.iceGatheringTimer);
            this.iceGatheringTimer = null;
          }
          resolve();
        }
      };

      this.pc.addEventListener("icegatheringstatechange", onStateChange);

      this.iceGatheringTimer = this.timers.setTimeout(() => {
        this.pc?.removeEventListener("icegatheringstatechange", onStateChange);
        this.iceGatheringTimer = null;
        resolve();
      }, 1000);
    });
  }

  private cleanup(): void {
    if (this.retryTimer !== null) {
      this.timers.clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }

    if (this.iceGatheringTimer !== null) {
      this.timers.clearTimeout(this.iceGatheringTimer);
      this.iceGatheringTimer = null;
    }

    if (this.abortController) {
      try {
        this.abortController.abort();
      } catch {
        // Ignore abort errors
      }
      this.abortController = null;
    }

    if (this.pc) {
      try {
        this.pc.getTransceivers().forEach((t) => {
          try {
            t.stop();
          } catch {
            // Ignore stop errors
          }
        });
      } catch {
        // Ignore transceiver errors
      }

      try {
        this.pc.close();
      } catch {
        // Ignore close errors
      }
      this.pc = null;
    }
  }

  async disconnect(): Promise<void> {
    if (this.resourceUrl) {
      try {
        await this.fetch(this.resourceUrl, { method: "DELETE" });
      } catch {
        // Ignore delete errors
      }
    }

    this.cleanup();
    this.resourceUrl = null;
    this.retryCount = 0;
  }

  getPeerConnection(): RTCPeerConnection | null {
    return this.pc;
  }

  isConnected(): boolean {
    return this.pc !== null && this.pc.connectionState === "connected";
  }
}

export interface RelayManagerConfig {
  inputCanvas: HTMLCanvasElement;
  outputVideo: HTMLVideoElement;
  onVideoStarted?: () => void;
  onLog?: (message: string) => void;
  frameRate?: number;
}

export class RelayManager {
  private readonly canvas: HTMLCanvasElement;
  private readonly video: HTMLVideoElement;
  private readonly onVideoStarted?: () => void;
  private readonly log: (message: string) => void;
  private readonly frameRate: number;

  private whipClient: WHIPClient | null = null;
  private whepClient: WHEPClient | null = null;
  private canvasStream: MediaStream | null = null;
  private videoStarted = false;
  private pollTimer: number | null = null;

  constructor(config: RelayManagerConfig) {
    this.canvas = config.inputCanvas;
    this.video = config.outputVideo;
    this.onVideoStarted = config.onVideoStarted;
    this.log = config.onLog ?? console.log;
    this.frameRate = config.frameRate ?? 30;

    this.video.onplaying = () => this.handleVideoPlaying();
  }

  warmup(): void {
    console.log("[Relay] Warming up WebRTC...");
    this.canvasStream = this.canvas.captureStream(this.frameRate);
    console.log("[Relay] WebRTC warmed up");
  }

  async start(): Promise<void> {
    this.pollForStatus();
  }

  private pollForStatus(): void {
    const checkStatus = async () => {
      try {
        const res = await fetch(window.location.origin + "/status");
        if (!res.ok) {
          this.scheduleStatusPoll();
          return;
        }
        const data = await res.json();
        if (data.state === "STREAMING" && data.whip_url) {
          console.log("[Relay] Stream ready, starting WHIP");
          await this.startWHIP();
        } else {
          this.scheduleStatusPoll();
        }
      } catch {
        this.scheduleStatusPoll();
      }
    };

    void checkStatus();
  }

  private scheduleStatusPoll(): void {
    this.pollTimer = defaultTimerProvider.setTimeout(() => {
      this.pollTimer = null;
      this.pollForStatus();
    }, 100);
  }

  private async startWHIP(): Promise<void> {
    this.log("Connecting to server...");

    try {
      if (!this.canvasStream) {
        this.canvasStream = this.canvas.captureStream(this.frameRate);
      }

      const videoTrack = this.canvasStream.getVideoTracks()[0];
      if (!videoTrack) {
        throw new Error("No video track from canvas");
      }

      this.whipClient = new WHIPClient({
        url: WHIP_PROXY,
        skipIceGathering: true,
      });

      const pc = await this.setupWHIPWithPolling(videoTrack);

      pc.oniceconnectionstatechange = () => {
        console.log("[Relay] WHIP ICE:", pc.iceConnectionState);
        if (pc.iceConnectionState === "connected") {
          this.log("Connected, waiting for AI...");
        } else if (pc.iceConnectionState === "failed") {
          this.log("Connection failed");
        }
      };

      await this.startWHEP();
    } catch (e) {
      console.error("[Relay] WHIP error:", e);
      this.log("Connection error");
    }
  }

  private async setupWHIPWithPolling(
    videoTrack: MediaStreamTrack,
  ): Promise<RTCPeerConnection> {
    const pc = defaultPeerConnectionFactory.create({
      iceServers: DEFAULT_ICE_SERVERS,
      iceCandidatePoolSize: 10,
    });

    const transceiver = pc.addTransceiver(videoTrack, {
      direction: "sendonly",
    });
    this.setH264Preference(transceiver);

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    const response = await fetch(WHIP_PROXY, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription!.sdp,
    });

    if (response.status === 202) {
      const { id } = await response.json();
      await this.pollWHIPResult(id, pc);
    } else if (response.ok) {
      const answerSdp = await response.text();
      console.log("[Relay] Got WHIP answer");
      await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
    } else {
      throw new Error("WHIP proxy error: " + response.status);
    }

    return pc;
  }

  private async pollWHIPResult(
    id: string,
    pc: RTCPeerConnection,
  ): Promise<void> {
    const poll = async (): Promise<void> => {
      const response = await fetch(SDP_ORIGIN + "/whip/result/" + id);
      if (response.status === 202) {
        await new Promise((r) => setTimeout(r, 100));
        return poll();
      }
      if (!response.ok) {
        throw new Error("WHIP proxy error: " + response.status);
      }
      const answerSdp = await response.text();
      console.log("[Relay] Got WHIP answer");
      await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
    };

    await poll();
  }

  private setH264Preference(transceiver: RTCRtpTransceiver): void {
    if (!transceiver.setCodecPreferences) return;
    try {
      const caps = RTCRtpSender.getCapabilities("video");
      if (!caps?.codecs?.length) return;
      const h264 = caps.codecs.filter((c) =>
        c.mimeType.toLowerCase().includes("h264"),
      );
      if (h264.length) transceiver.setCodecPreferences(h264);
    } catch {
      // Ignore
    }
  }

  private async startWHEP(): Promise<void> {
    this.log("Waiting for AI stream...");

    try {
      this.whepClient = new WHEPClient({
        url: WHEP_PROXY,
        skipIceGathering: true,
        maxRetries: 30,
        retryDelayMs: 100,
        onTrack: (e) => {
          console.log("[Relay] WHEP track:", e.track.kind);
          if (e.track.kind === "video") {
            this.video.srcObject = e.streams[0] || new MediaStream([e.track]);
            if (!this.videoStarted) {
              this.log("Starting stream...");
            }
          }
        },
      });

      await this.setupWHEPWithPolling();
    } catch (e) {
      console.error("[Relay] WHEP error:", e);
    }
  }

  private async setupWHEPWithPolling(): Promise<void> {
    const pc = defaultPeerConnectionFactory.create({
      iceServers: DEFAULT_ICE_SERVERS,
      iceCandidatePoolSize: 10,
    });

    pc.ontrack = (e) => {
      console.log("[Relay] WHEP track:", e.track.kind);
      if (e.track.kind === "video") {
        this.video.srcObject = e.streams[0] || new MediaStream([e.track]);
        if (!this.videoStarted) {
          this.log("Starting stream...");
        }
      }
    };

    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("audio", { direction: "recvonly" });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    let retries = 0;
    const maxRetries = 30;

    const attemptConnect = async (): Promise<void> => {
      const response = await fetch(WHEP_PROXY, {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: pc.localDescription!.sdp,
      });

      if (response.status === 202) {
        const { id } = await response.json();
        await this.pollWHEPResult(id, pc);
        return;
      }

      if (!response.ok) {
        if (retries < maxRetries) {
          retries++;
          await new Promise((r) => setTimeout(r, 100));
          return attemptConnect();
        }
        throw new Error("WHEP failed after retries");
      }

      const answerSdp = await response.text();
      await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
    };

    await attemptConnect();
  }

  private async pollWHEPResult(
    id: string,
    pc: RTCPeerConnection,
  ): Promise<void> {
    let retries = 0;
    const maxRetries = 30;

    const poll = async (): Promise<void> => {
      const response = await fetch(SDP_ORIGIN + "/whep/result/" + id);
      if (response.status === 202) {
        await new Promise((r) => setTimeout(r, 0));
        return poll();
      }
      if (!response.ok) {
        if (retries < maxRetries) {
          retries++;
          await new Promise((r) => setTimeout(r, 100));
          return this.setupWHEPWithPolling();
        }
        return;
      }
      const answerSdp = await response.text();
      await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
    };

    await poll();
  }

  private handleVideoPlaying(): void {
    if (!this.videoStarted) {
      this.videoStarted = true;
      console.log("[Relay] Video playing");
      this.onVideoStarted?.();
    }
  }

  async stop(): Promise<void> {
    if (this.pollTimer !== null) {
      defaultTimerProvider.clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }

    if (this.whipClient) {
      await this.whipClient.disconnect();
      this.whipClient = null;
    }

    if (this.whepClient) {
      await this.whepClient.disconnect();
      this.whepClient = null;
    }

    if (this.canvasStream) {
      this.canvasStream.getTracks().forEach((t) => t.stop());
      this.canvasStream = null;
    }

    this.videoStarted = false;
  }
}
