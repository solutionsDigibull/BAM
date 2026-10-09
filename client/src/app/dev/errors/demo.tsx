"use client";

import { Card, Flex, Typography } from "antd";

import { ApiErrorState, type ApiErrorLike } from "@/components/ui-kit";

// Bodies shaped like sbam-api problem+json (server/contracts/sbam_contracts/api.py ProblemDetail).
const SAMPLES: ApiErrorLike[] = [
  { statusCode: 403, problem: { title: "Forbidden", code: "WRONG_PORTAL" } },
  {
    statusCode: 404,
    problem: { title: "Not Found", detail: "Project not found.", code: "NOT_FOUND" },
  },
  { statusCode: 409, problem: { title: "Conflict", code: "CONFLICT" } },
  { statusCode: 422, errors: { name: ["required"] }, problem: { title: "Validation failed" } },
  { statusCode: 423, problem: { title: "Locked", code: "LOCKED" } },
  { statusCode: 503, problem: { title: "Unavailable", detail: "Temporal is restarting." } },
  { statusCode: 412, problem: { title: "Precondition Failed", code: "STALE_ETAG" } },
];

export function ErrorStatesDemo() {
  const reload = () => window.location.reload();
  return (
    <Flex vertical gap={16} style={{ maxWidth: 720, margin: "32px auto", padding: "0 16px" }}>
      <Typography.Title level={1} style={{ fontSize: 24 }}>
        API error states
      </Typography.Title>
      {SAMPLES.map((e) => (
        <Card key={e.statusCode} size="small" title={`HTTP ${e.statusCode}`}>
          <ApiErrorState error={e} onReload={reload} onRetry={reload} />
        </Card>
      ))}
    </Flex>
  );
}
