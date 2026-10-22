"use client";

// The upload panel of the Documents screen (E4.10, TD §8.5): an Uppy Dashboard that uploads to tusd with a
// token from the API, and the list of files that were refused with their reasons. Client-only (Uppy needs
// the browser), loaded with `dynamic(..., { ssr: false })` by documents-screen.tsx. The documents list and
// the stage chips (#77) sit beside this panel, not in it.
import "@uppy/core/css/style.min.css";
import "@uppy/dashboard/css/style.min.css";

import Dashboard from "@uppy/dashboard";
import { useCustomMutation } from "@refinedev/core";
import { Alert, Flex, Typography } from "antd";
import { useEffect, useRef, useState } from "react";

import type { UploadRequest, UploadToken } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { uploadsPath } from "../../workspace-api";
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
  /** Called when a batch has finished, so the documents list can refresh. */
  onUploaded: () => void;
};

export default function UploadPanel({ projectId, active, onUploaded }: Props) {
  const target = useRef<HTMLDivElement>(null);
  const announce = useCustomMutation<UploadToken, ApiError, UploadRequest>();
  const [rejections, setRejections] = useState<Rejection[]>([]);
  const [failure, setFailure] = useState<string | null>(null);

  // The Uppy instance is created once per project; it reads the live values through these refs.
  const live = useRef({ active, onUploaded, send: announce.mutateAsync });
  useEffect(() => {
    live.current = { active, onUploaded, send: announce.mutateAsync };
  });

  useEffect(() => {
    if (!target.current) return;
    const uppy: SbamUppy = createUploader({
      projectId,
      getActive: () => live.current.active,
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
      {rejections.length > 0 ? (
        <Alert
          type="warning"
          showIcon
          message="Some files were not uploaded"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {rejections.map((r, i) => (
                <li key={`${r.name}-${i}`}>
                  <strong>{r.name}</strong>: {r.reason}
                </li>
              ))}
            </ul>
          }
        />
      ) : null}
    </Flex>
  );
}
