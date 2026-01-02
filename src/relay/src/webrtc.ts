import { ORIGIN, SDP_ORIGIN, WHIP_PROXY, WHEP_PROXY } from './config'

let whipPC: RTCPeerConnection | null = null
let whepPC: RTCPeerConnection | null = null
let canvasStream: MediaStream | null = null
let videoTrack: MediaStreamTrack | null = null
let whipStarted = false
let videoStarted = false
let whepRetries = 0

let outputVideo: HTMLVideoElement
let inputCanvas: HTMLCanvasElement
let onVideoStarted: () => void

const ICE_SERVERS: RTCIceServer[] = [
  { urls: 'stun:stun.l.google.com:19302' },
  { urls: 'stun:stun1.l.google.com:19302' },
]

export function initWebRTC(
  video: HTMLVideoElement,
  canvas: HTMLCanvasElement,
  onStarted: () => void
): void {
  outputVideo = video
  inputCanvas = canvas
  onVideoStarted = onStarted
}

function setH264Preference(transceiver: RTCRtpTransceiver): void {
  if (!transceiver.setCodecPreferences) return
  try {
    const caps = RTCRtpSender.getCapabilities('video')
    if (!caps?.codecs?.length) return
    const h264 = caps.codecs.filter((c) => c.mimeType.toLowerCase().includes('h264'))
    if (h264.length) transceiver.setCodecPreferences(h264)
  } catch {
    /* ignore */
  }
}

export function warmupWebRTC(): void {
  console.log('[Relay] Warming up WebRTC...')
  canvasStream = inputCanvas.captureStream(30)
  videoTrack = canvasStream.getVideoTracks()[0]

  whipPC = new RTCPeerConnection({
    iceServers: ICE_SERVERS,
    iceCandidatePoolSize: 10,
  })

  const transceiver = whipPC.addTransceiver(videoTrack, { direction: 'sendonly' })
  setH264Preference(transceiver)
  console.log('[Relay] WebRTC warmed up')
}

export async function pollStatus(log: (msg: string) => void): Promise<void> {
  try {
    const res = await fetch(ORIGIN + '/status')
    if (!res.ok) {
      setTimeout(() => pollStatus(log), 100)
      return
    }
    const data = await res.json()
    if (data.state === 'STREAMING' && data.whip_url && !whipStarted) {
      whipStarted = true
      console.log('[Relay] Stream ready, starting WHIP')
      startWHIP(log)
    } else if (!whipStarted) {
      setTimeout(() => pollStatus(log), 100)
    }
  } catch {
    setTimeout(() => pollStatus(log), 100)
  }
}

async function startWHIP(log: (msg: string) => void): Promise<void> {
  log('Connecting to server...')
  try {
    if (!videoTrack) {
      canvasStream = inputCanvas.captureStream(30)
      videoTrack = canvasStream.getVideoTracks()[0]
    }
    if (!videoTrack) throw new Error('No video track from canvas')

    if (!whipPC || whipPC.signalingState === 'closed') {
      whipPC = new RTCPeerConnection({
        iceServers: ICE_SERVERS,
        iceCandidatePoolSize: 10,
      })
      const transceiver = whipPC.addTransceiver(videoTrack, { direction: 'sendonly' })
      setH264Preference(transceiver)
    }

    whipPC.oniceconnectionstatechange = () => {
      console.log('[Relay] WHIP ICE:', whipPC!.iceConnectionState)
      if (whipPC!.iceConnectionState === 'connected') log('Connected, waiting for AI...')
      else if (whipPC!.iceConnectionState === 'failed') log('Connection failed')
    }

    const offer = await whipPC.createOffer()
    await whipPC.setLocalDescription(offer)

    await new Promise<void>((r) => {
      if (whipPC!.iceGatheringState === 'complete') r()
      else {
        whipPC!.onicegatheringstatechange = () => {
          if (whipPC!.iceGatheringState === 'complete') r()
        }
        setTimeout(r, 2000)
      }
    })

    console.log('[Relay] Sending WHIP offer via proxy')
    const response = await fetch(WHIP_PROXY, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp' },
      body: whipPC.localDescription!.sdp,
    })

    if (response.status === 202) {
      const { id } = await response.json()
      pollWhipResult(id, log)
      return
    }
    if (!response.ok) throw new Error('WHIP proxy error: ' + response.status)

    const answerSdp = await response.text()
    console.log('[Relay] Got WHIP answer')
    await whipPC.setRemoteDescription({ type: 'answer', sdp: answerSdp })
    startWHEP(log)
  } catch (e) {
    console.error('[Relay] WHIP error:', e)
    log('Connection error')
  }
}

async function pollWhipResult(id: string, log: (msg: string) => void): Promise<void> {
  try {
    const response = await fetch(SDP_ORIGIN + '/whip/result/' + id)
    if (response.status === 202) {
      setTimeout(() => pollWhipResult(id, log), 100)
      return
    }
    if (!response.ok) throw new Error('WHIP proxy error: ' + response.status)
    const answerSdp = await response.text()
    console.log('[Relay] Got WHIP answer')
    await whipPC!.setRemoteDescription({ type: 'answer', sdp: answerSdp })
    startWHEP(log)
  } catch (e) {
    console.error('[Relay] WHIP poll error:', e)
    log('Connection error')
  }
}

async function startWHEP(log: (msg: string) => void): Promise<void> {
  log('Waiting for AI stream...')
  try {
    if (whepPC) {
      try {
        whepPC.close()
      } catch {
        /* ignore */
      }
    }
    whepPC = new RTCPeerConnection({
      iceServers: ICE_SERVERS,
      iceCandidatePoolSize: 10,
    })

    whepPC.ontrack = (e) => {
      console.log('[Relay] WHEP track:', e.track.kind)
      if (e.track.kind === 'video') {
        outputVideo.srcObject = e.streams[0] || new MediaStream([e.track])
        if (!videoStarted) log('Starting stream...')
      }
    }

    whepPC.addTransceiver('video', { direction: 'recvonly' })
    whepPC.addTransceiver('audio', { direction: 'recvonly' })

    const offer = await whepPC.createOffer()
    await whepPC.setLocalDescription(offer)

    await new Promise<void>((r) => {
      if (whepPC!.iceGatheringState === 'complete') r()
      else {
        whepPC!.onicegatheringstatechange = () => {
          if (whepPC!.iceGatheringState === 'complete') r()
        }
        setTimeout(r, 2000)
      }
    })

    const response = await fetch(WHEP_PROXY, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp' },
      body: whepPC.localDescription!.sdp,
    })

    if (response.status === 202) {
      const { id } = await response.json()
      pollWhepResult(id, log)
      return
    }
    if (!response.ok) {
      if (whepRetries < 30) {
        whepRetries++
        setTimeout(() => startWHEP(log), 100)
        return
      }
      throw new Error('WHEP failed')
    }

    const answerSdp = await response.text()
    await whepPC.setRemoteDescription({ type: 'answer', sdp: answerSdp })
    whepRetries = 0
  } catch (e) {
    console.error('[Relay] WHEP error:', e)
    if (whepRetries < 30) {
      whepRetries++
      setTimeout(() => startWHEP(log), 100)
    }
  }
}

async function pollWhepResult(id: string, log: (msg: string) => void): Promise<void> {
  try {
    const response = await fetch(SDP_ORIGIN + '/whep/result/' + id)
    if (response.status === 202) {
      setTimeout(() => pollWhepResult(id, log), 0)
      return
    }
    if (!response.ok) {
      if (whepRetries < 30) {
        whepRetries++
        setTimeout(() => startWHEP(log), 100)
      }
      return
    }
    const answerSdp = await response.text()
    await whepPC!.setRemoteDescription({ type: 'answer', sdp: answerSdp })
    whepRetries = 0
  } catch (e) {
    console.error('[Relay] WHEP poll error:', e)
    if (whepRetries < 30) {
      whepRetries++
      setTimeout(() => startWHEP(log), 100)
    }
  }
}

export function onVideoPlaying(): void {
  if (!videoStarted) {
    videoStarted = true
    console.log('[Relay] Video playing')
    onVideoStarted()
  }
}

