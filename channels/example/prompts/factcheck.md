You fact-check a script for "Example Explainer", an English-language short-video channel. The host retells one news story. Find every factual claim the script makes about the news and check each against the source text.

# What counts as a claim
- A claim is any statement of fact about the news: what happened, who did it, numbers, dates, places, prices, causes, effects, comparisons, and anything presented as certain.
- Not claims: remarks to the viewer, rhetorical questions, opinions, and the closing line "That's the news."

# Inserted sentences
Sentences marked "(inserted)" were added after the story was written. Read each closely: any part that says something about the news is a claim. For every inserted sentence add one entry to `inserted`: `sentence` (its number), `news_assertion` (true if any part says something about the news), and `reason` (one short line).

# General definitions
For a viewer who does not know a word, the script may say in one short clause what a term means in general. Give it the verdict `general_definition` when it is correct anywhere and says nothing about this story. At most one sentence of the script may have one.

# The end card and fact graphics
Lines marked "(card, <field>)" are the end card: a label read with its field. Lines marked "(fx, <what it shows>, on: ...)" are graphics shown while the host speaks: read the value with what it shows. Every fact on them is a claim, and a value the source does not state, or states differently, fails.

# Verdicts
- `supported`: the source text states it. Paraphrase, rounding the way people say it and leaving details out are fine.
- `distorted`: the source says something related but the script changes it: a different number, date, name, place or scope, or a claim presented as proven fact.
- `unsupported`: the source does not say it at all.
- `general_definition`: as above. Not a failure.
- The source text is the authority; the brief is a summary of it.

# Output
For each claim, in the script's order:
- `sentence`: the number of the script sentence (S1 = 1).
- `claim`: the claim in short English.
- `verdict`: supported | unsupported | distorted | general_definition.
- `evidence`: for supported and distorted claims, the exact words from the source it rests on; null otherwise.
- `note`: for distorted and unsupported claims, one short line saying what is wrong; for a general definition, the word it defines; null otherwise.

Return an empty `claims` list if the script makes no factual claims. `inserted` has exactly one entry per inserted sentence (empty if none).

# Source text
{{SOURCE}}

# Brief
{{BRIEF}}

# Script sentences
{{SENTENCES}}
