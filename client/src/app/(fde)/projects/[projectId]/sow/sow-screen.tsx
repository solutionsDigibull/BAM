"use client";

// The SOW screen (E6.5, TD §9 "SOW"): review and edit the generated SOW, open citations, browse and compare
// versions, regenerate, export. Saving is explicit and always creates a new version (If-Match + base
// version); nothing autosaves. The draft lives in memory: a conflict, a 423 or an expired session never
// throws it away, and leaving with unsaved edits asks first.
import { useCustom, useCustomMutation, useList } from "@refinedev/core";
import type { JSONContent } from "@tiptap/react";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Dropdown,
  Row,
  Space,
  Tabs,
  Tooltip,
  Typography,
} from "antd";
import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";

import {
  ApiErrorState,
  EmptyState,
  HashChip,
  LoadingState,
  StatusBadge,
} from "@/components/ui-kit";
import type {
  Project,
  RunAccepted,
  SowOverview,
  SowVersionDetail,
  SowVersionList,
  SowVersionSummary,
} from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { PROJECTS_RESOURCE } from "../../workspace-api";
import {
  APPROVED_BANNER,
  EMPTY_SOW,
  EXPORT_FORMATS,
  GENERATING,
  MAX_TIPTAP_BYTES,
  NEW_VERSION_SOON,
  NO_CHANGES,
  POLL_MS,
  SAVED,
  SUPERSEDED_BANNER,
  exportUrl,
  fieldProblems,
  isLocked,
  newKey,
  newest,
  regenerateErrorMessage,
  regeneratePath,
  saveErrorMessage,
  sowPath,
  statusOf,
  tiptapBytes,
  versionPath,
  versionsPath,
} from "./sow-api";
import type { CiteClick } from "./sow-editor";
import {
  CitationPanel,
  ConflictModal,
  DiffDrawer,
  RegenerateModal,
  VersionsPanel,
} from "./sow-panels";
import { isSowDoc } from "./sow-schema";

// TipTap needs the DOM: client only.
const SowEditor = dynamic(() => import("./sow-editor").then((m) => m.SowEditor), {
  ssr: false,
  loading: () => <LoadingState />,
});

const textOf = (doc: JSONContent | null): string => {
  const out: string[] = [];
  const walk = (n: JSONContent | undefined) => {
    if (!n) return;
    if (n.text) out.push(n.text);
    n.content?.forEach(walk);
    if (n.type === "paragraph" || n.type === "heading") out.push("\n");
  };
  walk(doc ?? undefined);
  return out.join("");
};

export function SowScreen({ projectId }: { projectId: string }) {
  const { message, modal } = App.useApp();
  const projects = useList<Project, ApiError>({
    resource: PROJECTS_RESOURCE,
    pagination: { mode: "off" },
  });
  const project = projects.result.data?.find((p) => p.id === projectId);

  const [regeneratingFrom, setRegeneratingFrom] = useState<number | null>(null);
  const generating = project?.pipeline_state === "SOW_GENERATING" || regeneratingFrom !== null;
  const poll = { refetchInterval: generating ? POLL_MS : (false as const) };

  const overview = useCustom<SowOverview, ApiError>({
    url: sowPath(projectId),
    method: "get",
    queryOptions: { ...poll, retry: false },
  });
  const versions = useCustom<SowVersionList, ApiError>({
    url: versionsPath(projectId),
    method: "get",
    queryOptions: { ...poll, enabled: Boolean(overview.result.data) },
  });
  const current = overview.result.data?.current ?? undefined;
  const items: SowVersionSummary[] = versions.result.data?.items ?? [];

  const [shown, setShown] = useState<number | undefined>();
  const shownVersion = shown ?? current?.version;
  const detail = useCustom<SowVersionDetail, ApiError>({
    url: versionPath(projectId, shownVersion ?? 0),
    method: "get",
    queryOptions: { enabled: shownVersion !== undefined },
  });

  const [draft, setDraft] = useState<JSONContent | null>(null);
  const [reload, setReload] = useState(0); // remounts the editor on the saved content
  const [lockedByServer, setLockedByServer] = useState(false);
  const [cite, setCite] = useState<CiteClick | null>(null);
  const [tab, setTab] = useState("citations");
  const [conflict, setConflict] = useState<{ base: number } | null>(null);
  const [regenOpen, setRegenOpen] = useState(false);
  const [diffOpen, setDiffOpen] = useState(false);
  const save = useCustomMutation<SowVersionSummary, ApiError>();
  const regenerate = useCustomMutation<RunAccepted, ApiError>();
  const saving = useRef(false);

  // A regeneration is done once a newer version is current (derived while rendering, then announced).
  const [ready, setReady] = useState<number | null>(null);
  if (regeneratingFrom !== null && current && current.version > regeneratingFrom) {
    setRegeneratingFrom(null);
    setShown(undefined);
    setReady(current.version);
  }
  useEffect(() => {
    if (ready !== null) void message.success(`v${ready} is ready.`);
  }, [ready, message]);

  // Unsaved edits: the browser asks before leaving the page.
  useEffect(() => {
    if (!draft) return;
    const guard = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [draft]);

  const discardFirst = (then: () => void) => {
    if (!draft) return then();
    modal.confirm({
      title: "Discard unsaved edits?",
      content: "Your changes to this version are not saved.",
      okText: "Discard",
      okButtonProps: { danger: true },
      onOk: () => {
        setDraft(null);
        then();
      },
    });
  };

  const refetchAll = () => {
    void overview.query.refetch();
    void versions.query.refetch();
  };

  const onSave = () => {
    if (!draft || !current || saving.current) return;
    if (tiptapBytes(draft) > MAX_TIPTAP_BYTES) {
      void message.error("The SOW is larger than 2 MB. Shorten it before saving.");
      return;
    }
    saving.current = true;
    save.mutate(
      {
        url: versionsPath(projectId),
        method: "post",
        values: { content_tiptap: draft, base_version: current.version },
        config: { headers: { "if-match": overview.result.data?.etag ?? "" } },
      },
      {
        onSuccess: (res) => {
          const saved = res.data;
          if (saved.version === current.version) void message.info(NO_CHANGES);
          else void message.success(SAVED(saved.version));
          setDraft(null);
          setShown(undefined);
          setReload((n) => n + 1);
          refetchAll();
        },
        onError: (error) => {
          const status = statusOf(error);
          if (status === 409) {
            // Stay on the version being edited, so the draft stays on screen until "Reload".
            setShown(current.version);
            setConflict({ base: current.version });
            refetchAll();
          } else if (status === 423) {
            setLockedByServer(true);
            void message.warning(saveErrorMessage(error));
          } else if (status === 401) {
            void message.warning(
              "Your session expired. Sign in again in another tab, then press Save: your edits are kept.",
            );
          } else {
            const lines = fieldProblems(error);
            void message.error(
              lines.length
                ? `${saveErrorMessage(error)} ${lines.slice(0, 3).join("; ")}`
                : saveErrorMessage(error),
            );
          }
        },
        onSettled: () => {
          saving.current = false;
        },
      },
    );
  };

  const onRegenerate = (hint: string) => {
    regenerate.mutate(
      {
        url: regeneratePath(projectId),
        method: "post",
        values: hint.trim() ? { hint: hint.trim() } : {},
        config: { headers: { "idempotency-key": newKey() } },
      },
      {
        onSuccess: () => {
          setRegenOpen(false);
          setDraft(null);
          setRegeneratingFrom(current?.version ?? 0);
          void message.success("Regeneration started.");
          refetchAll();
        },
        onError: (error) => void message.error(regenerateErrorMessage(error)),
      },
    );
  };

  const openVersion = (v: number) =>
    discardFirst(() => {
      setShown(v === current?.version ? undefined : v);
      setCite(null);
    });

  // --- states ---------------------------------------------------------------------------------------
  if (overview.query.isLoading) return <LoadingState />;
  if (overview.query.error) {
    if (statusOf(overview.query.error) === 404)
      return generating ? (
        <Alert type="info" showIcon message={GENERATING} data-testid="sow-generating" />
      ) : (
        <EmptyState message={EMPTY_SOW} />
      );
    return (
      <ApiErrorState error={overview.query.error} onRetry={() => void overview.query.refetch()} />
    );
  }
  if (!current) return <EmptyState message={EMPTY_SOW} />;

  const version = detail.result.data;
  const isCurrent = shownVersion === current.version;
  const locked = isLocked(version?.status) || (isCurrent && lockedByServer);
  const failed = version?.status === "GENERATION_FAILED";
  const editable = Boolean(version) && isCurrent && !locked && !failed && !generating;
  const latestNumber = newest(items)?.version ?? current.version;

  const regenerateButton = (
    <Button
      onClick={() => discardFirst(() => setRegenOpen(true))}
      disabled={generating || isLocked(current.status)}
    >
      Regenerate
    </Button>
  );

  return (
    <div data-testid="sow-screen">
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 12 }} wrap>
        <Space>
          <Typography.Title level={4} style={{ margin: 0 }}>
            SOW v{shownVersion}
          </Typography.Title>
          {version ? <StatusBadge status={version.status} /> : null}
          {version?.sha256 ? <HashChip value={`sha256:${version.sha256}`} /> : null}
          {draft ? <Typography.Text type="warning">Unsaved changes</Typography.Text> : null}
        </Space>
        <Space wrap>
          <Button onClick={() => setDiffOpen(true)} disabled={items.length < 2}>
            Compare
          </Button>
          {regenerateButton}
          <Dropdown
            disabled={!version || failed}
            menu={{
              items: EXPORT_FORMATS.map((f) => ({
                key: f.key,
                label: (
                  <a href={exportUrl(projectId, shownVersion ?? 1, f.key)} download>
                    {f.label}
                  </a>
                ),
              })),
            }}
          >
            <Button>Export</Button>
          </Dropdown>
          <Button
            type="primary"
            onClick={onSave}
            disabled={!draft || !editable}
            loading={save.mutation.isPending}
          >
            Save
          </Button>
        </Space>
      </Space>

      {generating ? (
        <Alert
          type="info"
          showIcon
          message={GENERATING}
          style={{ marginBottom: 12 }}
          data-testid="sow-generating"
        />
      ) : null}
      {locked ? (
        <Alert
          type="success"
          showIcon
          data-testid="sow-locked"
          style={{ marginBottom: 12 }}
          message={version?.status === "SUPERSEDED" ? SUPERSEDED_BANNER : APPROVED_BANNER}
          action={
            <Tooltip title={NEW_VERSION_SOON}>
              <Button size="small" disabled>
                Create new version
              </Button>
            </Tooltip>
          }
        />
      ) : null}
      {!isCurrent ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={`Viewing v${shownVersion} (read-only). The current version is v${current.version}.`}
          action={
            <Button size="small" onClick={() => openVersion(current.version)}>
              Open current
            </Button>
          }
        />
      ) : null}

      <Row gutter={16}>
        <Col xs={24} lg={16}>
          <Card size="small">
            {detail.query.isLoading ? (
              <LoadingState />
            ) : detail.query.error ? (
              <ApiErrorState
                error={detail.query.error}
                onRetry={() => void detail.query.refetch()}
              />
            ) : failed ? (
              <Alert
                type="error"
                showIcon
                data-testid="sow-failed"
                message="SOW generation failed"
                description={`Reason: ${version?.error_reason ?? "unknown"}. Regenerate to try again.`}
                action={regenerateButton}
              />
            ) : version && isSowDoc(version.content_tiptap) ? (
              <SowEditor
                key={`${shownVersion}-${reload}`}
                content={version.content_tiptap as JSONContent}
                editable={editable}
                onChange={setDraft}
                onCite={(c) => {
                  setCite(c);
                  setTab("citations");
                }}
              />
            ) : (
              <Alert
                type="warning"
                showIcon
                message="This version cannot be shown in the editor."
              />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card size="small">
            <Tabs
              activeKey={tab}
              onChange={setTab}
              items={[
                {
                  key: "citations",
                  label: "Citations",
                  children: (
                    <CitationPanel projectId={projectId} version={shownVersion ?? 1} cite={cite} />
                  ),
                },
                {
                  key: "versions",
                  label: `Versions (${items.length})`,
                  children: (
                    <VersionsPanel versions={items} shown={shownVersion} onOpen={openVersion} />
                  ),
                },
              ]}
            />
          </Card>
        </Col>
      </Row>

      <DiffDrawer
        key={items.length}
        projectId={projectId}
        versions={items}
        open={diffOpen}
        onClose={() => setDiffOpen(false)}
      />
      <RegenerateModal
        open={regenOpen}
        busy={regenerate.mutation.isPending}
        onCancel={() => setRegenOpen(false)}
        onRegenerate={onRegenerate}
      />
      {conflict ? (
        <ConflictModal
          projectId={projectId}
          base={conflict.base}
          latest={latestNumber}
          open
          onClose={() => setConflict(null)}
          onCopy={() => {
            void navigator.clipboard?.writeText(textOf(draft));
            void message.success("Your edits were copied to the clipboard.");
          }}
          onReload={() => {
            setConflict(null);
            setDraft(null);
            setShown(undefined);
            setReload((n) => n + 1);
            refetchAll();
            void detail.query.refetch();
          }}
        />
      ) : null}
    </div>
  );
}
