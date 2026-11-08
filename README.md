# MEDKids Medical Rap Pipeline (MVP)

> **Quick start (Windows):** double-click **`run.bat`**, then use the app at <http://127.0.0.1:8000/>.
> Full instructions are in [How to run the app](#how-to-run-the-app) below.

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

## How to run the app

This is an internal tool that runs on your own computer. There is nothing to deploy and no account to create:
the app runs in **local mode**, so there are no passwords or API keys to enter.

### What you need

- **Windows 10/11** (the one-click launcher is a Windows file; macOS/Linux steps are further down).
- **Python 3.10 or newer.** To check, open Command Prompt and run `py -0p` (or `python --version`). Anaconda's
  Python 3.10+ works too. If you don't have it, install Python 3.12 from <https://www.python.org/downloads/> and
  tick **"Add python.exe to PATH"** during setup.
- **Internet access on the first run only**, to download the Python packages.
- Optional: an **Anthropic API key**, only if you want Claude to write the lyrics (see
  [Use Claude for real lyrics](#5-use-claude-for-real-lyrics)). Without it, a built-in offline writer is used.

### 1. Get the code

Either:

- **With git:** open Command Prompt in the folder where you keep projects and run
  ```
  git clone https://github.com/skveer1902/medkids-medical-rap-pipeline.git
  ```
- **Without git:** on the GitHub page click **Code → Download ZIP**, then right-click the ZIP and choose
  **Extract All**.

You now have a folder called `medkids-medical-rap-pipeline` containing `run.bat`, `app`, `data` and so on.

### 2. Start the app

1. Open the `medkids-medical-rap-pipeline` folder in File Explorer.
2. Double-click **`run.bat`**. (If Windows shows "Windows protected your PC", click **More info → Run anyway**;
   the file is a plain text script you can open in Notepad to inspect.)
3. A black window titled **MEDKids Rap Studio** opens. It:
   - finds Python 3.10+ on your computer,
   - creates a private Python environment in a `.venv` folder (**first run only**, about 1–2 minutes),
   - installs or checks the required packages,
   - creates a `.env` settings file from `.env.example` if you don't have one,
   - prepares the database (`data/medkids.db`),
   - starts the app and opens <http://127.0.0.1:8000/> in your default browser.
4. When the window says **"MEDKids Rap Studio is running at http://127.0.0.1:8000"**, the app is ready.

**Keep the black window open while you use the app.** Closing it stops the app. Later runs skip the setup and
start in a few seconds.

### 3. Use the app

The browser shows **MEDKids Rap Studio**. Top right, **Working as** switches between the two roles. One person
usually does both; the roles just keep writing and approving as separate steps:

| Role | What it does |
|---|---|
| **Author** | creates topics, proposes facts, writes raps, fixes flagged lines |
| **Reviewer** | approves or rejects facts, then approves, rejects or requests changes to raps |

Work through the four steps on the left:

1. **Topic** (as Author). Click **New topic**. Click **Heart** or **Brain** to fill the form with a sample, or
   type your own: topic name, category, youngest and oldest age (4–10), the approved source text, one learning
   goal per line, any misconceptions to block (one per line) and allowed science words. Click **Create topic**.
2. **Facts.** As Author, click **Propose facts from source**: the AI drafts facts, and each one must quote the
   source word for word or it is marked *Uncertain*. Then switch **Working as** to **Reviewer**, tick the facts
   that are correct and click **Approve selected** (or **Reject selected**). Only approved facts can be used in songs.
3. **Raps** (as Author). Choose the age group (a topic covering 4–10 defaults to **Ages 4–6**; pick **Ages 7–10**
   for older children), style, number of verses and versions, then click **Write rap**. The app writes the
   lyrics, checks every line for medical accuracy and child safety, scores it, and automatically fixes failing
   lines (up to 3 tries). Untick **Auto-fix problems** to see the raw result instead.
4. **Review** (as Reviewer). The lyric sheet shows a coloured bar beside each line:
   - **teal**: matches an approved fact (the fact is shown under the line),
   - **amber**: needs your attention,
   - **red**: blocked (inaccurate or unsafe).

   On the right are the must-pass checks, the score breakdown, the medical and safety findings, and the history.
   Click lines to point at them, write feedback, then:
   - **Approve**: the rap becomes final (only possible once all must-pass checks pass),
   - **Request changes**: sends it back; switch to Author and click **Fix flagged lines**,
   - **Add comment** or **Reject**.

   Once approved, click **Download approved lyrics** to save a JSON copy. Approved lyrics are also saved
   automatically in `data/outputs/approved/`.

Everything you create is stored in `data/medkids.db` and is still there the next time you start the app.

### 4. Stop the app

Close the black **MEDKids Rap Studio** window, or click in it and press **Ctrl+C** (then any key). The browser
page stops working until you run `run.bat` again.

### 5. Use Claude for real lyrics

Out of the box the app uses a simple **offline writer** (no internet, no cost), so lyrics are basic. To have
Claude write them:

1. Open `.env` in the project folder with Notepad.
2. Set these lines:
   ```
   LLM_PROVIDER=claude
   ANTHROPIC_API_KEY=sk-ant-...your key...
   CLAUDE_MODEL=<a current model id from https://docs.claude.com/en/docs/about-claude/models>
   ```
3. Save, close the black window, and run `run.bat` again.

Keep `.env` private: it holds your API key and is never uploaded to GitHub. To go back to the offline writer,
set `LLM_PROVIDER=mock`.

### 6. Run it on macOS or Linux

From the project folder in a terminal:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                      # first time only
AUTH_MODE=dev DEV_AUTH_ENABLED=true uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000/>. Stop with **Ctrl+C**. Next time, only the `source` and last lines are needed.

### 7. Get the latest version

- **If you cloned with git:** in the project folder run `git pull`, then start `run.bat` as usual (it installs any
  new packages automatically).
- **If you downloaded the ZIP:** download it again and copy your `.env` and `data` folder into the new one to
  keep your settings and saved work.

### 8. Start again with an empty app

Stop the app, delete `data/medkids.db`, and run `run.bat`. A fresh, empty database is created. (This deletes all
topics, facts and raps.) To also reinstall packages, delete the `.venv` folder.

### 9. Common problems

| Problem | What to do |
|---|---|
| Window says **"Python 3.10 or newer was not found"** | Install Python 3.12 from python.org with "Add python.exe to PATH" ticked, then run `run.bat` again. |
| Window closes immediately or shows **"Setup failed"** | Read the message above it. Usually a package download failed: check your internet connection and run `run.bat` again. |
| Browser shows **"This site can't be reached"** | The app isn't running. Start `run.bat` and wait for the "is running" line, then refresh. |
| Error about **port 8000** ("address already in use" or "only one usage of each socket address") | The app is already running in another window (or another program uses port 8000). Close the other black window and try again. |
| Browser asks for **API keys** | You started the server without `run.bat`. Use `run.bat`, or see the macOS/Linux command above (it turns on local mode). |
| **"Approve some facts in step 2 before writing a rap"** | Switch to **Reviewer** and approve facts on the Facts step first. |
| **Approve** is greyed out or says **"Can't approve yet"** | The rap failed a must-pass check. As Author, click **Fix flagged lines** (or **Run checks again**), then review again. |
| Rap shows **"Needs a person to fix"** | The 3 automatic fixes didn't solve it. Change the facts or write a new rap, or reject it. |
| Lyrics are very simple | You're using the offline writer. See [Use Claude for real lyrics](#5-use-claude-for-real-lyrics). |
| Error mentioning **`CLAUDE_MODEL` / `ANTHROPIC_API_KEY`** | Check the three Claude lines in `.env`, or set `LLM_PROVIDER=mock`. |
| **"Database schema version ... != code version"** | Your database is from an older version. Follow [Start again with an empty app](#8-start-again-with-an-empty-app). |

---

# Reference

The sections below describe the manual setup, the API and the internals. You don't need them to use the app.

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
py -3.12 -m venv .venv
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

Click **Authorize**, or add an `X-API-Key` header, to call the protected endpoints. In local mode, send
`X-Dev-User: yourname:author` or `X-Dev-User: yourname:reviewer` instead.

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
| POST | `/api/topics/{id}/facts/approve` | Reviewer approves/rejects facts or adds verified manual facts |
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
| `409 topic has no approved facts` | Run `/facts/extract`, then `/facts/approve` with a reviewer key. |
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
  api/                  routes_topics / routes_generation / routes_evaluation / routes_review / routes_session,
                        deps, presenters
  static/               web app (index.html, styles.css, app.js)
  core/                 config (env settings + production guards), logging (JSON + redaction), exceptions
  models/               database.py (SQLAlchemy models, schema init), schemas.py (Pydantic contracts)
  llm/                  base.py (retry/backoff, JSON validation, usage/cost), claude/openai/mock providers
  prompts/              versioned prompt templates (one per stage)
  services/             content_processor, fact_extractor, age_adapter, prompt_builder, rap_generator,
                        medical_validator, safety_validator, quality_evaluator, revision_service,
                        workflow (state machine), pipeline (orchestration), review_service, security
  utils/text_utils.py   normalisation, untrusted-data wrapping, rhyme/syllable heuristics
data/sample_topics/     Heart + Brain demonstration topics
scripts/                init_db.py, seed_topics.py, run_demo.py, make_local_env.py
demo/                   in-browser demo (runs the UI with no server)
run.bat                 one-click Windows launcher (local mode)
tests/                  pytest suite (offline)
```
