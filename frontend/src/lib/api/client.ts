// Server Components/Route Handlers run inside the frontend container, where
// "localhost" doesn't reach the backend container — they need the Docker
// service DNS name instead. The browser (client components) still uses the
// host-facing NEXT_PUBLIC_API_URL. See docker-compose.yml's frontend service.
const API_BASE_URL =
  typeof window === "undefined"
    ? (process.env.API_URL_INTERNAL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1");

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Skip attaching the stored access token (e.g. auth endpoints themselves). */
  skipAuth?: boolean;
}

function getStoredAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("bodp_access_token");
}

/**
 * Thin fetch wrapper shared by every API call in the app — attaches the
 * bearer token, base URL, and JSON handling in one place, and normalizes
 * backend errors (see app/core/errors.py's {"error": {code, message,
 * request_id}} shape) into a single ApiError type callers can catch.
 */
export async function apiFetch<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, skipAuth, headers, ...rest } = options;

  const finalHeaders: Record<string, string> = {
    ...(headers as Record<string, string> | undefined),
  };

  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;

  if (body !== undefined && !isFormData) {
    finalHeaders["Content-Type"] = "application/json";
  }
  // FormData bodies must NOT set Content-Type manually — the browser needs
  // to add the multipart boundary itself.

  if (!skipAuth) {
    const token = getStoredAccessToken();
    if (token) finalHeaders["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    headers: finalHeaders,
    body: isFormData ? (body as FormData) : body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const isJson = response.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await response.json().catch(() => null) : null;

  if (!response.ok) {
    const message =
      (data && typeof data === "object" && "error" in data
        ? (data as { error?: { message?: string } }).error?.message
        : null) ?? `Request failed with status ${response.status}`;
    throw new ApiError(response.status, message, data);
  }

  return data as T;
}

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}
