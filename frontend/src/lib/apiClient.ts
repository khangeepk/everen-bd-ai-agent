/**
 * Minimal fetch wrapper for the real backend API.
 *
 * The first real API client in this frontend -- every prior phase rendered
 * mock data only (design/layout passes, confirmed with the user before
 * building). This one is genuinely wired: see
 * src/components/dashboard/ChatPanel.tsx and src/lib/chatQueries.ts.
 *
 * Auth: every backend route requires a verified JWT (Clerk/Auth.js JWKS --
 * see backend/app/api/deps.py::get_current_user). This frontend has no
 * sign-in flow yet, so for local development set NEXT_PUBLIC_DEV_API_TOKEN
 * in .env.local to a token obtained separately (e.g. from a Clerk/Auth.js
 * session). Building a real sign-in flow is a later phase -- see
 * hasApiToken(), which callers use to skip straight to mock data rather than
 * firing a request that can only 401.
 *
 * CORS: the backend's CORS_ORIGINS setting must include this frontend's
 * origin (http://localhost:3000 in local dev) for these calls to succeed
 * from a browser -- see backend/app/core/config.py. Not a frontend concern,
 * but worth knowing when a call fails with a CORS error rather than a
 * clean ApiError.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const DEV_TOKEN = process.env.NEXT_PUBLIC_DEV_API_TOKEN;

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

/**
 * Whether a dev API token is configured.
 *
 * Callers check this before attempting a real call, so an unconfigured
 * frontend goes straight to mock results instead of firing a request that
 * can only ever 401.
 */
export function hasApiToken(): boolean {
  return Boolean(DEV_TOKEN);
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

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      method: options.method ?? "GET",
      headers: {
        "Content-Type": "application/json",
        ...(DEV_TOKEN ? { Authorization: `Bearer ${DEV_TOKEN}` } : {}),
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
