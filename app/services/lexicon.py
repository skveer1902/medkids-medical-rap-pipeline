"""Curated word lists used by deterministic checks.

These lists are a starting point for the pilot, not a complete or validated
lexicon. They should be maintained by MEDKids educators and medical reviewers.
"""

from __future__ import annotations

import re

from app.utils.text_utils import stem
from app.utils.text_utils import words as _words

# Words that signal a line is making a statement about the body/health.
_MEDICAL_TERMS_RAW = """
heart heartbeat beat beats beating pump pumps pumping pulse blood vessel vessels artery arteries
vein veins capillary capillaries oxygen nutrient nutrients muscle muscles organ organs cell cells
brain nerve nerves neuron neurons signal signals spinal cord nervous system systems sense senses
memory memories remember think thinking thought learn learning move movement moving balance
lung lungs breathe breath breathing air bone bones skeleton skin stomach digest digestion food
kidney kidneys liver germ germs virus bacteria sick healthy health medicine doctor doctors nurse
nurses eye eyes ear ears nose tongue taste smell touch hear hearing see sight body bodies chest
circulation circulate circulatory sleep exercise vitamin vitamins tooth teeth heal healing
""".split()
# Generic verbs (make, carry, control, feel ...) are deliberately NOT listed: on their own they
# do not signal a body claim. Lines like "the heart makes ..." are caught by ``_CLAIM_VERBS``.
MEDICAL_TERMS: frozenset[str] = frozenset(stem(w) for w in _MEDICAL_TERMS_RAW)

# Technical terms flagged for every age band unless listed as required vocabulary.
ADVANCED_JARGON: frozenset[str] = frozenset(
    """myocardium myocardial aorta aortic atrium atria ventricle ventricles ventricular hemoglobin
    haemoglobin erythrocyte erythrocytes leukocyte plasma pulmonary systemic cardiovascular
    neurotransmitter neurotransmitters synapse synapses synaptic axon axons dendrite dendrites
    hippocampus cerebellum cerebrum cortex neocortex amygdala thalamus hypothalamus brainstem
    medulla myelin glial endocrine hormone hormones metabolism""".split()
)

# Terms acceptable for ages 7-10 (with explanation) but flagged for ages 4-6.
INTERMEDIATE_JARGON: frozenset[str] = frozenset(
    """artery arteries vein veins capillary capillaries circulatory circulation neuron neurons
    nervous spinal oxygen nutrients nutrient cells organ organs vessels""".split()
)

ABSOLUTE_WORDS: frozenset[str] = frozenset({"always", "never", "only", "every", "all", "forever", "100"})

_CLAIM_VERBS = (
    r"is|are|was|has|have|helps?|makes?|does|do|can|works?|pumps?|keeps?|controls?|sends?|gets?|"
    r"carries|carry|moves?|tells?|needs?|beats?|thinks?|stores?|cleans?|filters?|produces?|creates?|'s"
)


def is_medical_statement(text: str, topic: str | None = None) -> bool:
    """Heuristic: does this lyric line appear to state something about the body?

    A line counts if it contains body/health vocabulary other than the topic
    name, or if the topic name is followed closely by a claim verb
    ("the heart pumps ..."). A bare hook such as "Heart! Heart! Sing it loud!"
    does not count.
    """
    topic_stems = {stem(w) for w in _words(topic)} if topic else set()
    terms = {stem(w) for w in _words(text)} & MEDICAL_TERMS
    if terms - topic_stems:
        return True
    if terms and topic:
        pattern = rf"\b{re.escape(topic.lower())}\w*\b(\s+\w+){{0,2}}?\s*\b({_CLAIM_VERBS})\b"
        return re.search(pattern, text.lower().replace("’", "'")) is not None
    return False
