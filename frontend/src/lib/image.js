// src/lib/image.js — client-side downscale for an image the user is about
// to send to SARANA (see Controls.jsx's image picker / App.jsx's
// handleSendImage()). This is an OPTIMIZATION only, not a correctness
// requirement: the backend's own compression (actions/screen_processor.py's
// _compress(), reused as-is by main.py's
// _process_dashboard_image_commands()) is authoritative and re-compresses
// regardless of what arrives — so a browser where this fails/skips still
// works correctly, it just uploads a larger payload. Standard Canvas API
// only (no new dependency); works on desktop/Android/iOS Safari alike.

const MAX_DIM      = 1600;  // a little roomier than the backend's own
                             // 1280x720 cap, so small text in a
                             // photographed document/menu/sign stays
                             // legible before the server's own final resize
const JPEG_QUALITY = 0.85;

/** Downscale + re-encode `file` as JPEG. Resolves {base64, mimeType} where
 * base64 has no "data:...;base64," prefix. Always outputs image/jpeg —
 * simplest reliable path across browsers/formats, and the backend accepts
 * it directly. Rejects (never resolves) on any decode/canvas failure —
 * callers should fall back to readFileAsBase64() below. */
export function prepareImageForUpload(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();

    img.onload = () => {
      try {
        const { width, height } = img;
        const scale = Math.min(1, MAX_DIM / Math.max(width, height));
        const w = Math.max(1, Math.round(width * scale));
        const h = Math.max(1, Math.round(height * scale));

        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          URL.revokeObjectURL(url);
          reject(new Error("Canvas 2D context unavailable."));
          return;
        }
        ctx.drawImage(img, 0, 0, w, h);

        canvas.toBlob(
          (blob) => {
            URL.revokeObjectURL(url);
            if (!blob) {
              reject(new Error("Could not encode image."));
              return;
            }
            const reader = new FileReader();
            reader.onload = () => {
              const dataUrl = String(reader.result || "");
              const base64 = dataUrl.split(",")[1] || "";
              if (!base64) {
                reject(new Error("Could not read encoded image."));
                return;
              }
              resolve({ base64, mimeType: "image/jpeg" });
            };
            reader.onerror = () => reject(new Error("Could not read encoded image."));
            reader.readAsDataURL(blob);
          },
          "image/jpeg",
          JPEG_QUALITY,
        );
      } catch (e) {
        URL.revokeObjectURL(url);
        reject(e);
      }
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Could not load image."));
    };
    img.src = url;
  });
}

/** Fallback for when prepareImageForUpload() fails (e.g. a format the
 * browser's <img>/canvas pipeline can't decode inline) — sends the file's
 * own raw bytes and its own reported MIME type unchanged. The backend
 * validates/decodes independently either way (see dashboard/server.py's
 * /ws "image_command" handling), so this is safe to attempt; it may simply
 * be rejected server-side as an unsupported type if the file truly isn't a
 * usable image. */
export function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || "");
      const base64 = dataUrl.split(",")[1] || "";
      if (!base64) {
        reject(new Error("Could not read image file."));
        return;
      }
      resolve({ base64, mimeType: file.type || "image/jpeg" });
    };
    reader.onerror = () => reject(new Error("Could not read image file."));
    reader.readAsDataURL(file);
  });
}
