# Untangle backend

Three AI agents (Architect, Delivery, Evidence & Risk) with competing objectives negotiate a
project plan from a user's brain dump. No LLM picks the winner: a fixed protocol applies
explainable rules (unanimity + hard constraints, Nash bargaining tie-break, escalation to the
user). Agents can run in-process or as independent A2A servers over AGNTCY SLIM.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (AGNTCY's SDK needs Python 3.13; uv installs it).

```bash
cd backend
uv venv .venv --python 3.13
source .venv/bin/activate          # prompt should show (.venv), not (venv)
uv pip install -r requirements.txt
```

Create `backend/.env` (git-ignored, never commit it):

```
LLM_MODEL=
LLM_BASE_URL=
LLM_API_KEY=
```

Fill in the values from the AGNTCY lab: in the lab's terminal, run

```bash
echo $LLM_MODEL; echo $LLM_BASE_URL; echo $LLM_API_KEY
```

and paste each after its `=` (no quotes or spaces).

The app reads `backend/.env` automatically. You can instead export these variables
in your shell; existing shell variables take precedence over values in the file.
If your assigned lab already configures them, use that terminal without printing or
copying the API key elsewhere. An AGNTCY lab is optional; model inference and SLIM
messaging are configured separately.

Choose one provider configuration:

**Groq**

```dotenv
LLM_API_KEY=your-groq-key
LLM_MODEL=openai/gpt-oss-20b
LLM_BASE_URL=https://api.groq.com/openai/v1
```

**Google Gemini**

```dotenv
LLM_API_KEY=your-google-api-key
LLM_MODEL=gemini-3.8-flash
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
```

Gemini's native `/v1beta` root is also recognized, and full `/v1` proxy URLs are
preserved. Evidence review additionally requires a provider with strict structured
outputs enabled. Evidence-only provider overrides are documented in
[the Evidence README](evidence/README.md#evidence-specific-configuration).

Check everything works (instant, no LLM calls):

```bash
python test_rules.py               # expect "all 11 passed"
```

## Run

**Chat with it** (brain dump → questions → negotiation → changes):

```bash
python intake.py
```

**One negotiation on the sample idea** (`shared/sample_plan.json`):

```bash
python negotiation.py              # result saved to runs/latest.json
```

Both run the agents in-process by default.

**API for the frontend** (CORS allows `http://localhost:3000`; override with `CORS_ORIGINS`):

```bash
uvicorn api:app --reload --port 8000
```

| Endpoint | Purpose |
|---|---|
| `POST /agents/evidence/review` | Independent proposal review with research metadata |
| `POST /sessions` `{"text": ...}` | Brain dump → plan + questions; negotiation starts once limits are known |
| `GET /sessions/{id}` | Current session |
| `POST /sessions/{id}/messages` `{"text": ...}` | Answers, new ideas, changes, decisions; `"go"` skips open questions |
| `GET /sessions/{id}/stream` | Server-sent events: a `session` event on every change, live during negotiation |

Every response is `{"id", "status", "summary", "error", "transport", "plan"}`, with `status` one
of `needs_answers`, `negotiating`, `idle`. Messages return 409 while agents are negotiating.
Sessions live in memory, so restarting the server clears them.

**Try it without a frontend** at http://localhost:8000/docs ("Try it out" on each endpoint):

1. `POST /sessions` with
   `{"text": "An app where campus organizers post leftover food and students come pick it up. Just me, 10h a week, 2 months, $0 budget. Must have push notifications."}`
   → `"status": "negotiating"` and an `"id"` (about 15s: it reads the idea and asks questions).
   If the dump leaves out a limit, you get `"needs_answers"` and the questions are in `plan.questions`.
2. `GET /sessions/{id}` every ~30s → `plan.negotiation` grows, then `"status": "idle"` with a
   `summary` such as `"consensus: ..."` or `"escalated: ..."`.
3. `POST /sessions/{id}/messages` with `{"text": "a friend joined, she can do 6 hours a week"}`
   → `"negotiating"` again, with the change recorded in `plan.events`.

**Live updates in the frontend** (browser `EventSource`):

```ts
const source = new EventSource(`http://localhost:8000/sessions/${id}/stream`);
source.addEventListener("session", (e) => {
  const session = JSON.parse((e as MessageEvent).data);
  // session.status, session.plan.nodes, session.plan.negotiation, ...
});
// source.close() when leaving the page
```

If `localhost:8000` shows a plain "Error code: 404, File not found" page, another program
(e.g. an old `python -m http.server 8000`) is using the port: stop it, or run on `--port 8001`.

## Run the agents over AGNTCY SLIM

Each agent becomes its own A2A server on SLIM; the protocol broadcasts each round to them
(the same pub/sub pattern as coffeeAgntcy Lungo's auction supervisor).

1. Start Docker (on macOS 13, use Colima: `colima start --cpu 4 --memory 8`).
2. Start the SLIM server from a coffeeAgntcy checkout:
   ```bash
   cd coffeeAgntcy/coffeeAGNTCY/coffee_agents/lungo
   docker compose up -d slim
   ```
3. Terminal A, start the three agents (each in its own process):
   ```bash
   python agent_server.py all
   ```
   Wait for three `serving on slim://localhost:46357/untangle/agents/...` lines.
4. Terminal B, run with the SLIM transport:
   ```bash
   UNTANGLE_TRANSPORT=slim python negotiation.py
   UNTANGLE_TRANSPORT=slim python intake.py
   ```
   Terminal A logs `received review request` / `replied` per agent. The facilitator itself
   makes no LLM calls (`'calls': 0`); all reasoning happens in the agent processes.

## Files

### Evidence & Risk integration

The `evidence` server now runs the research-backed implementation in `evidence/`,
through the same AGNTCY A2A executor and SLIM transport as Architect and Delivery.
No extra Evidence service is needed for `python agent_server.py all`.

It uses the existing `LLM_*` settings; the provider must support strict structured
outputs. Optional `EVIDENCE_MODEL`, `EVIDENCE_BASE_URL`, `EVIDENCE_API_KEY`, and
`SEMANTIC_SCHOLAR_API_KEY` overrides are described in [Evidence setup](evidence/README.md).
Evidence votes support abstention, are not overridden by scores, and retain research
metadata in `Plan.negotiation[].evidence`. Its score is informational and excluded
from the Nash tie-break. Architect and Delivery keep their existing scoring rules.

From the repository root with `backend/.venv` activated:

```bash
LLM_MODEL=test LLM_BASE_URL=https://example.invalid LLM_API_KEY=test \
  python -m pytest backend/tests backend/test_rules.py -q
python -m uvicorn api:app --app-dir backend --reload  # sessions + SSE + Evidence
```

The unified `api:app` serves the session routes and `/agents/evidence/review`.
`backend.main:app` remains an Evidence-only development entry point. Transport selection
applies to session negotiation;
the negotiation CLI uses SLIM when `UNTANGLE_TRANSPORT=slim` is set.

### Module map

| File | What it does |
|---|---|
| `schemas.py` | Plan format shared with the frontend (`python schemas.py` regenerates `shared/plan.schema.json`) |
| `agents.py` | The three agents: objectives, scoring formulas, minimum scores, prompts |
| `negotiation.py` | The protocol: critique → counteroffer → vote rounds, fixed decision rules, escalation |
| `constraints.py` | Hard constraints checked in code: capacity, budget, required must-haves |
| `intake.py` | Brain dump extraction, questions, merging user messages and events |
| `transport.py` | How agents are reached: in-process or A2A over SLIM |
| `agent_server.py` | Runs an agent as an A2A server on SLIM |
| `llm.py` | LLM client for the LiteLLM proxy |
| `test_rules.py` | Checks for the code-only rules |
| `baseline.py` | Single-LLM planner with no safeguards, for comparison |
| `compare.py` | Eval: agents vs baseline on 3 ideas → `shared/baseline_comparison.json` (`--repeats N`) |
| `api.py` | FastAPI app for sessions, SSE, and Evidence review |
| `evidence/` | Research, structured votes, HTTP API and adapter to the AGNTCY-served Evidence agent |

## Troubleshooting

- **`No module named 'pydantic'`**: wrong venv. Run `deactivate`, then
  `source backend/.venv/bin/activate`, or call `.venv/bin/python` directly.
- **`Missing LLM_MODEL ... in backend/.env`**: create `backend/.env` as above.
- **Votes show `unavailable (no reply over SLIM)`**: check `agent_server.py all` is running
  and SLIM is up (`docker ps` shows `lungo-slim`). Unavailable Evidence votes abstain;
  other unavailable agents reject. Neither counts as consent.
- **`Cannot connect to the Docker daemon`**: start Colima / Docker first.
