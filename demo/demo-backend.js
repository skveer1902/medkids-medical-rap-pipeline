/* MEDKids Rap Studio: in-browser demo backend.
 *
 * Answers the web UI's /api/* calls without a server, so the real UI (app/static/app.js)
 * can be explored anywhere. It is a JavaScript port of the pipeline's OFFLINE MOCK mode:
 * the deterministic mock "AI" plus the deterministic rules the Python services apply
 * (evidence grounding, misconception rules, child-safety rules, scoring, capped targeted
 * revision and the two-reviewer approval gate). Nothing is sent anywhere; data lives in
 * memory and resets when the page reloads. Real Claude generation needs the Python app.
 */
(() => {
  "use strict";

  // ------------------------------------------------------------------ principals
  const PRINCIPALS = {
    "demo-author": { name: "Ana (author)", role: "author" },
    "demo-medical": { name: "Dr. Mo (medical reviewer)", role: "medical_reviewer" },
    "demo-educator": { name: "Ed (educator)", role: "educator" },
  };
  const PERMISSIONS = {
    read: ["author", "medical_reviewer", "educator", "admin"],
    author: ["author", "admin"],
    approve_facts: ["medical_reviewer"],
    review: ["medical_reviewer", "educator"],
  };
  const REQUIRED_APPROVERS = ["medical_reviewer", "educator"];
  const MAX_REVISIONS = 3;
  const THRESHOLD = 80;
  const WEIGHTS = { medical_accuracy: 0.30, educational_coverage: 0.25, age_appropriateness: 0.20, rhyme_rhythm: 0.15, memorability: 0.10 };
  const MODEL = "mock-deterministic-v1 (browser demo)";

  class HttpError extends Error {
    constructor(status, code, message, details) { super(message); this.status = status; this.code = code; this.details = details || {}; }
  }
  const notFound = (m) => new HttpError(404, "not_found", m);
  const conflict = (m, d) => new HttpError(409, "invalid_workflow_transition", m, d);
  const rejected = (m, d) => new HttpError(422, "input_rejected", m, d);
  const forbidden = (m) => new HttpError(403, "forbidden", m);

  // ------------------------------------------------------------------ text utils
  const STOPWORDS = new Set(`a an and are as at be been being but by can could did do does doing for from had has have
    having he her here hers him his how i if in into is it its itself just me more most my no nor not of off on once only
    or other our ours out over own same she should so some such than that the their theirs them then there these they this
    those through to too under until up very was we were what when where which while who whom why will with you your yours
    yourself let lets yeah yo hey oh go come get got gonna wanna all every everyone everybody one way thing things keep keeps
    make makes made like say says said know see look day each also us im youre thats understand identify why matters matter
    about part helps help`.split(/\s+/));
  const words = (t) => (String(t).toLowerCase().replace(/’/g, "'").match(/[a-z]+(?:'[a-z]+)?/g) || []);
  function stem(w) {
    w = w.toLowerCase().replace("'s", "");
    for (const suf of ["ing", "ies", "ied", "es", "ed", "ly", "s"]) {
      if (w.length > suf.length + 2 && w.endsWith(suf)) {
        const base = w.slice(0, -suf.length);
        return suf === "ies" || suf === "ied" ? base + "y" : base;
      }
    }
    return w;
  }
  const contentWords = (t) => new Set(words(t).filter((w) => !STOPWORDS.has(w) && w.length > 2).map(stem));
  function overlap(claim, ref) {
    const a = contentWords(claim); if (!a.size) return 0;
    const b = contentWords(ref); let n = 0; for (const w of a) if (b.has(w)) n++; return n / a.size;
  }
  const splitSentences = (t) => t.split(/(?<=[.!?])\s+|\n+/).map((s) => s.trim()).filter((s) => s.length > 2);
  const norm = (t) => String(t).normalize("NFKC").toLowerCase().replace(/[‘’]/g, "'").replace(/[^a-z0-9' ]+/g, " ").replace(/\s+/g, " ").trim();
  const normalizeText = (t) => String(t).normalize("NFKC").replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "").replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  const slugify = (t) => (t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "topic");
  function syllables(word) {
    let w = word.toLowerCase().replace(/[^a-z]/g, ""); if (!w) return 0; if (w.length <= 3) return 1;
    w = w.replace(/(?:[^laeiouy]es|ed|[^laeiouy]e)$/, "").replace(/^y/, "");
    return Math.max(1, (w.match(/[aeiouy]{1,2}/g) || []).length);
  }
  const lineSyllables = (t) => words(t).reduce((n, w) => n + syllables(w), 0);
  const lastWord = (t) => { const ws = words(t); return ws.length ? ws[ws.length - 1] : ""; };
  const rhymeKey = (w) => { w = w.replace(/[^a-z]/g, ""); const m = w.match(/[aeiouy]+[^aeiouy]*$/); return m ? m[0] : w; };
  function rhymes(a, b) {
    if (!a || !b) return false; if (a === b) return true;
    const ka = rhymeKey(a), kb = rhymeKey(b); if (ka && ka === kb) return true;
    return a.length >= 3 && b.length >= 3 && a.slice(-3) === b.slice(-3);
  }
  const mean = (xs) => xs.reduce((a, b) => a + b, 0) / (xs.length || 1);
  const pstdev = (xs) => { const m = mean(xs); return Math.sqrt(mean(xs.map((x) => (x - m) ** 2))); };
  const now = () => new Date().toISOString();
  const hex = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
  const clone = (o) => JSON.parse(JSON.stringify(o));

  // ------------------------------------------------------------------ lexicon
  const MEDICAL_TERMS = new Set(`heart heartbeat beat beats beating pump pumps pumping pulse blood vessel vessels artery arteries
    vein veins capillary capillaries oxygen nutrient nutrients muscle muscles organ organs cell cells brain nerve nerves neuron
    neurons signal signals spinal cord nervous system systems sense senses memory memories remember think thinking thought learn
    learning move movement moving balance lung lungs breathe breath breathing air bone bones skeleton skin stomach digest digestion
    food kidney kidneys liver germ germs virus bacteria sick healthy health medicine doctor doctors nurse nurses eye eyes ear ears
    nose tongue taste smell touch hear hearing see sight body bodies chest circulation circulate circulatory sleep exercise vitamin
    vitamins tooth teeth heal healing`.split(/\s+/).map(stem));
  const ADVANCED_JARGON = new Set(`myocardium myocardial aorta aortic atrium atria ventricle ventricles ventricular hemoglobin
    haemoglobin erythrocyte erythrocytes leukocyte plasma pulmonary systemic cardiovascular neurotransmitter neurotransmitters
    synapse synapses synaptic axon axons dendrite dendrites hippocampus cerebellum cerebrum cortex neocortex amygdala thalamus
    hypothalamus brainstem medulla myelin glial endocrine hormone hormones metabolism`.split(/\s+/));
  const INTERMEDIATE_JARGON = new Set(`artery arteries vein veins capillary capillaries circulatory circulation neuron neurons
    nervous spinal oxygen nutrients nutrient cells organ organs vessels`.split(/\s+/));
  const CLAIM_VERBS = "is|are|was|has|have|helps?|makes?|does|do|can|works?|pumps?|keeps?|controls?|sends?|gets?|carries|carry|moves?|tells?|needs?|beats?|thinks?|stores?|cleans?|filters?|produces?|creates?|'s";
  function isMedicalStatement(text, topic) {
    const topicStems = new Set(topic ? words(topic).map(stem) : []);
    const terms = new Set(words(text).map(stem).filter((w) => MEDICAL_TERMS.has(w)));
    for (const t of terms) if (!topicStems.has(t)) return true;
    if (terms.size && topic) {
      const esc = topic.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      return new RegExp(`\\b${esc}\\w*\\b(\\s+\\w+){0,2}?\\s*\\b(${CLAIM_VERBS})\\b`).test(text.toLowerCase().replace(/’/g, "'"));
    }
    return false;
  }
  const INJECTION = [
    /\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|rules?|prompts?|guidelines?)\b/i,
    /\bsystem\s*prompt\b/i, /\byou\s+are\s+now\b/i,
    /\b(pretend|act)\s+(to\s+be|as)\s+(an?\s+)?(ai|assistant|system|admin\w*|developer|different)\b/i,
    /\bdeveloper\s+mode\b|\bjailbreak\b/i, /\b(mark|set|label|classify)\b.{0,40}\b(approved|supported|verified)\b/i,
    /\bapprove\b.{0,30}\b(this|these|all|every)\b/i, /^\s*(system|assistant|user)\s*:/i,
    /<\/?\s*(system|instructions?|untrusted_[a-z_]+)\s*>/i, /<\s*script\b/i,
  ];
  const looksLikeInjection = (t) => INJECTION.some((p) => p.test(t));

  const GLOBAL_MISCONCEPTIONS = [
    ["The heart makes/produces blood (blood is made mainly in bone marrow)", /\bheart\w*\b.{0,20}\b(makes?|making|produces?|creates?)\b.{0,10}\bblood\b/i],
    ["The heart cleans or filters the blood", /\bheart\w*\b.{0,20}\b(clean|cleans|filter|filters|cleaning|filtering)\b.{0,10}\bblood\b/i],
    ["Blood inside the body is blue", /\bblood\b.{0,15}\b(is|turns|looks)\b.{0,10}\bblue\b/i],
    ["People only use 10% of their brain", /\b(only|just)\b.{0,15}\b(10|ten)\s*(%|percent)\b.{0,20}\bbrain/i],
    ["The brain is a muscle", /\bbrain\b\s*(is|'s)\s+(a\s+)?muscle\b/i],
  ];

  const r = (p) => new RegExp(p, "i");
  const SAFETY_RULES = [
    ["graphic_medical", "critical", r("\\b(gore|gory|guts|spurt\\w*|gush\\w*|squirt\\w*|bleed(s|ing)?\\s+out|blood\\s*bath|bloody|mangled|splatter\\w*|severed|rip\\w*\\s+(it\\s+|you\\s+|them\\s+)?(out|open|apart)|cut\\s+(you\\s+|it\\s+|them\\s+)?open|chop\\w*\\s+(off|up))\\b"), "Graphic or gory description of the body.", "Describe what the body does, not injuries.", false],
    ["frightening", "major", r("\\b(die|dies|dying|dead|death|kill\\w*|murder\\w*|scary|scared|terrif\\w*|nightmares?|monsters?|explod\\w*|heart\\s+attacks?|strokes?|cancer|tumou?rs?|coma|you\\s+could\\s+get\\s+sick)\\b"), "Language that could frighten a young child.", "Keep the tone safe and reassuring.", true],
    ["inappropriate_vocabulary", "major", r("\\b(damn|hell|crap|sucks?|stupid|dumb|idiots?|morons?|losers?|shut\\s+up|butt|freaks?|ugly)\\b"), "Rude or insulting vocabulary.", "Use kind, positive words.", false],
    ["unsafe_health_advice", "critical", r("\\b(take|swallow|eat|drink|try)\\b.{0,25}\\b(pills?|tablets?|medicines?|medications?|drugs?|supplements?)\\b"), "Advice about taking medicine.", "Remove medicine advice; children should ask a trusted adult.", false],
    ["unsafe_health_advice", "critical", r("\\b\\d+\\s?(mg|milligrams?|ml|millilit\\w*|pills?|tablets?|doses?)\\b"), "Contains a dose or quantity of medicine.", "Remove all dosing information.", false],
    ["unsafe_health_advice", "critical", r("\\b(don'?t|do\\s+not|never|no\\s+need\\s+to)\\s+(go\\s+to|see|visit|tell|call|ask)\\s+(the\\s+|a\\s+|your\\s+)?(doctors?|nurses?|dentists?|grown-?ups?|parents?|adults?)\\b"), "Discourages seeking help from adults or health professionals.", "Encourage asking a trusted adult.", false],
    ["unsafe_health_advice", "critical", r("\\b(skip|stop)\\s+(eating|meals?|breakfast|lunch|dinner|drinking\\s+water|your\\s+medicine)\\b"), "Encourages skipping food, water or medicine.", "Remove this advice.", false],
    ["unsafe_health_advice", "major", r("\\bhold\\s+your\\s+breath\\b.{0,25}\\b(long|until|minutes?|forever)\\b"), "Encourages a potentially unsafe breath-holding challenge.", "Remove the challenge.", false],
    ["unsafe_health_advice", "major", r("\\b(diets?|dieting|lose\\s+weight|calories|skinny)\\b"), "Diet/weight content is not appropriate for this audience.", "Remove diet and weight references.", false],
    ["misleading_health_claim", "major", r("\\b(cures?|never\\s+(get|be)\\s+sick|always\\s+(be\\s+)?healthy|100\\s*%|guarantee\\w*|detox\\w*|miracle|superfoods?|immune\\s+to)\\b"), "Absolute or misleading health promise.", "Avoid promises; describe what the body does.", false],
    ["stereotype", "major", r("\\b(boys|girls|men|women|ladies|guys)\\s+(are|aren'?t|can'?t|cannot|don'?t|shouldn'?t|should)\\b"), "Generalisation about a gender group.", "Make statements about everyone.", false],
    ["stereotype", "major", r("\\bonly\\s+(boys|girls|men|women)\\b|\\blike\\s+a\\s+girl\\b"), "Gender stereotype.", "Make statements about everyone.", false],
    ["stereotype", "major", r("\\b(doctors?|surgeons?|scientists?)\\s+(are|is)\\s+(always\\s+)?(men|guys|boys)\\b|\\bnurses?\\s+(are|is)\\s+(always\\s+)?(women|girls|ladies)\\b"), "Occupational gender stereotype.", "Anyone can be a doctor, nurse or scientist.", false],
    ["discriminatory", "critical", r("\\b(fat|skinny|chubby|disabled|crippled?|retard\\w*|lame|weird)\\s+(kids?|people|persons?|ones?|bodies)\\b"), "Demeaning reference to a group of people.", "Remove; describe bodies respectfully.", false],
    ["stereotype", "minor", r("\\b(fat|chubby|obese)\\b"), "Body-size reference; check for body shaming.", "Avoid body-size words.", false],
    ["copyright_imitation", "major", r("\\b(remix\\s+of|sampled\\s+from|in\\s+the\\s+style\\s+of)\\b"), "Possible imitation of an existing work.", "Keep lyrics fully original.", false],
  ];
  const NEGATION = /(don'?t\s+be|no\s+need\s+to\s+be|not|never|nothing|no)\s+\w*\s*$/i;

  const AGE = {
    "4-6": { maxWords: 9, maxLong: 1, jargon: new Set([...ADVANCED_JARGON, ...INTERMEDIATE_JARGON]) },
    "7-10": { maxWords: 13, maxLong: 2, jargon: ADVANCED_JARGON },
  };

  // ------------------------------------------------------------------ store
  const db = { topics: [], raps: [], nextTopic: 1, nextRap: 1, nextReview: 1 };
  const getTopic = (id) => { const t = db.topics.find((x) => x.id === Number(id)); if (!t) throw notFound(`topic ${id} not found`); return t; };
  const getRap = (id) => { const x = db.raps.find((y) => y.id === Number(id)); if (!x) throw notFound(`rap ${id} not found`); return x; };
  const approvedFacts = (t) => t.facts.filter((f) => f.status === "approved");
  const currentVersion = (rap) => rap.versions.find((v) => v.version_number === rap.current_version);
  const latest = (v, kind) => [...v.validations].reverse().find((x) => x.kind === kind) || null;
  const allLines = (ly) => ly.sections.flatMap((s) => s.lines);

  // ------------------------------------------------------------------ topics & facts
  function createTopic(body, who) {
    const name = String(body.topic || "").trim();
    const errors = [];
    if (!/^[A-Za-z][A-Za-z0-9 '\-]{1,79}$/.test(name)) errors.push({ loc: ["body", "topic"], msg: "must start with a letter (2-80 letters, digits, spaces)" });
    const amin = Number(body.target_age_min), amax = Number(body.target_age_max);
    if (!(amin >= 4 && amin <= 10)) errors.push({ loc: ["body", "target_age_min"], msg: "must be 4-10" });
    if (!(amax >= 4 && amax <= 10)) errors.push({ loc: ["body", "target_age_max"], msg: "must be 4-10" });
    if (amin > amax) errors.push({ loc: ["body", "target_age_max"], msg: "youngest age must not be above oldest age" });
    const content = normalizeText(body.educational_content || "");
    if (!content) errors.push({ loc: ["body", "educational_content"], msg: "must not be empty" });
    if (content.length > 20000) errors.push({ loc: ["body", "educational_content"], msg: "is longer than 20000 characters" });
    const objectives = (body.learning_objectives || []).map((o) => String(o).trim()).filter(Boolean);
    if (!objectives.length) errors.push({ loc: ["body", "learning_objectives"], msg: "add at least one learning objective" });
    if (errors.length) throw new HttpError(422, "request_validation_failed", "request body or parameters are invalid", { errors });
    let slug = slugify(name), base = slug, n = 2;
    while (db.topics.some((t) => t.slug === slug)) slug = `${base}-${n++}`;
    const flags = splitSentences(content).map((s, i) => [s, i]).filter(([s]) => looksLikeInjection(s))
      .map(([s, i]) => ({ type: "possible_prompt_injection", sentence_index: i, excerpt: s.slice(0, 80) }));
    const objs = body.primary_objective && !objectives.includes(body.primary_objective) ? [body.primary_objective, ...objectives] : objectives;
    const t = {
      id: db.nextTopic++, slug, title: name, category: body.category || "Human Anatomy",
      target_age_min: amin, target_age_max: amax, primary_objective: body.primary_objective || objs[0],
      forbidden_claims: (body.forbidden_claims || []).filter((c) => c && c.claim).map((c) => ({ claim: c.claim, pattern: c.pattern || null })),
      required_vocabulary: (body.required_vocabulary || []).map((v) => String(v).trim().toLowerCase()).filter(Boolean),
      content_flags: flags, is_demo_data: !!body.is_demo_data, created_by: who.name, created_at: now(),
      objectives: objs.slice(0, 12).map((text, i) => ({ objective_key: `${slug}_obj_${String(i + 1).padStart(2, "0")}`, text, position: i + 1 })),
      sources: [{ id: 1, source_key: `${slug}_src_001`, title: body.source_title || `${name} educational content`, uri: null,
        version: body.source_version || "1", content, content_sha256: "(computed on the server)", is_approved_source: !!body.source_is_approved, created_at: now() }],
      facts: [],
    };
    db.topics.push(t);
    return t;
  }
  const nextFactKey = (t) => { let n = t.facts.length + 1; while (t.facts.some((f) => f.fact_key === `${t.slug}_fact_${String(n).padStart(3, "0")}`)) n++; return `${t.slug}_fact_${String(n).padStart(3, "0")}`; };
  const forbiddenPatterns = (t) => t.forbidden_claims.filter((c) => c.pattern).map((c) => { try { return new RegExp(c.pattern, "i"); } catch { return null; } }).filter(Boolean);

  function extractFacts(t) {
    const src = t.sources[0];
    const seen = new Set(t.facts.map((f) => norm(f.text)));
    const created = [];
    for (const sentence of splitSentences(src.content).slice(0, 15)) {
      const text = sentence.replace(/\.$/, "");
      if (seen.has(norm(text))) continue;
      seen.add(norm(text));
      const reasons = [];
      if (overlap(text, sentence) < 0.5) reasons.push("fact wording is not grounded in its evidence quote");
      if (looksLikeInjection(sentence)) reasons.push("evidence contains instruction-like text (possible prompt injection)");
      if (forbiddenPatterns(t).some((p) => p.test(text)) || GLOBAL_MISCONCEPTIONS.some(([, p]) => p.test(text))) reasons.push("matches a known misconception for this topic");
      const f = {
        fact_key: nextFactKey(t), text, status: reasons.length ? "uncertain" : "candidate", source_document_id: src.id,
        evidence_quote: sentence, evidence_verified: true, uncertainty_reason: reasons.join("; ") || null,
        proposed_by: `mock:${MODEL}`, reviewed_by: null, reviewed_at: null, is_demo_data: t.is_demo_data,
      };
      t.facts.push(f); created.push(f);
    }
    return created;
  }

  function reviewFacts(t, body, who) {
    if (who.role !== "medical_reviewer") throw forbidden("only a medical reviewer can approve or reject medical facts");
    const approve = body.approve_fact_ids || [], reject = body.reject_fact_ids || [];
    if (!approve.length && !reject.length) throw rejected("provide at least one fact to approve or reject");
    const byKey = Object.fromEntries(t.facts.map((f) => [f.fact_key, f]));
    const unknown = [...approve, ...reject].filter((k) => !byKey[k]);
    if (unknown.length) throw rejected(`unknown fact ids: ${unknown.join(", ")}`);
    const bad = approve.filter((k) => !["candidate", "approved"].includes(byKey[k].status));
    if (bad.length) throw rejected(`these facts are uncertain or rejected and cannot be approved directly: ${bad.join(", ")}`);
    for (const k of approve) Object.assign(byKey[k], { status: "approved", reviewed_by: who.name, reviewed_at: now() });
    for (const k of reject) Object.assign(byKey[k], { status: "rejected", reviewed_by: who.name, reviewed_at: now() });
    return { topic_id: t.id, approved: approve, rejected: reject, added: [], approved_fact_count: approvedFacts(t).length };
  }

  // ------------------------------------------------------------------ generation (mock songwriter)
  const TAGS = ["Clap your hands and stomp your feet!", "Sing it with me, hip hip hooray!", "Here we go, here we go!",
    "Sing it out, that's right!", "Say it loud and sing along!", "Now we know it, too!", "Singing is so much fun!",
    "Sing it with me, one, two, three!", "Throw your hands up high, oh my!", "Spin around and shout it out!",
    "Hey hey, that's the way!", "Give a cheer, the facts are here!"];
  const SWAPS = { approximately: "about", continuously: "nonstop", throughout: "through", receives: "gets", processes: "works on", information: "messages" };
  function tagFor(line, used) {
    const lw = lastWord(line);
    for (const t of TAGS) if (!used.has(t) && rhymes(lw, lastWord(t))) { used.add(t); return t; }
    for (const t of TAGS) if (!used.has(t)) { used.add(t); return t; }
    return "Clap along and sing with me!";
  }
  const factLine = (f) => { const t = (f.simplified || f.text).replace(/\.$/, ""); return t[0].toUpperCase() + t.slice(1) + "!"; };
  const injectedMistake = (topic) => /heart/i.test(topic) ? "The heart makes blood all day!"
    : /brain/i.test(topic) ? "We only use ten percent of our brain!" : "Sing this song and you will never get sick!";

  function writeLyrics(t, req, band) {
    const facts = approvedFacts(t).map((f) => {
      let s = f.text; if (band === "4-6") for (const [a, b] of Object.entries(SWAPS)) s = s.replaceAll(a, b);
      return { id: f.fact_key, text: f.text, simplified: s };
    });
    const used = new Set();
    const sections = [];
    const prefix = (name) => name === "intro" ? "in" : name === "chorus" ? "ch" : name === "outro" ? "out" : "v" + name.split("_")[1];
    const mk = (name, lines) => ({ section: name, lines: lines.map((l, i) => ({ line_id: `${prefix(name)}_l${String(i + 1).padStart(2, "0")}`, text: l.text, fact_ids: l.ids || [] })) });
    if (req.include_intro) sections.push(mk("intro", [{ text: "Hey kids, are you ready?" }, { text: "Let's go, nice and steady!" }]));
    const nv = Math.min(4, Math.max(2, Number(req.num_verses) || 2));
    for (let v = 0; v < nv; v++) {
      const chunk = facts.filter((_, i) => i % nv === v).slice(0, 3);
      const lines = [];
      if (v === 0 && req.demo_inject_error) lines.push({ text: injectedMistake(t.title) }, { text: tagFor(injectedMistake(t.title), used) });
      for (const f of (chunk.length ? chunk : facts.slice(0, 1))) { const fl = factLine(f); lines.push({ text: fl, ids: [f.id] }, { text: tagFor(fl, used) }); }
      sections.push(mk(`verse_${v + 1}`, lines));
      if (v === 0) {
        const ch = [{ text: `${t.title}! ${t.title}! Sing it loud!` }, { text: "Sing it again and make us proud!" }];
        if (facts.length) { const fl = factLine(facts[0]); ch.push({ text: fl, ids: [facts[0].id] }, { text: tagFor(fl, used) }); }
        sections.push(mk("chorus", ch));
      }
    }
    if (req.include_outro) sections.push(mk("outro", [{ text: `That's the ${t.title.toLowerCase()} song, now you know!` }, { text: "Thanks for singing, here we go!" }]));
    const order = (s) => s === "intro" ? 0 : s === "verse_1" ? 1 : s === "chorus" ? 2 : s === "outro" ? 9 : 2 + Number(s.split("_")[1]);
    sections.sort((a, b) => order(a.section) - order(b.section));
    const arrangement = [...(req.include_intro ? ["intro"] : [])];
    for (let n = 1; n <= nv; n++) { arrangement.push(`verse_${n}`); if (n <= 2 || n === nv) arrangement.push("chorus"); }
    if (req.include_outro) arrangement.push("outro");
    return { title: `The ${t.title} Rap`, sections, arrangement };
  }

  // ------------------------------------------------------------------ validation
  function medicalCheck(t, lyrics, factKeys) {
    const facts = Object.fromEntries(approvedFacts(t).filter((f) => factKeys.includes(f.fact_key)).map((f) => [f.fact_key, f.text]));
    const rules = [...GLOBAL_MISCONCEPTIONS, ...t.forbidden_claims.filter((c) => c.pattern).map((c) => { try { return [c.claim, new RegExp(c.pattern, "i")]; } catch { return null; } }).filter(Boolean)];
    const claims = [];
    for (const line of allLines(lyrics)) {
      for (const [claim, p] of rules) if (p.test(line.text)) claims.push({ line_id: line.line_id, claim: line.text, status: "contradicted", source_fact_id: null, rationale: `States a known misconception: ${claim}`, checks: ["forbidden_claim_rule"] });
      const ids = line.fact_ids.filter((id) => facts[id]);
      if (ids.length) {
        for (const id of ids) {
          const ok = overlap(line.text, facts[id]) >= 0.5;
          claims.push({ line_id: line.line_id, claim: line.text, status: ok ? "supported" : "ambiguous", source_fact_id: ok ? id : null, rationale: ok ? "Matches the cited approved fact." : "Wording drifts from the cited fact.", checks: ["llm_triage"] });
        }
      } else if (isMedicalStatement(line.text, t.title)) {
        let best = null, score = 0;
        for (const [id, txt] of Object.entries(facts)) { const s = overlap(line.text, txt); if (s > score) { score = s; best = id; } }
        if (best && score >= 0.6) claims.push({ line_id: line.line_id, claim: line.text, status: "supported", source_fact_id: best, rationale: "Matches an approved fact.", checks: ["llm_triage"] });
        else if (!claims.some((c) => c.line_id === line.line_id)) claims.push({ line_id: line.line_id, claim: line.text, status: "unsupported", source_fact_id: null, rationale: "No approved fact covers this statement.", checks: ["llm_triage"] });
      }
    }
    const sev = { contradicted: "critical", unsupported: "major", ambiguous: "minor", outside_scope: "minor" };
    claims.forEach((c) => { c.severity = sev[c.status] || null; });
    const failing = [...new Set(claims.filter((c) => c.severity === "critical" || c.severity === "major").map((c) => c.line_id))].sort();
    const review = [...new Set(claims.filter((c) => c.severity === "minor").map((c) => c.line_id))].filter((x) => !failing.includes(x)).sort();
    const summary = {}; for (const s of ["supported", "contradicted", "unsupported", "ambiguous", "outside_scope"]) summary[s] = claims.filter((c) => c.status === s).length;
    return { status: failing.length ? "failed" : review.length ? "requires_review" : "passed", claims, failing_line_ids: failing, review_line_ids: review, summary,
      automated_triage_available: true, disclaimer: "Automated claim checking is triage only. It is not an independent medical authority; a human medical reviewer must sign off." };
  }

  function safetyCheck(t, lyrics, band) {
    const findings = [];
    const profile = AGE[band];
    const allowed = new Set(t.required_vocabulary.flatMap((v) => words(v)));
    for (const line of allLines(lyrics)) {
      for (const [category, severity, p, expl, suggestion, negatable] of SAFETY_RULES) {
        const m = line.text.match(p); if (!m) continue;
        let sev = severity, e = expl;
        if (negatable && NEGATION.test(line.text.slice(0, m.index))) { sev = "minor"; e += " (negated context; reviewer should confirm tone)"; }
        findings.push({ line_id: line.line_id, category, severity: sev, explanation: `${e} Matched: '${m[0]}'.`, suggestion, source: "rule" });
      }
      const ws = words(line.text);
      if (ws.length > profile.maxWords) findings.push({ line_id: line.line_id, category: "unnecessary_complexity", severity: ws.length > profile.maxWords * 1.5 ? "major" : "minor", explanation: `${ws.length} words; target is at most ${profile.maxWords} for ages ${band}.`, suggestion: "Shorten the line.", source: "rule" });
      const longW = ws.filter((w) => syllables(w) >= 4 && !allowed.has(w));
      if (longW.length > profile.maxLong) findings.push({ line_id: line.line_id, category: "unnecessary_complexity", severity: "minor", explanation: `Several long words for ages ${band}: ${longW.join(", ")}.`, suggestion: "Use shorter, familiar words.", source: "rule" });
      const jargon = [...new Set(ws.filter((w) => profile.jargon.has(w) && !allowed.has(w)))].sort();
      if (jargon.length) findings.push({ line_id: line.line_id, category: "unnecessary_complexity", severity: band === "4-6" && jargon.some((w) => ADVANCED_JARGON.has(w)) ? "major" : "minor", explanation: `Technical terms not in the approved vocabulary for ages ${band}: ${jargon.join(", ")}.`, suggestion: "Replace with everyday words or add the term to the topic vocabulary.", source: "rule" });
    }
    const blocking = findings.filter((f) => f.severity !== "minor");
    return { status: blocking.length ? "failed" : findings.length ? "requires_review" : "passed", findings,
      failing_line_ids: [...new Set(blocking.map((f) => f.line_id).filter(Boolean))].sort(), llm_review_available: true };
  }

  function evaluate(t, rap, v, med, saf) {
    const lyrics = v.lyrics, lines = allLines(lyrics);
    const objectives = t.objectives.map((o) => ({ id: o.objective_key, text: o.text }));
    const scores = {};
    // medical
    const c = med.claims;
    if (!c.length) scores.medical_accuracy = { score: 50, explanation: "No factual claims detected; nothing verified." };
    else if (c.some((x) => x.status === "contradicted")) scores.medical_accuracy = { score: 0, explanation: `${c.filter((x) => x.status === "contradicted").length} contradicted claim(s).` };
    else { const sup = c.filter((x) => x.status === "supported").length, uns = c.filter((x) => x.status === "unsupported").length;
      scores.medical_accuracy = { score: Math.max(0, Math.round((100 * sup / c.length - 15 * uns) * 10) / 10), explanation: `${sup}/${c.length} claims supported by approved facts; ${uns} unsupported.` }; }
    // coverage
    const texts = lines.map((l) => l.text); const windows = [...texts, ...texts.slice(1).map((x, i) => `${texts[i]} ${x}`)];
    const covered = {};
    for (const o of objectives) { const ow = contentWords(o.text); let best = 0;
      for (const w of windows) { const cw = contentWords(w); let n = 0; for (const x of ow) if (cw.has(x)) n++; best = Math.max(best, ow.size ? n / ow.size : 0); }
      covered[o.id] = best >= 0.6; }
    const nCov = Object.values(covered).filter(Boolean).length;
    scores.educational_coverage = { score: objectives.length ? Math.round(1000 * nCov / objectives.length) / 10 : 0, explanation: `${nCov}/${objectives.length} learning objectives verifiably covered.` };
    // age
    const prof = AGE[rap.age_band]; const lens = lines.map((l) => words(l.text).length);
    const over = lens.filter((n) => n > prof.maxWords).length; const cx = saf.findings.filter((f) => f.category === "unnecessary_complexity");
    const majors = cx.filter((f) => f.severity === "major").length;
    scores.age_appropriateness = { score: Math.max(0, 100 - 8 * over - 4 * (cx.length - majors) - 15 * majors), explanation: `Average ${mean(lens).toFixed(1)} words/line (max ${prof.maxWords} for ages ${rap.age_band}); ${over} long line(s); ${cx.length} complexity finding(s).` };
    // rhyme & rhythm
    let rhymed = 0, total = 0; const cvs = [];
    for (const s of lyrics.sections) { const ends = s.lines.map((l) => lastWord(l.text));
      ends.forEach((w, i) => { total++; if ([i - 2, i - 1, i + 1, i + 2].some((j) => j >= 0 && j < ends.length && j !== i && rhymes(w, ends[j]))) rhymed++; });
      const syl = s.lines.map((l) => lineSyllables(l.text)); if (syl.length >= 2 && mean(syl) > 0) cvs.push(pstdev(syl) / mean(syl)); }
    const rhythm = Math.max(0, 1 - (cvs.length ? mean(cvs) : 1));
    scores.rhyme_rhythm = { score: Math.round(1000 * (0.7 * (total ? rhymed / total : 0) + 0.3 * rhythm)) / 10, explanation: `${rhymed}/${total} lines rhyme with a neighbour; syllable consistency ${rhythm.toFixed(2)} (heuristic, uncalibrated).` };
    // memorability
    const chorus = lyrics.sections.find((s) => s.section === "chorus");
    if (!chorus) scores.memorability = { score: 0, explanation: "No chorus." };
    else { let pts = 20; const notes = ["chorus present"];
      if (lyrics.arrangement.filter((x) => x === "chorus").length >= 2) { pts += 25; notes.push("chorus repeats"); }
      if (chorus.lines.some((l) => l.text.toLowerCase().includes(t.title.toLowerCase()))) { pts += 20; notes.push("topic named in chorus"); }
      if (chorus.lines.length >= 2 && chorus.lines.length <= 4) { pts += 15; notes.push("short chorus"); }
      const cw = chorus.lines.flatMap((l) => words(l.text).filter((w) => w.length > 2)); if (cw.length !== new Set(cw).size) { pts += 20; notes.push("internal repetition / hook"); }
      scores.memorability = { score: pts, explanation: notes.join(", ") + "." }; }
    for (const k of Object.keys(scores)) scores[k].weight = WEIGHTS[k];
    const totalScore = Math.round(10 * Object.entries(scores).reduce((a, [k, s]) => a + s.score * WEIGHTS[k], 0)) / 10;
    const names = new Set(lyrics.sections.map((s) => s.section));
    const required = ["chorus", ...Array.from({ length: Number(rap.generation_request.num_verses || 2) }, (_, i) => `verse_${i + 1}`)];
    const gates = { medical_accuracy_not_failed: med.status !== "failed", child_safety_not_failed: saf.status !== "failed",
      required_structure_present: required.every((x) => names.has(x)), cites_approved_facts: lines.some((l) => l.fact_ids.length) };
    const recs = [];
    if (med.failing_line_ids.length) recs.push(`Fix medically inaccurate/unsupported lines: ${med.failing_line_ids.join(", ")}.`);
    if (med.review_line_ids.length) recs.push(`Medical reviewer should check ambiguous lines: ${med.review_line_ids.join(", ")}.`);
    if (saf.failing_line_ids.length) recs.push(`Fix child-safety issues on lines: ${saf.failing_line_ids.join(", ")}.`);
    const missing = Object.entries(covered).filter(([, v2]) => !v2).map(([k]) => k); if (missing.length) recs.push(`Add lines covering objectives: ${missing.join(", ")}.`);
    if (scores.age_appropriateness.score < 80) recs.push("Shorten long lines and replace technical words.");
    if (scores.rhyme_rhythm.score < 60) recs.push("Strengthen end rhymes (AABB couplets) and even out line lengths.");
    if (scores.memorability.score < 70) recs.push("Make the chorus shorter, repeat it, and include the topic name as a hook.");
    const softOk = totalScore >= THRESHOLD;
    return { total_score: totalScore, scores, hard_gates: gates, soft_score_passed: softOk, automated_checks_passed: Object.values(gates).every(Boolean) && softOk,
      threshold: THRESHOLD, recommendations: recs, rubric_version: "pilot-1.0", weights: WEIGHTS, created_at: now() };
  }

  // ------------------------------------------------------------------ workflow
  const ALLOWED = { draft: ["pending_review", "validation_failed", "rejected"], validation_failed: ["draft", "rejected"],
    pending_review: ["approved", "rejected", "revision_requested", "validation_failed"], revision_requested: ["draft", "rejected"], approved: [], rejected: [] };
  function transition(rap, to, actor, reason) {
    if (rap.status === to) return;
    if (!ALLOWED[rap.status].includes(to)) throw conflict(`cannot move rap ${rap.id} from '${rap.status}' to '${to}'`);
    rap.events.push({ from_status: rap.status, to_status: to, actor, reason: reason || null, version_number: rap.current_version, created_at: now() });
    rap.status = to; rap.updated_at = now();
  }

  function validate(rap) {
    if (!["draft", "validation_failed", "pending_review"].includes(rap.status)) throw conflict(`cannot validate a rap in status '${rap.status}'`);
    const t = getTopic(rap.topic_id), v = currentVersion(rap);
    const med = medicalCheck(t, v.lyrics, v.approved_fact_keys), saf = safetyCheck(t, v.lyrics, rap.age_band);
    v.validations.push({ kind: "medical", status: med.status, report: med, validator_version: "1.0.0", created_at: now() });
    v.validations.push({ kind: "safety", status: saf.status, report: saf, validator_version: "1.0.0", created_at: now() });
    if (med.status === "failed" || saf.status === "failed") transition(rap, "validation_failed", "system:pipeline", `medical=${med.status}, safety=${saf.status}`);
    else if (rap.status === "validation_failed") transition(rap, "draft", "system:pipeline", "re-validation passed");
    return [med, saf];
  }
  function evaluateRap(rap) {
    if (!["draft", "validation_failed", "pending_review"].includes(rap.status)) throw conflict(`cannot evaluate a rap in status '${rap.status}'`);
    const v = currentVersion(rap), med = latest(v, "medical"), saf = latest(v, "safety");
    if (!med || !saf) throw conflict("validate the current version before evaluating it");
    const ev = evaluate(getTopic(rap.topic_id), rap, v, med.report, saf.report);
    v.evaluations.push(ev);
    if (Object.values(ev.hard_gates).every(Boolean)) {
      if (rap.status === "draft") transition(rap, "pending_review", "system:pipeline", ev.soft_score_passed ? "automated checks passed" : `hard gates passed; soft score ${ev.total_score} below threshold ${THRESHOLD}`);
    } else if (["draft", "pending_review"].includes(rap.status)) {
      transition(rap, "validation_failed", "system:pipeline", `hard gates failed: ${Object.entries(ev.hard_gates).filter(([, x]) => !x).map(([k]) => k).join(", ")}`);
    }
    return ev;
  }

  function collectProblems(v, reviewerIssues) {
    const problems = {};
    const add = (id, why) => { if (id) (problems[id] = problems[id] || []).push(why); };
    const med = latest(v, "medical"), saf = latest(v, "safety");
    if (med) for (const c of med.report.claims) if (c.severity === "critical" || c.severity === "major") add(c.line_id, `medical: ${c.status} - ${c.rationale}`);
    if (saf) for (const f of saf.report.findings) if (f.severity !== "minor") add(f.line_id, `safety/${f.category}: ${f.explanation}`);
    for (const i of reviewerIssues || []) add(i.line_id, `reviewer (${i.severity}): ${i.reason}`);
    return problems;
  }

  function revise(rap, actor, autoValidate) {
    const v = currentVersion(rap); let changeType, problems;
    if (rap.status === "revision_requested") {
      changeType = "reviewer_revision";
      const issues = rap.reviews.filter((x) => x.rap_version_number === v.version_number && ["request_revision", "comment"].includes(x.decision)).flatMap((x) => x.issues);
      problems = collectProblems(v, issues);
      if (!Object.keys(problems).length) throw rejected("the revision request has no line-level issues to fix; select lines when requesting changes, or write a new rap");
    } else if (rap.status === "validation_failed") {
      changeType = "auto_revision";
      if (rap.auto_revision_attempts >= MAX_REVISIONS) { rap.needs_human_intervention = true; throw conflict(`automatic revision limit (${MAX_REVISIONS}) reached; human intervention required`); }
      problems = collectProblems(v);
      if (!Object.keys(problems).length) { rap.needs_human_intervention = true; throw conflict("validation failures are not tied to specific lines; human intervention required"); }
      rap.auto_revision_attempts++;
    } else throw conflict(`cannot revise a rap in status '${rap.status}'`);

    const t = getTopic(rap.topic_id);
    const facts = approvedFacts(t).filter((f) => v.approved_fact_keys.includes(f.fact_key)).map((f) => ({ id: f.fact_key, text: f.text, simplified: f.text }));
    const lines = Object.fromEntries(allLines(v.lyrics).map((l) => [l.line_id, l.text]));
    const lyrics = clone(v.lyrics); const changed = [];
    Object.keys(problems).forEach((id, i) => {
      if (!(id in lines) || !facts.length) return;
      const ranked = [...facts].sort((a, b) => (overlap(lines[id], b.text) - overlap(lines[id], a.text)) || (Math.abs(facts.indexOf(a) - i) - Math.abs(facts.indexOf(b) - i)));
      const pick = ranked.find((f) => factLine(f) !== lines[id]) || ranked[0];
      for (const s of lyrics.sections) for (const l of s.lines) if (l.line_id === id && factLine(pick) !== l.text) { l.text = factLine(pick); l.fact_ids = [pick.id]; changed.push(id); }
    });
    if (!changed.length) return false;
    const nv = { ...clone(v), version_number: v.version_number + 1, lyrics, change_type: changeType, changed_line_ids: changed,
      change_reason: changed.map((id) => `${id}: ${problems[id].join(" | ")}`).join("; "), validations: [], evaluations: [], created_at: now() };
    rap.versions.push(nv); rap.current_version = nv.version_number;
    transition(rap, "draft", actor, `${changeType} -> v${nv.version_number}`);
    if (autoValidate) { validate(rap); evaluateRap(rap); }
    return true;
  }

  function runChecks(rap, autoRevise) {
    validate(rap); evaluateRap(rap);
    while (autoRevise && rap.status === "validation_failed") {
      if (rap.auto_revision_attempts >= MAX_REVISIONS) { rap.needs_human_intervention = true; break; }
      let changed;
      try { changed = revise(rap, "system:pipeline", false); } catch { break; }
      if (changed) { validate(rap); evaluateRap(rap); }
    }
  }

  function generate(body, who) {
    const t = getTopic(body.topic_id);
    if (!approvedFacts(t).length) throw conflict("topic has no approved facts; extract candidates and have a medical reviewer approve them first");
    let band = body.age_band;
    if (band) { const [lo, hi] = band === "4-6" ? [4, 6] : [7, 10]; if (hi < t.target_age_min || lo > t.target_age_max) throw rejected(`age band ${band} does not overlap the topic's target ages ${t.target_age_min}-${t.target_age_max}`); }
    else band = t.target_age_min <= 6 ? "4-6" : "7-10";
    const n = Math.min(3, Math.max(1, Number(body.num_candidates) || 1));
    const out = [];
    for (let i = 0; i < n; i++) {
      const lyrics = writeLyrics(t, body, band);
      const rap = { id: db.nextRap++, topic_id: t.id, status: "draft", age_band: band, style: body.style || "playful",
        generation_request: { ...body, num_verses: Number(body.num_verses) || 2 }, current_version: 1, auto_revision_attempts: 0,
        needs_human_intervention: false, created_by: who.name, created_at: now(), updated_at: now(), approved_at: null,
        versions: [{ version_number: 1, title: lyrics.title, lyrics, objectives_covered: t.objectives.map((o) => o.objective_key),
          change_type: "generated", changed_line_ids: [], change_reason: null, provider: "mock", model: MODEL, prompt_name: "rap_generation",
          prompt_version: "1.0.0", prompt_sha256: "(see app/prompts)", generation_config: { style: body.style, num_verses: Number(body.num_verses) || 2 },
          approved_fact_keys: approvedFacts(t).map((f) => f.fact_key), run_id: hex(), input_tokens: 0, output_tokens: 0, estimated_cost_usd: null,
          validations: [], evaluations: [], created_at: now() }],
        reviews: [], events: [{ from_status: null, to_status: "draft", actor: who.name, reason: "generated", version_number: 1, created_at: now() }] };
      db.raps.push(rap);
      if (body.run_pipeline !== false) runChecks(rap, body.auto_revise !== false);
      out.push(rap);
    }
    return out;
  }

  // ------------------------------------------------------------------ review
  function approvalsNow(rap) {
    const out = {};
    for (const x of rap.reviews) if (x.rap_version_number === rap.current_version && x.decision === "approve" && !out[x.reviewer_role]) out[x.reviewer_role] = x.reviewer_name;
    return out;
  }
  function blockers(rap) {
    const b = [];
    if (rap.status !== "pending_review") return [`rap is '${rap.status}', not 'pending_review'`];
    const v = currentVersion(rap), med = latest(v, "medical"), saf = latest(v, "safety"), ev = v.evaluations[v.evaluations.length - 1];
    if (!med || !saf || !ev) b.push("current version has not been fully validated and evaluated");
    else {
      if (med.status === "failed") b.push("unresolved critical/major medical accuracy errors");
      if (saf.status === "failed") b.push("unresolved critical/major child-safety findings");
      const failed = Object.entries(ev.hard_gates).filter(([, x]) => !x).map(([k]) => k); if (failed.length) b.push(`hard gates failed: ${failed.join(", ")}`);
    }
    if (rap.reviews.some((x) => x.rap_version_number === rap.current_version && x.decision === "comment" && x.issues.some((i) => i.severity === "critical")))
      b.push("a reviewer raised critical issues on this version; request a revision first");
    return b;
  }
  function review(rap, body, who) {
    if (!PERMISSIONS.review.includes(who.role)) throw forbidden("only medical reviewers and educators can review raps");
    const d = body.decision;
    if (!["approve", "reject", "request_revision", "comment"].includes(d)) throw rejected("unknown decision");
    if (d !== "approve" && !(body.feedback || (body.issues && body.issues.length))) throw rejected(`'${d}' requires feedback or issues`);
    if (["approved", "rejected"].includes(rap.status) && d !== "comment") throw conflict(`rap is already '${rap.status}'`);
    if (d === "approve") {
      const b = blockers(rap); if (b.length) throw conflict("approval blocked: " + b.join("; "), { blockers: b });
      const ex = approvalsNow(rap);
      if (ex[who.role]) throw conflict(`version ${rap.current_version} already approved by a ${who.role}`);
      if (Object.values(ex).includes(who.name)) throw conflict("the same person cannot provide more than one required approval");
    } else if (d === "request_revision" && rap.status !== "pending_review") throw conflict(`cannot request revision for a rap in status '${rap.status}'`);
    const dec = { id: db.nextReview++, rap_version_number: rap.current_version, reviewer_name: who.name, reviewer_role: who.role, decision: d,
      feedback: body.feedback || null, issues: (body.issues || []).map((i) => ({ line_id: i.line_id || null, severity: i.severity || "major", reason: i.reason })), created_at: now() };
    rap.reviews.push(dec);
    if (d === "approve" && REQUIRED_APPROVERS.every((role) => approvalsNow(rap)[role])) {
      transition(rap, "approved", who.name, `approved by ${Object.entries(approvalsNow(rap)).map(([k, v]) => `${k}: ${v}`).join(", ")}`);
      rap.approved_at = now();
    } else if (d === "reject") transition(rap, "rejected", who.name, "rejected by reviewer");
    else if (d === "request_revision") transition(rap, "revision_requested", who.name, "reviewer requested revision");
    return dec;
  }

  // ------------------------------------------------------------------ presenters
  const topicSummary = (t) => ({ id: t.id, slug: t.slug, title: t.title, category: t.category, target_age_min: t.target_age_min, target_age_max: t.target_age_max, is_demo_data: t.is_demo_data, created_at: t.created_at });
  const rapSummary = (x) => ({ id: x.id, topic_id: x.topic_id, status: x.status, age_band: x.age_band, style: x.style, current_version: x.current_version, needs_human_intervention: x.needs_human_intervention, created_at: x.created_at, updated_at: x.updated_at });
  function rapDetail(x) {
    const v = currentVersion(x), med = latest(v, "medical"), saf = latest(v, "safety");
    return { ...rapSummary(x), title: v.title, lyrics: v.lyrics, objectives_covered: v.objectives_covered,
      medical_validation: med ? med.report : null, safety_validation: saf ? saf.report : null,
      evaluation: v.evaluations.length ? v.evaluations[v.evaluations.length - 1] : null,
      reviews: x.reviews, approvals_for_current_version: Object.keys(approvalsNow(x)).sort(), approvals_required: REQUIRED_APPROVERS,
      auto_revision_attempts: x.auto_revision_attempts, is_demo_data: getTopic(x.topic_id).is_demo_data };
  }
  const history = (x) => ({ rap_id: x.id, status: x.status,
    versions: x.versions.map(({ validations, evaluations, ...rest }) => rest), events: x.events, reviews: x.reviews,
    validations: x.versions.flatMap((v) => v.validations.map((val) => ({ version: v.version_number, ...val }))),
    evaluations: x.versions.flatMap((v) => v.evaluations.map((e) => ({ version: v.version_number, ...e }))) });

  // ------------------------------------------------------------------ samples & seed
  const SAMPLES = [
    { topic: "Brain", category: "Body Systems", target_age_min: 7, target_age_max: 10, source_title: "MEDKids Brain starter card (demonstration data, unreviewed)", source_version: "demo-0.1", source_is_approved: false, is_demo_data: true,
      primary_objective: "Understand the brain as a body control and information-processing organ",
      educational_content: "The brain is part of the nervous system. Nerves carry signals between the brain and the body. The brain helps control movement. The brain helps us think, learn, and remember. The brain receives and processes information from the senses. The brain works together with the rest of the body.",
      learning_objectives: ["The brain is part of the nervous system.", "The brain helps control movement.", "The brain supports thinking, learning, and memory.", "The brain receives and processes information from the senses."],
      forbidden_claims: [{ claim: "The brain does every job alone", pattern: "\\bbrain\\b.{0,20}\\b(does|controls?|runs?)\\b.{0,15}\\b(everything|every\\s+job|all\\s+by\\s+itself|alone)\\b" },
        { claim: "People only use 10% of their brain", pattern: "\\b(only|just)\\b.{0,15}\\b(10|ten)\\s*(%|percent)\\b" }, { claim: "The brain is a muscle", pattern: "\\bbrain\\b\\s*(is|'s)\\s+(a\\s+)?muscle\\b" }],
      required_vocabulary: ["nervous system", "nerves", "signals", "senses", "information"] },
    { topic: "Heart", category: "Human Anatomy", target_age_min: 4, target_age_max: 6, source_title: "MEDKids Heart starter card (demonstration data, unreviewed)", source_version: "demo-0.1", source_is_approved: false, is_demo_data: true,
      primary_objective: "Understand the heart as a muscle that pumps blood",
      educational_content: "The heart is a muscle. The heart pumps blood through the body. Blood travels through tubes called blood vessels. Blood carries oxygen and nutrients through the body. The heart works all day and all night to support the body. You can feel your heartbeat in your chest.",
      learning_objectives: ["The heart is a muscle.", "The heart pumps blood.", "Blood carries oxygen and nutrients through the body.", "The heart works continuously to support the body."],
      forbidden_claims: [{ claim: "The heart makes blood", pattern: "\\bheart\\w*\\b.{0,20}\\b(makes?|produces?|creates?)\\b.{0,10}\\bblood\\b" },
        { claim: "The heart makes feelings or love", pattern: "\\bheart\\b.{0,15}\\b(makes?|gives?|holds?)\\b.{0,10}\\b(feelings?|love|emotions?)\\b" },
        { claim: "The heart is on the left side of the chest", pattern: "\\bheart\\b.{0,25}\\bleft\\s+side\\b" },
        { claim: "The heart stops when you sleep", pattern: "\\bheart\\b.{0,20}\\b(stops?|rests?)\\b.{0,20}\\b(sleep|night)\\b" }],
      required_vocabulary: ["muscle", "oxygen", "nutrients", "blood vessels", "heartbeat"] },
  ];
  (function seed() {
    const author = PRINCIPALS["demo-author"], med = PRINCIPALS["demo-medical"];
    const heart = createTopic(SAMPLES[1], author);
    extractFacts(heart);
    reviewFacts(heart, { approve_fact_ids: heart.facts.map((f) => f.fact_key) }, med);
    generate({ topic_id: heart.id, style: "playful", num_verses: 2, include_outro: true }, author);
    const brain = createTopic(SAMPLES[0], author);
    extractFacts(brain);
  })();

  // ------------------------------------------------------------------ router
  function route(method, path, query, body, who) {
    const need = (perm) => { if (!PERMISSIONS[perm].includes(who.role)) throw forbidden(`role '${who.role}' is not allowed to perform '${perm}' actions`); };
    let m;
    if (method === "GET" && path === "/api/health") return { status: "ok", app_env: "demo", llm_provider: "mock", database: "ok", version: "0.1.0-demo" };
    if (method === "GET" && path === "/api/me") return { name: who.name, role: who.role, permissions: Object.keys(PERMISSIONS).filter((p) => PERMISSIONS[p].includes(who.role)).sort(), required_approval_roles: REQUIRED_APPROVERS, llm_provider: "mock", app_env: "demo" };
    if (method === "GET" && path === "/api/samples") { need("read"); return clone(SAMPLES); }
    if (path === "/api/topics") {
      if (method === "GET") { need("read"); return db.topics.map(topicSummary); }
      if (method === "POST") { need("author"); return [201, createTopic(body, who)]; }
    }
    if ((m = path.match(/^\/api\/topics\/(\d+)$/)) && method === "GET") { need("read"); return getTopic(m[1]); }
    if ((m = path.match(/^\/api\/topics\/(\d+)\/facts\/extract$/)) && method === "POST") {
      need("author"); const t = getTopic(m[1]); const created = extractFacts(t);
      return { topic_id: t.id, run_id: hex(), candidates: created.filter((f) => f.status === "candidate"), uncertain: created.filter((f) => f.status === "uncertain") };
    }
    if ((m = path.match(/^\/api\/topics\/(\d+)\/facts\/approve$/)) && method === "POST") { need("approve_facts"); return reviewFacts(getTopic(m[1]), body, who); }
    if (path === "/api/raps/generate" && method === "POST") { need("author"); return [201, { raps: generate(body, who).map(rapDetail) }]; }
    if (path === "/api/raps" && method === "GET") {
      need("read"); let list = [...db.raps].sort((a, b) => b.id - a.id);
      if (query.get("topic_id")) list = list.filter((x) => x.topic_id === Number(query.get("topic_id")));
      if (query.get("status")) list = list.filter((x) => x.status === query.get("status"));
      return list.slice(0, Number(query.get("limit") || 100)).map(rapSummary);
    }
    if ((m = path.match(/^\/api\/raps\/(\d+)$/)) && method === "GET") { need("read"); return rapDetail(getRap(m[1])); }
    if ((m = path.match(/^\/api\/raps\/(\d+)\/history$/)) && method === "GET") { need("read"); return history(getRap(m[1])); }
    if ((m = path.match(/^\/api\/raps\/(\d+)\/validate$/)) && method === "POST") { need("author"); const x = getRap(m[1]); const [med, saf] = validate(x); return { medical_accuracy: med, child_safety: saf, rap: rapDetail(x) }; }
    if ((m = path.match(/^\/api\/raps\/(\d+)\/evaluate$/)) && method === "POST") { need("author"); const x = getRap(m[1]); return { evaluation: evaluateRap(x), rap: rapDetail(x) }; }
    if ((m = path.match(/^\/api\/raps\/(\d+)\/revise$/)) && method === "POST") { need("author"); const x = getRap(m[1]); const changed = revise(x, who.name, body ? body.auto_validate !== false : true); return { changed, rap: rapDetail(x) }; }
    if ((m = path.match(/^\/api\/raps\/(\d+)\/review$/)) && method === "POST") {
      need("review"); const x = getRap(m[1]); const dec = review(x, body || {}, who);
      return { decision: dec, rap: rapDetail(x), approval_blockers: x.status === "pending_review" ? blockers(x) : [] };
    }
    if ((m = path.match(/^\/api\/raps\/(\d+)\/export$/)) && method === "GET") {
      need("read"); const x = getRap(m[1]); if (x.status !== "approved") throw conflict("only approved raps can be exported");
      const v = currentVersion(x), t = getTopic(x.topic_id);
      return { rap_id: x.id, topic: t.title, topic_slug: t.slug, age_band: x.age_band, version: v.version_number, title: v.title, lyrics: v.lyrics,
        objectives_covered: v.objectives_covered, approved_fact_keys: v.approved_fact_keys, approvals: approvalsNow(x), approved_at: x.approved_at,
        provenance: { provider: v.provider, model: v.model, prompt_name: v.prompt_name, prompt_version: v.prompt_version, run_id: v.run_id },
        is_demo_data: t.is_demo_data, audio: null };
    }
    throw notFound(`no demo route for ${method} ${path}`);
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (!url.pathname.startsWith("/api/")) return realFetch(input, init);
    const method = (init.method || "GET").toUpperCase();
    const key = (init.headers && (init.headers["X-API-Key"] || init.headers["x-api-key"])) || null;
    const json = (status, payload) => new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
    const slow = url.pathname.endsWith("/generate") || url.pathname.endsWith("/revise") || url.pathname.endsWith("/extract");
    await new Promise((res) => setTimeout(res, slow ? 650 : 60));
    if (url.pathname === "/api/health") return json(200, route("GET", "/api/health"));
    const who = PRINCIPALS[key];
    if (!who) return json(401, { error: { code: "unauthenticated", message: key ? "invalid API key" : "missing credentials", details: {} } });
    try {
      const body = init.body ? JSON.parse(init.body) : undefined;
      const out = route(method, url.pathname, url.searchParams, body, who);
      return Array.isArray(out) && typeof out[0] === "number" ? json(out[0], clone(out[1])) : json(200, clone(out));
    } catch (e) {
      if (e instanceof HttpError) return json(e.status, { error: { code: e.code, message: e.message, details: e.details } });
      console.error(e);
      return json(500, { error: { code: "internal_error", message: "the demo engine hit an unexpected error", details: {} } });
    }
  };

  window.MEDKIDS_DEMO = {
    keys: { author: "demo-author", medical_reviewer: "demo-medical", educator: "demo-educator" },
    showExport(data) {
      const dlg = document.getElementById("export-dialog");
      document.getElementById("export-json").textContent = JSON.stringify(data, null, 2);
      dlg.showModal();
    },
  };
})();
