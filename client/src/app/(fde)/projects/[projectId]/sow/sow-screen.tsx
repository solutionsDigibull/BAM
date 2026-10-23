"use client";

// The SOW screen (E6.5, TD §9 "SOW"): review and edit the generated SOW, open citations, browse and compare
// versions, regenerate, export. Saving is explicit and always creates a new version (If-Match + base
// version); nothing autosaves. A conflict, a 423 or an expired session never throws the draft away: it is
// also kept in this tab's sessionStorage (sow-draft.ts) and offered back after a sign-in. Leaving with unsaved
// edits, by link, tab close or reload, asks first.
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
import { useRouter } from "next/navigation";
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
  SowApproval,
  SowOverview,
  SowVersionDetail,
  SowVersionList,
  SowVersionSummary,
} from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { PROJECTS_RESOURCE } from "../../workspace-api";
import {
  APPROVED_BANNER,
  approveBlocked,
  approveErrorMessage,
  approveFailure,
  approvePath,
  EMPTY_SOW,
  EXPORT_FORMATS,
  GENERATING,
  MAX_TIPTAP_BYTES,
  NEW_VERSION_SOON,
  NO_CHANGES,
  ORCHESTRATOR_DOWN,
  POLL_MS,
  SAVE_FIRST,
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
import { ApprovalDetails, ApproveDialog, StaleApprovalModal } from "./sow-approve";
import { type StoredDraft, clearDraft, readDraft, writeDraft } from "./sow-draft";
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
  const router = useRouter();
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
  // Unsaved edits from before a sign-in (read once, in the browser; the server render shows the loader).
  const [stored, setStored] = useState<StoredDraft | null>(() =>
    typeof window === "undefined" ? null : readDraft(projectId),
  );
  const [restoredDoc, setRestoredDoc] = useState<JSONContent | null>(null);
  const save = useCustomMutation<SowVersionSummary, ApiError>();
  const regenerate = useCustomMutation<RunAccepted, ApiError>();
  const approve = useCustomMutation<SowApproval, ApiError>();
  const saving = useRef(false);
  const approving = useRef(false); // one approval in flight, however fast the clicks (E7.3-T05)
  const [approveOpen, setApproveOpen] = useState(false);
  const [stale, setStale] = useState<number | null>(null);
  const [approval, setApproval] = useState<SowApproval | null>(null);

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

  // Keep the draft for after a sign-in while it is unsaved.
  const base = current?.version;
  useEffect(() => {
    if (draft && base !== undefined) writeDraft(projectId, base, draft);
  }, [draft, base, projectId]);

  const dropDraft = () => {
    setDraft(null);
    setRestoredDoc(null);
    clearDraft(projectId);
  };

  const confirmDiscard = (then: () => void) =>
    modal.confirm({
      title: "Discard unsaved edits?",
      content: "Your changes to this version are not saved.",
      okText: "Discard",
      okButtonProps: { danger: true },
      onOk: () => {
        dropDraft();
        then();
      },
    });

  const discardFirst = (then: () => void) => (draft ? confirmDiscard(then) : then());

  // Unsaved edits: closing or reloading the tab asks (the browser's own prompt), and so does an in-app link
  // (the project tabs, the breadcrumb): App Router has no route-change event, so links are caught on click.
  useEffect(() => {
    if (!draft) return;
    const unload = (e: BeforeUnloadEvent) => e.preventDefault();
    const click = (e: MouseEvent) => {
      const a = (e.target as Element | null)?.closest?.("a[href]");
      if (!(a instanceof HTMLAnchorElement)) return;
      const url = new URL(a.href, window.location.href);
      const leaving =
        url.origin === window.location.origin &&
        url.pathname !== window.location.pathname &&
        !a.hasAttribute("download") &&
        a.target !== "_blank" &&
        e.button === 0 &&
        !(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey);
      if (!leaving) return;
      e.preventDefault();
      e.stopPropagation();
      confirmDiscard(() => router.push(url.pathname + url.search));
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", click, true);
    };
  }, [draft]); // eslint-disable-line react-hooks/exhaustive-deps

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
          dropDraft();
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
          dropDraft();
          setRegeneratingFrom(current?.version ?? 0);
          void message.success("Regeneration started.");
          refetchAll();
        },
        onError: (error) => void message.error(regenerateErrorMessage(error)),
      },
    );
  };

  const onApprove = (v: number, sha256: string) => {
    if (approving.current) return;
    approving.current = true;
    approve.mutate(
      {
        url: approvePath(projectId, v),
        method: "post",
        values: { sha256 }, // the hash shown in the dialog, never a newer one (E7.1 hash binding)
        successNotification: false,
        errorNotification: false,
      },
      {
        onSuccess: (res) => {
          setApproveOpen(false);
          setApproval(res.data);
          void message.success(`SOW v${res.data.version} approved.`);
          refetchAll();
          void detail.query.refetch();
          void projects.query.refetch(); // the pipeline moves on to SOW_APPROVED
        },
        onError: (error) => {
          const kind = approveFailure(error);
          setApproveOpen(false);
          if (kind === "stale") setStale(v);
          else if (kind === "unavailable") void message.error(ORCHESTRATOR_DOWN);
          else void message.error(approveErrorMessage(error));
        },
        onSettled: () => {
          approving.current = false;
        },
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
  const approveReason = approveBlocked(version, latestNumber, generating);
  const shownApproval = approval && approval.version === shownVersion ? approval : null;

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
          {locked ? null : (
            <Tooltip
              title={
                approveReason ?? "Approve this version: it is locked and the Workflow is generated"
              }
            >
              <span>
                <Button
                  type="primary"
                  disabled={Boolean(approveReason) || approve.mutation.isPending}
                  onClick={() =>
                    draft
                      ? modal.confirm({
                          title: "Unsaved edits",
                          content: SAVE_FIRST,
                          okText: "Save now",
                          onOk: onSave,
                        })
                      : setApproveOpen(true)
                  }
                >
                  Approve
                </Button>
              </span>
            </Tooltip>
          )}
        </Space>
      </Space>

      {stored && !draft && !generating ? (
        <Alert
          type="warning"
          showIcon
          data-testid="sow-restore"
          style={{ marginBottom: 12 }}
          message={
            stored.baseVersion === current.version
              ? `You have unsaved edits to v${stored.baseVersion} from before you signed in again.`
              : `You have unsaved edits to v${stored.baseVersion}, but v${current.version} is now current.`
          }
          action={
            <Space>
              {stored.baseVersion === current.version && editable ? (
                <Button
                  size="small"
                  type="primary"
                  onClick={() => {
                    setRestoredDoc(stored.doc);
                    setDraft(stored.doc);
                    setStored(null);
                    setReload((n) => n + 1);
                  }}
                >
                  Restore my edits
                </Button>
              ) : (
                <Button
                  size="small"
                  onClick={() => {
                    void navigator.clipboard?.writeText(textOf(stored.doc));
                    void message.success("Your edits were copied to the clipboard.");
                  }}
                >
                  Copy my edits
                </Button>
              )}
              <Button
                size="small"
                onClick={() => {
                  clearDraft(projectId);
                  setStored(null);
                }}
              >
                Discard
              </Button>
            </Space>
          }
        />
      ) : null}
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
          description={shownApproval ? <ApprovalDetails approval={shownApproval} /> : undefined}
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
                content={restoredDoc ?? (version.content_tiptap as JSONContent)}
                original={version.content_tiptap as JSONContent}
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
      {version?.sha256 ? (
        <ApproveDialog
          open={approveOpen}
          version={version.version}
          sha256={version.sha256}
          busy={approve.mutation.isPending}
          onConfirm={() => onApprove(version.version, version.sha256 ?? "")}
          onCancel={() => setApproveOpen(false)}
        />
      ) : null}
      <StaleApprovalModal
        open={stale !== null}
        version={stale ?? 0}
        onClose={() => setStale(null)}
        onReload={() => {
          setStale(null);
          setShown(undefined);
          setReload((n) => n + 1);
          refetchAll();
          void detail.query.refetch();
        }}
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
            dropDraft();
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
