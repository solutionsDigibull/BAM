// Refine data provider over the server-side proxy (E1.14). Every call goes to /api/proxy/api/v1, so the
// browser never holds a token. Errors become Refine HttpErrors carrying the problem+json body.
import type { BaseRecord, DataProvider, HttpError } from "@refinedev/core";

import type { FieldError, ProblemDetail } from "@/contracts";

export const API_BASE = "/api/proxy/api/v1";

const ALL_PAGE_SIZE = 100; // the server's largest page
const MAX_PAGES = 50; // a broken cursor must not loop forever: 5 000 rows is far past any list here

/** HttpError with the parsed problem+json, so UI code can branch on status and `code`. */
export type ApiError = HttpError & { problem?: Partial<ProblemDetail> };

// ETag of the last representation read per resource/id; sent back as If-Match on update/delete.
// ponytail: module-level map, per browser tab; move into the query cache if it ever goes stale.
const etags = new Map<string, string>();
const key = (resource: string, id: string | number) => `${resource}/${id}`;

function fieldErrors(errors: FieldError[] | undefined): HttpError["errors"] {
  if (!errors?.length) return undefined;
  const out: Record<string, string[]> = {};
  for (const e of errors) (out[e.path] ??= []).push(e.message);
  return out;
}

export async function toApiError(res: Response): Promise<ApiError> {
  let problem: Partial<ProblemDetail> | undefined;
  try {
    problem = (await res.json()) as Partial<ProblemDetail>;
  } catch {
    problem = undefined; // non-JSON body
  }
  return {
    message: problem?.detail ?? problem?.title ?? res.statusText ?? "Request failed",
    statusCode: res.status,
    errors: fieldErrors(problem?.errors),
    problem,
  };
}

async function request(
  url: string,
  init: RequestInit & { etagKey?: string } = {},
): Promise<{ body: unknown; res: Response }> {
  const { etagKey, ...rest } = init;
  const headers = new Headers(rest.headers);
  if (rest.body !== undefined) headers.set("content-type", "application/json");
  headers.set("accept", "application/json");
  const res = await fetch(url, { ...rest, headers, credentials: "same-origin" });
  if (!res.ok) throw await toApiError(res);
  const etag = res.headers.get("etag");
  if (etagKey && etag) etags.set(etagKey, etag);
  const body = res.status === 204 ? null : await res.json();
  return { body, res };
}

const withIfMatch = (k: string, headers?: Record<string, string>) => {
  const etag = etags.get(k);
  return { ...(etag ? { "if-match": etag } : {}), ...headers };
};

/** If-Match for a record read with getOne, for writes that aren't PATCH (e.g. POST .../versions). */
export const ifMatch = (resource: string, id: string | number) => withIfMatch(key(resource, id));

export const dataProvider: DataProvider = {
  getApiUrl: () => API_BASE,

  // sbam-api lists are cursor pages (`Page[T]`: items + next_cursor), not offset pages with a total.
  // `pagination.mode: "off"` means every row (pickers, grids that filter locally), so the cursor is
  // followed to its end; `cursor.next` stays set only if MAX_PAGES stopped it short.
  getList: async ({ resource, pagination, sorters, meta }) => {
    const all = pagination?.mode === "off";
    const fetchPage = async (cursor: unknown) => {
      const qs = new URLSearchParams();
      if (all) qs.set("limit", String(ALL_PAGE_SIZE));
      else if (pagination?.pageSize) qs.set("limit", String(pagination.pageSize));
      if (cursor) qs.set("cursor", String(cursor));
      if (sorters?.[0])
        qs.set("sort", `${sorters[0].order === "desc" ? "-" : ""}${sorters[0].field}`);
      const query = qs.size ? `?${qs}` : "";
      const { body } = await request(`${API_BASE}/${resource}${query}`);
      return body as { items: BaseRecord[]; next_cursor?: string | null };
    };
    let page = await fetchPage(meta?.cursor);
    const items = [...page.items];
    for (let n = 1; all && page.next_cursor && n < MAX_PAGES; n++) {
      page = await fetchPage(page.next_cursor);
      items.push(...page.items);
    }
    return {
      data: items as never,
      total: items.length,
      cursor: { next: page.next_cursor ?? undefined },
    };
  },

  getOne: async ({ resource, id }) => {
    const k = key(resource, id);
    const { body } = await request(`${API_BASE}/${resource}/${id}`, { etagKey: k });
    return { data: body as never };
  },

  create: async ({ resource, variables, meta }) => {
    const { body } = await request(`${API_BASE}/${resource}`, {
      method: "POST",
      body: JSON.stringify(variables),
      headers: meta?.headers,
    });
    return { data: body as never };
  },

  update: async ({ resource, id, variables, meta }) => {
    const k = key(resource, id);
    const { body } = await request(`${API_BASE}/${resource}/${id}`, {
      method: "PATCH",
      body: JSON.stringify(variables),
      headers: withIfMatch(k, meta?.headers),
      etagKey: k,
    });
    return { data: body as never };
  },

  deleteOne: async ({ resource, id, meta }) => {
    const k = key(resource, id);
    const { body } = await request(`${API_BASE}/${resource}/${id}`, {
      method: "DELETE",
      headers: withIfMatch(k, meta?.headers),
    });
    etags.delete(k);
    return { data: body as never };
  },

  // Commands (e.g. POST /projects/{id}/sow:generate) pass Idempotency-Key via `headers`.
  custom: async ({ url, method, payload, query, headers }) => {
    const qs = query ? `?${new URLSearchParams(query as Record<string, string>)}` : "";
    const { body } = await request(`${url.startsWith("/") ? url : `${API_BASE}/${url}`}${qs}`, {
      method: method.toUpperCase(),
      body: payload === undefined ? undefined : JSON.stringify(payload),
      headers,
    });
    return { data: body as never };
  },
};
