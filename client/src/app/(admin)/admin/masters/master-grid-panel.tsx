"use client";

import { DownloadOutlined, PlusOutlined } from "@ant-design/icons";
import { useCustomMutation, useInvalidate, useList } from "@refinedev/core";
import { App, AutoComplete, Button, Flex, InputNumber, Select } from "antd";
import dynamic from "next/dynamic";
import { useCallback, useMemo, useState } from "react";

import { ApiErrorState, EmptyState, LoadingState, Meta, STALE_MESSAGE } from "@/components/ui-kit";
import type { MasterEntry, MasterTypeInfo } from "@/contracts";
import { dataProvider, ifMatch, type ApiError } from "@/providers/data-provider";

import { EntryDrawer } from "./entry-drawer";
import {
  EDIT_HINT,
  facetValues,
  FACETS,
  matchesFacets,
  matchesSearch,
  pushHistory,
  readHistory,
  saveCsv,
  toCsv,
  type Facets,
} from "./grid-tools";
import { emptyMessage, entriesResource, scopeQuery } from "./masters-api";

const MasterGrid = dynamic(() => import("./master-grid"), {
  ssr: false,
  loading: () => <LoadingState />,
});

const MAX_ADD = 50;
const isStale = (e: ApiError) =>
  e.statusCode === 412 || (e.statusCode === 409 && e.problem?.code === "VERSION_CONFLICT");

/**
 * One master as an AG Grid (#783): search with history, facet filters, CSV, add, cell edit (a new
 * version with If-Match), duplicate and retire. `filter` and `defaults` let Tool Factory show Agents
 * and Assistants as two views of the Agents master.
 */
export function MasterGridPanel({
  info,
  projectId,
  rowFilter,
  defaults,
  singleClick = false,
  duplicate = false,
  label = info.tab,
  extra,
  renderDrawer,
}: {
  info: MasterTypeInfo;
  projectId?: string;
  rowFilter?: (e: MasterEntry) => boolean;
  defaults?: Record<string, string>;
  singleClick?: boolean;
  duplicate?: boolean;
  label?: string;
  extra?: React.ReactNode;
  /** A master with its own editor (Rules, #784) replaces the generic entry drawer. */
  renderDrawer?: (d: {
    entryId?: string;
    onClose: () => void;
    onSaved: (e: MasterEntry) => void;
  }) => React.ReactNode;
}) {
  const { message } = App.useApp();
  const invalidate = useInvalidate();
  const type = info.master_type;
  const resource = entriesResource(type);
  const [search, setSearch] = useState("");
  const [history, setHistory] = useState<string[]>(() => readHistory(type));
  const [facets, setFacets] = useState<Facets>({});
  const [drawer, setDrawer] = useState<{ id?: string } | null>(null);
  const [count, setCount] = useState(5);
  const [adding, setAdding] = useState(false);
  const { mutateAsync } = useCustomMutation<MasterEntry, ApiError>();
  const { result, query } = useList<MasterEntry, ApiError>({
    resource,
    pagination: { mode: "off" },
    meta: { query: scopeQuery(projectId) },
  });
  const all = useMemo(
    () => (result.data ?? []).filter((e) => !rowFilter || rowFilter(e)),
    [result.data, rowFilter],
  );
  const rows = useMemo(
    () => all.filter((e) => matchesSearch(e, search) && matchesFacets(e, facets)),
    [all, search, facets],
  );
  const reload = useCallback(
    () => invalidate({ resource, invalidates: ["list", "detail"] }),
    [invalidate, resource],
  );

  // Read the row first: its ETag goes in If-Match, and a row that moved on is the stale case.
  const fresh = useCallback(
    async (e: MasterEntry): Promise<MasterEntry | null> => {
      const { data } = await dataProvider.getOne<MasterEntry>({ resource, id: e.id });
      if (data.version === e.version) return data;
      message.warning(STALE_MESSAGE);
      void reload();
      return null;
    },
    [resource, message, reload],
  );
  const fail = useCallback(
    (err: unknown) => {
      const e = err as ApiError;
      if (isStale(e)) {
        message.warning(STALE_MESSAGE);
        void reload();
      } else message.error(e.problem?.detail ?? e.problem?.title ?? e.message ?? "Request failed");
    },
    [message, reload],
  );

  const actions = useMemo(
    () => ({
      onOpen: (e: MasterEntry) => setDrawer({ id: e.id }),
      onEdit: async (e: MasterEntry, field: string, value: string) => {
        try {
          const current = await fresh(e);
          if (!current) return;
          const res = await mutateAsync({
            url: `${resource}/${e.id}/versions`,
            method: "post",
            values: { payload: { ...current.payload, [field]: value } },
            config: { headers: ifMatch(resource, e.id) },
            successNotification: false,
          });
          message.success(`Saved ${e.stable_id || e.row_label} (version ${res.data.version})`);
          await reload();
        } catch (err) {
          fail(err);
        }
      },
      onRetire: async (e: MasterEntry) => {
        try {
          if (!(await fresh(e))) return;
          await mutateAsync({
            url: `${resource}/${e.id}/retire`,
            method: "post",
            values: {},
            config: { headers: ifMatch(resource, e.id) },
            successNotification: false,
          });
          message.success(`Retired ${e.stable_id || e.row_label}`);
          await reload();
        } catch (err) {
          fail(err);
        }
      },
      onDuplicate: duplicate
        ? async (e: MasterEntry) => {
            const [idField, nameField] = [
              info.columns[0]?.field ?? "",
              info.columns[1]?.field ?? "",
            ];
            try {
              await mutateAsync({
                url: `${resource}${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
                method: "post",
                values: {
                  payload: {
                    ...e.payload,
                    [idField]: "", // the server assigns the next id
                    [nameField]: `${String(e.payload[nameField] ?? "")} (copy)`.trim(),
                  },
                },
                successNotification: false,
              });
              message.success(`Duplicated ${e.stable_id}`);
              await reload();
            } catch (err) {
              fail(err);
            }
          }
        : undefined,
    }),
    [fresh, mutateAsync, resource, message, reload, fail, duplicate, info.columns, projectId],
  );

  const addRows = async () => {
    setAdding(true);
    const idField = info.columns[0]?.field ?? "";
    try {
      await mutateAsync({
        url: `${resource}/bulk${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
        method: "post",
        values: {
          mode: "upsert",
          rows: Array.from({ length: count }, () => ({ ...defaults, [idField]: "" })),
        },
        successNotification: false,
      });
      message.success(`Added ${count} blank ${count === 1 ? "row" : "rows"}`);
      await reload();
    } catch (err) {
      fail(err);
    } finally {
      setAdding(false);
    }
  };

  const addButton = info.editable && (
    <Button type="primary" icon={<PlusOutlined />} onClick={() => setDrawer({})}>
      Add Row
    </Button>
  );
  const onDrawerClose = () => setDrawer(null);
  const onDrawerSaved = (e: MasterEntry) => {
    setDrawer(null);
    void reload();
    message.success(
      drawer?.id ? `Saved ${e.row_label} (version ${e.version})` : `Created ${e.row_label}`,
    );
  };
  const drawerView =
    drawer &&
    (renderDrawer ? (
      renderDrawer({ entryId: drawer.id, onClose: onDrawerClose, onSaved: onDrawerSaved })
    ) : (
      <EntryDrawer
        info={info}
        projectId={projectId}
        entryId={drawer.id}
        defaults={defaults}
        onClose={onDrawerClose}
        onSaved={onDrawerSaved}
      />
    ));

  if (query.isLoading) return <LoadingState />;
  if (query.isError) return <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!all.length)
    return (
      <>
        {extra}
        <EmptyState message={emptyMessage(label)} action={addButton || undefined} />
        {drawerView}
      </>
    );

  const searchBox = (
    <AutoComplete
      allowClear
      style={{ flex: "1 1 200px", minWidth: 0, maxWidth: 340 }}
      value={search}
      options={history.map((h) => ({ value: h }))}
      onChange={setSearch}
      onSelect={setSearch}
      placeholder={`Search ${label}...`}
      aria-label={`Search ${label}`}
      onBlur={() => setHistory(pushHistory(type, search))}
    />
  );
  return (
    <>
      <Flex justify="space-between" align="center" wrap gap={12} style={{ margin: "16px 0 8px" }}>
        <Flex gap={8} wrap align="center">
          {searchBox}
          {(FACETS[type] ?? []).map((f) => (
            <Select
              key={f.field}
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              style={{ minWidth: 150, maxWidth: "100%" }}
              placeholder={f.label}
              aria-label={`Filter by ${f.label}`}
              value={facets[f.field] ?? []}
              onChange={(v: string[]) => setFacets((s) => ({ ...s, [f.field]: v }))}
              options={facetValues(all, f.field).map((v) => ({ value: v, label: v }))}
            />
          ))}
        </Flex>
        <Flex align="center" gap={8} wrap>
          <Meta>
            {rows.length} of {all.length} rows
          </Meta>
          <Button
            icon={<DownloadOutlined />}
            onClick={() => saveCsv(`${label.replace(/\W+/g, "_")}.csv`, toCsv(info, rows))}
          >
            Export CSV
          </Button>
          {info.editable && (
            <>
              <InputNumber
                min={1}
                max={MAX_ADD}
                value={count}
                onChange={(v) => setCount(v ?? 1)}
                aria-label="Number of rows to add"
                style={{ width: 64 }}
              />
              <Button loading={adding} onClick={addRows}>
                Add {count} blank {count === 1 ? "row" : "rows"}
              </Button>
            </>
          )}
          {addButton}
        </Flex>
      </Flex>
      {extra}
      {rows.length ? (
        <MasterGrid info={info} rows={rows} actions={actions} singleClick={singleClick} />
      ) : (
        <EmptyState message={`No ${label} entries match your search.`} />
      )}
      <Meta>{info.editable ? EDIT_HINT : "Read only"}</Meta>
      {drawerView}
    </>
  );
}
