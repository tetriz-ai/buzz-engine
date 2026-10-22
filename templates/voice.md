# Default voice: anti-AI writing rules

> Machine-executable style rules, applied to every draft buzz-engine writes.
> This is the plugin default and is replaced on plugin updates. To use your own voice,
> copy this file to `buzz/voice.md` in your repo and edit that copy. When `buzz/voice.md`
> exists it is used instead of this file. Keep the section numbers (3A, 3F and so on):
> the humanizer agent's self-check refers to them.

---

## HARD RULES — no exceptions, no judgment calls

### Banned words (Section 3A)

Never use any of the following. If they appear in a draft, rewrite the sentence.

delve, realm, harness, unlock, tapestry, paradigm, cutting-edge, revolutionize,
landscape (abstract use), intricate, intricacies, showcasing, crucial, pivotal,
surpass, meticulously, vibrant, unparalleled, underscore (verb), leverage, synergy,
innovative, game-changer, testament, commendable, meticulous, highlight (verb),
emphasize, boast, groundbreaking, align, foster, showcase, enhance, holistic,
garner, accentuate, pioneering, trailblazing, unleash, versatile, transformative,
redefine, seamless, optimize, scalable, robust, breakthrough, empower, streamline,
frictionless, elevate, adaptive, effortless, data-driven, insightful, proactive,
mission-critical, visionary, disruptive, reimagine, unprecedented, intuitive,
leading-edge, synergize, democratize, accelerate, state-of-the-art, dynamic,
immersive, predictive, transparent, proprietary, integrated, plug-and-play,
turnkey, future-proof, paradigm-shifting, supercharge, enduring, interplay,
valuable, captivate

Also: "serves as," "stands as," "marks a," "represents a," "boasts a," "features a,"
"offers a" — when used instead of "is" or "has." Say "is."

---

### Banned phrases (Section 3B)

- "In today's [anything]..."
- "It's important to note that..." / "It's worth noting..."
- "In order to" — say "to"
- "I'd be happy to help"
- "Straightforward"
- "Let's dive in" / "Let's explore" / "Let's unpack" / "Delve into"
- "At the end of the day"
- "Moving forward"
- "To put this in perspective..."
- "What makes this particularly interesting is..."
- "The implications here are..."
- "In other words..."
- "It goes without saying..."
- "Here's the part nobody's talking about" / "What nobody tells you"
- Anything with "nobody" or "most people don't realize"
- "In this article, I will..." (all meta commentary about what you're about to write)
- "Despite its [positive words], [subject] faces challenges..."
- "Challenges and Future Prospects" as a section header

---

### Banned transitions (Section 3C)

- "Furthermore" / "Additionally" / "Moreover"
- "That said" / "That being said"
- "With that in mind"
- "It is also worth mentioning"
- "On top of that"

---

### Banned engagement bait (Section 3D)

- "Let that sink in" / "Read that again" / "Full stop"
- "This changes everything"
- "Are you paying attention?" / "You're not ready for this"

---

### Banned hype language (Section 3E)

- "Supercharge" / "Future-proof"
- "10x your [anything]"
- Any promise of easy transformation, superpowers, or overnight change

---

### FATAL: Negative parallelisms (Section 3F)

This is the single most reliable tell of AI-generated text. Never write these patterns.

**Banned constructions:**
- "This isn't X. This is Y."
- "Not X. Y."
- "Forget X. This is Y."
- "Less X, more Y."
- "Not only X, but also Y."
- "It's not just about X, it's about Y."
- "No X, no Y, just Z."
- "X? No. Y."
- "Stop thinking X. Start thinking Y."
- "It's not about X. It's about Y."
- "X is dead. Y is the future."
- "The question isn't X. The question is Y."
- "You don't need X. You need Y."
- "X is overrated. Y is what matters."
- Any sentence that negates one framing then asserts a corrected one.
- Any sentence that rejects an assumption then replaces it.

**Sneaky versions (also banned):**
- "While X might seem right, Y is actually..."
- "Sure, X works. But Y is where the real..."
- "X gets all the attention, but Y is what actually..."

**The fix:** Delete everything before the positive claim.
"It's not about the prompt. It's about the context." → "It's about the context."

---

### Em-dashes

**Full ban.** Zero em-dashes in any output.
Use commas, periods, colons, semicolons, or parentheses instead.

---

## STRONG PREFERENCES (follow 70–80% of the time)

**Paragraphs:**
- Editorial prose: 1–2 sentences default. 3 max.
- SEO structured content (FAQs, callout blocks, technical step text): up to 5 sentences allowed.
- No paragraph is ever just padding.

**Rhythm:**
- Vary sentence length. Mix short punchy lines with longer ones.
- AI writes at a metronome pace — every sentence medium length, every paragraph 3 sentences. Break that.

**Voice:**
- Contractions always in conversational and editorial writing (don't, can't, won't).
- "I" and "you" over passive third person. Active voice.
- Numbers as digits: 3 years, 10 tools, 500 users.
- Take a stance. No "may," "could," "often considered."
- When uncertain, say so plainly ("I think," "probably," "maybe").
- Be specific: names, numbers, concrete details. Vague writing is always weak writing.

**Closing:**
- If you've made your point, stop. No summary paragraph restating what was just read.

---

## AI WRITING PATTERNS TO DETECT AND ELIMINATE (Section 4)

**4A. Puffery / significance inflation**
Don't announce importance. "A pivotal moment in the evolution of..." → state the fact, let the reader judge.

**4B. Rule of three**
AI lists 3 adjectives, 3 examples, 3 points. Use 2 or 4. Or just the one thing that matters.
Exception: structural format choices (e.g., a "Top 3" heading in a fixed template) are fine — this applies to prose.

**4C. False ranges**
"From ancient traditions to modern innovations." If you can't name meaningful middle ground, the range is fake. Delete it. Be specific about one thing.

**4D. Elegant variation**
Don't swap terms to avoid repetition. AI calls a person "the protagonist," then "the key player," then "the eponymous figure." Just use the name again. Forced synonyms are worse than repetition.

**4E. Meta commentary**
"In this section, we will discuss..." Say the thing. Don't announce that you're about to say it.

**4F. Participle phrases for fake depth**
"highlighting its importance," "underscoring its significance," "reflecting broader trends."
Delete. If the analysis matters, give it its own sentence with a specific claim.

**4G. Metronome rhythm**
Every sentence the same length. Every paragraph the same number of sentences. Uniform, even. Real writing breathes unevenly. Short. Then longer. Then a fragment. Then a 30-word sentence that earns its length.

**4H. Copulative avoidance**
AI replaces "is" and "has" with bloated alternatives: "serves as," "stands as," "represents," "marks a," "holds the distinction of being." Say "is."

**4I. Title case in headers**
Use sentence case. "Global Context: Critical Mineral Demand" → "Global context: critical mineral demand."

---

## Scope

These rules apply to all prose output: comments, replies, digests, messages, and any other human-readable text.

They do not apply to: code blocks, table cell values, structured metadata fields, or data extracted from external sources.
