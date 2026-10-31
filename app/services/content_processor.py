"""Source content processing: normalisation, limits, hashing and flags.

The MVP accepts plain text only (no file uploads, no URL fetching, no PDF/OCR),
which avoids executing or parsing untrusted file formats. Source text is
always treated as data; instruction-like passages are flagged for reviewers
and any fact whose evidence comes from such a passage is marked uncertain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.exceptions import InputRejectedError
from app.utils.text_utils import normalize_text, sha256, split_sentences

_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|rules?|prompts?|guidelines?)\b",
        r"\bsystem\s*prompt\b",
        r"\byou\s+are\s+now\b",
        r"\b(pretend|act)\s+(to\s+be|as)\s+(an?\s+)?(ai|assistant|system|admin\w*|developer|different)\b",
        r"\bdeveloper\s+mode\b|\bjailbreak\b",
        r"\b(mark|set|label|classify)\b.{0,40}\b(approved|supported|verified)\b",
        r"\bapprove\b.{0,30}\b(this|these|all|every)\b",
        r"^\s*(system|assistant|user)\s*:",
        r"</?\s*(system|instructions?|untrusted_[a-z_]+)\s*>",
        r"<\s*script\b",
    )
]


@dataclass
class ProcessedSource:
    content: str
    sha256: str
    sentences: list[str]
    flags: list[dict[str, object]] = field(default_factory=list)

    @property
    def flagged_sentence_indexes(self) -> set[int]:
        return {int(f["sentence_index"]) for f in self.flags if "sentence_index" in f}


def detect_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def process_source(raw: str, *, max_chars: int, source_key: str = "src_001") -> ProcessedSource:
    content = normalize_text(raw)
    if not content:
        raise InputRejectedError("educational_content is empty after normalisation")
    if len(content) > max_chars:
        raise InputRejectedError(
            f"educational_content is {len(content)} characters; the limit is {max_chars}"
        )
    sentences = split_sentences(content)
    flags: list[dict[str, object]] = []
    for i, sentence in enumerate(sentences):
        if detect_injection(sentence):
            flags.append(
                {
                    "type": "possible_prompt_injection",
                    "source_key": source_key,
                    "sentence_index": i,
                    "excerpt": sentence[:80],
                    "action": "treated as data; facts from this passage are marked uncertain",
                }
            )
    return ProcessedSource(content=content, sha256=sha256(content), sentences=sentences, flags=flags)
