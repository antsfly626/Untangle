# Evidence & Risk Agent

Independent, asynchronous review of one exact candidate proposal, registered as the
Evidence agent in the existing AGNTCY A2A-over-SLIM runtime. The HTTP endpoint is
also available for development. No separate negotiation engine is introduced.

## Evidence-specific configuration

Follow the [backend README](../README.md#setup) for Python installation, shared
`LLM_*` provider settings, and starting the unified session API. The backend reads
those values from the ignored `backend/.env` file or the current shell environment;
never stage or commit `.env` files.

For Evidence-specific credentials or provider routing, these optional variables can
be added to that same local file or exported in the shell:

```dotenv
EVIDENCE_API_KEY=your-provider-key
EVIDENCE_MODEL=your-structured-output-capable-model
SEMANTIC_SCHOLAR_API_KEY=your-key
EVIDENCE_BASE_URL=https://your-provider.example/v1
```

`EVIDENCE_API_KEY` and `EVIDENCE_MODEL` override `LLM_API_KEY` and `LLM_MODEL`.
When `EVIDENCE_API_KEY` is set, also set `EVIDENCE_BASE_URL` for its provider;
otherwise Evidence uses the shared `LLM_BASE_URL`. Without an explicit Evidence
endpoint, generic proxy roots get `/v1` appended when needed, and Gemini roots are
normalized to `/v1beta/openai/`. Explicit `EVIDENCE_BASE_URL` values are preserved.

The configured provider must support **strict structured outputs** via Chat
Completions; JSON mode alone is insufficient. An incompatible proxy produces a safe
error rather than regex parsing. The standalone Evidence HTTP service loads
credentials lazily; the shared agent server still requires `LLM_MODEL`,
`LLM_BASE_URL`, and `LLM_API_KEY` at startup.

To run an Evidence-only development server instead of the unified session API:

```sh
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000/docs>. This is a local development endpoint; deployment
authentication and per-session authorization belong in the application's API layer.

## Request and result

```sh
curl http://localhost:8000/agents/evidence/review \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "session-1",
    "round_id": "round-1",
    "proposal_id": "proposal-1",
    "project_idea": "AI study assistant",
    "constraints": {"team_size": 2, "deadline_days": 7},
    "candidate_plan": {
      "features": ["Keyboard-accessible quizzes"],
      "retention": "Delete uploaded documents immediately after processing",
      "security": "Private per-user upload access",
      "claims": "Learning benefits are unvalidated; collect pilot feedback"
    },
    "prior_objections": []
  }'
```

The HTTP response is an `EvidenceResponse` envelope:

- `session_id`, `round_id`: exact input references.
- `review`: proposal ID, evidence agent ID, explicit vote, informational score,
  categorized/severity-tagged objections, requested changes, concise reasoning,
  retrieved paper IDs and per-assumption evidence assessments.
- `papers`: API-sourced IDs, titles, HTTP(S) URLs, years, nullable abstracts/DOIs,
  authors and source. Never populated from model-generated metadata.
- `warnings`: retrieval failures, skipped records, absent abstracts and full-text
  limitations. Display these even if the vote is accept.
- `research_plan`: empirical assumptions, up to two neutral queries, relevance
  rationale and whether research is essential.
- `risk_ids`: deterministic UUID-based IDs aligned with `review.objections`.
  Identical findings have identical IDs; changed findings get new IDs. Namespace
  graph instances with session/proposal ID. Connect via `affected_feature` (existing
  node ID when available, otherwise feature name). Assessments link papers to
  assumption text and expose supporting/challenging/mixed/inconclusive stances.

Risk IDs identify findings, not acceptance states. Accepted/rejected/unresolved
decision states come from the negotiation protocol, not this agent's individual vote.

The endpoint returns 422 for invalid inputs, 503 for missing model configuration, and
502 for model failures/refusals or invalid/inconsistent outputs. The response
identifies authentication, access, model/endpoint, quota, connection, timeout, or
invalid-output failures with fixed messages that never include provider error bodies. Research failures
alone remain warnings and allow risk evaluation to continue. If research is essential
and no abstracts are available, an output other than abstain is invalidated rather
than converted into an invented vote. Abstract relevance and remaining uncertainty
are evaluated by the model; availability alone does not establish adequacy.

Model calls use a 30-second SDK timeout and at most one SDK retry each. Research
uses at most two queries, four records/query, a 6-second request timeout and one
retry; long rate-limit waits are skipped. HTTP timeout values are per operation,
not an end-to-end session deadline. Callers should apply their session deadline.

## AGNTCY runtime (already connected)

The existing `agent_server.py` wraps `agents.EVIDENCE` in an A2A `AgentExecutor` and
serves its card through `AgntcyFactory.create_app_session().add_a2a_card(...)`.
`EvidenceAgent` now delegates to `evidence/negotiator.py`, which invokes the real
research/evaluation pipeline and adapts results to shared `Review`/`CandidateVote`.
Architect and Delivery retain their existing implementations.

Start SLIM using the current [backend instructions](../README.md), then:

```sh
# From backend/, in the Python 3.13 environment:
python agent_server.py all
# Or start just this agent in its own terminal:
python agent_server.py evidence
# In another terminal, with all three servers running:
UNTANGLE_TRANSPORT=slim python negotiation.py
```

`UNTANGLE_TRANSPORT=local` remains the default for development; it uses the same
research-backed Evidence agent. The SLIM server address is `SLIM_SERVER` (default
`localhost:46357`); all participants must share `SLIM_SHARED_SECRET`. Existing pinned
SDK versions and server startup code are retained.

The negotiation transport already sends `op="review"` and `op="vote"` JSON in A2A
TextParts. No caller rewrite is needed. Each review includes a proposal reference
based on round and candidate content. Candidate voting preserves the supplied
candidate ID. Full Evidence envelopes plus a SHA-256 candidate fingerprint travel
in `Review.evidence` / `CandidateVote.evidence` and are saved in
`Plan.negotiation[].evidence`, including papers, warnings, risks, and requested changes.
The receiver checks candidate identity/content, session, round, agent and vote;
stale or duplicate ballots cannot count as an acceptance.

The Evidence score is normalized to 0–1 for existing displays but marked
`score_informational=True` and excluded from the Nash tie-break. Votes are never
threshold-overridden. Explicit abstentions and provider failures remain nonaccepts.
Other agents’ scoring and the existing consensus protocol are unchanged.

For typed shared plans, the model can return a structured `counteroffer` with node
status changes and new mitigation nodes with cost/effort estimates. These are
validated and fed to the existing counteroffer protocol. Locked/required nodes are
protected. The adapter does not infer preference for an optional improvement from a
score: `prefer_over_current` stays false, so Evidence does not automatically approve
a Pareto improvement merely because it accepts that candidate.

The same A2A executor also supports the original `type="evaluate_plan"` application
message through `handle_evidence_message()`, with envelope/payload ID validation.
This alternate request returns the original `agent_review` envelope. The negotiation
broadcaster uses the `op` contract above. Neither application JSON format is an
AGNTCY SDK schema; A2A/SLIM carries the JSON using the existing shared transport.

For direct Python calls from the repository root,
`backend.evidence.agent.review_plan(request)` returns only `EvidenceReview`;
prefer `review_with_evidence(request)` to retain papers/warnings.

## Tests and manual scenarios

```sh
LLM_MODEL=test LLM_BASE_URL=https://example.invalid LLM_API_KEY=test \
  python -m pytest backend/tests backend/test_rules.py -q
# Existing Developer A rules, with placeholders to satisfy legacy import-time config:
LLM_MODEL=test LLM_BASE_URL=https://example.invalid LLM_API_KEY=test \
  python -m pytest backend/test_rules.py -q
# Optional, costs API usage and requires configured credentials:
RUN_EVIDENCE_LIVE=1 python -m pytest backend/tests/test_evidence_live.py -s
```

Automated tests mock model and HTTP responses and do not prove live model judgment.
The opt-in test exercises the actual structured cloud call and, if the planner
chooses research, Semantic Scholar. It does not assert a predetermined vote.

For a behavioral demo, submit these variations with distinct proposal IDs:

| Scenario | Candidate variation | Inspect |
| --- | --- | --- |
| A: Privacy | Store uploads forever, no deletion mechanism | Specific retention objection and feasible deletion policy; rejection is reasonable |
| B: MVP | Temporary private uploads, keyboard-accessible quizzes, limited scope | Ability to accept without generic invented blockers |
| C: Missing information | Guaranteed learning improvements with no population, measures or supporting evidence | Abstention or concrete challenge with the missing information explained |
| D: Revision | Replace A's retention with B's policy; include A's prior objections | Fresh evaluation of the new ID; resolved concern no longer blocks automatically |

Source references: [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
and [Semantic Scholar Academic Graph API](https://api.semanticscholar.org/api-docs/graph).

AGNTCY references: [CoffeeAGNTCY](https://github.com/agntcy/coffeeAgntcy),
[App SDK A2A serving](https://github.com/agntcy/app-sdk#1-serve-an-a2a-agent),
and [AGNTCY documentation](https://docs.agntcy.org/).

Gemini source: [Google’s OpenAI compatibility documentation](https://ai.google.dev/gemini-api/docs/openai).
