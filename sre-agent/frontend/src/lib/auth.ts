// Token-management helpers.
//
// The token lives in localStorage under `shp_token`. On 401 the API client
// clears it and the Zustand store; the router pushes the user to /login.

const KEY = "shp_token";

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string): void {
  try {
    localStorage.setItem(KEY, token);
  } catch {
    /* localStorage disabled — caller falls back to in-memory only */
  }
}

export function clearStoredToken(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
