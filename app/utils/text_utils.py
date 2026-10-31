"""Small, dependency-free text helpers used by deterministic checks."""

from __future__ import annotations

import hashlib
import re
import unicodedata

STOPWORDS = frozenset(
    """a an and are as at be been being but by can could did do does doing for from had has have
    having he her here hers him his how i if in into is it its itself just me more most my no nor
    not of off on once only or other our ours out over own same she should so some such than that
    the their theirs them then there these they this those through to too under until up very was
    we were what when where which while who whom why will with you your yours yourself let lets
    yeah yo hey oh go come get got gonna wanna all every everyone everybody one way thing things
    keep keeps make makes made like say says said know see look day each also us im youre thats
    understand identify why matters matter about part helps help""".split()
)

_WORD_RE = re.compile(r"[a-z]+(?:'[a-z]+)?")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_text(text: str) -> str:
    """NFKC-normalise, strip control characters and collapse whitespace runs."""
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "topic"


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p and len(p.strip()) > 2]


def words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower().replace("’", "'"))


def stem(word: str) -> str:
    """Very small suffix stripper; good enough for overlap heuristics."""
    w = word.lower().replace("'s", "")
    for suffix in ("ing", "ies", "ied", "es", "ed", "ly", "s"):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            base = w[: -len(suffix)]
            if suffix in ("ies", "ied"):
                return base + "y"
            return base
    return w


def content_words(text: str) -> set[str]:
    return {stem(w) for w in words(text) if w not in STOPWORDS and len(w) > 2}


def overlap_ratio(claim: str, reference: str) -> float:
    """Share of the claim's content words that also appear in the reference."""
    cw = content_words(claim)
    if not cw:
        return 0.0
    return len(cw & content_words(reference)) / len(cw)


def estimate_syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    groups = re.findall(r"[aeiouy]{1,2}", w)
    return max(1, len(groups))


def line_syllables(text: str) -> int:
    return sum(estimate_syllables(w) for w in words(text))


def last_word(text: str) -> str:
    ws = words(text)
    return ws[-1] if ws else ""


def rhyme_key(word: str) -> str:
    """Last vowel group plus trailing consonants (e.g. 'beat' -> 'eat')."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    match = re.search(r"[aeiouy]+[^aeiouy]*$", w)
    return match.group(0) if match else w


def words_rhyme(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True  # identical words count as a (weak) rhyme / repetition
    ka, kb = rhyme_key(a), rhyme_key(b)
    if ka and ka == kb:
        return True
    return len(a) >= 3 and len(b) >= 3 and a[-3:] == b[-3:]


def escape_for_tag(content: str, tag: str) -> str:
    """Neutralise attempts to close/open a delimiter tag from inside untrusted data."""
    pattern = re.compile(rf"<\s*/?\s*{re.escape(tag)}", re.IGNORECASE)
    content = pattern.sub(lambda m: m.group(0).replace("<", "&lt;"), content)
    return re.sub(r"<\s*/?\s*untrusted_", lambda m: m.group(0).replace("<", "&lt;"), content, flags=re.I)


def wrap_untrusted(tag: str, content: str, **attrs: str) -> str:
    safe_attrs = " ".join(
        f'{k}="{re.sub(r"[^A-Za-z0-9_.:-]", "", str(v))}"' for k, v in attrs.items()
    )
    opener = f"<{tag} {safe_attrs}>" if safe_attrs else f"<{tag}>"
    return f"{opener}\n{escape_for_tag(content, tag)}\n</{tag}>"
