"use client";

// Tells the user, before they start a build, that something a build depends on is down: a refused LLM key
// found at the end of a five-minute knowledge build was the reason for this. Silent while everything works.
import { useCustom } from "@refinedev/core";
import { Alert } from "antd";

import type { Preflight, PreflightCheck } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

export const PREFLIGHT_PATH = "preflight";
const REFRESH_MS = 60_000; // the API caches for a minute too

const problems = (checks: PreflightCheck[] | undefined, status: PreflightCheck["status"]) =>
  (checks ?? []).filter((c) => c.status === status);

export function PreflightBanner() {
  const report = useCustom<Preflight, ApiError>({
    url: PREFLIGHT_PATH,
    method: "get",
    queryOptions: { refetchInterval: REFRESH_MS, retry: false },
  });
  const data = report.result.data;
  if (report.query.error || !data || !Array.isArray(data.checks)) return null;
  const down = problems(data.checks, "down");
  const degraded = problems(data.checks, "degraded");
  const mock = problems(data.checks, "mock");
  const [first, ...rest] = down.length ? down : degraded.length ? degraded : mock;
  if (!first) return null;
  const type = down.length ? "error" : degraded.length ? "warning" : "info";
  const title = down.length
    ? "Builds will fail until this is fixed"
    : degraded.length
      ? "Something is slow or limited"
      : "The mock LLM is on: results are synthetic";
  return (
    <Alert
      showIcon
      type={type}
      data-testid="preflight"
      message={title}
      description={
        <>
          {[first, ...rest].map((c) => (
            <div key={c.id}>
              <strong>{c.name}.</strong> {c.fix ?? c.reason}
            </div>
          ))}
        </>
      }
    />
  );
}
