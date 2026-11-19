# 0034 — Enterprise Assistant: cited document and graph Q&A behind the middle-pane chat, on its own Bifrost alias

- Status: Proposed
- Date: 2026-11-19
- Related: issue #729 (decision), umbrella #710, delivered under #711, goal box #720; refines [0015](0015-llm-gateway.md) and [0018](0018-llm-wrapper.md); builds on [0008](0008-knowledge-cognee-okf.md) (knowledge), [0017](0017-authorization-chain.md) (authorization), [0016](0016-audit-observability-and-nfrs.md) (audit); prompt fencing as in #759 and #767

## Context

The middle pane of the new UI is the Enterprise Assistant. Version A (#711) works without a language model:
every reply is computed in the browser from the project snapshot the other screens already use (documents,
pipeline run, knowledge versions). It answers a fixed set of questions from real data (where the FDE is, what
blocks a step, how many files and which need attention, the next step, stage progress) and names a file that
could not be read with its reason. Anything else gets "I can't answer open questions yet". It never approves,
certifies or deploys. The reply function is the single seam: it can be swapped for another adapter without
changing the pane.

What version A cannot do: answer questions about the **content** of the documents ("What are the payment terms
in the RFQ?"), including the Context "Verify" questions that are answered from the graph, and understand wording
it was not written for.

**What the backend has today (the [CONTRACT GAP]).** There is no chat or answer endpoint. The knowledge API
exposes versions, the graph and nodes, but no search or answer route. LLM calls go only through Bifrost with an
`sbam-*` alias, and none exists for chat.

Issue #729 listed three options: keep version A; document and graph Q&A with citations; or that plus a
general free-form assistant. This ADR records the owner's decision. It does not re-open the options.

## Decision

1. **Option 2 only, this iteration: document and graph Q&A with citations.** The general free-form assistant
   (option 3) is out of scope and is not built.
2. **The seam is swapped for an LLM-backed adapter for the recognised Q&A set and the Context "Verify"
   questions only.** A question that is not recognised still gets version A's "can't answer open questions
   yet" reply. The browser decides which path a question takes; the server never turns a free-form question
   into an answer.
3. **A new Bifrost alias `sbam-assistant`**: primary `gpt-4o-mini`, fallback `gpt-4o`. It is not a reuse of
   `sbam-knowledge`, so its spend and rate caps are separate and its usage is attributable. The prompt lives in
   the prompt registry as `assistant-answer`. Every call is logged to `llm_calls` and Langfuse like any other
   LLM call ([0018](0018-llm-wrapper.md)). Mock provider in tests; never a real model call in CI.
4. **The answer contract carries citations**: each citation names a source document (document version id and
   title) and the knowledge node refs that support the claim.
5. **Grounding: always cite, otherwise decline.** The answer is built only from the project's **READY**
   knowledge version. If the model cannot ground an answer in it (no READY version, nothing retrieved, no
   citation that resolves against the retrieved sources), the endpoint declines with a fixed message. It never
   guesses. Citations are checked server-side against what was retrieved (as `sow/citations.py` does for the
   SOW); an answer with an unresolvable citation is a decline, not a partial answer.
6. **Prompt injection.** The retrieved passages and the user's question go to the model as **data blocks**,
   never as instructions, using the same fencing as #759 and #767. The assistant is read-only over knowledge:
   it never approves, certifies, deploys or changes anything, and its output is never executed or fed to
   another tool.
7. **Scope and limits.** Tenant- and project-scoped like every route: a project the caller is not assigned to,
   or that belongs to another tenant, is 404. The call counts against the project token budget
   ([0015](0015-llm-gateway.md)) and is refused like any other call once the cap is reached. A per-request
   token cap applies (input and output). Rate limit: **10 requests per minute per user** (proposed; a
   setting). The answer is **synchronous** with a hard output token cap for the MVP. A long answer that needs
   more would become a `202` with a Run, in a later iteration; nothing long runs in the request.
8. **History stays in the browser** (version A). It is not stored server-side this iteration. The server
   audits only that a question was asked: its **length** and that an answer (or a decline) was returned,
   never the question or the answer text, and logs carry ids and counts only. This mirrors the SOW-hint
   pattern. Stored chat, with its audit and retention, is future work and needs its own decision.
9. **The endpoint (to be built under a follow-up issue, not here): `POST /projects/{project_id}/assistant/answer`**
   (written `POST P/assistant/answer` in the API doc). FDE-only, tenant-scoped, with an RBAC-matrix row and
   IDOR tests (cross-tenant and unassigned are 404). The screen needs loading, empty, error and forbidden
   states. Contracts are added in `server/contracts` first and the TypeScript is regenerated.

## Consequences

- The Context "Verify" questions and content questions become answerable, with sources the FDE can open.
- A separate alias makes the assistant's cost visible and capped on its own, at the price of one more alias
  to configure in Bifrost, the price table (`LLM_PRICE_USD_PER_MTOK`) and the budget.
- Declining when nothing grounds an answer means some questions get no answer. That is intended: a wrong,
  uncited answer is worse than none for an FDE building a customer workflow.
- Free-form wording is still not understood. Option 3 needs its own ADR (what the model may see, streaming or
  202, how a suggested action stays an explicit audited action).
- Because history is not stored, a browser change loses the thread. Accepted for now.
- Retrieval quality depends on the Cognee graph of the READY version; stale or failed builds mean declines,
  not wrong answers.

**Follow-ups (new issues, not part of this ADR):** the `POST P/assistant/answer` route, contract and RBAC row;
the `sbam-assistant` alias and `assistant-answer` prompt with mock-provider tests; the adapter in the client
(recognised set and Verify questions only) with the four screen states; the rate-limit setting.
