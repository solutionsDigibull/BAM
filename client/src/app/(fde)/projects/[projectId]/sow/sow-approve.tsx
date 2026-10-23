"use client";

// SOW approval (E7.3, TD §9.3): the confirmation the FDE signs, the approved banner, and the answer to a
// stale approval. The API is E7.1 (POST P/sow/versions/{v}/approve {sha256}); the impact preview of a
// scope change arrives with E7.2's impact endpoint (#95).
import { Alert, Button, Descriptions, Modal, Typography } from "antd";

import { HashChip } from "@/components/ui-kit";
import type { SowApproval } from "@/contracts";

import { STALE_TITLE } from "./sow-api";

export function ApproveDialog({
  open,
  version,
  sha256,
  busy,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  version: number;
  sha256: string;
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
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
        A later scope change starts a new version; its impact on the built Sub-Skills is shown then.
      </Typography.Paragraph>
    </Modal>
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
