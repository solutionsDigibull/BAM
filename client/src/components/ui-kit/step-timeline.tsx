import { Steps } from "antd";

export const PIPELINE_STEPS = [
  "Upload",
  "Knowledge",
  "SOW",
  "Approve",
  "ISATVON",
  "Build",
  "Certified",
  "Frozen",
] as const;

/** Project pipeline timeline (TD §9.3). `current` is the index of the active step. */
export function StepTimeline({
  current,
  failed = false,
  steps = PIPELINE_STEPS,
}: {
  current: number;
  failed?: boolean;
  steps?: readonly string[];
}) {
  return (
    <Steps
      size="small"
      current={current}
      status={failed ? "error" : "process"}
      items={steps.map((title) => ({ title }))}
    />
  );
}
