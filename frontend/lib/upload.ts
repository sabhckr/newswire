import { API_BASE, api, ApiError, currentCsrfToken } from "@/lib/api";
import type { VideoOut } from "@/lib/types";

export const VIDEO_TYPES = ["video/mp4", "video/quicktime"];

type UploadState = { upload_id: string; size: number; received: number; chunk_size: number };
export type UploadProgress = { fraction: number; stage: "uploading" | "retrying" | "checking" };

const MAX_RETRIES = 6;

const aborted = () => new ApiError(0, "aborted", "Upload cancelled");

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(aborted());
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        reject(aborted());
      },
      { once: true },
    );
  });
}

/** PUT one piece with XHR, which (unlike fetch) reports upload progress. */
function sendPiece(id: string, offset: number, piece: Blob, onLoaded: (bytes: number) => void, signal?: AbortSignal): Promise<UploadState> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(aborted());
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `${API_BASE}/videos/uploads/${id}?offset=${offset}`);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    const token = currentCsrfToken();
    if (token) xhr.setRequestHeader("X-CSRF-Token", token);
    xhr.upload.onprogress = (e) => onLoaded(e.loaded);
    xhr.onload = () => {
      let payload: unknown = null;
      try {
        payload = JSON.parse(xhr.responseText);
      } catch {
        payload = null;
      }
      if (xhr.status >= 200 && xhr.status < 300 && payload) return resolve(payload as UploadState);
      const err = (payload as { error?: { code: string; message: string; details?: unknown } } | null)?.error;
      if (xhr.status === 401) window.dispatchEvent(new CustomEvent("newswire:unauthorized"));
      reject(new ApiError(xhr.status, err?.code ?? "http_error", err?.message ?? `Upload failed (HTTP ${xhr.status})`, err?.details));
    };
    xhr.onerror = () => reject(new ApiError(0, "network_error", "The connection dropped during the upload."));
    xhr.ontimeout = xhr.onerror;
    xhr.onabort = () => reject(aborted());
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(piece);
  });
}

/** Worth trying again: the network, a proxy hiccup, or the server being restarted. */
const transient = (e: unknown) => e instanceof ApiError && (e.status === 0 || e.status === 408 || e.status === 429 || e.status >= 500);

/**
 * Upload a video in pieces (8 MB each, as the server says). A dropped connection is retried and the
 * upload continues where it stopped, so large files work on slow or unstable connections and behind
 * proxies that limit request size. Rejects with ApiError ("aborted" when cancelled).
 */
export async function uploadVideo(file: File, onProgress: (p: UploadProgress) => void, signal?: AbortSignal): Promise<VideoOut> {
  if (signal?.aborted) throw aborted();
  let start: UploadState;
  try {
    start = await api<UploadState>("/videos/uploads", { body: { filename: file.name.slice(0, 255), size: file.size }, signal });
  } catch (e) {
    throw (e as Error).name === "AbortError" ? aborted() : e;
  }
  const id = start.upload_id;
  let received = start.received;
  let failures = 0;
  const report = (stage: UploadProgress["stage"], bytes = received) => onProgress({ fraction: Math.min(1, bytes / file.size), stage });
  try {
    report("uploading");
    while (received < file.size) {
      const end = Math.min(file.size, received + start.chunk_size);
      const offset = received;
      try {
        const r = await sendPiece(id, offset, file.slice(offset, end), (loaded) => report("uploading", offset + loaded), signal);
        received = r.received;
        failures = 0;
        report("uploading");
      } catch (e) {
        if (e instanceof ApiError && e.code === "aborted") throw e;
        const details = e instanceof ApiError ? (e.details as { received?: unknown } | undefined) : undefined;
        if (e instanceof ApiError && e.status === 409 && typeof details?.received === "number") {
          received = details.received; // the server has a different amount: continue from there
          continue;
        }
        if (!transient(e) || ++failures > MAX_RETRIES) {
          if (transient(e)) {
            throw new ApiError(0, "network_error", "The connection kept dropping, so the upload stopped. Check the internet connection and choose the file again.");
          }
          throw e;
        }
        report("retrying");
        await wait(Math.min(30_000, 1000 * 2 ** (failures - 1)), signal);
        try {
          // A piece cut off part way is kept on the server; ask how much arrived.
          received = (await api<UploadState>(`/videos/uploads/${id}`, { signal })).received;
        } catch (inner) {
          if (inner instanceof ApiError && inner.status === 404) throw inner;
          if ((inner as Error).name === "AbortError") throw aborted();
          // still offline: the next attempt will find out
        }
      }
    }
    report("checking", file.size);
    return await api<VideoOut>(`/videos/uploads/${id}/complete`, { method: "POST", signal });
  } catch (e) {
    const cancelled = (e instanceof ApiError && e.code === "aborted") || (e as Error).name === "AbortError";
    if (cancelled || !transient(e)) void api(`/videos/uploads/${id}`, { method: "DELETE" }).catch(() => undefined);
    throw cancelled ? aborted() : e;
  }
}

export function formatSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined) return "unknown length";
  const total = Math.round(s);
  if (total < 60) return `${total} s`;
  const m = Math.floor(total / 60);
  const sec = total % 60;
  if (m < 60) return `${m}:${String(sec).padStart(2, "0")}`;
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}
