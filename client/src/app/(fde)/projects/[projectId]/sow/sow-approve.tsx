"use client";

// SOW approval (E7.3, TD §9.3): the confirmation the FDE signs, the approved banner, and the answer to a
// stale approval (E7.1: POST P/sow/versions/{v}/approve {sha256}). A scope change (E7.2) starts with
// "Create new version" and a reason; its impact preview is the API's own (GET …/{v}/impact), never a
// diff worked out here.
import { Alert, Button, Descriptions, Input, Modal, Table, Tag, Typography } from "antd";
import { useState } from "react";

import { ApiErrorState, HashChip, LoadingState } from "@/components/ui-kit";
import type { SowApproval, SowImpact, SowImpactSubSkill } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { MAX_REASON, NO_REBUILD, STALE_TITLE, impactSummary, reasonProblem } from "./sow-api";
import { SectionChanges } from "./sow-panels";

export function ApproveDialog({
  open,
  version,
  sha256,
  impact,
  busy,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  version: number;
  sha256: string;
  /** a scope change's preview: what approving invalidates */
  impact?: SowImpact;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal
      open={open}
      title={`Approve SOW v${version}?`}
      okText="Approve"
      confirmLoading={busy}
      // a second click while the first is in flight sends nothing (E7.3-T05)
      okButtonProps={{ disabled: busy, "aria-label": "Confirm approval" }}
      cancelButtonProps={{ disabled: busy }}
      onOk={onConfirm}
      onCancel={onCancel}
      maskClosable={!busy}
      destroyOnHidden
    >
      <Descriptions size="small" column={1} style={{ marginBottom: 12 }}>
        <Descriptions.Item label="Version">v{version}</Descriptions.Item>
        <Descriptions.Item label="Content hash">
          <HashChip value={`sha256:${sha256}`} />
        </Descriptions.Item>
      </Descriptions>
      <Typography.Paragraph>
        You approve exactly this content: the hash above is what is signed. The version is then
        locked, and SBAM generates the Workflow from it.
      </Typography.Paragraph>
      {impact ? (
        <Alert
          type="warning"
          showIcon
          data-testid="approve-impact"
          message={`Scope change from v${impact.base_version}: ${impactSummary(impact)}.`}
          description="The Impact tab lists them."
        />
      ) : (
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          A later scope change starts a new version; its impact on the built Sub-Skills is shown
          then.
        </Typography.Paragraph>
      )}
    </Modal>
  );
}

/** "Create new version": a scope change starts from the approved SOW, with a reason (E7.2). */
export function NewVersionModal({
  open,
  approved,
  busy,
  onCreate,
  onCancel,
}: {
  open: boolean;
  approved: number;
  busy: boolean;
  onCreate: (reason: string) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const problem = reasonProblem(reason);
  return (
    <Modal
      open={open}
      title="Create a new version"
      okText="Create"
      confirmLoading={busy}
      okButtonProps={{ disabled: Boolean(problem) || busy }}
      cancelButtonProps={{ disabled: busy }}
      onOk={() => !problem && onCreate(reason.trim())}
      onCancel={onCancel}
      maskClosable={!busy}
      destroyOnHidden
    >
      <Typography.Paragraph>
        The new version starts as a copy of approved v{approved}. Edit it, check its impact, then
        approve it.
      </Typography.Paragraph>
      <Input.TextArea
        aria-label="Reason for the scope change"
        placeholder="Why does the scope change?"
        rows={3}
        showCount
        count={{ max: MAX_REASON }}
        value={reason}
        status={touched && problem ? "error" : undefined}
        onChange={(e) => {
          setReason(e.target.value);
          setTouched(true);
        }}
      />
      {touched && problem ? (
        <Typography.Text type="danger" data-testid="reason-problem">
          {problem}
        </Typography.Text>
      ) : null}
    </Modal>
  );
}

/** The impact preview of a scope-change draft, exactly as the API computed it. */
export function ImpactPanel({
  impact,
  loading,
  error,
  onRetry,
}: {
  impact: SowImpact | undefined;
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
}) {
  if (loading) return <LoadingState />;
  if (error) return <ApiErrorState error={error} onRetry={onRetry} />;
  if (!impact) return null;
  const changed = impact.changed_sections.filter((s) => s.change !== "UNCHANGED");
  return (
    <div data-testid="sow-impact">
      <Typography.Paragraph strong>
        v{impact.version} against approved v{impact.base_version}: {impactSummary(impact)}.
      </Typography.Paragraph>
      <Typography.Title level={5}>Changed sections</Typography.Title>
      {changed.length ? (
        <SectionChanges sections={changed} />
      ) : (
        <Typography.Text type="secondary">No section changed yet.</Typography.Text>
      )}
      {impact.added_modules.length ? (
        <>
          <Typography.Title level={5}>New modules</Typography.Title>
          {impact.added_modules.map((m) => (
            <Tag key={m} color="green" data-module={m}>
              {m}
            </Tag>
          ))}
        </>
      ) : null}
      <Typography.Title level={5}>Macro Skills affected</Typography.Title>
      {impact.affected_macro_skills.length ? (
        <ul data-testid="impact-macros" style={{ paddingLeft: 20 }}>
          {impact.affected_macro_skills.map((m) => (
            <li key={m.macro_category}>
              <Typography.Text code>{m.macro_category}</Typography.Text> {m.reason}
            </li>
          ))}
        </ul>
      ) : (
        <Typography.Text type="secondary">None.</Typography.Text>
      )}
      <Typography.Title level={5}>Sub-Skills to rebuild</Typography.Title>
      {impact.affected_sub_skills.length ? (
        <Table<SowImpactSubSkill>
          data-testid="impact-sub-skills"
          size="small"
          rowKey="skill_id"
          virtual // 300 rows render only what fits
          scroll={{ y: 360 }}
          pagination={false}
          dataSource={impact.affected_sub_skills}
          columns={[
            { title: "Sub-Skill", dataIndex: "skill_id", width: 140 },
            { title: "Reason", dataIndex: "reason" },
          ]}
        />
      ) : (
        <Alert type="success" showIcon message={NO_REBUILD} data-testid="impact-none" />
      )}
      <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
        {impact.reused_sub_skills_count} Sub-Skills are kept as built.
      </Typography.Paragraph>
    </div>
  );
}

export function StaleApprovalModal({
  open,
  version,
  onReload,
  onClose,
}: {
  open: boolean;
  version: number;
  onReload: () => void;
  onClose: () => void;
}) {
  return (
    <Modal
      open={open}
      title={STALE_TITLE}
      onCancel={onClose}
      footer={[
        <Button key="reload" type="primary" onClick={onReload}>
          Reload
        </Button>,
      ]}
      destroyOnHidden
    >
      <Alert
        type="warning"
        showIcon
        message={`v${version} was not approved: the SOW changed after you opened it.`}
        description="Reload to review the latest version, then approve that one."
      />
    </Modal>
  );
}

/** Who approved, when and which content, from the approval the API answered. */
export function ApprovalDetails({ approval }: { approval: SowApproval }) {
  return (
    <span data-testid="sow-approval">
      v{approval.version} approved{approval.approved_by ? ` by ${approval.approved_by}` : ""} on{" "}
      {new Date(approval.approved_at).toLocaleString()} ·{" "}
      <HashChip value={`sha256:${approval.sha256}`} />
    </span>
  );
}
