import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { type Page } from "@playwright/test";

const REPO_ROOT = resolve(__dirname, "..", "..");

/** Read SHP_API_TOKEN from .env.local. Tests fail loudly if it's missing —
 *  `make up` writes it on first launch. */
export function readToken(): string {
  const fromEnv = process.env.SHP_API_TOKEN;
  if (fromEnv) return fromEnv;
  const path = resolve(REPO_ROOT, ".env.local");
  if (!existsSync(path)) {
    throw new Error(
      `.env.local not found at ${path}. Run \`make up\` (or set SHP_API_TOKEN).`,
    );
  }
  for (const line of readFileSync(path, "utf-8").split(/\r?\n/)) {
    if (line.startsWith("SHP_API_TOKEN=")) return line.split("=", 2)[1].trim();
  }
  throw new Error(".env.local exists but has no SHP_API_TOKEN line.");
}

/** Inject the token into localStorage and reload — equivalent to pasting it
 *  in the login screen, without slowing every test down with the typing UX. */
export async function authenticate(page: Page) {
  const token = readToken();
  await page.goto("/");
  await page.evaluate((tok) => localStorage.setItem("shp_token", tok), token);
  await page.reload();
}
