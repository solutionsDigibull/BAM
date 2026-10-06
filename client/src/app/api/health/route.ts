// Liveness for the sbam-web container healthcheck (TD §20.3). No auth, no dependencies, no versions.
export const dynamic = "force-dynamic";

export function GET(): Response {
  return Response.json({ status: "ok" });
}
