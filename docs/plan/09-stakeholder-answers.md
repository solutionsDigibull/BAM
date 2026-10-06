
# SBAM Implementation Plan — Open Questions

Each question shows what the current plan assumes. To keep that assumption, answer **"Keep"**. Otherwise write your answer under it. Plan section numbers refer to `SBAM_Implementation_Plan.md`.

---

## A. Scope and boundary

**A1.** Is SBAM's scope correct: it starts at project creation and document upload, and it ends when SRIM acknowledges a frozen manifest (steps 1–9)? Should anything else be inside or outside SBAM? *(Plan §1.4, §2)*
Answer: Yes correct this whole process is done in FDE Login (User login) there will be an another login i.e. admin login here the 4 masters will there along with the RBAC

**A2.** The plan assumes no code exists yet. Is there an existing repository (shared API backend, a frontend, CAM/RFQ modules) that SBAM must be built inside? If so, what is the repo, and which modules already exist (auth, users, file upload, Temporal, Cognee)? *(§3.3, D-02)*
Answer: There is no code this the repo, those CAM and RFQ are other projects do not include this. This the fresh repo need to start from beginning.

**A3.** Which of the six FDE workflows (RFQ Automation, Quote Generation, Reflow Profile, Vision Inspection, Quality Reports, Assets & Maintenance) will SBAM build skills for first? Is there a pilot workflow or client?
Answer: This is the workflow after the SBAM build is completed from focus on the SBAM development. do not refer any of this workflow.

**A4.** Should SBAM generate any reports itself (for example a certification report PDF or Excel), or does all reporting belong to BuLLMind? *(§2.2)*
Answer: No the reports it should generate the SOW, JSON Manifest and Code generation using Jinja2 + AST. Other than this there is no report.

---

## B. Users, roles and approvals

**B1.** Which roles do you want? The plan assumes Admin, FDE, Reviewer, Approver, Certifier and Viewer. Are Reviewer and Certifier separate people, or does the FDE or Admin do those jobs? *(§14, §45.4)*
Answer: Thre will be an two login one for FDE (User) and other one is Admin it has four masters to give context or information to cognee (this is like reference to the cognee about the project/information)

**B2.** Who approves a SOW: an internal manager, the client, or both (two-step approval)? Is more than one approver ever needed (for example commercial and technical)? *(Phase 6)*
Answer: FDE approve the SOW. It is like HITL where FDE reads that SOW and if any changes he will se the TipTap editor and update it and approve.

**B3.** Do clients log in to SBAM to approve SOWs? Or does approval happen outside SBAM (email or PDF signature) and an internal user records it? *(D-27)*
Answer: In futur we need to give an Email or other integration for the approval process. for now FDE will approve the SOW inside FDE Login.

**B4.** Who certifies a skill, and is certification a human sign-off or automatic once every gate passes? *(Phase 13)*
Answer: automatic once every gate passes.

**B5.** Should separation of duties be enforced, so the SOW author cannot approve it and the contract author cannot certify it? *(§14)*
Answer: there is no approve login, the approve will done by FDE only.

**B6.** What identity system should SBAM use: an existing shared-backend login, SSO (Azure AD / Google / Keycloak), or a new one? Is MFA required? *(D-04)*
Answer:  Keycloak

**B7.** Is SBAM single-organisation (DigiBull internal) or multi-tenant, with several client organisations kept isolated?
Answer: Yes, multi-tenant, with several client organisations kept isolated

---

## C. Projects and Admin Masters

**C1.** What is a "project" in your terms: one client engagement, one SOW, or one workflow? Can one project have several SOWs? *(§6.3)*
Answer: Yes, FDE create a project then that project has all the information of that project. so there one SOW but they may have other supporting files will be there.

**C2.** For each of the five masters (Org, Rules, Agents, Skills, File Type) and README/Governance: what fields does it hold, and are masters global, per client or per project? Example data would help a lot.
Answer: I will attach the schema of the admin masters and some demo data.

**C3.** How exactly are masters used in the flow: as context for Cognee, as inputs to SOW generation, as validation rules for ISATVON, or all three?
Answer: For a particular project, the FDE can create the project in the Admin portal. Any files that the client uploads using Uppy will be considered the current and authoritative source data for Cognee. If additional information is available in the Admin tab for that project, Cognee will also refer to and use that information as a reference.

**C4.** What is the "Custom Master Context" in the FDE architecture diagram (ARCH-2), and how is it different from the five masters?
Answer: This is not required refer the five masters only.

---

## D. Document ingestion

**D1.** Which file types are mandatory for the MVP: PDF, DOCX, XLSX/CSV, MD, TXT, EML? Which CAD formats (DXF, STEP, PDF drawings?) and which ERP export formats? *(D-24)*
Answer: PDF, DOCX, XLSX/CSV, MD, TXT, EML and any other which will easily extract and give the context to Cognee.

**D2.** Is browser upload enough for the MVP? When should API ingestion, the FastMCP watchdog, webhooks, cron and email ingestion arrive? *(§2.3)*
Answer: currently via browser, others for future upgrades.

**D3.** What are the maximum file size and the maximum number of documents per project?
Answer: it is variable depending on the complexity of the project. [ size: upto15 Mb ; files: max upto 20 ]

**D4.** Do uploads need malware scanning? *(D-21)*
Answer:yes, use ClamAV for security scanning

**D5.** Do scanned PDFs or images need OCR?
Answer: MVP[ currently basic Extraction or LLM call via Bifrost (Openrouter-api )) ] ;  for future updates better OCR needed.

**D6.** Can documents be deleted, or only archived? How long must they be kept? *(D-20)*
Answer: after deleting 90 days recovery period.

---

## E. Knowledge layer (Cognee / OKF)

**E1.** Should Cognee run as a library inside the worker or as a separate Cognee service? Which graph and vector stores (Kuzu, Neo4j, pgvector, LanceDB)? *(D-06)*
Answer:Default Cognee DB  

**E2.** What do you need from OKF concretely: an exported bundle stored as an artifact, or OKF as the live format used for retrieval? *(D-08)*
Answer: OKF should give all the artifact so that LLM has all the context to generate the SOW. The information in the Ingested documents should preserve in the OKF Bundle so that LLM can create meaningfull SOW.

**E3.** Must the knowledge graph stay isolated per project, or should knowledge be shared across projects of the same client or domain?
Answer: isolated per project

**E4.** Does the user need to see or edit the knowledge graph in the UI, or is it backend-only?
Answer: Can only see & Explore but cannot edit

---

## F. SOW generation

**F1.** Do you have the standard SOW template(s)? What sections are mandatory? Are there company templates and client-specific ones?
Answer: So primary Is ISATVON template. I will attach the ISATVON document for reference.

**F2.** Should SOW generation be one shot (the whole document), section by section, or chat-assisted editing with the LLM inside TipTap?
Answer: SOW generation be one shot (the whole document) and HITL then FDE can Edit the SOW using TipTap.

**F3.** Must every SOW section cite its source documents? *(Plan assumes yes)*
Answer: yes 

**F4.** Does the approved SOW need exporting (PDF/DOCX/.md) to send to the client?
Answer:yes

**F5.** After approval, if the client changes scope, does a new SOW version invalidate all existing ISATVON rows and skills, or only the affected ones?
Answer: only affected ones and make the SOW as new version.

---

## G. ISATVON contract and grid

**G1.** Can you share a real example ISATVON sheet (macro-skill plus sub-skills, all seven fields filled in)? This is the most important input for freezing the schema. *(§9, D-03)*
Answer: Will attach the ISATVON sheet.

**G2.** Is nesting only one level (macro → sub), or can sub-skills have their own children?
Answer: Based on the SOW, the LLM needs to break down the project into **Macro Skills** (also known as Agents) and **Sub-Skills** (also known as skill).

* **Macro Skill:** Represents a major module or functional area of the overall project. The complete project should be divided into multiple Macro Skills.
* **Sub-Skill:** Each Macro Skill should be further broken down into smaller, well-defined features or functionalities. These Sub-Skills should be granular enough for **Jinja2 + AST** to generate the corresponding code for each feature.

A Macro Skill can contain multiple Sub-Skills.

The **ISATVON JSON** should be generated at the **Sub-Skill level**, not at the Macro Skill level. In other words, the **ISATVON breakdown must be performed for each individual Sub-Skill**, because the Sub-Skill represents the smallest functional unit that will be used as the basis for code generation using Jinja2 + AST.

The overall hierarchy should therefore be:

**SOW → Macro Skill → Sub-Skill → ISATVON JSON → Jinja2 + AST → Generated Code**

This ensures that each Sub-Skill has a clear, self-contained ISATVON representation that can be directly translated into the required implementation.

**G3.** Is the openpyxl Excel file a real deliverable (downloaded, edited offline, re-uploaded), or only an export? Must Excel re-upload be supported?
Answer: Only an excel export.

**G4.** Should "N – Notifications/Next" hold only status/alert configuration, or also real alert channels (Slack, Teams, email, webhooks) that SBAM must call?
Answer: It should notify then go to the next step.

**G5.** Can an FDE create rows manually from scratch, without LLM extraction?
Answer: No/NA

**G6.** Who else edits the grid besides the FDE (Reviewer comments? client?) Is real-time multi-user editing needed?
Answer: FDE only 

---

## H. Manifest and unit of certification

**H1.** What is certified: one macro-skill (with its sub-skills), each sub-skill separately, or the whole contract or project? *(Plan assumes macro-skill, D-12)*
Answer: certify project level (need to certify the full workflow once validated) as well as macro skill level (because after generating the macro skill and code for those if the validation passes then it certified Macro skill). 

**H2.** Who defines the manifest fields that SRIM needs (runtime config, agent mapping, constraints)? Does SRIM already have a manifest format SBAM must match?
Answer: generated JSON manifest is used in the SRIM

**H3.** Should the manifest be digitally signed (cosign/KMS), or is a SHA-256 hash enough? *(D-10)*
Answer: Future scope

---

## I. Code generation and templates

**I1.** Which automation types must have templates for the MVP? The documents mention UNSTRUCTURED_TO_SOW_LLM, SOW_TO_EXCEL_MCP, EXCEL_TO_JSON_AST and JSON_TO_PYTHON_JINJA2. Are there others?
Answer: Once the JSON manifest is generated, it will contain all the required information for the corresponding Sub-Skill. This JSON manifest should be provided as input to the LLM, which will use the information in the manifest to generate the required code.

After the code is generated, the AST will validate the generated code against the information and requirements defined in the JSON manifest. If the code passes all validation checks, the AST will certify it as  **Approved** . If validation fails, the code should be rejected and sent back for correction and regeneration.

The overall flow is:

**JSON Manifest → LLM → Code Generation → AST Validation → Approved / Rejected**

**I2.** Who writes and maintains the Jinja2 templates (platform team, or FDEs)? Do templates need a UI, or are they managed in Git?
Answer:

**I3.** Is every generated artifact a FastMCP tool, or are other outputs possible (plain Python scripts, Temporal workflows, SQL, Superset dashboards)?
Answer: FastMCP tools

**I4.** Should the LLM correction step (Activity D) fix only the ISATVON contract (plan recommendation), or may it also edit generated Python directly? *(D-17)*
Answer: ISATVON fix as well as edit python 

**I5.** How many automatic correction attempts are allowed before it goes back to the FDE? *(Plan assumes 2)*
Answer: 2

---

## J. Validation and dependency registry

**J1.** Who maintains the dependency registry, and what is in it initially: Apache Superset, FastMCP, DBHub, openpyxl, DuckDB, PostgreSQL, others? Which versions?
Answer: there is no duckDB we replaced with Postgres only. all is in docker container. Its simple SBAM is like a sandbox we will provide all the things the FDE can create, validate and certify the workflow.

**J2.** Which security scanners do you prefer (Bandit, Semgrep, pip-audit)? Should warnings block certification or only errors? *(D-14)*
Answer: ClamAV

**J3.** How strict should MyPy be for generated code (strict, or basic)?
Answer: strict

---

## K. Sandbox and test data

**K1.** Is a Linux server with KVM available for CubeSandbox (cloud VM with nested virtualisation, or bare metal)? Where will it run? *(D-09)*
Answer: Yes, run on linux server

**K2.** Where does mock/test data come from: FDE-curated, client-provided anonymised samples, generated by the LLM or CubeSandbox, or Alembic seed scripts?
Answer: LLM generated

**K3.** Who writes Promptfoo assertions: auto-generated from the O-field, written by the FDE, or both?
Answer: suggest the best

**K4.** Do generated tools need network access during sandbox tests (for example to call mock supplier APIs)?
Answer: Yes

**K5.** Is a minimum number of test cases or scenarios required per skill before certification?
Answer: 3

---

## L. Certification, versioning and freeze

**L1.** Can a certified or frozen skill ever be revoked or withdrawn? Who can do it? *(D-19)*
Answer: No, FDE

**L2.** What versioning scheme should be used: semantic (1.0.0 → 1.1.0) or a simple increment? What counts as a major vs minor change?
Answer: major do it like 1.0.0, 2.0.0 for minor 1.0.1 ..., 2.0.1 ...

**L3.** If an unchanged macro-skill sits in a changed contract, may it reuse its previous certification, or must everything re-certify? *(D-12)*
Answer:reuse its previous certification

---

## M. SRIM handover

**M1.** How should SRIM receive manifests: SRIM pulls from an SBAM API, SBAM pushes an event or webhook, a shared database table, or a shared S3 location? *(D-11)*
Answer: future scope

**M2.** Must SRIM send an acknowledgement back? What happens if SRIM rejects a manifest?
Answer: future scope

**M3.** Is SRIM being built at the same time as SBAM by another team? Who owns the handover contract?
Answer: future scope

**M4.** Mock/staging vs production: does SBAM need to know about the "Switch Toggle" (ARCH-2), or is it fully SRIM's concern?
Answer: future scope

---

## N. Tech stack, infrastructure and Docker

**N1.** Frontend: Vite + Refine (architecture docs) or Next.js (your prompt)? *(D-01)*
Answer: not vite, Next.js

**N2.** Where will SBAM be hosted: AWS, Azure, GCP, on-prem or a single VM? *(D-05, D-15)*
Answer: Local now future AWS

**N3.** Production containers: Docker Compose on VMs, or Kubernetes? *(D-15)*
Answer: Docker Compose on VMs

**N4.** Temporal: self-hosted (in Docker) or Temporal Cloud? *(D-28)*
Answer: self-hosted (in Docker)

**N5.** Object storage: AWS S3, MinIO, Azure Blob, or local disk? *(D-05)*
Answer: local disk (postgres) future AWS S3

**N6.** Which container registry and CI system do you use (GitHub Actions/GHCR, GitLab, Azure DevOps, other)? *(D-26)*
Answer: GitHub Actions

**N7.** What should Netbird be used for: private access to the CubeSandbox host, developer access to staging, or connecting to client networks? *(D-16)*
Answer:suggest best

**N8.** Which environments are needed: local, dev, staging, production? Is UAT a separate environment?
Answer: all (local, dev, staging, production)

**N9.** Must the full stack run on developer Windows laptops (Docker Desktop)? How much RAM do those laptops have? This decides whether Langfuse and PostHog run locally.
Answer: 64 and 32 RAM

---

## O. LLM and data policy

**O1.** Which LLM providers and models should Bifrost route to (OpenAI, Anthropic Claude, Azure OpenAI, Gemini, self-hosted)? Is there a preferred default model? *(D-07)*
Answer: OpenRouter, models depending on situation

**O2.** Can client documents be sent to external LLM providers, or must some clients use only private or self-hosted models?
Answer: yes

**O3.** Is there an LLM budget limit per project or per month?
Answer: No

---

## P. Security and compliance

**P1.** Is there a compliance requirement (ISO 27001, SOC 2, GDPR, DPDP, client NDAs) that affects data storage, retention or audit?
Answer: No for now, for future yes

**P2.** Must the audit log be tamper-evident (hash-chained, as planned), or is a normal audit table enough?
Answer: normal audit table

---

## Q. Observability

**Q1.** Langfuse, PostHog and OTEL: are all three required for the MVP? Do you already run any of them? What is the log/trace backend (Grafana, Datadog, ELK, other)? *(D-22, D-23)*
Answer: all three

**Q2.** Should SBAM alert people (email, Slack, Teams) on failures and pending approvals? Which channel?
Answer: alert in SBAM syetem only

---

## R. Non-functional targets

**R1.** How many concurrent users, projects per month and skills per project do you expect?
Answer: 10 max for MVP, for production need to scale

**R2.** Are the proposed targets acceptable: API p95 ≤ 500 ms, SOW draft ≤ 5 min, validation ≤ 5 min per skill, sandbox + Promptfoo ≤ 10 min, 99.5% availability, RPO 24 h / RTO 8 h? *(§42)*
Answer: Yes

---

## S. Team, timeline and delivery

**S1.** What are the team size and skills (backend Python, frontend React, DevOps, ML/LLM)? How many developers work in parallel?
Answer: 4 and has all the required skills

**S2.** Is there a target date for the MVP or a pilot demo? Should the plan include time estimates (weeks per phase) or only complexity?
Answer: need to finish as soon as possible without missing anything

**S3.** Is the MVP scope in §39 right, or should something be added, removed or moved later?
Answer: architecture is freezed no changes are there.

---

## T. The plan document itself

**T1.** Keep one master document, or split it (plan + technical design)?
Answer: Yes, split it.

**T2.** Output format: Markdown only, or also Word/PDF/Claude Doc?
Answer: .md

**T3.** Which sections were too detailed or not detailed enough?
Answer: Give it detailed so it will help the developer to finish fast.

---

## U. Complete user flow (please provide)

Please describe the flow you want, step by step. For each step, include the following where possible:

1. **Who** does it (Admin, FDE, Reviewer, Approver, Client, Certifier, system).
2. **Which screen** they are on and **what they click or enter**.
3. **What the system does** automatically after that action.
4. **What the user sees** next (result, status, notification).
5. **Decision points**: approve/reject, pass/fail, and where each path goes back to.
6. **Which steps you think are wrong or missing** in the current plan's flow (§5 and §13).

A rough numbered list or a hand-drawn diagram (photo) is fine.

Your flow: 1. first thing tehre will be an two login FDE and Admin.
2. this is the high level flow: 

In the **FDE login**, the FDE user can create a **Workflow** for a particular project.

The FDE will ingest all relevant project informationusing the Uppy. During ingestion, the system will also use the information available in the **Admin** section as additional context for the project.

Once the ingestion process is completed, the following processing pipeline will be executed:

1. **Information Ingestion**
   The FDE ingests all project-related information. The system combines this information with the relevant project information available in the Admin section.
2. **Cognee Processing**
   All the ingested information, along with the relevant Admin context, is provided to **Cognee**. Cognee processes the information and generates the required knowledge graph.
3. **OKF Bundle Generation**
   The generated knowledge graph is converted into an **OKF Bundle**, which serves as the structured knowledge package for the next stage.
4. **SOW Generation**
   The OKF Bundle is provided to the **LLM**, which uses the information contained in the bundle to generate the **Statement of Work (SOW)**.
5. **SOW Approval**
   The generated SOW is presented for review and approval. The workflow cannot proceed to the next stage until the SOW is approved.
6. **ISATVON Skill Generation**
   After SOW approval, the approved SOW is broken down into **ISATVON Skills**. The SOW should first be divided into **Macro Skills**, and each Macro Skill should then be further divided into **Sub-Skills**.
7. **JSON Manifest Generation**
   The ISATVON breakdown is performed at the **Sub-Skill level**. Each Sub-Skill is converted into a JSON manifest containing all the information required for implementation and code generation.
8. **Code Generation**
   The generated JSON manifest is provided to the **LLM**. The LLM uses the information in the manifest to generate the corresponding code for that Sub-Skill.
9. **AST Validation and Certification**
   The generated code is passed to the **AST validation layer**. The AST validates the code against the requirements and information defined in the JSON manifest.

   * If the code passes validation, the AST certifies it as **Approved/Certified**.
   * If the code fails validation, it is rejected and sent back for correction and regeneration.
10. **Tool and Workflow Certification**
    Once all required Sub-Skills have successfully passed validation and certification, the generated tools and the overall Workflow can be certified as **Approved**.

### Overall Workflow

**FDE Login → Create Workflow → Information Ingestion + Admin Context → Cognee → Knowledge Graph → OKF Bundle → LLM → SOW → SOW Approval → ISATVON Macro Skills → Sub-Skills → JSON Manifest → LLM Code Generation → AST Validation → Code Certification → Tool & Workflow Certification**

The key principle is that the **approved SOW is the source for the ISATVON breakdown, the Sub-Skill is the unit of implementation, the JSON manifest is the contract for code generation, and AST validation is the final certification gate for the generated code.**
