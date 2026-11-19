// File intake uses Uppy, the same library as BAM_UI. Nothing is uploaded by Uppy itself: the chosen
// files are read in the browser and handed to the step as the tool's argument.
import { useEffect, useMemo, useRef } from "react";
import Uppy from "@uppy/core";
import Dashboard from "@uppy/react/dashboard";
import "@uppy/core/css/style.min.css";
import "@uppy/dashboard/css/style.min.css";

export interface Picked {
  name: string;
  type: string;
  content: string; // base64
}

function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

export function Intake({ id, dark, onPick }: { id: string; dark: boolean; onPick: (files: Picked[]) => void }) {
  const uppy = useMemo(() => new Uppy({ id, autoProceed: false, restrictions: { maxFileSize: 25 * 1024 * 1024 } }), [id]);
  const latest = useRef(onPick);
  latest.current = onPick;
  useEffect(() => {
    const sync = async () => {
      const files = uppy.getFiles();
      latest.current(await Promise.all(files.map(async f => ({ name: f.name ?? "file", type: f.type, content: await toBase64(f.data as Blob) }))));
    };
    uppy.on("file-added", sync);
    uppy.on("file-removed", sync);
    return () => { uppy.destroy(); };
  }, [uppy]);
  return <Dashboard uppy={uppy} width="100%" height={220} theme={dark ? "dark" : "light"} hideUploadButton disableStatusBar proudlyDisplayPoweredByUppy={false} />;
}
