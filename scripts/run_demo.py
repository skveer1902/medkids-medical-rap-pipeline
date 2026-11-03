"""Heart and Brain end-to-end demonstration.

    python scripts/run_demo.py                 # offline, deterministic MockProvider
    LLM_PROVIDER=claude CLAUDE_MODEL=... ANTHROPIC_API_KEY=... python scripts/run_demo.py

For each topic: load -> extract candidate facts -> approve them with a
CLEARLY LABELLED DEVELOPMENT FIXTURE (not a human review) -> generate ->
validate -> evaluate (with capped auto-revision) -> save output -> show the
review status. The raps end in 'pending_review' (or 'validation_failed'):
the demo never approves lyrics, because only a human reviewer may.

The demo uses its own database file (data/demo.db) so it never touches the
application database.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    import _bootstrap  # noqa: F401  (when run as a script)
except ModuleNotFoundError:  # imported as scripts.<name>
    pass

from app.core.config import settings_from_env
from app.core.logging import configure_logging
from app.llm import build_provider
from app.models.database import init_db, make_engine, make_session_factory
from app.models.schemas import FactApprovalRequest, RapGenerateRequest
from app.services.fact_extractor import FactExtractor, approve_facts
from app.services.pipeline import Pipeline, current_version
from app.services.security import Principal
from scripts.seed_topics import SEED_PRINCIPAL, seed

ROOT = Path(__file__).resolve().parent.parent

# Development fixture standing in for a medical reviewer in the DEMO ONLY.
# It is not a person and its approvals are recorded under this explicit name.
DEV_FIXTURE_REVIEWER = Principal(
    name="DEV-FIXTURE (demo only - not a human medical review)",
    role="reviewer",
    auth_method="dev_fixture",
)


def print_lyrics(lyrics: dict) -> None:
    print(f"\n  \"{lyrics['title']}\"")
    for section in lyrics["sections"]:
        print(f"  [{section['section']}]")
        for line in section["lines"]:
            cite = f"  <- {', '.join(line['fact_ids'])}" if line["fact_ids"] else ""
            print(f"    {line['text']}{cite}")
    if lyrics.get("arrangement"):
        print(f"  arrangement: {' > '.join(lyrics['arrangement'])}")


def run(db_path: Path, out_dir: Path) -> list[dict]:
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    settings = settings_from_env().model_copy(update={"database_url": f"sqlite:///{db_path}",
                                                     "outputs_dir": str(out_dir)})
    if settings.app_env == "production":
        raise SystemExit("The demo uses a development fixture and refuses to run with APP_ENV=production.")
    configure_logging(settings.log_level)
    if db_path.exists():
        db_path.unlink()
    engine = make_engine(settings.database_url)
    init_db(engine)
    session = make_session_factory(engine)()
    provider = build_provider(settings)
    pipeline = Pipeline(settings, provider)
    print(f"Provider: {provider.name} / model: {provider.model}")

    results = []
    for topic in seed(session, settings):
        print("\n" + "=" * 72 + f"\nTOPIC: {topic.title}  (ages {topic.target_age_min}-{topic.target_age_max}, DEMO DATA)")
        run_id, facts = FactExtractor(provider, json_max_attempts=settings.llm_json_max_attempts).extract(session, topic)
        pipeline._persist_llm_calls(session, run_id)
        session.commit()
        candidates = [f for f in facts if f.status == "candidate"]
        print(f"1-2. Extracted {len(candidates)} candidate fact(s), {len(facts) - len(candidates)} uncertain:")
        for f in facts:
            print(f"     {f.fact_key} [{f.status}] {f.text}")

        approve_facts(session, topic, FactApprovalRequest(
            approve_fact_ids=[f.fact_key for f in candidates],
            note="Demo-only development fixture approval of DEMONSTRATION data; requires real medical review."),
            DEV_FIXTURE_REVIEWER)
        session.commit()
        print(f"3.   Approved {len(candidates)} fact(s) via development fixture '{DEV_FIXTURE_REVIEWER.name}'")

        rap = pipeline.generate(session, RapGenerateRequest(topic_id=topic.id), SEED_PRINCIPAL)[0]
        version = current_version(rap)
        med = next(v for v in reversed(version.validations) if v.kind == "medical")
        saf = next(v for v in reversed(version.validations) if v.kind == "safety")
        ev = version.evaluations[-1]
        print(f"4.   Generated rap id={rap.id}, now at version {rap.current_version} "
              f"(auto-revisions used: {rap.auto_revision_attempts}/{settings.max_revision_attempts})")
        print_lyrics(version.lyrics)
        print(f"\n5.   Medical validation: {med.status}  {med.report['summary']}")
        print(f"     Safety validation:  {saf.status}  ({len(saf.report['findings'])} finding(s))")
        for f in saf.report["findings"]:
            print(f"       - {f['line_id']}: {f['category']}/{f['severity']}: {f['explanation']}")
        print(f"6.   Evaluation: {ev.total_score}/100 (threshold {ev.threshold}); gates={ev.hard_gates}")
        for name, s in ev.scores.items():
            print(f"       {name:22s} {s['score']:5.1f}  {s['explanation']}")

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "is_demo_data": True,
            "fact_approval": "development fixture - NOT a human medical review",
            "topic": topic.title, "rap_id": rap.id, "status": rap.status, "version": rap.current_version,
            "lyrics": version.lyrics, "medical_validation": med.report, "safety_validation": saf.report,
            "evaluation": {"total": ev.total_score, "scores": ev.scores, "hard_gates": ev.hard_gates,
                           "recommendations": ev.recommendations},
            "provenance": {"provider": version.provider, "model": version.model,
                           "prompt": f"{version.prompt_name}@{version.prompt_version}",
                           "prompt_sha256": version.prompt_sha256, "run_id": version.run_id},
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"demo_{topic.slug}.json"
        out_file.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"7.   Saved {out_file.relative_to(ROOT) if out_file.is_relative_to(ROOT) else out_file}")
        print(f"8.   REVIEW STATUS: {rap.status}"
              + ("  (needs human intervention)" if rap.needs_human_intervention else "")
              + "  -> awaiting the reviewer's approval")
        results.append(payload)
    session.close()
    engine.dispose()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "demo.db"))
    parser.add_argument("--out", default=str(ROOT / "data" / "outputs"))
    args = parser.parse_args()
    run(Path(args.db), Path(args.out))


if __name__ == "__main__":
    main()
