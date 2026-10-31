# MEDKids Medical Rap Pipeline — Development Report (MVP v0.1.0)

Date: 25 Oct 2026 · Reference: *MEDKids Medical Rap Pipeline Research* (RET, 18 Oct 2026) and the MVP build brief.

## 1. Files created

| Area | Files |
|---|---|
| App entry | `app/main.py` (app factory, middleware, error handlers, health) |
| API | `app/api/routes_topics.py`, `routes_generation.py`, `routes_evaluation.py`, `routes_review.py`, `deps.py`, `presenters.py` |
| Core | `app/core/config.py`, `logging.py`, `exceptions.py` |
| Models | `app/models/database.py` (12 tables), `app/models/schemas.py` (API + LLM output contracts) |
| LLM | `app/llm/base.py`, `claude_provider.py`, `openai_provider.py`, `mock_provider.py`, `__init__.py` (factory) |
| Prompts | `app/prompts/fact_extraction.txt`, `age_simplification.txt`, `rap_generation.txt`, `medical_validation.txt`, `safety_validation.txt`, `lyric_revision.txt` (all v1.0.0) |
| Services | `content_processor`, `fact_extractor`, `age_adapter`, `prompt_builder`, `rap_generator`, `medical_validator`, `safety_validator`, `quality_evaluator`, `revision_service`, `pipeline`, `workflow`, `review_service`, `topic_service`, `security`, `lexicon` |
| Utils | `app/utils/text_utils.py` |
| Data | `data/sample_topics/heart.json`, `brain.json` (demo data), `data/outputs/` |
| Scripts | `scripts/init_db.py`, `seed_topics.py`, `run_demo.py` |
| Tests | `tests/conftest.py`, `test_inputs_and_config.py`, `test_fact_extraction.py`, `test_prompts_and_age.py`, `test_rap_generation.py`, `test_llm_providers.py`, `test_medical_validation.py`, `test_safety_validation.py`, `test_pipeline.py`, `test_api.py`, `test_demo_and_logging.py` |
| Ops / docs | `requirements.txt`, `pyproject.toml`, `.env.example`, `.gitignore`, `.dockerignore`, `Dockerfile`, `README.md`, `ARCHITECTURE.md`, this report |

About 6,900 lines of Python.

## 2. Features implemented

- **Topic input** with Pydantic validation: age range 4–10, supported categories, non-empty and size-limited
  content, unique objectives, validated misconception regexes, and rejection of extra fields.
- **Source processing**: normalisation, SHA-256 provenance, and prompt-injection flagging.
- **Fact extraction**: the LLM proposes facts, and deterministic grounding (a verbatim evidence quote in the
  stored source) separates `candidate` from `uncertain`. The model can never approve. A **human medical
  reviewer** approval gate covers candidates, rejections and manually added facts with verified evidence.
- **Age adaptation** for 4–6 and 7–10, with a meaning-preservation guard and fallback to the approved wording.
- **Rap generation** from approved facts only: style enum (no "in the style of" free text), verses, chorus
  rules, intro/outro and 1–3 candidates. Output is structured JSON, validated with Pydantic and checked for
  structure, with bounded retries. Line ids are deterministic and every line carries its cited fact ids.
- **Medical validation**: deterministic misconception rules (topic-specific plus global), LLM claim triage
  post-checked against approved facts, a coverage guard, and a fail-closed fallback. The structured report
  separates supported, contradicted, unsupported, ambiguous and outside-scope claims.
- **Child-safety validation**: 16 categorised rule groups with negation handling, age-band complexity and
  jargon checks, and structure checks, plus LLM subjective review (fail-closed).
- **Quality evaluation**: configurable 30/25/20/15/10 weights and threshold, per-criterion explanations,
  recommendations, and hard gates that no score can override.
- **Targeted revision**: only failing or flagged lines can change (enforced in code), with a configurable cap
  of at most 3 and escalation (`needs_human_intervention`). Every version is immutable and stores provenance.
- **Human review**: an explicit state machine (`draft`, `validation_failed`, `pending_review`,
  `revision_requested`, `approved`, `rejected`). Approval requires a medical reviewer **and** an educator,
  from distinct people, on the current version, with no unresolved failures. A revision invalidates earlier
  approvals.
- **Storage**: SQLite/SQLAlchemy holds topics, sources, objectives, facts, versions, model and prompt
  versions, validations, evaluations, reviews, the workflow audit trail and per-call token, cost and latency.
  Approved raps are exported as JSON files and through an API endpoint.
- **Security**: API-key auth with roles on all non-health endpoints, and a development-only header auth that
  needs a double opt-in and is blocked in production. Body and field limits, safe error responses, and JSON
  logs with redaction.

## 3. API endpoints

`POST/GET /api/topics`, `GET /api/topics/{id}`, `POST /api/topics/{id}/facts/extract`,
`POST /api/topics/{id}/facts/approve`, `POST /api/raps/generate`, `GET /api/raps`, `GET /api/raps/{id}`,
`POST /api/raps/{id}/validate`, `POST /api/raps/{id}/evaluate`, `POST /api/raps/{id}/revise`,
`POST /api/raps/{id}/review`, `GET /api/raps/{id}/history`, `GET /api/raps/{id}/export` (addition),
`GET /api/health`. OpenAPI/Swagger is served at `/docs`.

## 4. Tests executed and results

**Environment actually used:** Python 3.13.17, FastAPI 0.161.0, Starlette 1.7.0, Pydantic 2.13.5,
SQLAlchemy 2.1.4, httpx2 2.13.1, pytest 9.1.1. PyPI was blocked in the build environment, so FastAPI and
SQLAlchemy were loaded from their official GitHub release sources and pytest from a local cache, on
`PYTHONPATH`. That is equivalent code, but `pip install -r requirements.txt` itself was **not** executed.

| Check | Result |
|---|---|
| `pytest` (offline, MockProvider, in-memory SQLite) | **142 passed, 0 failed** (103 test functions plus parametrized cases) |
| `ruff check` (E, F, W, B, UP, I) | all checks passed |
| `scripts/run_demo.py` (Heart + Brain, mock) | both raps reached `pending_review` at v1. Heart scored 91.1/100 (medical passed; safety `requires_review` for one long line). Brain scored 85.7/100. No approval was performed. |
| Live `uvicorn` + curl | health 200, `/docs` 200, topic → extract → approve → generate → medical approval → educator approval → `approved` → export OK, unauthenticated read → 401 |

Coverage spans input validation, fact extraction, age simplification, prompt construction, generation, JSON
parsing failures, medical and safety validation, scoring, revision retry limits, database operations, all
endpoints, reviewer authorization, invalid approval transitions and LLM provider failures. It includes
adversarial tests for unsupported claims, an over-confident triage model, hallucinated fact and line ids,
prompt injection in sources and lyrics, a model smuggling an "approved" flag, revision touching protected
lines, and secrets in error messages.

**Not verified (no credentials or tooling in the build environment):** real Claude and OpenAI API calls (the
adapters were tested against fake SDK clients that mirror the current SDK signatures, checked against the
`anthropic` 2.3.0 and `openai` 3.37.0 source); the `docker build`; Python 3.11/3.12 runs; and resolving
`requirements.txt` from PyPI.

## 5. Known limitations

- Rhyme, syllable, objective-coverage and "medical line" detection are lexical heuristics, uncalibrated
  against human ratings (the research doc warns these perform poorly on short lyrics).
- Misconception rules and lexicons are starter lists; they catch only anticipated phrasings.
- The mock provider writes simple, low-rhyme lyrics. Real lyric quality has to be assessed with Claude.
- Static API keys are not suitable for production identity. There is no rate limiting or CORS configuration.
- SQLite with `create_all` and a version check, but no Alembic migrations yet.
- Generation runs synchronously in the request thread; long LLM calls hold a worker.
- Below-threshold soft scores go to human review flagged, not to auto-revision, because they are not line-targetable.
- A reviewer `request_revision` without line-level issues cannot be auto-revised (returns 422; regenerate instead).
- Approved raps are terminal. A post-approval edit requires a new rap.
- Sample Heart and Brain facts are unreviewed demonstration data.

## 6. Deferred to later phases

Audio and music generation (the export has a reserved `audio` field, and an `audio_review` state is planned);
`published` and CMS integration; the multi-model blind benchmark harness; RAG over a larger approved corpus;
PDF/OCR ingestion and file uploads; a reviewer UI; SSO; PostgreSQL; and async workers.

## 7. Recommended next steps

1. Have RET's medical reviewer and educator formally approve the Heart and Brain fact cards, and expand the
   forbidden-misconception lists.
2. Run the demo with `LLM_PROVIDER=claude` and a current model, then review 10–20 candidates per age band to
   calibrate thresholds and weights against reviewer ratings (inter-rater agreement).
3. Freeze 8–12 objectives across the pilot and build a regression set of approved and rejected lyrics.
4. Build a minimal reviewer UI showing each lyric line beside its mapped facts and findings.
5. Production hardening per ARCHITECTURE.md §9: SSO, PostgreSQL + Alembic, rate limiting, secrets manager,
   logging retention, provider data terms, and a children's privacy review.
6. Benchmark Claude against alternatives on cost per *approved* song, then design the licensed audio stage.

## Assumptions made

- The categories are Human Anatomy, Body Parts, Body Systems, Organ Functions, Medical Professions and Health
  Education.
- A topic spanning both bands defaults to the younger (4–6) band unless the request names one.
- Required approvers are `medical_reviewer` and `educator` (configurable). The research proposal's
  content-owner sign-off for rights and branding is left to the existing RET process.
- The retry cap defaults to 3 per the build brief (the research doc suggests 2; it is configurable).
- Soft-score weights follow the build brief (30/25/20/15/10), and accuracy and safety are additionally hard
  gates, per the research doc.
