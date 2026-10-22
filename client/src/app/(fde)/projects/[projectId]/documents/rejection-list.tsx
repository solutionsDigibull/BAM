"use client";

// The files an upload did not take, each with its reason (E4.10). A file that is a deleted document of this
// project (E4.4) also gets a "Restore it" button, so the FDE gets the document back instead of a copy.
import { Alert, Button } from "antd";

import type { Rejection } from "./upload-session";

type Props = {
  rejections: Rejection[];
  restoring: boolean;
  onRestore: (rejection: Rejection) => void;
};

export function RejectionList({ rejections, restoring, onRestore }: Props) {
  if (rejections.length === 0) return null;
  return (
    <Alert
      type="warning"
      showIcon
      message="Some files were not uploaded"
      description={
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {rejections.map((r, i) => (
            <li key={`${r.name}-${i}`}>
              <strong>{r.name}</strong>: {r.reason}
              {r.restore ? (
                <Button
                  size="small"
                  type="link"
                  disabled={restoring}
                  onClick={() => onRestore(r)}
                  aria-label={`Restore ${r.restore.title}`}
                >
                  Restore it
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      }
    />
  );
}
