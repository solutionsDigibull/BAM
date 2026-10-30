import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE, dataProvider } from "./data-provider";

const fetchMock = vi.fn<typeof fetch>();
const json = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), {
    ...init,
    headers: { "content-type": "application/json", ...(init.headers as Record<string, string>) },
  });

describe("dataProvider (E1.14)", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("e1_14_t09 update sends back the ETag from getOne as If-Match", async () => {
    fetchMock.mockResolvedValueOnce(json({ id: "p1", name: "a" }, { headers: { etag: '"5"' } }));
    fetchMock.mockResolvedValueOnce(json({ id: "p1", name: "b" }, { headers: { etag: '"6"' } }));
    await dataProvider.getOne({ resource: "projects", id: "p1" });
    await dataProvider.update({ resource: "projects", id: "p1", variables: { name: "b" } });
    const [url, init] = fetchMock.mock.calls[1]!;
    expect(url).toBe(`${API_BASE}/projects/p1`);
    expect(init?.method).toBe("PATCH");
    expect(new Headers(init?.headers).get("if-match")).toBe('"5"');
  });

  it("e1_14_t05 422 problem+json becomes field errors", async () => {
    fetchMock.mockResolvedValueOnce(
      json(
        {
          title: "Validation failed",
          status: 422,
          errors: [{ path: "name", message: "required" }],
        },
        { status: 422 },
      ),
    );
    await expect(
      dataProvider.create({ resource: "projects", variables: {} }),
    ).rejects.toMatchObject({ statusCode: 422, errors: { name: ["required"] } });
  });

  it("maps a cursor page to Refine's list shape", async () => {
    fetchMock.mockResolvedValueOnce(json({ items: [{ id: 1 }, { id: 2 }], next_cursor: "c2" }));
    const out = await dataProvider.getList({
      resource: "projects",
      pagination: { currentPage: 1, pageSize: 2 },
      sorters: [{ field: "name", order: "desc" }],
    });
    expect(fetchMock.mock.calls[0]![0]).toBe(`${API_BASE}/projects?limit=2&sort=-name`);
    expect(out).toMatchObject({ total: 2, cursor: { next: "c2" } });
  });

  it("a_15b_t01 pagination off follows the cursor until the list ends", async () => {
    fetchMock.mockResolvedValueOnce(json({ items: [{ id: 1 }, { id: 2 }], next_cursor: "c2" }));
    fetchMock.mockResolvedValueOnce(json({ items: [{ id: 3 }, { id: 4 }], next_cursor: "c3" }));
    fetchMock.mockResolvedValueOnce(json({ items: [{ id: 5 }], next_cursor: null }));
    const out = await dataProvider.getList({ resource: "projects", pagination: { mode: "off" } });
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      `${API_BASE}/projects?limit=100`,
      `${API_BASE}/projects?limit=100&cursor=c2`,
      `${API_BASE}/projects?limit=100&cursor=c3`,
    ]);
    expect(out.data).toEqual([{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }, { id: 5 }]);
    expect(out).toMatchObject({ total: 5, cursor: { next: undefined } });
  });

  it("a_15b_t02 a list of exactly one full page and an empty list take one request", async () => {
    fetchMock.mockResolvedValueOnce(json({ items: [{ id: 1 }], next_cursor: null }));
    await dataProvider.getList({ resource: "projects", pagination: { mode: "off" } });
    fetchMock.mockResolvedValueOnce(json({ items: [] }));
    const empty = await dataProvider.getList({ resource: "projects", pagination: { mode: "off" } });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(empty.data).toEqual([]);
  });

  it("a_15b_t03 a cursor that never ends stops at the page cap and says it was cut short", async () => {
    fetchMock.mockImplementation(async () => json({ items: [{ id: 1 }], next_cursor: "again" }));
    const out = await dataProvider.getList({ resource: "projects", pagination: { mode: "off" } });
    expect(fetchMock).toHaveBeenCalledTimes(50);
    expect(out.cursor?.next).toBe("again");
  });

  it("a_15b_t04 a page that fails midway fails the whole list, never a short one", async () => {
    fetchMock.mockResolvedValueOnce(json({ items: [{ id: 1 }], next_cursor: "c2" }));
    fetchMock.mockResolvedValueOnce(json({ title: "Bad" }, { status: 422 }));
    await expect(
      dataProvider.getList({ resource: "projects", pagination: { mode: "off" } }),
    ).rejects.toMatchObject({ statusCode: 422 });
  });

  it("a_15b_t05 a paged list still sends one request with the caller's cursor", async () => {
    fetchMock.mockResolvedValueOnce(json({ items: [{ id: 1 }], next_cursor: "c9" }));
    const out = await dataProvider.getList({
      resource: "projects",
      pagination: { currentPage: 2, pageSize: 10 },
      meta: { cursor: "c2" },
    });
    expect(fetchMock.mock.calls).toHaveLength(1);
    expect(fetchMock.mock.calls[0]![0]).toBe(`${API_BASE}/projects?limit=10&cursor=c2`);
    expect(out.cursor?.next).toBe("c9");
  });

  it("keeps a non-JSON error body as a plain HttpError", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("<html>", { status: 502, statusText: "Bad Gateway" }),
    );
    await expect(dataProvider.getOne({ resource: "projects", id: "x" })).rejects.toMatchObject({
      statusCode: 502,
      message: "Bad Gateway",
    });
  });
});
