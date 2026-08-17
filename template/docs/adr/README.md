# Decisions

`AGENTS.md` bans comments and sends rationale to two places: the commit message, and this
directory. This is the second one — the place a decision goes when it will still matter in
six months and the commit that made it will not be the thing anyone finds.

## When to write one

Not often. An ADR earns its keep when **a reasonable person would undo the decision** —
when the code looks like it could be simpler, and the reason it is not lives outside the
file. The three shapes that qualify here:

- Something verified against a real system that contradicts the obvious reading. "We tried
  the obvious thing and it silently did nothing" is the highest-value ADR there is.
- A constraint one half imposes on the other, or on a tool neither owns.
- An option that was considered and rejected, where the rejected one is what someone will
  propose next.

Anything an agent can work out from the diff is not an ADR. Neither is a preference.

Create the directory's first real entry when a decision actually gets made — not upfront,
and not to fill in a template.

## How

One file per decision, `NNNN-a-sentence-saying-what-was-decided.md`, numbered in order and
never renumbered. The title is the decision, in the present tense, as a claim: *"Row-level
security is on for every table, and forced"*, not *"RLS decision"*. Then the reasoning, a
**Considered options** section naming what was rejected and why, and a **Consequences**
section for what this costs and what it makes impossible.

**Cite nothing by section number.** Name the thing — the route, the function, the rule, the
invariant — and let the reader grep. A section number points into one revision of one
document, and an ADR outlives the document whose structure it borrowed.

An ADR is never quietly edited to match a change of mind. Write a new one that supersedes
it, or amend it with a dated note saying what changed and what forced it. The old reasoning
is the part with the value: it says what was believed at the time, which is exactly what
someone reopening the question needs.
