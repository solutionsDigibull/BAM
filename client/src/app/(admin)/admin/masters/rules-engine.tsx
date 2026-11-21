"use client";

import { useList } from "@refinedev/core";
import { Alert, Flex, Select, Tag } from "antd";
import { useMemo, useState } from "react";

import { Meta } from "@/components/ui-kit";
import type { MasterEntry, MasterTypeInfo } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { MasterGridPanel } from "./master-grid-panel";
import { entriesResource, scopeQuery } from "./masters-api";
import { RuleDrawer } from "./rule-drawer";
import {
  OTHER,
  RULE_STATUSES,
  RULE_TYPES,
  ruleCategory,
  ruleSkills,
  ruleStatus,
  workflowMap,
} from "./rules-tools";

const CATEGORIES = [...RULE_TYPES, OTHER];

/**
 * The Rules Engine (#784): the workflow map built from the Skills `N · Next` chains with the rule
 * count per skill, the rules as an AG Grid (status and category filters, a new version per edit,
 * retire) and the editor drawer with its Rule and Prompts tabs. Every write goes through the API.
 */
export function RulesEngine({ info, projectId }: { info: MasterTypeInfo; projectId?: string }) {
  const [skill, setSkill] = useState<string | null>(null);
  const [categories, setCategories] = useState<string[]>([]);
  const [statuses, setStatuses] = useState<string[]>([]);
  const query = { pagination: { mode: "off" as const }, meta: { query: scopeQuery(projectId) } };
  const rules = useList<MasterEntry, ApiError>({ resource: entriesResource("RULES"), ...query });
  const skills = useList<MasterEntry, ApiError>({ resource: entriesResource("SKILLS"), ...query });
  const map = useMemo(
    () => workflowMap(skills.result.data ?? [], rules.result.data ?? []),
    [skills.result.data, rules.result.data],
  );
  const rowFilter = useMemo(
    () => (e: MasterEntry) =>
      (!skill || ruleSkills(e).includes(skill.toUpperCase())) &&
      (!categories.length || categories.includes(ruleCategory(e))) &&
      (!statuses.length || statuses.includes(ruleStatus(e))),
    [skill, categories, statuses],
  );

  const toolbar = (
    <div style={{ margin: "16px 0 8px" }}>
      {skills.query.isError ? (
        <Alert
          type="warning"
          showIcon
          message="The workflow map could not load"
          description={skills.query.error?.problem?.detail ?? skills.query.error?.message}
        />
      ) : (
        <Flex gap={6} wrap role="group" aria-label="Workflow map" align="center">
          <Meta>Workflow</Meta>
          {map.length === 0 && !skills.query.isLoading && <Meta>No skills yet</Meta>}
          {map.map((n, i) => (
            <Tag.CheckableTag
              key={n.id}
              checked={skill === n.id}
              aria-pressed={skill === n.id}
              style={{ border: "1px solid var(--sbam-grid-line, #e5e7eb)", margin: 0 }}
              onChange={(on) => setSkill(on ? n.id : null)}
            >
              {i + 1}. {n.id} {n.name} · {n.rules} {n.rules === 1 ? "rule" : "rules"}
            </Tag.CheckableTag>
          ))}
        </Flex>
      )}
      <Flex gap={8} wrap style={{ marginTop: 8 }}>
        <Select
          mode="multiple"
          allowClear
          maxTagCount="responsive"
          style={{ minWidth: 160, maxWidth: "100%" }}
          placeholder="Category"
          aria-label="Filter by Category"
          value={categories}
          onChange={setCategories}
          options={CATEGORIES.map((c) => ({ value: c }))}
        />
        <Select
          mode="multiple"
          allowClear
          maxTagCount="responsive"
          style={{ minWidth: 160, maxWidth: "100%" }}
          placeholder="Status"
          aria-label="Filter by Status"
          value={statuses}
          onChange={setStatuses}
          options={RULE_STATUSES.map((s) => ({ value: s }))}
        />
      </Flex>
    </div>
  );

  return (
    <MasterGridPanel
      info={info}
      projectId={projectId}
      rowFilter={rowFilter}
      label="Rules"
      extra={toolbar}
      renderDrawer={(d) => <RuleDrawer info={info} projectId={projectId} {...d} />}
    />
  );
}
