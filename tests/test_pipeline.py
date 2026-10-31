"""End-to-end pipeline (service level): checks, capped revision, workflow guards, persistence."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.core.exceptions import WorkflowError
from app.models.database import LLMCallLog, Rap, SchemaMeta, WorkflowEvent, init_db, make_engine
from app.models.schemas import RapGenerateRequest
from app.services.pipeline import Pipeline, current_version
from app.services.workflow import transition
from tests.conftest import AUTHOR, MEDICAL, make_settings


def rap_json(bad_line: str | None = "The heart makes blood!") -> str:
    v1 = [{"text": bad_line, "fact_ids": []}] if bad_line else []
    v1 += [{"text": "The heart is a muscle!", "fact_ids": ["heart_fact_001"]},
           {"text": "Clap your hands and stomp your feet!", "fact_ids": []}]
    return json.dumps({"title": "Heart Beat", "sections": [
        {"section": "verse_1", "lines": v1},
        {"section": "chorus", "lines": [{"text": "Heart! Heart! Sing it loud!", "fact_ids": []},
                                        {"text": "Sing it again and make us proud!", "fact_ids": []}]},
        {"section": "verse_2", "lines": [{"text": "The heart pumps blood through the body!", "fact_ids": ["heart_fact_002"]},
                                         {"text": "Sing it with me, hip hip hooray!", "fact_ids": []}]},
    ], "objectives_covered": []})


def generate(pipeline, session, topic, **kw) -> Rap:
    return pipeline.generate(session, RapGenerateRequest(topic_id=topic.id, **kw), AUTHOR)[0]


def test_full_pipeline_reaches_pending_review_and_persists_everything(pipeline, session, approved_heart):
    rap = generate(pipeline, session, approved_heart)
    assert rap.status == "pending_review" and rap.needs_human_intervention is False
    v = current_version(rap)
    assert {x.kind for x in v.validations} == {"medical", "safety"}
    assert v.evaluations and v.evaluations[-1].automated_checks_passed
    assert v.prompt_name == "rap_generation" and v.provider == "mock" and v.approved_fact_keys
    events = session.scalars(select(WorkflowEvent).where(WorkflowEvent.rap_id == rap.id)).all()
    assert [e.to_status for e in events][0] == "draft" and events[-1].to_status == "pending_review"
    tasks = {c.task for c in session.scalars(select(LLMCallLog))}
    assert {"age_simplification", "rap_generation", "medical_validation", "safety_validation"} <= tasks
    assert rap.status != "approved"  # never auto-approved


def test_targeted_revision_fixes_only_failing_line(pipeline, session, approved_heart, mock):
    mock.queue("rap_generation", rap_json())
    rap = generate(pipeline, session, approved_heart)
    assert rap.current_version == 2 and rap.auto_revision_attempts == 1
    v1, v2 = rap.versions
    assert v2.change_type == "auto_revision" and v2.changed_line_ids == ["v1_l01"]
    old = {ln["line_id"]: ln["text"] for s in v1.lyrics["sections"] for ln in s["lines"]}
    new = {ln["line_id"]: ln["text"] for s in v2.lyrics["sections"] for ln in s["lines"]}
    assert {k for k in old if old[k] != new[k]} == {"v1_l01"}
    assert "makes blood" not in new["v1_l01"]
    assert rap.status == "pending_review"


def test_revision_cannot_touch_untargeted_lines(pipeline, session, approved_heart, mock):
    mock.queue("rap_generation", rap_json())
    mock.queue("lyric_revision", json.dumps({"revised_lines": [
        {"line_id": "v1_l01", "text": "The heart pumps blood through the body!", "fact_ids": ["heart_fact_002"]},
        {"line_id": "ch_l01", "text": "I rewrote your approved chorus!", "fact_ids": []}]}))
    rap = generate(pipeline, session, approved_heart)
    v2 = rap.versions[1]
    chorus = next(s for s in v2.lyrics["sections"] if s["section"] == "chorus")
    assert chorus["lines"][0]["text"] == "Heart! Heart! Sing it loud!"
    assert v2.generation_config["ignored_line_ids"] == ["ch_l01"]


def test_revision_loop_is_capped_and_escalates(pipeline, session, approved_heart, mock):
    mock.queue("rap_generation", rap_json())
    bad = [json.dumps({"revised_lines": [{"line_id": "v1_l01", "text": f"The heart makes blood {i}!", "fact_ids": []}]})
           for i in range(10)]
    mock.queue("lyric_revision", *bad)
    rap = generate(pipeline, session, approved_heart)
    assert rap.status == "validation_failed"
    assert rap.needs_human_intervention is True
    assert rap.auto_revision_attempts == 3 and len(rap.versions) == 4
    assert len(mock.calls_for("lyric_revision")) == 3  # never more than the cap
    with pytest.raises(WorkflowError, match="limit"):
        pipeline.revise(session, rap, AUTHOR)


def test_no_change_revisions_still_count_towards_cap(pipeline, session, approved_heart, mock):
    mock.queue("rap_generation", rap_json())
    same = json.dumps({"revised_lines": [{"line_id": "v1_l01", "text": "The heart makes blood!", "fact_ids": []}]})
    mock.queue("lyric_revision", *[same] * 10)
    rap = generate(pipeline, session, approved_heart)
    assert rap.needs_human_intervention and rap.auto_revision_attempts == 3 and len(rap.versions) == 1


def test_zero_revision_attempts_escalates_immediately(session, approved_heart, mock, tmp_path):
    p = Pipeline(make_settings(tmp_path, max_revision_attempts=0), mock)
    mock.queue("rap_generation", rap_json())
    rap = generate(p, session, approved_heart)
    assert rap.status == "validation_failed" and rap.needs_human_intervention
    assert not mock.calls_for("lyric_revision")


def test_auto_revise_disabled_leaves_failure_for_author(pipeline, session, approved_heart, mock):
    mock.queue("rap_generation", rap_json())
    rap = generate(pipeline, session, approved_heart, auto_revise=False)
    assert rap.status == "validation_failed" and rap.current_version == 1
    assert pipeline.revise(session, rap, AUTHOR) is True
    assert rap.status == "pending_review" and rap.versions[-1].change_type == "auto_revision"


def test_generation_requires_approved_facts(pipeline, session, heart_topic):
    with pytest.raises(WorkflowError, match="no approved facts"):
        pipeline.generate(session, RapGenerateRequest(topic_id=heart_topic.id), AUTHOR)


def test_workflow_guards(pipeline, session, approved_heart):
    rap = generate(pipeline, session, approved_heart, run_pipeline=False)
    assert rap.status == "draft"
    with pytest.raises(WorkflowError, match="validate"):
        pipeline.evaluate(session, rap)
    with pytest.raises(WorkflowError):
        pipeline.revise(session, rap, AUTHOR)  # nothing to revise in draft
    pipeline.validate(session, rap)
    pipeline.evaluate(session, rap)
    assert rap.status == "pending_review"
    # automation can never approve, even by calling the state machine directly
    with pytest.raises(WorkflowError, match="human reviewer"):
        transition(session, rap, "approved", "system:pipeline")
    with pytest.raises(WorkflowError, match="human reviewer"):
        transition(session, rap, "approved", AUTHOR)
    with pytest.raises(WorkflowError):
        transition(session, rap, "draft", MEDICAL)  # pending_review -> draft is not a valid edge


def test_multiple_candidates_capped(pipeline, session, approved_heart):
    raps = pipeline.generate(session, RapGenerateRequest(topic_id=approved_heart.id, num_candidates=2,
                                                          run_pipeline=False), AUTHOR)
    assert len(raps) == 2 and raps[0].id != raps[1].id
    assert session.scalar(select(func.count(Rap.id))) == 2


def test_schema_version_recorded_and_mismatch_detected(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'x.db'}")
    init_db(engine)
    init_db(engine)  # idempotent
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        assert s.get(SchemaMeta, "schema_version").value == "1"
        s.get(SchemaMeta, "schema_version").value = "0"
        s.commit()
    with pytest.raises(RuntimeError, match="schema version"):
        init_db(engine)
