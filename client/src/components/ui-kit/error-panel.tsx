import { Alert, Result } from "antd";
import type { ReactNode } from "react";

/** RFC 9457 problem+json, as returned by the SBAM API (TD §8). */
export type Problem = { status: number; title?: string; detail?: string; code?: string };

export const LOCKED_MESSAGE = "This item is frozen / approved — create a new version to change it.";

export function ErrorPanel({ problem }: { problem: Problem }) {
  if (problem.status === 423) {
    return <Result status="warning" title="Locked" subTitle={LOCKED_MESSAGE} />;
  }
  if (problem.status === 403) {
    return <Forbidden />;
  }
  return (
    <Alert
      type="error"
      showIcon
      message={problem.title ?? "Something went wrong"}
      description={problem.detail}
    />
  );
}

export function Forbidden({ hint, extra }: { hint?: string; extra?: ReactNode }) {
  return (
    <Result
      status="403"
      title="Forbidden"
      subTitle={hint ?? "You don't have access to this page."}
      extra={extra}
    />
  );
}
