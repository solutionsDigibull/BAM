// The Staging UI frame (#861, ADR 0036): the workbench shows the certified UI bundle in an iframe whose files come from
// this app's own route (`/api/staging-ui/<project>/<workflow>/<file>`), which adds the FDE's token server-side and
// forwards the API's strict CSP. The bundle talks to its workflow server at `/mcp`; a small host script served beside
// it (`__host.js`) sends those calls to the Staging MCP route of the API proxy, so the calls are real and the page
// never holds a token.
import { isUuid } from "@/lib/roles";

export const STAGING_UI_ROOT = "/api/staging-ui";
export const HOST_SCRIPT = "__host.js";

/** The frame's address for a workflow (the trailing slash makes the bundle's relative `./assets/...` URLs resolve under it). */
export const stagingFrameSrc = (projectId: string, workflowId: string): string =>
  `${STAGING_UI_ROOT}/${projectId}/${workflowId}/`;

/** Upstream path of one bundle file (`""` is the entry file), or null for ids that are not UUIDs or a path that could leave the bundle. */
export function stagingUpstream(
  projectId: string,
  workflowId: string,
  segments: readonly string[] | undefined,
): string | null {
  if (!isUuid(projectId) || !isUuid(workflowId)) return null;
  const parts = segments ?? [];
  if (!parts.every((s) => /^[A-Za-z0-9._~@-]+$/.test(s) && s !== "." && s !== "..")) return null;
  return `/api/v1/projects/${projectId}/workflows/${workflowId}/staging/ui/${parts.map(encodeURIComponent).join("/")}`;
}

/** The script that points the bundle's `/mcp` calls at the Staging MCP route. It reads the ids from its own address. */
export function hostScript(): string {
  return String.raw`(() => {
  const m = location.pathname.match(/^\/api\/staging-ui\/([0-9a-f-]{36})\/([0-9a-f-]{36})(?:\/|$)/);
  if (!m) return;
  const target = location.origin + "/api/proxy/api/v1/projects/" + m[1] + "/workflows/" + m[2] + "/staging/mcp";
  const native = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), location.href);
    if (url.origin === location.origin && url.pathname === "/mcp") {
      return native(input instanceof Request ? new Request(target, input) : target, init);
    }
    return native(input, init);
  };
})();
`;
}

/** The entry page with a `<base>` and the host script first, so both apply before the bundle's own scripts. Next redirects
 *  `/api/staging-ui/<p>/<w>/` to the same address without the slash, which would make `./app.js` resolve beside the workflow
 *  folder: the `<base>` pins relative URLs under it. Anything but an HTML page is returned as it is. */
export function withHostScript(html: string, projectId: string, workflowId: string): string {
  const src = stagingFrameSrc(projectId, workflowId);
  const tag = `<base href="${src}"><script src="${src}${HOST_SCRIPT}"></script>`;
  return /<head[^>]*>/i.test(html)
    ? html.replace(/<head[^>]*>/i, (h) => `${h}${tag}`)
    : `${tag}${html}`;
}
