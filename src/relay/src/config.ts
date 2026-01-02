export const SDP_PORT = "{{SDP_PORT}}";

export const ORIGIN = window.location.origin;
export const SDP_ORIGIN = `${window.location.protocol}//${window.location.hostname}:${SDP_PORT}`;
export const WS_URL = ORIGIN.replace("http", "ws") + "/ws";
export const WHIP_PROXY = SDP_ORIGIN + "/whip";
export const WHEP_PROXY = SDP_ORIGIN + "/whep";
