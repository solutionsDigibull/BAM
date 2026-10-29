"use client";

import { CheckOutlined, CopyOutlined } from "@ant-design/icons";
import { Button, Input, Tag, Tooltip } from "antd";
import { useState } from "react";

const HASH = /^(sha256:)?([0-9a-f]{64})$/;

export function HashChip({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const [manual, setManual] = useState(false); // no clipboard (plain http, permission denied)
  const match = HASH.exec(value);
  if (!match) return <Tag color="error">invalid hash</Tag>;
  const hex = match[2] ?? "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setManual(true); // show the full value selected, to copy by hand
    }
  };

  if (manual)
    return (
      <Input
        readOnly
        size="small"
        aria-label="Hash to copy"
        value={value}
        autoFocus
        onFocus={(e) => e.currentTarget.select()}
        onBlur={() => setManual(false)}
        style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", width: 560 }}
      />
    );

  return (
    <Tooltip title={value}>
      <Tag style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>
        {hex.slice(0, 8)}
        <Button
          type="text"
          size="small"
          aria-label="Copy hash"
          icon={copied ? <CheckOutlined /> : <CopyOutlined />}
          onClick={copy}
          style={{ marginLeft: 4, height: 18, width: 18, minWidth: 18 }}
        />
      </Tag>
    </Tooltip>
  );
}
