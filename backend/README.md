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

## Troubleshooting

- **`No module named 'pydantic'`**: wrong venv. Run `deactivate`, then
  `source backend/.venv/bin/activate`, or call `.venv/bin/python` directly.
- **`Missing LLM_MODEL ... in backend/.env`**: create `backend/.env` as above.
- **Votes show `unavailable (no reply over SLIM)`**: check `agent_server.py all` is running
  and SLIM is up (`docker ps` shows `lungo-slim`). Unavailable agents count as reject by design.
- **`Cannot connect to the Docker daemon`**: start Colima / Docker first.
