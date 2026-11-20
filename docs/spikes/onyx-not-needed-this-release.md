# Spike MUI-7 (#786): is Onyx needed next to Cognee?

## Question

The approved Admin Masters UI shipped a browser-side Onyx connector. Do we need Onyx next to Cognee? If yes, what would a server-side connector look like?

## Findings

The connector was removed in the port to BAM ([ADR 0029](../adr/0029-approved-masters-ui.md), decision 5), for two reasons:

- It uploaded documents from the browser straight to a third-party server. That bypasses the ClamAV scan and `StorageBackend`, the only sanctioned path for project files.
- It kept the Onyx API token in the browser (localStorage). A secret must never live in the browser.

Cognee already covers knowledge for this release: documents go through ingestion (scan, standardize, `StorageBackend`) and then into the Cognee knowledge graph. Nothing in the current epics needs a second document index.

## Decision

**Onyx is not needed for this release.** No Onyx service, client or connector is added next to Cognee. No story is opened.

## If it is ever revisited

A connector must be server-side only and meet all of these:

- **Secret:** the API token comes from env or from the project connections store ([ADR 0030](../adr/0030-project-connections-and-secrets.md)), write-only, never sent to the browser.
- **Files:** read from `StorageBackend` only after the ClamAV scan has passed. Never uploaded from the browser, never read from a local path.
- **Tenancy:** every call is tenant-scoped through the base repository. Cross-tenant or unassigned access returns 404.
- **Audit:** every push to Onyx writes an audit-log entry (who, project, document, result).
- **Execution:** long work runs in a Temporal activity. LLM or embedding calls, if any, go through Bifrost with an alias.
- **Approval:** a new ADR first, with the case for why Cognee is not enough.

## Recommendation

Close the question as not needed. Reopen only if a concrete requirement appears that Cognee cannot meet.

## ADR link

[0029 Approved Admin Masters UI](../adr/0029-approved-masters-ui.md)
