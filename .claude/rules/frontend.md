---
paths:
  - "client/**"
---

# Frontend rules (client/)

- Next.js App Router, TypeScript strict, ESLint + Prettier, Vitest + Testing Library, and Playwright for E2E
  (`client/e2e`, scenarios E01–E20).
- Data access goes only through the Refine data provider and the generated types in `client/src/contracts/`.
  Never hand-edit those types; run `make contracts`.
- Tokens stay server-side (Auth.js session plus the `/api/proxy` route). Never put tokens in
  localStorage, sessionStorage or client-readable cookies.
- AG Grid, TipTap, Monaco, Uppy and Xyflow are client components: use `"use client"` and dynamic import with `ssr: false`.
- Every screen has loading, empty, error (problem+json `detail`) and forbidden states. Hide actions the
  user can't perform, but treat the backend as authoritative.
- Admin screens live in `src/app/(admin)/`, FDE screens in `src/app/(fde)/projects/[projectId]/…`. Every route
  and nav entry already exists, so fill in your page folder and don't restructure the layout or providers
  (owner D1).
- The FDE UI has no "create Workflow" action. SBAM generates the Workflow from the approved SOW.
- Component tests live next to the component (`*.test.tsx`). Name tests after the issue test-case IDs.
