"""Deterministic offline provider for tests, demos and local development.

Default behaviour per task is built from ``LLMRequest.context`` (structured
copies of the prompt inputs), so outputs are reproducible and need no API
key. Tests can override any call by queueing scripted items per task:

* a ``str``  - returned verbatim as the model's text (e.g. malformed JSON);
* an ``Exception`` instance - raised from the call (e.g. ``LLMRateLimitError``);
* a callable ``(LLMRequest) -> str``.

Every request is recorded in ``self.requests`` for assertions.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from app.llm.base import LLMProvider, LLMRequest, LLMResponse
from app.services.lexicon import is_medical_statement
from app.utils.text_utils import last_word, overlap_ratio, split_sentences, words_rhyme

ScriptItem = str | Exception | Callable[[LLMRequest], str]

# Non-factual "tag" lines used to build rhyming couplets. They deliberately
# avoid body/health vocabulary so they make no medical claims.
_TAG_LINES = [
    "Clap your hands and stomp your feet!",
    "Sing it with me, hip hip hooray!",
    "Here we go, here we go!",
    "Sing it out, that's right!",
    "Say it loud and sing along!",
    "Now we know it, too!",
    "Singing is so much fun!",
    "Sing it with me, one, two, three!",
    "Throw your hands up high, oh my!",
    "Spin around and shout it out!",
    "Hey hey, that's the way!",
    "Give a cheer, the facts are here!",
]
_DEFAULT_TAG = "Clap along and sing with me!"

_SIMPLE_SWAPS = {
    "approximately": "about",
    "continuously": "nonstop",
    "throughout": "through",
    "receives": "gets",
    "processes": "works on",
    "information": "messages",
}


def _tag_for(line: str, used: set[str]) -> str:
    lw = last_word(line)
    for tag in _TAG_LINES:
        if tag not in used and words_rhyme(lw, last_word(tag)):
            used.add(tag)
            return tag
    for tag in _TAG_LINES:
        if tag not in used:
            used.add(tag)
            return tag
    return _DEFAULT_TAG


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, model: str = "mock-deterministic-v1", **kwargs: Any) -> None:
        kwargs.setdefault("sleep", lambda _s: None)
        kwargs.setdefault("backoff_base_seconds", 0.0)
        super().__init__(model, **kwargs)
        self._scripts: dict[str, deque[ScriptItem]] = defaultdict(deque)
        self.requests: list[LLMRequest] = []

    # -- scripting -----------------------------------------------------------

    def queue(self, task: str, *items: ScriptItem) -> MockProvider:
        self._scripts[task].extend(items)
        return self

    def calls_for(self, task: str) -> list[LLMRequest]:
        return [r for r in self.requests if r.task == task]

    # -- core ------------------------------------------------------------------

    def _complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(replace(request))  # snapshot: callers mutate the prompt on retries
        queue = self._scripts.get(request.task)
        if queue:
            item = queue.popleft()
            if isinstance(item, Exception):
                raise item
            text = item(request) if callable(item) else item
        else:
            handler = getattr(self, f"_handle_{request.task}", None)
            if handler is None:
                text = json.dumps({"error": f"no mock handler for {request.task}"})
            else:
                text = json.dumps(handler(request.context))
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=max(1, (len(request.system) + len(request.prompt)) // 4),
            output_tokens=max(1, len(text) // 4),
            latency_ms=0,
            stop_reason="end_turn",
        )

    # -- default deterministic handlers ---------------------------------------

    def _handle_fact_extraction(self, ctx: dict[str, Any]) -> dict[str, Any]:
        facts = []
        for src in ctx.get("sources", []):
            for sentence in split_sentences(src["content"]):
                facts.append(
                    {
                        "text": sentence.rstrip("."),
                        "evidence_quote": sentence,
                        "source_id": src["source_id"],
                        "classification": "supported",
                        "note": None,
                    }
                )
        return {"facts": facts[: ctx.get("max_facts", 15)]}

    def _handle_age_simplification(self, ctx: dict[str, Any]) -> dict[str, Any]:
        items = []
        for f in ctx.get("facts", []):
            text = f["text"]
            if ctx.get("band") == "4-6":
                for a, b in _SIMPLE_SWAPS.items():
                    text = text.replace(a, b)
            items.append({"fact_id": f["fact_id"], "simplified_text": text})
        return {"items": items}

    def _handle_rap_generation(self, ctx: dict[str, Any]) -> dict[str, Any]:
        spec = ctx["spec"]
        topic = spec["topic_title"]
        facts = spec["approved_facts"]
        used_tags: set[str] = set()

        def fact_line(f: dict[str, str]) -> dict[str, Any]:
            text = (f.get("simplified_text") or f["text"]).rstrip(".")
            return {"text": text[0].upper() + text[1:] + "!", "fact_ids": [f["fact_id"]]}

        sections: list[dict[str, Any]] = []
        if spec.get("include_intro"):
            sections.append(
                {"section": "intro", "lines": [{"text": "Hey kids, are you ready?", "fact_ids": []},
                                               {"text": "Let's go, nice and steady!", "fact_ids": []}]}
            )
        n_verses = spec["num_verses"]
        for v in range(n_verses):
            chunk = facts[v::n_verses] or facts[:1]
            lines: list[dict[str, Any]] = []
            for f in chunk[:3]:
                fl = fact_line(f)
                lines.append(fl)
                lines.append({"text": _tag_for(fl["text"], used_tags), "fact_ids": []})
            sections.append({"section": f"verse_{v + 1}", "lines": lines})
            if v == 0:
                chorus_lines = [
                    {"text": f"{topic}! {topic}! Sing it loud!", "fact_ids": []},
                    {"text": "Sing it again and make us proud!", "fact_ids": []},
                ]
                if facts and spec["chorus"]["max_lines"] >= 4:
                    fl = fact_line(facts[0])
                    chorus_lines += [fl, {"text": _tag_for(fl["text"], used_tags), "fact_ids": []}]
                sections.append({"section": "chorus", "lines": chorus_lines})
        if spec.get("include_outro"):
            sections.append(
                {"section": "outro", "lines": [{"text": f"That's the {topic.lower()} song, now you know!", "fact_ids": []},
                                               {"text": "Thanks for singing, here we go!", "fact_ids": []}]}
            )
        return {
            "title": f"The {topic} Rap",
            "sections": sections,
            "objectives_covered": [o["objective_id"] for o in spec["learning_objectives"]],
        }

    def _handle_medical_validation(self, ctx: dict[str, Any]) -> dict[str, Any]:
        facts = {f["fact_id"]: f for f in ctx.get("facts", [])}
        claims = []
        for section in ctx["lyrics"]["sections"]:
            for line in section["lines"]:
                ids = [i for i in line.get("fact_ids", []) if i in facts]
                if ids:
                    for fid in ids:
                        supported = overlap_ratio(line["text"], facts[fid]["text"]) >= 0.5
                        claims.append(
                            {
                                "line_id": line["line_id"],
                                "claim": line["text"],
                                "status": "supported" if supported else "ambiguous",
                                "fact_id": fid if supported else None,
                                "rationale": "mock: lexical comparison with cited fact",
                            }
                        )
                elif is_medical_statement(line["text"], ctx.get("topic")):
                    best = max(facts.values(), key=lambda f: overlap_ratio(line["text"], f["text"]), default=None)
                    if best and overlap_ratio(line["text"], best["text"]) >= 0.6:
                        claims.append({"line_id": line["line_id"], "claim": line["text"], "status": "supported",
                                       "fact_id": best["fact_id"], "rationale": "mock: matches approved fact"})
                    else:
                        claims.append({"line_id": line["line_id"], "claim": line["text"], "status": "unsupported",
                                       "fact_id": None, "rationale": "mock: no approved fact covers this"})
        return {"claims": claims}

    def _handle_safety_validation(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return {"findings": []}

    def _handle_lyric_revision(self, ctx: dict[str, Any]) -> dict[str, Any]:
        facts = ctx.get("facts", [])
        current = {ln["line_id"]: ln["text"] for s in ctx["lyrics"]["sections"] for ln in s["lines"]}
        revised = []
        for i, line_id in enumerate(ctx.get("problems", {})):
            if facts:
                # closest approved fact whose wording differs from the flagged line
                cur = current.get(line_id, "")
                ranked = sorted(facts, key=lambda x: (-overlap_ratio(cur, x["text"]), abs(facts.index(x) - i)))

                def render(f: dict[str, str]) -> str:
                    t = (f.get("simplified_text") or f["text"]).rstrip(".")
                    return t[0].upper() + t[1:] + "!"

                f = next((x for x in ranked if render(x) != cur), ranked[0])
                revised.append({"line_id": line_id, "text": render(f), "fact_ids": [f["fact_id"]]})
            else:
                revised.append({"line_id": line_id, "text": "Clap along and sing with me!", "fact_ids": []})
        return {"revised_lines": revised, "notes": "mock: replaced flagged lines with approved facts"}
