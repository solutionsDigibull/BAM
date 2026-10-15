"use client";

import { useCustomMutation } from "@refinedev/core";
import { Alert, Input, Modal, Typography } from "antd";
import { useState } from "react";

import { ApiErrorState } from "@/components/ui-kit";
import type { Project } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { PIPELINE_RUNNING_MESSAGE, archivePath, problemCode } from "./project-api";

/** Archive needs the project name typed back. The API still decides (409 PIPELINE_RUNNING). */
export function ArchiveDialog({
  project,
  onClose,
  onArchived,
}: {
  project: Project;
  onClose: () => void;
  onArchived: (project: Project) => void;
}) {
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const { mutateAsync, mutation } = useCustomMutation<Project, ApiError>();
  const confirmed = typed === project.name;

  const archive = async () => {
    setError(null);
    try {
      const res = await mutateAsync({
        url: archivePath(project.id),
        method: "post",
        values: {},
      });
      onArchived(res.data);
    } catch (e) {
      setError(e as ApiError);
    }
  };

  return (
    <Modal
      open
      title="Archive project"
      okText="Archive"
      okButtonProps={{ danger: true, disabled: !confirmed }}
      confirmLoading={mutation.isPending}
      onOk={archive}
      onCancel={onClose}
      destroyOnHidden
    >
      {error &&
        (problemCode(error) === "PIPELINE_RUNNING" ? (
          <Alert
            type="warning"
            showIcon
            message={PIPELINE_RUNNING_MESSAGE}
            description="The project's documents are being processed. Try again once the pipeline is idle or waiting for review."
            style={{ marginBottom: 16 }}
          />
        ) : (
          <div style={{ marginBottom: 16 }}>
            <ApiErrorState error={error} onRetry={archive} />
          </div>
        ))}
      <Typography.Paragraph>
        Archiving makes{" "}
        <Typography.Text strong ellipsis={{ tooltip: project.name }} style={{ maxWidth: 360 }}>
          {project.name}
        </Typography.Text>{" "}
        read-only. Its documents, SOW and Workflows are kept.
      </Typography.Paragraph>
      <Typography.Paragraph style={{ marginBottom: 8 }}>
        Type the project name to confirm:
      </Typography.Paragraph>
      <Input
        aria-label="Project name to confirm"
        value={typed}
        onChange={(e) => setTyped(e.target.value)}
        onPressEnter={() => confirmed && archive()}
        placeholder={project.name}
        autoFocus
      />
    </Modal>
  );
}
