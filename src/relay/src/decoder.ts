type CanvasContext = ImageBitmapRenderingContext | CanvasRenderingContext2D;

let canvas: HTMLCanvasElement;
let ctx: CanvasContext;
let useBitmapRenderer: boolean;
let latestFrame: ArrayBuffer | null = null;
let pendingDecode: Promise<void> | null = null;

export function initDecoder(canvasEl: HTMLCanvasElement): void {
  canvas = canvasEl;
  useBitmapRenderer = !!canvas.getContext("bitmaprenderer");
  ctx = useBitmapRenderer
    ? canvas.getContext("bitmaprenderer")!
    : canvas.getContext("2d")!;

  if (!useBitmapRenderer) {
    const ctx2d = ctx as CanvasRenderingContext2D;
    ctx2d.fillStyle = "#000";
    ctx2d.fillRect(0, 0, 512, 512);
  }
}

export function queueFrame(frame: ArrayBuffer): void {
  latestFrame = frame;
  decodeLoop();
}

function decodeLoop(): void {
  if (!latestFrame || pendingDecode) return;

  const frame = latestFrame;
  latestFrame = null;

  pendingDecode = createImageBitmap(new Blob([frame], { type: "image/jpeg" }))
    .then((bitmap) => {
      if (useBitmapRenderer) {
        (ctx as ImageBitmapRenderingContext).transferFromImageBitmap(bitmap);
      } else {
        (ctx as CanvasRenderingContext2D).drawImage(bitmap, 0, 0, 512, 512);
        bitmap.close();
      }
    })
    .catch(() => {})
    .finally(() => {
      pendingDecode = null;
      if (latestFrame) decodeLoop();
    });
}
