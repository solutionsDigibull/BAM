"use client";

import { useSyncExternalStore } from "react";

/**
 * The admin portal's light/dark choice (#785), kept in this browser only. Storage can be missing or
 * throw (private mode, blocked site data): the shell then stays light and the toggle still works for
 * the session. Not the workbench store (`src/workbench/lib/store.ts`), which is another portal's.
 */
export type AdminTheme = "light" | "dark";

export const ADMIN_THEME_KEY = "sbam-admin-theme";

const listeners = new Set<() => void>();
let memory: AdminTheme | null = null; // the choice when storage refuses it

function read(): AdminTheme {
  try {
    const saved = window.localStorage.getItem(ADMIN_THEME_KEY);
    if (saved === "dark" || saved === "light") return saved;
  } catch {
    // storage unavailable: fall back to this page's memory
  }
  return memory ?? "light";
}

export function setAdminTheme(next: AdminTheme): void {
  memory = next;
  try {
    window.localStorage.setItem(ADMIN_THEME_KEY, next);
  } catch {
    // not persisted; the in-memory choice still applies
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => e.key === ADMIN_THEME_KEY && listener();
  window.addEventListener("storage", onStorage); // another tab changed it
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** The current theme; the server render (and the first client render) is light. */
export function useAdminTheme(): AdminTheme {
  return useSyncExternalStore(subscribe, read, () => "light");
}
