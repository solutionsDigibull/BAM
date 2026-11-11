"use client";

// The default Build & Test view: one card per Sub-Skill, in words. What state it is in, what that means,
// which gates failed (click for the findings) and the buttons that make sense right now. The 19-column
// matrix stays available as "Matrix" for people who want every gate at once; it needs horizontal
// scrolling, which is why it is not the first thing you see.
import { useCustom } from "@refinedev/core";
import { Button, Card, Flex, Progress, Tag, Typography } from "antd";

import type { BuildRow, GateName, GateStatus, HeldPatchResponse } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import type { GridActions } from "./build-grid";
import {
  canRetry,
  GATE_LABEL,
  GATES,
  gateStatus,
  heldOf,
  heldPatchUrl,
  MAX_ATTEMPT,
  type GateFamily,
} from "./build-api";

const STATE_COLOR: Record<string, string> = {
  BUILDING: "processing",
  CORRECTING: "processing",
  TESTING: "processing",
  NEEDS_FDE_REVIEW: "warning",
  TESTING_BLOCKED: "warning",
  BUILD_ERROR: "error",
  DEPENDENCY_FAILED: "error",
  CERTIFIED: "purple",
  FROZEN: "purple",
  VALID: "green",
};

const FAMILIES: { family: GateFamily; label: string }[] = [
  { family: "Contract", label: "Contract" },
  { family: "AST", label: "Code structure" },
  { family: "Static", label: "Static checks" },
  { family: "Tests", label: "Loop tests" },
];

/** What the state means and what to do, in one or two sentences. */
export function explain(
  row: BuildRow,
  failed: string[],
): { text: string; type: "info" | "warning" | "error" | "success" } {
  const loop = row.loop_tests;
  const tests = loop ? ` Loop tests: ${loop.passed} of ${loop.total} passed.` : "";
  switch (row.state) {
    case "BUILDING":
      return {
        text: `Generating and checking the code (${row.stage.replaceAll("_", " ")}).`,
        type: "info",
      };
    case "TESTING":
      return { text: `Running the loop tests in the sandbox.${tests}`, type: "info" };
    case "CORRECTING":
      return {
        text: `A check failed, so SBAM is correcting the code (attempt ${(row.attempt ?? 0) + 1} of ${MAX_ATTEMPT + 1}).`,
        type: "info",
      };
    case "NEEDS_FDE_REVIEW":
      return {
        text:
          `Automatic correction stopped and needs you.${failed.length ? ` Failed: ${failed.join(", ")}.` : ""}${tests}` +
          " Open the failed checks or the test results to see why. The ISATVON cannot be edited while a build is waiting, so fix the test cases or decide a proposed patch, then Retry. Retry asks the corrector again, now with the database schema in front of it.",
        type: "warning",
      };
    case "TESTING_BLOCKED":
      return {
        text: "The sandbox could not run the tests (for example it was unreachable). Nothing is wrong with the code; Retry re-runs only the tests.",
        type: "warning",
      };
    case "BUILD_ERROR":
      return {
        text: "A platform problem stopped the build, not your Sub-Skill. Ask an admin to check the build worker, then Retry.",
        type: "error",
      };
    case "DEPENDENCY_FAILED":
      return {
        text: "A library the code needs is missing from the runtime. Ask an admin to add it, then Retry.",
        type: "error",
      };
    case "CERTIFIED":
    case "FROZEN":
      return { text: `All checks passed.${tests}`, type: "success" };
    default:
      return { text: row.state, type: "info" };
  }
}

function GateSummary({ row, onFindings }: { row: BuildRow; onFindings: (g: GateName) => void }) {
  return (
    <Flex vertical gap={8}>
      {FAMILIES.map(({ family, label }) => {
        const gates = GATES.filter((g) => g.family === family);
        const status = (g: GateName): GateStatus => gateStatus(row, g);
        const passed = gates.filter((g) => status(g.name) === "PASSED").length;
        const failed = gates.filter((g) => status(g.name) === "FAILED");
        const running = gates.some((g) => status(g.name) === "RUNNING");
        return (
          <Flex key={family} gap={12} align="center" wrap>
            <Typography.Text style={{ width: 120 }}>{label}</Typography.Text>
            <Progress
              style={{ width: 160, margin: 0 }}
              size="small"
              percent={Math.round((passed / gates.length) * 100)}
              status={failed.length ? "exception" : running ? "active" : undefined}
              format={() => `${passed}/${gates.length}`}
            />
            {failed.map((g) => (
              <Button
                key={g.name}
                size="small"
                danger
                aria-label={`Findings for ${row.skill_id} ${g.label}: Failed`}
                onClick={() => onFindings(g.name)}
              >
                {GATE_LABEL[g.name]} failed: see why
              </Button>
            ))}
          </Flex>
        );
      })}
    </Flex>
  );
}

function SubSkillCard({
  projectId,
  workflowId,
  row,
  actions,
}: {
  projectId: string;
  workflowId: string;
  row: BuildRow;
  actions: GridActions;
}) {
  const review = row.state === "NEEDS_FDE_REVIEW";
  // Only offer "Review patch" when a held patch exists: the button used to open a drawer that said
  // "No ISATVON patch is waiting", because a review is usually about failed tests, not a patch.
  const held = useCustom<HeldPatchResponse, ApiError>({
    url: heldPatchUrl(projectId, workflowId, row.sub_skill_id),
    method: "get",
    queryOptions: { enabled: review, retry: false },
  });
  const hasPatch = review && heldOf(held.result.data) !== null;
  const failed = GATES.filter((g) => gateStatus(row, g.name) === "FAILED").map((g) => g.label);
  const note = explain(row, failed);
  const allowed = canRetry(row.state);
  return (
    <Card
      size="small"
      style={review ? { borderColor: "#fcd34d" } : undefined}
      title={
        <Flex gap={8} align="center" wrap>
          <Typography.Text strong>{row.skill_id}</Typography.Text>
          <Typography.Text type="secondary">{row.skill_name}</Typography.Text>
        </Flex>
      }
      extra={
        <Flex gap={8} align="center">
          <Typography.Text type="secondary">
            Attempt {(row.attempt ?? 0) + 1} of {MAX_ATTEMPT + 1}
          </Typography.Text>
          <Tag color={STATE_COLOR[row.state] ?? "default"} style={{ margin: 0 }}>
            {row.state}
          </Tag>
        </Flex>
      }
    >
      <Flex vertical gap={16}>
        <Typography.Paragraph
          style={{ margin: 0 }}
          type={note.type === "error" ? "danger" : note.type === "warning" ? "warning" : undefined}
        >
          {note.text}
        </Typography.Paragraph>
        <GateSummary row={row} onFindings={(g) => actions.onFindings(row, g)} />
        <Flex gap={8} wrap>
          <Button aria-label={`Tests ${row.skill_id}`} onClick={() => actions.onTests(row)}>
            Test results
          </Button>
          {hasPatch ? (
            <Button
              aria-label={`Review patch ${row.skill_id}`}
              onClick={() => actions.onPatch(row)}
            >
              Review proposed patch
            </Button>
          ) : null}
          <Button
            type={allowed ? "primary" : "default"}
            disabled={!allowed}
            aria-label={`Retry ${row.skill_id}`}
            title={
              allowed ? "Retry after your fix" : "Retry is offered when the build waits for you"
            }
            onClick={() => actions.onRetry(row)}
          >
            Retry
          </Button>
        </Flex>
      </Flex>
    </Card>
  );
}

export function BuildCards({
  projectId,
  workflowId,
  rows,
  actions,
}: {
  projectId: string;
  workflowId: string;
  rows: BuildRow[];
  actions: GridActions;
}) {
  return (
    <Flex vertical gap={12} data-testid="build-cards">
      {rows.map((row) => (
        <SubSkillCard
          key={row.sub_skill_id}
          projectId={projectId}
          workflowId={workflowId}
          row={row}
          actions={actions}
        />
      ))}
    </Flex>
  );
}
