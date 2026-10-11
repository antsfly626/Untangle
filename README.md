# Untangle

Untangle is an AI-powered brainstorming platform where autonomous agents debate ideas, challenge assumptions, negotiate decisions, and turn scattered thoughts into research-backed mind maps, system architectures, and actionable project plans.

Created for UCSC AI Hackathon

## Run locally

The frontend can be explored without a backend: it opens with an illustrative
“Create a collaborative project intelligence” example. To create or load a live
project, start both the backend and frontend.

### 1. Install prerequisites

- Python 3.13 and [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Node.js 22.6 or newer and npm

### 2. Set up the backend

From the repository root:

```sh
cd backend
uv venv .venv --python 3.13
uv pip install -r requirements.txt
```

Create `backend/.env` in your editor. This file is ignored by Git: keep real
credentials there or in your shell environment, and never stage or commit them.
Use an API key and model name from your provider:

```dotenv
LLM_API_KEY=your-provider-api-key
LLM_MODEL=your-structured-output-capable-model
LLM_BASE_URL=https://your-provider.example/v1
```

The Evidence agent needs its model to return data in a precise, validated format so
reviews and votes can be checked safely. Choose a provider that supports strict
structured outputs. Provider examples and Evidence-specific overrides are in the
[backend guide](backend/README.md) and
[Evidence guide](backend/evidence/README.md).

### 3. Start the backend

Open a terminal at the repository root:

```sh
backend/.venv/bin/python -m uvicorn api:app --app-dir backend --reload --port 8000
```

The API and interactive documentation are at <http://localhost:8000/docs>. Sessions
are held in memory and are cleared when the backend restarts. The default local
transport runs the agents in-process; using AGNTCY SLIM is optional (see the
[backend guide](backend/README.md#run-the-agents-over-agntcy-slim)).

### 4. Start the frontend

Open another terminal at the repository root:

```sh
cd frontend
npm ci
npm run dev
```

Open <http://localhost:3000>. You can explore the labeled sample immediately. To use
the AI, enter an idea and its team size, deadline, weekly hours, and budget, then
select **Start project**. Answer any questions in Project updates. Alternatively,
load a live session ID. Live requests use the configured backend provider and may
consume its quota or incur charges. The sample data is local and is never submitted
to the backend.

Never put provider API keys in frontend variables or browser code. By default the
frontend connects to `http://localhost:8000`; for a different API URL, set
`NEXT_PUBLIC_API_URL` in `frontend/.env.local` and restart Next.js. Configure the
backend's `CORS_ORIGINS` to allow the frontend's origin.

## Tests

Frontend checks (from `frontend/`):

```sh
npm test
npm run lint
npm run typecheck
npm run build
npx playwright install chromium  # once per machine
npm run test:e2e
```

The browser suite starts a production frontend on port 3100 and mocks backend
HTTP/SSE responses; it does not call AI models or verify live agent behavior.

Backend checks (from the repository root):

```sh
LLM_MODEL=test LLM_BASE_URL=https://example.invalid LLM_API_KEY=test \
  backend/.venv/bin/python -m pytest backend/tests backend/test_rules.py -q
```

For the complete live testing checklist, session API details, and deployment notes,
see [backend/README.md](backend/README.md),
[frontend/README.md](frontend/README.md).
