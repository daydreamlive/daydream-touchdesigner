export interface PeerConnectionFactory {
  create(config: RTCConfiguration): RTCPeerConnection;
}

export type FetchFn = typeof fetch;

export interface TimerProvider {
  setTimeout(callback: () => void, ms: number): number;
  clearTimeout(id: number): void;
  setInterval(callback: () => void, ms: number): number;
  clearInterval(id: number): void;
}

export const defaultPeerConnectionFactory: PeerConnectionFactory = {
  create: (config) => new RTCPeerConnection(config),
};

export const defaultFetch: FetchFn = fetch.bind(globalThis);

export const defaultTimerProvider: TimerProvider = {
  setTimeout: (cb, ms) => window.setTimeout(cb, ms),
  clearTimeout: (id) => window.clearTimeout(id),
  setInterval: (cb, ms) => window.setInterval(cb, ms),
  clearInterval: (id) => window.clearInterval(id),
};
