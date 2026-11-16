# Fact-checking

The point of the chain is that nothing unsupported by the source reaches a render. The check is built from three parts: the **draft lock**, the **claim check** and the **repair loop**.

## The draft lock

The pipeline writes a plain *draft* from the facts sheet, then a *styled script* from the draft. The styling pass is where invented facts creep in (a joke that asserts something, a number "improved"). The draft lock aligns the two sentence by sentence (`pipeline/script/lock.py`) and labels each sentence of the final script:

- `draft`: unchanged;
- `changed`: a draft sentence the styling pass edited (similarity between `steps.script.changed_ratio` and `same_ratio`);
- `punchup`: a sentence the styling pass inserted;
- sentences of the draft that were dropped are listed too.

The origin travels with every sentence, so a failure can be routed to the pass that caused it.

## The claim check

`factcheck` (the source text is cut at `steps.factcheck.max_source_chars`) numbers the final script's display sentences and asks the checker role (`models.checker`, `prompts/factcheck.md`, a JSON schema) for every factual claim about the news. Each claim gets:

- the sentence it is in;
- a verdict: `supported`, `unsupported`, `distorted` or `general_definition`;
- the evidence quote from the source (marked `evidence_found: false` when the quote is not actually in the source; this does not change the verdict) and a note.

Rules the code enforces whatever the model said:

- jokes about the viewer, a group or the host are not claims; a joke that asserts something about the news subject is;
- `general_definition` is a clause saying what a role, field or term means in general; it is not a failure, but only one sentence may have it, and a second is turned into `unsupported`;
- sentences the styling pass inserted must each be reviewed exactly once in a separate list, saying whether they assert anything about the news; an inserted sentence cannot pass by being skipped, or the answer is rejected as off-schema;
- the end card's lines and the fact graphics are checked the same way, in calls of their own; for them a general definition is also a failure.

`unsupported` and `distorted` fail. Everything is stored with the script for the review message.

## The repair loop

When a draft sentence fails, the runner sends the work back to `write`, then `style`; when a sentence the styling pass added or changed fails, back to `style` only; the failing sentences and their notes are given to the pass as `ctx.failures`. The check runs again on the new script. `steps.factcheck.repair_rounds` bounds the rounds (default 1). A script that still fails is marked *flagged*: it is still rendered and sent to review, with a warning and the failing claims shown to the human. A repair round that itself fails keeps the first version, flagged. Passes are stored, so a crash mid-loop resumes after the last finished pass and a repaired round is not repeated.

## Run it on your own text

```sh
cat > inputs.json <<'EOF'
{"Story": "<the source text>", "Brief": "<facts sheet or empty>", "Draft": "<your script>", "Script": "<your script>"}
EOF
reelline step factcheck --input inputs.json --channel my-channel
```

It prints the claims with verdicts, evidence and notes as JSON. Only the checker role is called (cost goes to the ledger).

## A planted-error test of your own

To see how well the check catches errors with your models and prompts:

1. Take 20 or more real source texts and good scripts for them (your own, or ones the pipeline made and a person verified).
2. Make copies with one error *planted* in each, by hand or by script, of the kinds you care about: a changed number, a swapped name, a reversed relationship, a date moved, an added claim the source does not make, a joke that asserts something. Keep a list of which sentence you changed and how.
3. Run the check (above) on the clean scripts and on the planted ones.
4. Count: *caught* (a failing verdict on the planted sentence), *missed* (supported), and *false alarms* on the clean scripts (a failing verdict on a true sentence). Look at the misses by kind of error.
5. Change one thing at a time (the checker model, the prompt, the source's length limit `steps.factcheck.max_source_chars`) and rerun. Keep the planted set fixed so numbers are comparable.

Report the planted set's size and the miss rate with the result; a checker's score on easy errors says little about subtle ones.
