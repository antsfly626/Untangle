# Untangle workspace

> Current UI scope: the mind map visualizes the user's input as AI-extracted topic hierarchy
> (`proposed_by="user"`). Related topics can be nested under broader user-mentioned topics,
> and each hierarchy branch is kept together in the layout. The map excludes agent-generated ideas, critiques, votes, protocol
> outcomes, research papers and tasks. Node details show descriptions and connected
> ideas, without negotiation statuses or proposal metadata. User constraints and explicitly
> provided team, time and budget limits are grouped under a Constraints branch; assumed
> defaults are not presented as user requirements. Research and approved tasks
> remain separate panels; original objections remain in the backend ledger. This
> supersedes the earlier discussion/risk-map design described in historical notes.


Next.js, Tailwind and React Flow workspace for Developer B. Displays the existing
session API's project graph, protocol outcome, Evidence reviews, retrieved papers,
risks, and implementation board. An explicitly labeled, local-only example session
is shown by default so the frontend can be explored without a backend; starting a
project or loading a session replaces it with live session data. Example implementation
tasks are illustrative only and do not bypass the backend's approval gate for live work.

## Run locally

Use Node.js 22.6+ (Node 24 recommended for the built-in TypeScript test runner), npm,
and the existing backend Python environment. From the repository root, terminal 1:

```sh
backend/.venv/bin/python -m uvicorn api:app --app-dir backend --reload
```

Configure model credentials in the ignored `backend/.env`; see
[backend setup](../backend/README.md). The shared runtime requires `LLM_MODEL`,
`LLM_BASE_URL`, and `LLM_API_KEY`. Terminal 2:

```sh
cd frontend
npm ci
npm run dev
```

Open http://localhost:3000. Enter an idea with team size, deadline, weekly hours and
budget, or load an existing session ID. Reply to backend questions in Project updates.
The first request invokes the real intake model. Live calls can incur provider charges.

The browser defaults to http://localhost:8000. To change it, set
`NEXT_PUBLIC_API_URL` in `frontend/.env.local` and restart Next.js. Configure backend
`CORS_ORIGINS` for the frontend origin (default http://localhost:3000).
Never put provider keys in browser variables. `npm run dev` belongs in `frontend`,
not the repository root.

## Integration and end-to-end tests

From frontend:

```sh
npm test                       # Pure adapter and contract tests
npm run lint
npm run typecheck
npm run build
npx playwright install chromium # Once per machine
npm run test:e2e                # Real browser, mocked HTTP and named session SSE
```

Browser tests build the app and start a production server on port 3100; keep that
port free. Your development server can keep running on port 3000. Fixtures are
clearly labeled test-only and never imported by app components. Browser tests do not
call models or prove live agent judgment. Failures save traces in `test-results/`.

From the repository root:

```sh
LLM_MODEL=test LLM_BASE_URL=https://example.invalid LLM_API_KEY=test \
  backend/.venv/bin/python -m pytest backend/tests backend/test_rules.py -q
# Optional paid cloud-model/research integration using backend/.env:
RUN_EVIDENCE_LIVE=1 backend/.venv/bin/python -m pytest backend/tests/test_evidence_live.py -s
```

For full live testing, follow [the scenario checklist](../DEVELOPER_B_TASKS.md):
initial idea → retention objection → revised proposal → accepted tasks or visible
unresolved outcome. Check the research panel against returned paper metadata and
open its source links. A model may choose not to research; this is displayed explicitly.

For real AGNTCY transport, start the SLIM broker and three agent processes as described
in backend/README.md, then start the API from the repository root with:

```sh
UNTANGLE_TRANSPORT=slim backend/.venv/bin/python -m uvicorn api:app --app-dir backend --reload
```

The UI shows the server-reported transport. Default local mode exercises the same
agent implementations in process but does not test SLIM communication.

## Contract and Developer A handoff

- `src/lib/contracts.ts` mirrors consumed fields from `schemas.py` and Evidence models.
- `src/lib/graphAdapter.ts` is a pure adapter; canvas positions never reach negotiation.
- `src/lib/api.ts` consumes POST /sessions, GET /sessions/{id}, POST messages and the
  actual `event: session` SSE full snapshot format. Refresh is available on disconnect.
- `Workspace` uses a minimal session entry/update form. It can be embedded alongside
  Developer A's chat; no replacement debate timeline or transport is implemented.
- Only an idle, error-free session with protocol consensus and no constraint violations
  approves tasks. Individual votes and prose cannot establish consensus.
- When explicit plan tasks exist, only tasks linked to agreed nodes appear. If no tasks
  exist, agreed features produce labeled `Implement …` work items, with existing estimates.
  No dependencies, priorities or estimates are invented. Task progress is local React
  state, survives live updates, and resets on page reload or switching sessions.
- Historical Evidence reviews retain proposal/round IDs, objections and verified papers.
  Risk links use exact feature IDs or unique labels; ambiguous targets remain on the review.
  Papers may appear for multiple proposals so assessments retain their context.
- The backend has no structured risk-resolution links and no proposal/status fields on
  every historical protocol decision. Old objections remain unresolved. Superseded and
  mitigated styles are ready but never inferred from model prose or a later accept vote.
- Legacy inline `Node.evidence` lacks a paper ID and verified retrieval provenance, so
  research displays only validated Evidence envelopes from the negotiation ledger.
- Existing registration remains `evidence.interfaces.handle_evidence_message` for the
  `evaluate_plan` contract; `agents.EVIDENCE` handles shared review/vote operations through
  the existing A2A/SLIM executor. No backend endpoints or transport were added here.

Select any node for rationale and relationships. The accessible node directory is an
alternative to canvas navigation. Source links open actual retrieved URLs; metadata or
research failures produce warnings rather than fabricated sources.

## Agent decision dropdown

A collapsed **Agent decisions + details** box sits below Browse all nodes. Expand
it for the protocol outcome, constraint issues, and per-round agent entries. Each
entry expands to show rationale, votes, counteroffers, and validated Evidence details
including objections, requested changes, proposal IDs and retrieved sources. Historical
entries remain visible after revisions. These results never become mind-map nodes.
