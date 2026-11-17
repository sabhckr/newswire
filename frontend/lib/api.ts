/**
 * Thin fetch wrapper for the NEWSWIRE API. The browser talks only to this origin; Next.js proxies
 * /api/* to FastAPI. Session auth is an HttpOnly cookie; unsafe methods carry the CSRF token.
 */

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

function readCsrfCookie(): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(/(?:^|;\s*)nw_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

export type Query = Record<string, string | number | boolean | null | undefined | (string | number)[]>;

export function qs(query?: Query): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

type Options = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Query;
  signal?: AbortSignal;
  form?: FormData;
};

export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const method = opts.method ?? (opts.body !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  if (method !== "GET") {
    const token = csrfToken ?? readCsrfCookie();
    if (token) headers["X-CSRF-Token"] = token;
  }
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}${qs(opts.query)}`, {
      method,
      headers,
      body,
      credentials: "same-origin",
      signal: opts.signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network_error", "Cannot reach the NEWSWIRE server. Check that the API is running.");
  }
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  const payload = type.includes("application/json") ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    const err = (payload as { error?: { code: string; message: string; details?: unknown } } | null)?.error;
    const e = new ApiError(res.status, err?.code ?? "http_error", err?.message ?? `Request failed (HTTP ${res.status})`, err?.details);
    if (res.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("newswire:unauthorized"));
    }
    throw e;
  }
  return payload as T;
}

/** URL for a file download (CSV export, backup) that the browser fetches directly. */
export function downloadUrl(path: string, query?: Query): string {
  return `${API_BASE}${path}${qs(query)}`;
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === "validation_error" && Array.isArray(e.details) && e.details.length) {
      const d = e.details[0] as { loc?: (string | number)[]; message?: string };
      const field = d.loc?.filter((x) => x !== "body").join(".");
      return field ? `${field}: ${d.message}` : d.message ?? e.message;
    }
    return e.message;
  }
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}
