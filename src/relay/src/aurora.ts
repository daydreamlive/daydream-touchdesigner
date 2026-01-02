const AURORA_WORKER_CODE = `
let canvas, ctx;
let t = Math.random() * 100;
let running = true;
const DT = 0.016;

const blobs = [
    { cx: 256, cy: 256, rx: 120, ry: 80, sx: 0.7, sy: 0.5, baseR: 200, hue: 260, phase: 0 },
    { cx: 256, cy: 256, rx: 100, ry: 120, sx: 0.5, sy: 0.8, baseR: 180, hue: 320, phase: 2 },
    { cx: 256, cy: 256, rx: 140, ry: 100, sx: 0.6, sy: 0.4, baseR: 160, hue: 220, phase: 4 },
    { cx: 256, cy: 256, rx: 80, ry: 140, sx: 0.4, sy: 0.6, baseR: 140, hue: 290, phase: 1 },
    { cx: 256, cy: 256, rx: 60, ry: 60, sx: 1.2, sy: 0.9, baseR: 80, hue: 200, phase: 3 },
    { cx: 256, cy: 256, rx: 50, ry: 70, sx: 0.9, sy: 1.1, baseR: 70, hue: 340, phase: 5 }
];

function drawFrame() {
    if (!running || !ctx) return;
    const start = performance.now();
    t += DT;
    
    ctx.fillStyle = 'rgba(0, 0, 0, 0.08)';
    ctx.fillRect(0, 0, 512, 512);
    
    for (const blob of blobs) {
        const x = blob.cx + Math.sin(t * blob.sx + blob.phase) * blob.rx;
        const y = blob.cy + Math.cos(t * blob.sy + blob.phase * 0.7) * blob.ry;
        const r = blob.baseR + Math.sin(t * 2 + blob.phase) * 25;
        const hue = (blob.hue + t * 12) % 360;
        
        const gradient = ctx.createRadialGradient(x, y, 0, x, y, r);
        gradient.addColorStop(0, 'hsla(' + hue + ', 75%, 60%, 0.18)');
        gradient.addColorStop(0.15, 'hsla(' + hue + ', 72%, 57%, 0.14)');
        gradient.addColorStop(0.3, 'hsla(' + hue + ', 70%, 54%, 0.10)');
        gradient.addColorStop(0.5, 'hsla(' + hue + ', 67%, 50%, 0.06)');
        gradient.addColorStop(0.7, 'hsla(' + hue + ', 63%, 46%, 0.03)');
        gradient.addColorStop(0.85, 'hsla(' + hue + ', 58%, 43%, 0.01)');
        gradient.addColorStop(1, 'hsla(' + hue + ', 55%, 40%, 0)');
        
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, 512, 512);
    }
    
    const elapsed = performance.now() - start;
    setTimeout(drawFrame, Math.max(0, 16 - elapsed));
}

self.onmessage = (e) => {
    if (e.data.type === 'init') {
        canvas = e.data.canvas;
        ctx = canvas.getContext('2d');
        ctx.fillStyle = '#000';
        ctx.fillRect(0, 0, 512, 512);
        drawFrame();
    } else if (e.data.type === 'stop') {
        running = false;
    }
};
`

let auroraWorker: Worker | null = null

export function startAuroraWorker(canvas: HTMLCanvasElement): void {
  const offscreen = canvas.transferControlToOffscreen()
  const blob = new Blob([AURORA_WORKER_CODE], { type: 'application/javascript' })
  auroraWorker = new Worker(URL.createObjectURL(blob))
  auroraWorker.postMessage({ type: 'init', canvas: offscreen }, [offscreen])
  console.log('[Relay] Aurora worker started')
}

export function stopAuroraWorker(): void {
  if (auroraWorker) {
    auroraWorker.postMessage({ type: 'stop' })
    auroraWorker.terminate()
    auroraWorker = null
  }
}

