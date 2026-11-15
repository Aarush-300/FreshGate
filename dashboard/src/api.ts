export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (fn: (() => void) | null) => {
  onUnauthorized = fn;
};

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/api/auth/")) {
      onUnauthorized?.();
    }
    const detail = typeof payload === "object" && payload !== null && "detail" in payload
      ? String((payload as { detail: unknown }).detail)
      : typeof payload === "string" && payload.trim()
        ? payload
        : `Request failed (${response.status})`;
    throw new ApiError(detail, response.status);
  }
  return payload as T;
}

export const jsonBody = (value: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(value),
});

export const patchBody = (value: unknown): RequestInit => ({
  method: "PATCH",
  body: JSON.stringify(value),
});

export const deleteReq: RequestInit = { method: "DELETE" };

export const putBody = (v: unknown): RequestInit => ({
  method: "PUT",
  body: JSON.stringify(v),
});
