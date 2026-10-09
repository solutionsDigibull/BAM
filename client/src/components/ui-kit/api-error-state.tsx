"use client";

import { Alert, Button, Modal, Result } from "antd";
import { useSyncExternalStore } from "react";

import { ErrorPanel } from "./error-panel";

/** The part of a data-provider error this needs (see ApiError in providers/data-provider.ts). */
export type ApiErrorLike = {
  statusCode: number;
  message?: string;
  errors?: Record<string, unknown>;
  problem?: { title?: string; detail?: string | null; code?: string };
};

export const STALE_MESSAGE =
  "This item changed since you opened it. Reload to see the latest version.";
export const CONFLICT_MESSAGE = "Someone else changed this at the same time. Reload and try again.";

const noop = () => () => {};
// false during SSR and hydration, true after: a portal-based Modal opened on the server can't hydrate.
const useIsClient = () =>
  useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );

/**
 * One UI state per API status (E1.14). 401 never reaches here: the auth provider redirects to login.
 * 422 field errors also land on the form fields themselves (Refine useForm reads `errors`).
 */
export function ApiErrorState({
  error,
  onReload,
  onRetry,
}: {
  error: ApiErrorLike;
  onReload?: () => void;
  onRetry?: () => void;
}) {
  const isClient = useIsClient();
  const { statusCode: status, problem } = error;
  const detail = problem?.detail ?? error.message;
  const reload = onReload && (
    <Button type="primary" onClick={onReload}>
      Reload
    </Button>
  );

  switch (status) {
    case 404:
      return <Result status="404" title="Not found" subTitle={detail} />;
    case 409:
      return (
        <Alert
          type="warning"
          showIcon
          message="Conflict"
          description={CONFLICT_MESSAGE}
          action={reload}
        />
      );
    case 412:
      return (
        <Modal open={isClient} title="Out of date" closable={false} footer={reload ?? null}>
          {STALE_MESSAGE}
        </Modal>
      );
    case 422:
      return (
        <Alert
          type="error"
          showIcon
          message={problem?.title ?? "Check the highlighted fields"}
          description={
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {Object.entries(error.errors ?? {}).map(([field, msgs]) => (
                <li key={field}>
                  {field}: {[msgs].flat().join(", ")}
                </li>
              ))}
            </ul>
          }
        />
      );
    case 503:
      return (
        <Alert
          type="warning"
          showIcon
          message="Service temporarily unavailable"
          description={detail}
          action={onRetry && <Button onClick={onRetry}>Retry</Button>}
        />
      );
    default:
      return (
        <ErrorPanel
          problem={{
            status,
            title: problem?.title,
            detail: detail ?? undefined,
            code: problem?.code,
          }}
        />
      );
  }
}
