/**
 * Minimal fetch wrapper for the real backend API.
 *
 * The first real API client in this frontend -- every prior phase rendered
 * mock data only (design/layout passes, confirmed with the user before
 * building). This one is genuinely wired: see
 * src/components/dashboard/ChatPanel.tsx, src/lib/chatQueries.ts, and
 * src/lib/outreachQueueApi.ts.
 *
 * Auth: every backend route requires a verified JWT (Clerk/Auth.js JWKS --
 * see backend/app/core/security.py::get_identity). Historically this
 * frontend had no way to get one locally, so a developer had to hand-paste
 * a static NEXT_PUBLIC_DEV_API_TOKEN. That's gone. Instead, ensureApiToken()
 * asks the backend itself for a short-lived local dev session (see
 * backend/app/api/v1/dev_auth.py -- POST /dev/session, which 404s in
 * production, so this path is a genuine no-op against a production
 * backend). The token is cached in memory only (not localStorage --
 * re-fetched on a hard reload, which is fine since it's cheap and local).
 *
 * NEXT_PUBLIC_DEV_API_TOKEN still works as an explicit override (e.g. CI, or
 * a real Clerk-issued token for someone testing production auth locally) --
 * if set, it always wins and the dev-session fetch is never attempted.
 *
 * CORS: the backend's CORS_ORIGINS setting must include this frontend's
 * origin (http://localhost:3000 in local dev, already the default -- see
 * backend/app/core/config.py) for these calls to succeed from a browser.
 * Not a frontend concern, but worth knowing when a call fails with a CORS
 * error rather than a clean ApiError.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const STATIC_DEV_TOKEN = process.env.NEXT_PUBLIC_DEV_API_TOKEN;

/** Raised for both network failures and non-2xx API responses. */
export class ApiError extends Error {
  /** HTTP status code, or null for a network-level failure (no response at all). */
  readonly status: number | null;

  constructor(message: string, status: number | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface DevSessionResponse {
  access_token: string;
  token_type: string;
  expires_in_minutes: number;
}

let cachedToken: string | null = null;
let cachedTokenExpiresAtMs = 0;
let inFlightSessionFetch: Promise<string | null> | null = null;

/**
 * Ask the backend for a short-lived local dev session token.
 *
 * Returns null (never throws) when the backend is unreachable or is running
 * in production (POST /dev/session 404s there by design) -- both are
 * legitimate "no token available" outcomes, not errors this module should
 * surface itself. Result is cached in memory for its lifetime and shared
 * across concurrent callers via inFlightSessionFetch so a burst of calls at
 * page load only fires one request.
 *
 * @returns A bearer token, or null if none could be obtained.
 */
async function fetchDevSessionToken(): Promise<string | null> {
  const now = Date.now();
  if (cachedToken && now < cachedTokenExpiresAtMs) {
    return cachedToken;
  }
  if (inFlightSessionFetch) {
    return inFlightSessionFetch;
  }

  inFlightSessionFetch = (async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/dev/session`, { method: "POST" });
      if (!response.ok) {
        // 404 in production (by design) or any other failure -- either way,
        // no dev session is available; callers fall back to mock data.
        return null;
      }
      const body = (await response.json()) as DevSessionResponse;
      cachedToken = body.access_token;
      // Refresh a little early (5 minutes of margin) rather than waiting to
      // hit an expired-token 401 mid-session.
      cachedTokenExpiresAtMs = now + Math.max(body.expires_in_minutes - 5, 1) * 60_000;
      return cachedToken;
    } catch {
      return null;
    } finally {
      inFlightSessionFetch = null;
    }
  })();

  return inFlightSessionFetch;
}

/**
 * Resolve the bearer token to use for a real API call, if any.
 *
 * @returns STATIC_DEV_TOKEN if explicitly set (always wins, no network
 *   call); otherwise a locally-minted dev session token, or null if none is
 *   available (production backend, or backend unreachable).
 */
async function getAuthToken(): Promise<string | null> {
  if (STATIC_DEV_TOKEN) {
    return STATIC_DEV_TOKEN;
  }
  return fetchDevSessionToken();
}

/**
 * Whether a real call is currently possible -- i.e. an auth token can be
 * obtained. Callers check this before attempting a real call, so an
 * unauthenticated frontend (e.g. pointed at a production backend with no
 * override token) goes straight to mock results instead of firing a request
 * that can only ever 401.
 *
 * Async because obtaining a local dev session requires a network round trip
 * the first time; the result is cached, so repeat calls in the same session
 * resolve instantly.
 */
export async function hasApiToken(): Promise<boolean> {
  return (await getAuthToken()) !== null;
}

interface ApiFetchOptions {
  method?: "GET" | "POST";
  body?: unknown;
  searchParams?: Record<string, string | number | boolean | undefined>;
}

/**
 * Call the real backend API.
 *
 * @param path - Path relative to NEXT_PUBLIC_API_BASE_URL, e.g. "/leads".
 * @param options - Method, JSON body, and query params.
 * @returns The parsed JSON response, typed as `T` by the caller.
 * @throws ApiError on a non-2xx response or a network failure (e.g. the
 *   backend isn't running).
 */
export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const url = new URL(`${API_BASE_URL}${path}`);
  if (options.searchParams) {
    for (const [key, value] of Object.entries(options.searchParams)) {
      if (value !== undefined) {
        url.searchParams.set(key, String(value));
      }
    }
  }

  const token = await getAuthToken();

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      method: options.method ?? "GET",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
  } catch {
    throw new ApiError(`Could not reach the API at ${API_BASE_URL}. Is the backend running?`, null);
  }

  if (!response.ok) {
    let detail = response.statusText || `Request failed with status ${response.status}`;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // Response body wasn't JSON -- keep the statusText-based detail above.
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}
