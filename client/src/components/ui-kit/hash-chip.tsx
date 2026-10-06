"use client";

import { CheckOutlined, CopyOutlined } from "@ant-design/icons";
import { Button, Tag, Tooltip } from "antd";
import { useState } from "react";

const HASH = /^(sha256:)?([0-9a-f]{64})$/;

export function HashChip({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const match = HASH.exec(value);
  if (!match) return <Tag color="error">invalid hash</Tag>;
  const hex = match[2] ?? "";

  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

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
