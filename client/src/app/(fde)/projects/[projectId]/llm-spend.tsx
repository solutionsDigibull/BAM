"use client";

// What this project has spent on model calls, by stage (from llm_calls: failed attempts count, they were
// billed). Shown only when there is something to show; a failure to load it never blocks the overview.
import { useCustom } from "@refinedev/core";
import { Card, Flex, Statistic, Table, Typography } from "antd";

import type { LlmUsage } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

export const STAGE_NAMES: Record<string, string> = {
  "sbam-knowledge": "Knowledge graph",
  "sbam-embed": "Embeddings",
  "sbam-sow": "SOW",
  "sbam-ocr": "OCR",
  "sbam-isatvon": "Workflow design",
  "sbam-codegen": "Code generation",
  "sbam-testgen": "Test generation",
  "sbam-corrector": "Auto-correction",
};

export const usagePath = (projectId: string) => `projects/${projectId}/llm-usage`;
export const stageName = (alias: string) => STAGE_NAMES[alias] ?? alias;
const tokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 100_000 ? 0 : 1)}k` : `${n}`);

export function LlmSpend({ projectId }: { projectId: string }) {
  const usage = useCustom<LlmUsage, ApiError>({ url: usagePath(projectId), method: "get" });
  const data = usage.result.data;
  if (usage.query.error || !data || !Array.isArray(data.stages) || data.calls === 0) return null;
  return (
    <Card title="LLM spend">
      <Flex gap={32} wrap align="center">
        <Statistic title="Total" prefix="$" value={data.total_usd} />
        <Statistic title="Model calls" value={data.calls} />
        <Statistic
          title="Tokens in / out"
          value={`${tokens(data.tokens_in)} / ${tokens(data.tokens_out)}`}
          formatter={(v) => String(v)}
        />
      </Flex>
      <Table
        style={{ marginTop: 16 }}
        size="small"
        pagination={false}
        rowKey="alias"
        dataSource={data.stages}
        columns={[
          { title: "Stage", dataIndex: "alias", render: (a: string) => stageName(a) },
          { title: "Calls", dataIndex: "calls", width: 80 },
          {
            title: "Failed",
            dataIndex: "failed",
            width: 80,
            render: (n: number) =>
              n > 0 ? <Typography.Text type="danger">{n}</Typography.Text> : 0,
          },
          {
            title: "Cost",
            width: 130,
            // Some providers report no price: zero cost with tokens used means "unknown", not "free".
            render: (_: unknown, r: LlmUsage["stages"][number]) =>
              Number(r.cost_usd) === 0 && r.tokens_in > 0 ? (
                <Typography.Text type="secondary">not reported</Typography.Text>
              ) : (
                `$${r.cost_usd}`
              ),
          },
        ]}
      />
    </Card>
  );
}
