"use client";

import { useList } from "@refinedev/core";
import { Flex, Segmented, Tag } from "antd";
import { useMemo, useState } from "react";

import type { MasterEntry, MasterTypeInfo } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { chips } from "./grid-tools";
import { MasterGridPanel } from "./master-grid-panel";
import { cellText, entriesResource, scopeQuery } from "./masters-api";

type View = "Tools" | "Agents" | "Assistants";
const KIND: Record<"Agents" | "Assistants", string> = { Agents: "Agent", Assistants: "Assistant" };

/**
 * Tool Factory (#783): the Tooling master with A-Z category toggles and the BuLLM seal on its id,
 * plus the Agents master as two views, Agents and Assistants (its `kind`).
 */
export function ToolFactory({
  tools,
  agents,
  projectId,
}: {
  tools?: MasterTypeInfo;
  agents?: MasterTypeInfo;
  projectId?: string;
}) {
  const [view, setView] = useState<View>("Tools");
  const [picked, setPicked] = useState<string[]>([]);
  const { result } = useList<MasterEntry, ApiError>({
    resource: entriesResource("TOOLING"),
    pagination: { mode: "off" },
    meta: { query: scopeQuery(projectId) },
  });
  const categories = useMemo(
    () =>
      [...new Set((result.data ?? []).flatMap((e) => chips(cellText(e.payload.category))))].sort(
        (a, b) => a.localeCompare(b),
      ),
    [result.data],
  );
  const toolFilter = useMemo(
    () => (e: MasterEntry) =>
      !picked.length || chips(cellText(e.payload.category)).some((c) => picked.includes(c)),
    [picked],
  );
  const kindFilter = useMemo(
    () => (e: MasterEntry) =>
      (cellText(e.payload.kind) || "Agent") ===
      KIND[view === "Assistants" ? "Assistants" : "Agents"],
    [view],
  );
  const options = [tools && "Tools", agents && "Agents", agents && "Assistants"].filter(
    (v): v is View => !!v,
  );
  const toggles = (
    <Flex gap={6} wrap role="group" aria-label="Category" style={{ margin: "4px 0 12px" }}>
      {categories.map((c) => {
        const on = picked.includes(c);
        return (
          <Tag.CheckableTag
            key={c}
            style={{ border: "1px solid var(--sbam-grid-line, #e5e7eb)", margin: 0 }}
            checked={on}
            aria-pressed={on}
            onChange={(next) => setPicked((s) => (next ? [...s, c] : s.filter((x) => x !== c)))}
          >
            {c}
          </Tag.CheckableTag>
        );
      })}
    </Flex>
  );
  return (
    <>
      <Segmented<View>
        options={options}
        value={options.includes(view) ? view : options[0]}
        onChange={setView}
        style={{ marginTop: 16 }}
      />
      {view === "Tools" && tools ? (
        <MasterGridPanel
          key="tools"
          info={tools}
          projectId={projectId}
          rowFilter={toolFilter}
          label="Tools"
          extra={categories.length ? toggles : undefined}
        />
      ) : agents ? (
        <MasterGridPanel
          key={view}
          info={agents}
          projectId={projectId}
          rowFilter={kindFilter}
          defaults={{ kind: view === "Assistants" ? "Assistant" : "Agent" }}
          label={view}
        />
      ) : null}
    </>
  );
}
