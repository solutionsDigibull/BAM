"use client";

import { Typography } from "antd";
import type { ReactNode } from "react";
import Markdown, { type Components } from "react-markdown";

type MdNode = { type: string; value?: string; children?: MdNode[] };

/** remark plugin: raw HTML becomes literal text, so `<script>` shows as typed and never runs. */
function htmlAsText() {
  const walk = (node: MdNode) => {
    if (node.type === "html") node.type = "text";
    node.children?.forEach(walk);
  };
  return (tree: MdNode) => walk(tree);
}

// Links open in a new tab without referrer; images aren't fetched (no tracking pixels), their alt shows.
const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow">
      {children}
    </a>
  ),
  img: ({ alt }) => <span>{alt ? `[image: ${alt}]` : "[image]"}</span>,
};

/**
 * Sanitised preview of context notes (E3.9). react-markdown builds React elements (no innerHTML) and its
 * default URL filter drops `javascript:` and other unsafe links; raw HTML is shown as text.
 */
export function MarkdownPreview({ source }: { source: string | null | undefined }): ReactNode {
  if (!source?.trim()) {
    return <Typography.Text type="secondary">Nothing to preview yet.</Typography.Text>;
  }
  return (
    <div
      data-testid="markdown-preview"
      style={{ lineHeight: 1.6, overflowWrap: "anywhere", fontSize: 14 }}
    >
      <Markdown remarkPlugins={[htmlAsText]} components={components}>
        {source}
      </Markdown>
    </div>
  );
}
