"use client";

// The upload panel of the Documents screen (E4.10, TD §8.5): an Uppy Dashboard that uploads to tusd with a
// token from the API, and the list of files that were refused with their reasons. Client-only (Uppy needs
// the browser), loaded with `dynamic(..., { ssr: false })` by documents-screen.tsx. The documents list and
// the stage chips (#77) sit beside this panel, not in it.
import "@uppy/core/css/style.min.css";
import "@uppy/dashboard/css/style.min.css";

import Dashboard from "@uppy/dashboard";
import { useCustomMutation } from "@refinedev/core";
import { Alert, App, Flex, Typography } from "antd";
import { useEffect, useRef, useState } from "react";

import type { DocumentOut, UploadRequest, UploadToken } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { restorePath, uploadsPath } from "../../workspace-api";
import { restoreErrorMessage } from "./document-state";
import { RejectionList } from "./rejection-list";
import {
  DUPLICATE_NOTE,
  NOTE,
  createUploader,
  type Rejection,
  type SbamUppy,
} from "./upload-session";

type Props = {
  projectId: string;
  /** Documents of the project that count toward the 20-file limit. */
  active: number;
  /** Every document of the project, deleted ones included (a file in the recycle bin is not re-uploaded). */
  documents: DocumentOut[];
  /** Called when a batch has finished, so the documents list can refresh. */
  onUploaded: () => void;
};

export default function UploadPanel({ projectId, active, documents, onUploaded }: Props) {
  const { message } = App.useApp();
  const target = useRef<HTMLDivElement>(null);
  const announce = useCustomMutation<UploadToken, ApiError, UploadRequest>();
  const restore = useCustomMutation<DocumentOut, ApiError>();
  const [rejections, setRejections] = useState<Rejection[]>([]);
  const [failure, setFailure] = useState<string | null>(null);

  // The Uppy instance is created once per project; it reads the live values through these refs.
  const live = useRef({ active, documents, onUploaded, send: announce.mutateAsync });
  useEffect(() => {
    live.current = { active, documents, onUploaded, send: announce.mutateAsync };
  });

  const onRestore = (r: Rejection) => {
    if (!r.restore) return;
    const { id, title } = r.restore;
    restore.mutate(
      { url: restorePath(projectId, id), method: "post", values: {} },
      {
        onSuccess: () => {
          void message.success(`${title} restored`);
          setRejections((all) => all.filter((x) => x !== r));
        },
        onError: (error) => void message.error(restoreErrorMessage(error)),
        onSettled: () => live.current.onUploaded(), // restored, or gone for good: the lists changed
      },
    );
  };

  useEffect(() => {
    if (!target.current) return;
    const uppy: SbamUppy = createUploader({
      projectId,
      getActive: () => live.current.active,
      getDocuments: () => live.current.documents,
      announce: async (body) =>
        (await live.current.send({ url: uploadsPath(projectId), method: "post", values: body }))
          .data,
      onRejected: (r) => setRejections((all) => [...all, r]),
      onFailed: (message) => setFailure(message),
    });
    uppy.use(Dashboard, {
      inline: true,
      target: target.current,
      height: 320,
      width: "100%",
      note: NOTE,
      proudlyDisplayPoweredByUppy: false,
    });
    uppy.on("upload", () => {
      setRejections([]);
      setFailure(null);
    });
    uppy.on("complete", () => live.current.onUploaded());
    return () => uppy.destroy();
  }, [projectId]);

  return (
    <Flex vertical gap={12}>
      <div ref={target} data-testid="upload-dashboard" />
      <Typography.Text type="secondary">{DUPLICATE_NOTE}</Typography.Text>
      {failure ? (
        <Alert
          type="error"
          showIcon
          message={failure}
          description="Your files are kept. Retry the upload."
        />
      ) : null}
      <RejectionList
        rejections={rejections}
        restoring={restore.mutation.isPending}
        onRestore={onRestore}
      />
    </Flex>
  );
}
