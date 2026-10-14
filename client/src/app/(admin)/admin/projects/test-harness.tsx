// Test-only: renders a screen inside Refine with the real data provider and a fake sbam-api behind fetch.
// Example data follows the admin reference workbook (BuLLM - Manufacturing Masters v0.2).
import { Refine } from "@refinedev/core";
import { render } from "@testing-library/react";
import { App } from "antd";
import type { ReactNode } from "react";
import { vi } from "vitest";

import type { Project } from "@/contracts";
import { API_BASE, dataProvider } from "@/providers/data-provider";

import type { TenantUser } from "./project-api";

type Reply = { status?: number; body?: unknown; etag?: string };
type Handler = Reply | ((init: RequestInit) => Reply);
export type Call = { method: string; path: string; init: RequestInit };

export const problem = (status: number, code: string, detail = code): Reply => ({
  status,
  body: { type: "about:blank", title: code, status, detail, code },
});

export const project = (over: Partial<Project> = {}): Project => ({
  id: "0190f3a2-7b1c-7d4e-8f00-000000000001",
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  name: "EMS RFQ costing (ORG-EMS)",
  client_name: "Illustrative EMS manufacturer",
  description: "RFQ ID + revision + scenario + FG",
  context_notes: null,
  status: "ACTIVE",
  pipeline_state: "IDLE",
  row_version: 1,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-02T10:00:00Z",
  ...over,
});

export const user = (
  id: string,
  name: string,
  roles = ["sbam-fde"],
  active = true,
): TenantUser => ({
  id,
  keycloak_sub: `sub-${id}`,
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  email: `${name.toLowerCase().replace(/\W+/g, ".")}@acme.example`,
  display_name: name,
  portal_roles: roles,
  last_login_at: null,
  active,
});

/** Routes are "METHOD /path" (after /api/v1, query string ignored). A list of replies is used in order. */
export function fakeApi(routes: Record<string, Handler | Handler[]>) {
  const calls: Call[] = [];
  const queues = new Map(Object.entries(routes).map(([k, v]) => [k, [v].flat()]));
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(API_BASE, "");
    const method = (init.method ?? "GET").toUpperCase();
    calls.push({ method, path: `${path}${url.search}`, init });
    const queue = queues.get(`${method} ${path}`);
    if (!queue?.length) throw new Error(`unexpected ${method} ${path}`);
    const handler = queue.length > 1 ? queue.shift()! : queue[0]!;
    const reply = typeof handler === "function" ? handler(init) : handler;
    const status = reply.status ?? 200;
    return new Response(status === 204 ? null : JSON.stringify(reply.body ?? {}), {
      status,
      headers: {
        "content-type": status >= 400 ? "application/problem+json" : "application/json",
        ...(reply.etag ? { etag: reply.etag } : {}),
      },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

export function renderWithRefine(ui: ReactNode) {
  return render(
    <App>
      <Refine
        dataProvider={dataProvider}
        options={{
          disableTelemetry: true,
          reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } },
        }}
      >
        {ui}
      </Refine>
    </App>,
  );
}
