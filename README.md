# MEDKids Medical Rap Pipeline (MVP)

> **Quick start (Windows):** double-click **`run.bat`**. It sets everything up on first run, starts the app and
> opens it in your browser at <http://127.0.0.1:8000/>. No API keys needed. Keep the window open while you use it.

A human-supervised AI pipeline from Rebecca Everlene Trust Company (RET). It turns **approved** medical facts into
age-appropriate educational rap lyrics for children aged 4–10. The pipeline validates every lyric line against
those approved facts and checks it for child safety. It scores quality with an experimental rubric and fixes
failing lines through capped, targeted revision. **Nothing is published without human approval.**

```
Topic input → source processing → fact extraction (LLM proposes) → HUMAN fact approval → age adaptation
→ prompt construction → Claude generation → medical validation → child-safety validation → quality scoring
→ targeted revision (max 3) → HUMAN review (reviewer approves) → approved lyrics storage / export
```

Medical accuracy always takes priority over rhyme. See [ARCHITECTURE.md](ARCHITECTURE.md) for the design.

> **Demonstration data:** `data/sample_topics/heart.json` and `brain.json` are starter facts for testing,
> not formally approved MEDKids source documents. Topics, facts and raps built from them are flagged
> `is_demo_data` and cannot be approved when `APP_ENV=production`.

---

## 1. Prerequisites

- Python **3.10+** (tested on 3.13; Windows launcher verified with Anaconda 3.10)
- pip
- An Anthropic API key, only if you want real Claude generation. Everything else, including all tests,
  runs offline with the deterministic mock provider.

## 2. Create a virtual environment

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

```powershell
# Windows (PowerShell)
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Configure environment variables

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

Edit `.env` only if you want to change settings (for example to enable Claude). `run.bat` uses **local mode**,
so no keys are needed. If you run the API without `run.bat`, either use local mode or add API keys:

```
API_KEYS=me:author:<random-key>,me-review:reviewer:<random-key>
```

Generate keys with `python -c "import secrets;print(secrets.token_urlsafe(24))"`. Callers send them as an
`X-API-Key` header. The roles are:

| Role | Can do |
|---|---|
| `author` | create topics, extract candidate facts, generate / validate / evaluate / revise raps |
| `reviewer` | approve or reject facts; review and approve raps (the one human approval) |
| `admin` | author permissions; cannot approve facts or raps |

**Local mode** (what `run.bat` uses): `AUTH_MODE=dev` **and** `DEV_AUTH_ENABLED=true`. The web app then needs
no keys; API callers send `X-Dev-User: name:role`. Author and reviewer are usually the same person here; the
roles just keep writing and approving as separate steps.

## 5. Initialise the database

```bash
python scripts/init_db.py        # creates data/medkids.db (idempotent)
python scripts/seed_topics.py    # optional: loads the Heart + Brain demo topics (facts NOT approved)
```

The schema is created with SQLAlchemy `create_all`, and a `schema_meta` table records the schema version. The
app also initialises the schema on startup and refuses to run against a database with a different schema
version. For production, adopt Alembic migrations (see ARCHITECTURE.md §9).

## 6. Start FastAPI

```bash
uvicorn app.main:app --reload
```

## 7. Web app (MEDKids Rap Studio)

Open <http://127.0.0.1:8000/>. On Windows, `run.bat` sets everything up and opens it for you.

1. In local mode there is nothing to sign in to. (With API keys instead, click **API keys** and paste them.)
2. Use **Working as** (top right) to switch roles. Each role only sees the actions it is allowed to take, and the
   server enforces the same rules.
3. Follow the four steps:
   - **Topic:** create a topic (or load the Heart or Brain sample).
   - **Facts:** as Author, propose facts; switch to Reviewer to tick and approve them.
   - **Raps:** an author writes a rap; checks and auto-fixes run automatically.
   - **Review:** read the lyric sheet. Each line's margin colour shows whether it matches an approved fact (teal),
     needs a reviewer's eye (amber) or is blocked (red). Click lines to attach notes, then approve, request
     changes, comment or reject. Once the reviewer approves, download the approved lyrics.

The web app is plain HTML, CSS and JavaScript in `app/static/` (no build step). It calls the same authenticated
API as everything else, and all server text is inserted as text, never as HTML.

## 8. Swagger / OpenAPI

- Swagger UI: <http://127.0.0.1:8000/docs>
- ReDoc: <http://127.0.0.1:8000/redoc>
- Health (public): <http://127.0.0.1:8000/api/health>

Click **Authorize**, or add an `X-API-Key` header, to call the protected endpoints.

### Typical API workflow

```bash
A="X-API-Key: <author-key>"; R="X-API-Key: <reviewer-key>"
# 1. topic  (see data/sample_topics/heart.json for a full example body)
curl -X POST localhost:8000/api/topics -H "$A" -H 'content-type: application/json' -d @topic.json
# 2. candidate facts (never auto-approved)
curl -X POST localhost:8000/api/topics/1/facts/extract -H "$A"
# 3. reviewer approves selected facts
curl -X POST localhost:8000/api/topics/1/facts/approve -H "$R" -H 'content-type: application/json' \
     -d '{"approve_fact_ids": ["heart_fact_001","heart_fact_002"]}'
# 4. generate (runs validation, scoring and up to 3 targeted auto-revisions)
curl -X POST localhost:8000/api/raps/generate -H "$A" -H 'content-type: application/json' -d '{"topic_id": 1}'
# 5. human review: the reviewer approves the current version
curl -X POST localhost:8000/api/raps/1/review -H "$R" -H 'content-type: application/json' -d '{"decision":"approve"}'
curl localhost:8000/api/raps/1/export -H "$A"
```

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/topics` | Submit topic, source text, objectives, forbidden misconceptions |
| GET | `/api/topics`, `/api/topics/{id}` | List / view topics (with facts and flags) |
| POST | `/api/topics/{id}/facts/extract` | LLM proposes candidate facts (grounding-checked) |
| POST | `/api/topics/{id}/facts/approve` | Medical reviewer approves/rejects facts or adds verified manual facts |
| POST | `/api/raps/generate` | Generate 1–3 candidates; optional checks + capped auto-revision |
| GET | `/api/raps`, `/api/raps/{id}` | List (filter by `topic_id`, `status`) / full detail with findings and scores |
| POST | `/api/raps/{id}/validate` | Medical + child-safety validation of the current version |
| POST | `/api/raps/{id}/evaluate` | Rubric scoring and hard gates (validate first) |
| POST | `/api/raps/{id}/revise` | Targeted revision of failing or reviewer-flagged lines |
| POST | `/api/raps/{id}/review` | `approve` / `reject` / `request_revision` / `comment` |
| GET | `/api/raps/{id}/history` | All versions, validations, evaluations, reviews, transitions |
| GET | `/api/raps/{id}/export` | Structured export of an approved rap |
| GET | `/api/health` | Liveness + DB check |
| GET | `/api/me`, `/api/samples` | Current user and role; Heart/Brain sample topics (used by the web app) |
| GET | `/` | Web app (redirects to `/ui/`) |

Errors always look like `{"error": {"code": "...", "message": "...", "details": {...}}}`. The status codes are
401 (no or invalid credentials), 403 (wrong role), 404, 409 (invalid workflow transition, e.g. approving a rap
with unresolved medical errors), 413 (body too large), 422 (invalid input) and 502/503/504 (LLM failures, with
safe fixed messages).

## 9. Run the tests

```bash
pytest
```

The suite uses the deterministic `MockProvider` and in-memory SQLite. It makes **no external API calls** and
needs no credentials. It covers input validation, fact extraction, age adaptation, prompts, generation, JSON
parsing failures, medical and safety validation, scoring, the revision cap, the database, every endpoint,
reviewer authorization, invalid transitions, provider failures, and adversarial cases (unsupported claims,
prompt injection, a model trying to self-approve).

## 10. Heart and Brain demos

```bash
python scripts/run_demo.py
```

For each topic, the demo loads the topic and extracts candidate facts. It then approves them using a **clearly
labelled development fixture** (`DEV-FIXTURE (demo only - not a human medical review)`). It generates,
validates and evaluates a rap (with capped auto-revision), saves `data/outputs/demo_<topic>.json`, and prints the
review status. The raps stop at `pending_review`: the demo never approves lyrics. It uses its own
`data/demo.db` and refuses to run with `APP_ENV=production`.

## 11. Enable the Claude API

In `.env`:

```
LLM_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=<a current Claude model id from https://docs.claude.com/en/docs/about-claude/models>
# optional: CLAUDE_EFFORT=medium ; LLM_PRICE_INPUT_PER_MTOK / LLM_PRICE_OUTPUT_PER_MTOK for cost estimates
```

Then run `python scripts/run_demo.py` or start the API. The provider uses the official `anthropic` SDK
(`client.messages.create`) and requests JSON through `output_config.format` (structured outputs). Every response
is still validated locally with Pydantic and retried a bounded number of times. Set
`CLAUDE_USE_STRUCTURED_OUTPUTS=false` if the model you choose does not support structured outputs. Retries with
exponential backoff are handled by the app (SDK retries are disabled). Token usage, latency and estimated cost
are stored per call in the `llm_calls` table.

To use OpenAI instead, set `LLM_PROVIDER=openai`, `OPENAI_API_KEY` and `OPENAI_MODEL`. It uses the Responses API.

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| `401 missing credentials` | Send `X-API-Key`; make sure the key is listed in `API_KEYS` (restart after editing `.env`). |
| `403 role ... not allowed` | Use a key with the right role (for example, only `reviewer` can approve facts). |
| `409 topic has no approved facts` | Run `/facts/extract`, then `/facts/approve` with a medical-reviewer key. |
| `409 approval blocked: ...` | Read `details.blockers`. Fix validation failures (revise) or wait for status `pending_review`. |
| `CLAUDE_MODEL must be set` at startup | Set `CLAUDE_MODEL` or switch back to `LLM_PROVIDER=mock`. |
| `502 llm_auth_failed` | Check `ANTHROPIC_API_KEY`. |
| `502 llm_invalid_output` | The model kept returning invalid JSON. Try another model, `CLAUDE_USE_STRUCTURED_OUTPUTS=true`, or a higher `LLM_MAX_TOKENS`. |
| `503 llm_rate_limited` / `504 llm_timeout` | Retry later, or raise `LLM_MAX_RETRIES` / `LLM_TIMEOUT_SECONDS`. |
| `Database schema version ... != code version` | You have an old DB; delete `data/medkids.db` in development or migrate. |
| `ModuleNotFoundError: app` | Run commands from the project root. |
| Rap stuck in `validation_failed` with `needs_human_intervention` | The 3 auto-revisions did not fix it. Inspect `/history`, then edit facts and regenerate, or reject. |

## Project layout

```
app/
  main.py               FastAPI app factory, error handlers, health
  api/                  routes_topics / routes_generation / routes_evaluation / routes_review, deps, presenters
  core/                 config (env settings + production guards), logging (JSON + redaction), exceptions
  models/               database.py (SQLAlchemy models, schema init), schemas.py (Pydantic contracts)
  llm/                  base.py (retry/backoff, JSON validation, usage/cost), claude/openai/mock providers
  prompts/              versioned prompt templates (one per stage)
  services/             content_processor, fact_extractor, age_adapter, prompt_builder, rap_generator,
                        medical_validator, safety_validator, quality_evaluator, revision_service,
                        workflow (state machine), pipeline (orchestration), review_service, security
  utils/text_utils.py   normalisation, untrusted-data wrapping, rhyme/syllable heuristics
data/sample_topics/     Heart + Brain demonstration topics
scripts/                init_db.py, seed_topics.py, run_demo.py
tests/                  pytest suite (offline)
```
