# MEDKids Medical Rap Pipeline — Architecture

This document describes the MVP as built. It follows the RET research proposal *MEDKids Medical Rap Pipeline
Research* (18 Oct 2026): lock a curated fact ledger before creative generation, trace each factual lyric claim
to a source, separate hard gates from soft scores, and block publication until human review is complete. It
notes where the implementation deviates from, or extends, that proposal.

## 1. Design principles

1. **Accuracy over creativity.** The generator may only use approved facts; a rhyme never justifies a new fact.
2. **LLMs propose, humans dispose.** LLM output is untrusted. Fact candidates, claim triage and safety review
   from a model are inputs to human decisions, never decisions themselves.
3. **Defence in depth.** Each LLM-assisted check is paired with deterministic checks that can override it but
   are never overridden by it.
4. **Fail closed.** If an automated check is unavailable, the result is at best `requires_review`, never `passed`.
5. **Traceability.** Every fact has a source and evidence quote. Every lyric version stores its provider, model,
   prompt name, version and SHA-256, generation config and run id. Every status change is an audit event.
6. **Simple, replaceable parts.** Plain Python services, an explicit state machine and a provider-neutral LLM
   interface. No LangChain, queues or vector stores in the MVP.

## 2. Component map

```
            ┌──────────── FastAPI (app/api) ─────────────┐
            │ auth deps → routes → presenters            │
            └───────────────┬────────────────────────────┘
                            │ calls services only (no business logic in routes)
 ┌──────────────────────────▼───────────────────────────────────────────────────────┐
 │ services/                                                                        │
 │ topic_service ─ content_processor          fact_extractor ── approve_facts (human)│
 │ pipeline: age_adapter → prompt_builder → rap_generator → medical_validator       │
 │           → safety_validator → quality_evaluator → revision_service (capped)     │
 │ workflow (state machine)   review_service (human approval, export)   security    │
 └──────────────┬───────────────────────────────────────┬──────────────────────────┘
                │                                       │
       llm/ LLMProvider (base: retry, JSON,      models/ SQLAlchemy + SQLite
       usage/cost) ← Claude | OpenAI | Mock       (topics, facts, versions, results…)
```

## 3. Pipeline stages

| # | Stage | Module | Deterministic controls | LLM role |
|---|---|---|---|---|
| 1 | Topic input | `schemas.TopicCreate`, `topic_service` | age range 4–10 and min≤max, supported categories, non-empty content, length limits, regex validation, extra fields rejected | — |
| 1b | Source processing | `content_processor` | NFKC normalisation, control-char stripping, size limit, SHA-256, prompt-injection pattern flags | — |
| 2 | Fact extraction | `fact_extractor` | evidence quote must be a verbatim substring of the stored source; fact must overlap its quote; unknown source → uncertain; injection-like evidence → uncertain; forbidden-misconception match → uncertain | proposes candidates |
| 2b | Fact approval | `approve_facts` | `medical_reviewer` only; `uncertain`/`rejected` facts cannot be approved; manual facts need a verbatim quote; all-or-nothing | none |
| 3 | Age adaptation | `age_adapter` | two band profiles; meaning-preservation guard (no new absolutes, numbers or medical terms; must share key words; no forbidden match) with fallback to the approved wording | rephrases |
| 4 | Generation | `rap_generator`, `prompt_builder` | schema validation, structural checks (required sections, chorus length and topic hook), deterministic line ids, unapproved fact ids dropped, bounded retries | writes lyrics |
| 5 | Medical validation | `medical_validator` | topic + global misconception regexes → `contradicted`/critical; post-checks on LLM claims; coverage guard for un-checked body/health lines; fallback on failure | claim triage |
| 6 | Safety validation | `safety_validator` | categorised phrase rules with negation context, age-band complexity limits, jargon vs approved vocabulary, structure | subjective tone review |
| 7 | Evaluation | `quality_evaluator` | weighted soft scores + independent hard gates | — |
| 8 | Revision | `revision_service`, `pipeline.revise` | only failing/flagged line ids may change (enforced in code); cap of `MAX_REVISION_ATTEMPTS` (≤3); escalation flag | rewrites flagged lines |
| 9 | Human review | `review_service`, `workflow` | approval blockers; approvals bound to the current version; distinct people per required role | — |
| 10 | Storage/export | `models.database`, `export_approved` | approved export JSON + DB record | — |

### Claim statuses (Stage 5)

`supported` (an approved fact id is cited and lexically grounded), `contradicted` (a known misconception, or a
conflict with a fact; critical), `unsupported` (a medical statement with no approved fact; major), and
`ambiguous` or `outside_scope` (minor, for human review). Confidence scores are neither requested nor used. A
report is `failed` if any claim is critical or major, `requires_review` if any is minor or triage was
unavailable, and `passed` otherwise.

### Scoring (Stage 7)

| Criterion | Weight (default) | Method (heuristic, uncalibrated) |
|---|---:|---|
| Medical accuracy | 30% | share of supported claims; 0 if any contradiction; −15 per unsupported |
| Educational coverage | 25% | objectives whose content words appear in a line or adjacent line pair (≥60%) |
| Age appropriateness | 20% | over-long lines, complexity findings |
| Rhyme & rhythm | 15% | neighbour end-rhyme rate (70%) + syllable consistency (30%) |
| Memorability | 10% | chorus present, repeated, names topic, short, internal repetition |

Hard gates (they cannot be outweighed): the medical report is not `failed`, the safety report is not `failed`,
the required structure is present, and at least one approved fact is cited. **Deviation from the research
rubric:** the research doc treats medical accuracy purely as a hard gate and lists different soft weights
(coverage 30 / age 25 / rhythm 20 / clarity 15 / originality 10). The build follows the requested 30/25/20/15/10
weights *and* keeps accuracy and safety as hard gates. Weights and the threshold are configurable
(`SCORE_WEIGHTS`, `SOFT_SCORE_PASS_THRESHOLD`) for calibration. A rap whose gates pass but whose soft score
falls below the threshold still goes to `pending_review`, with `soft_score_passed=false` shown to reviewers.
The heuristics cannot target specific lines for revision, and a low score alone is not a safety problem.

## 4. Workflow state machine

```
draft ──validate/evaluate──▶ pending_review ──human──▶ approved (terminal)
  │                              │   │
  │                              │   └─human──▶ rejected (terminal)
  ▼                              └─human──▶ revision_requested ──revise──▶ draft
validation_failed ──revise (auto ≤3)──▶ draft
```

- `approved`, `rejected` and `revision_requested` can only be entered with a reviewer principal. The state
  machine itself raises if called with a system actor (tested).
- Approval requires: status `pending_review`; the current version's medical and safety reports not `failed`;
  all hard gates passed; no critical reviewer comment on that version; not demo data in production; and one
  approval from **each** role in `REQUIRED_APPROVAL_ROLES` (default `medical_reviewer,educator`), from distinct
  people, **for the current version**. A revision creates a new version, so earlier approvals stop counting.
- After `MAX_REVISION_ATTEMPTS` automatic revisions (including attempts that change nothing), the rap stays
  `validation_failed` with `needs_human_intervention=true`. The loop is a bounded `while` with the counter
  persisted on the rap, so it cannot run forever.
- The research proposal's richer states (`needs_fact_review`, `audio_review`, `published`) are deferred. Fact
  review happens at fact level (`candidate/uncertain/approved/rejected`) before generation.

## 5. Data model (SQLite via SQLAlchemy 2)

`topics` → `source_documents` (content, SHA-256, version, approved flag), `learning_objectives`, `facts`
(status, evidence quote, verification flag, proposer, reviewer, timestamps, demo flag); `raps` (status, band,
style, request, revision counter, escalation flag) → `rap_versions` (immutable lyrics JSON + provenance + token
usage + cost) → `validation_results`, `evaluations`; `review_decisions` (reviewer, role, decision, issues,
version); `workflow_events` (audit trail); `llm_calls` (task, model, prompt version, tokens, cost, latency,
attempts, error code, with **no prompt or output text**); `schema_meta`.

Lyrics are stored as `{title, sections:[{section, lines:[{line_id, text, fact_ids}]}], arrangement}`. The
chorus is written once and repeated through `arrangement`, which keeps line ids unique for claim mapping and
targeted revision. This matches the research data contract (`lyrics[{section,line_id,text}]`,
`fact_links[{line_id,fact_id}]`).

## 6. LLM layer

`LLMProvider` (abstract) implements retry with exponential backoff and jitter for rate-limit, timeout and 5xx
errors (auth and 4xx errors are not retried). It also handles `generate_json` with Pydantic validation and
bounded repair attempts (the validation error is fed back to the model), plus usage and cost tracking.
Providers implement one `_complete()` call.

- **ClaudeProvider:** official `anthropic` SDK, `messages.create(model, max_tokens, system, messages,
  output_config={format: json_schema, effort})`. The schema is transformed with `anthropic.transform_schema`.
  The current SDK (2.x) exposes no `temperature` parameter, so it is recorded as `not_supported_by_provider`
  in the generation config. SDK retries are disabled in favour of the app's uniform policy. SDK exceptions are
  mapped to app errors and never leaked.
- **OpenAIProvider (optional):** Responses API (`responses.create`, `instructions`, `input`,
  `max_output_tokens`, `text.format` JSON schema, `output_text`).
- **MockProvider:** deterministic handlers built from `LLMRequest.context` (a structured copy of the prompt
  inputs), plus per-task scripting of raw strings, exceptions or callables for tests.

A new provider object is created per HTTP request, so token accounting per run stays isolated.

## 7. Prompt engineering

There is one template per stage in `app/prompts/*.txt`, each with a `name`/`version` header. The SHA-256 of
the template file is stored with every generated or revised version. Each prompt states the role and task,
approved facts or sources, the age band and guidelines, an exact JSON output format, medical restrictions,
learning objectives (where relevant) and failure conditions. Rendering fails if a placeholder is missing or
unknown.

**Untrusted data:** source documents, objectives, facts, forbidden claims and lyrics are wrapped in
`<untrusted_*>` tags. Any opening or closing tag sequence inside the data is escaped, so content cannot close
its delimiter. Attribute values are sanitised, and system prompts instruct the model to ignore instructions
inside those tags. Deterministic layers (grounding, misconception rules, the coverage guard, line-id
enforcement in revision) do not depend on the model obeying those instructions. The tests include injection
attempts against extraction, validation and revision.

## 8. Security and privacy (MVP)

- Secrets come only from environment variables. Keys are `SecretStr`, never logged, and compared in constant
  time. No credentials are in source code.
- Authentication covers every endpoint except `/api/health`, so there is no public access to unpublished
  content. Role-based authorization is enforced server-side, in the route dependency and again in the services
  (for facts and reviews).
- The dev auth mode needs a double opt-in and is refused in production at settings validation. The request
  path re-checks it too. `LLM_PROVIDER=mock` and empty `API_KEYS` are also refused in production.
- Request limits: a 512 KB body cap (413), `MAX_SOURCE_CHARS` for source text, Pydantic length limits on every
  field, and caps on objectives, claims, candidates and verses.
- Errors are safe: one JSON error shape, 500s return only a request id, and LLM errors return fixed messages
  with no details (tested so that a secret embedded in an exception is not echoed).
- Logs are JSON with ids, counts and statuses only, never lyrics, sources or feedback, and a redaction filter
  masks key- or token-like strings.
- No child personal data is collected, the fixtures contain no real children's data or voices, and uploads are
  text only (no file parsing or URL fetching).

## 9. Production hardening checklist (not implemented in the MVP)

1. Replace static API keys with SSO/OIDC (and MFA for reviewers), and manage keys in a secrets manager with rotation.
2. Use PostgreSQL (or MEDKids' existing store), Alembic migrations, backups, and encryption at rest.
3. Add TLS termination, rate limiting per principal, CORS policy, and security headers at the gateway.
4. Restrict and audit read access to reviewer records; add data-retention rules for logs and LLM call records.
5. Review the LLM provider data-processing terms (zero-retention options) and complete a DPIA/COPPA-style review.
6. Have a maintained, educator- and clinician-owned lexicon and misconception list, replacing the starter lists.
7. Add licensing and rights review for any audio, voice or music stage; no real children's voices without consent policy.
8. Add a dependency vulnerability scan, container image scanning, and pinned lockfile (e.g. `pip-tools`/`uv lock`).
9. Run async workers if generation latency blocks request threads at scale.

## 10. Extension points

- **Audio (deferred):** `export_payload()["audio"]` is reserved. A future `audio_service` would consume
  *approved* versions only, add an `audio_review` state between `approved` and `published`, and store
  licensed-synthesis metadata and pronunciation QA (a research doc control).
- **Multi-model benchmark:** run the same frozen fact cards through `Pipeline` with different providers.
  `rap_versions` + `llm_calls` already capture model, prompt hash, tokens, cost, latency and revision cycles.
- **RAG:** when the approved corpus grows, add retrieval inside `fact_extractor`. Grounding checks still apply.
- **CMS:** `GET /api/raps/{id}/export` is the integration contract.

## 11. Known heuristic limits

Rhyme, syllable, coverage and "medical line" detection are lexical heuristics. They will misjudge slant
rhymes, metaphors and paraphrases. Misconception regexes catch only phrasings they anticipate. These modules
are triage aids, and the human medical reviewer and educator remain the authority. Calibrate them against
reviewer ratings (inter-rater agreement) before relying on thresholds.
