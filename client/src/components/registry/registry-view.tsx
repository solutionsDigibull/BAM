"use client";

// Runtime registry view (E3.12): the skill-runtime image digest and the packages it contains. Read
// only, for Admins (/admin/registry) and FDEs (/registry); the registry is platform-wide (#67), so no
// tenant data is involved. Layout follows the UI team's admin patterns (docs/reference/ui/README.md).

import { ReloadOutlined } from "@ant-design/icons";
import { useCustom } from "@refinedev/core";
import {
  Button,
  Card,
  ConfigProvider,
  Descriptions,
  Flex,
  Input,
  Segmented,
  Table,
  Tag,
  Typography,
} from "antd";
import dayjs from "dayjs";
import { useMemo, useState } from "react";

import { ApiErrorState, EmptyState, LoadingState } from "@/components/ui-kit";
import type { RuntimePackage, RuntimeRegistry } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

export const NOT_BUILT_MESSAGE = "Registry not built yet";
export const NOT_ALLOWED = "Not allowed";
export const SEARCH_MAX = 100;

const MUTED = "#64748b";
type Filter = "all" | "allowed" | "not-allowed";

/** Case-insensitive substring match on the package name or any of its import names. */
export function matches(p: RuntimePackage, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return p.name.includes(q) || p.top_level_modules.some((m) => m.toLowerCase().includes(q));
}

const head = (label: string) => (
  <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: 0.3, color: MUTED }}>
    {label.toUpperCase()}
  </span>
);

export function RegistryView({ endpoint }: { endpoint: "admin/registry" | "registry" }) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const { result, query } = useCustom<RuntimeRegistry, ApiError>({ url: endpoint, method: "get" });
  const registry = result.data;

  const packages = useMemo(() => registry?.packages ?? [], [registry]);
  const counts = useMemo(
    () => ({
      all: packages.length,
      allowed: packages.filter((p) => p.allowed).length,
      "not-allowed": packages.filter((p) => !p.allowed).length,
    }),
    [packages],
  );
  const rows = useMemo(
    () =>
      packages
        .filter((p) => filter === "all" || p.allowed === (filter === "allowed"))
        .filter((p) => matches(p, search)),
    [packages, filter, search],
  );

  const refresh = (
    <Button icon={<ReloadOutlined />} onClick={() => query.refetch()} loading={query.isFetching}>
      Refresh
    </Button>
  );

  // 404 = no registry synced yet (#67). Any other error wins over whatever `result.data` holds.
  const notBuilt =
    query.error?.statusCode === 404 || (!query.error && !!registry && packages.length === 0);

  let body;
  if (query.isLoading) body = <LoadingState />;
  else if (notBuilt)
    body = (
      <EmptyState
        message={NOT_BUILT_MESSAGE}
        action={
          <Typography.Text type="secondary">
            It appears after the skill-runtime image is built and synced.
          </Typography.Text>
        }
      />
    );
  else if (query.error)
    body = <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;
  else if (registry)
    body = (
      <>
        <Card size="small" style={{ marginBottom: 16 }}>
          <Descriptions
            size="small"
            column={{ xs: 1, md: 3 }}
            items={[
              {
                key: "digest",
                label: "Image digest",
                span: { xs: 1, md: 3 },
                children: registry.image_digest ? (
                  <Typography.Text
                    code
                    copyable={{ text: registry.image_digest, tooltips: ["Copy digest", "Copied"] }}
                    data-testid="image-digest"
                    style={{ wordBreak: "break-all" }}
                  >
                    {registry.image_digest}
                  </Typography.Text>
                ) : (
                  <Typography.Text type="secondary">Not tagged yet</Typography.Text>
                ),
              },
              { key: "python", label: "Python", children: registry.python_version },
              {
                key: "generated",
                label: "Generated",
                children: dayjs(registry.generated_at).format("D MMM YYYY, HH:mm"),
              },
              {
                key: "allowed",
                label: "Allowed libraries",
                children: `${counts.allowed} allowed · ${counts["not-allowed"]} dependencies`,
              },
            ]}
          />
        </Card>
        <Flex justify="space-between" align="center" wrap gap={12} style={{ marginBottom: 16 }}>
          <Flex gap={12} wrap align="center">
            <Input
              allowClear
              placeholder="Search package or import name..."
              aria-label="Search packages"
              maxLength={SEARCH_MAX}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 320, maxWidth: "100%" }}
            />
            <Segmented<Filter>
              value={filter}
              onChange={setFilter}
              options={[
                { value: "all", label: `All (${counts.all})` },
                { value: "allowed", label: `Allowed (${counts.allowed})` },
                { value: "not-allowed", label: `Not allowed (${counts["not-allowed"]})` },
              ]}
            />
          </Flex>
          <Typography.Text style={{ fontSize: 12, color: MUTED }}>
            {rows.length} of {counts.all} packages
          </Typography.Text>
        </Flex>
        <ConfigProvider
          theme={{
            components: { Table: { headerBg: "#f8fafc", headerSplitColor: "transparent" } },
          }}
        >
          <Table<RuntimePackage>
            rowKey="name"
            dataSource={rows}
            pagination={false}
            virtual
            scroll={{ x: 760, y: 560 }}
            style={{ border: "1px solid #e5e7eb", borderRadius: 10, overflow: "hidden" }}
            locale={{ emptyText: <EmptyState message="No packages match your search." /> }}
            columns={[
              {
                title: head("Package"),
                key: "name",
                width: 260,
                render: (_, p) => <Typography.Text strong>{p.name}</Typography.Text>,
              },
              {
                title: head("Version"),
                key: "version",
                width: 140,
                render: (_, p) => <Typography.Text code>{p.version}</Typography.Text>,
              },
              {
                title: head("Import names"),
                key: "modules",
                render: (_, p) => (
                  <Flex gap={4} wrap>
                    {p.top_level_modules.map((m) => (
                      <Tag key={m} bordered={false}>
                        {m}
                      </Tag>
                    ))}
                  </Flex>
                ),
              },
              {
                title: head("Allowed"),
                key: "allowed",
                width: 150,
                render: (_, p) =>
                  p.allowed ? (
                    <Tag color="success">Allowed</Tag>
                  ) : (
                    <Tag color="warning">{NOT_ALLOWED}</Tag>
                  ),
              },
            ]}
          />
        </ConfigProvider>
        <Flex justify="space-between" wrap gap={8} style={{ marginTop: 12 }}>
          <Typography.Text style={{ fontSize: 12, color: MUTED }}>
            Read-only · what the skill-runtime image really contains · sorted by name
          </Typography.Text>
          <Typography.Text style={{ fontSize: 12, color: MUTED }}>
            Sub-Skills may import only allowed libraries; the others are their dependencies
          </Typography.Text>
        </Flex>
      </>
    );

  return (
    <div>
      <Flex justify="space-between" align="flex-start" wrap gap={12} style={{ marginBottom: 20 }}>
        <div>
          <Typography.Title level={1} style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
            Runtime registry
          </Typography.Title>
          <Typography.Text style={{ color: MUTED }}>
            The skill-runtime image generated skills run in, and the libraries it contains
          </Typography.Text>
        </div>
        {refresh}
      </Flex>
      {body}
    </div>
  );
}
