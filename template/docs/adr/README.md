# Decisions

This is where a design decision lives. `AGENTS.md` bans comments and sends rationale to two
places: the commit message for why a change was made, and this directory for why the design is
the way it is — the part that will still matter when the commit is not the thing anyone finds.

## What belongs here

**The test is the cost of change.** That is the line Grady Booch draws between architecture and
the rest of design: a decision is significant when reversing it would be expensive. Michael
Nygard, who introduced this format in 2011, names where the expense shows up — the structure, a
quality the system is held to, a dependency, an interface, or the way the thing is built. A
choice that touches none of those is an implementation detail, however hard it was to get right.

In practice a decision earns a file when **a reasonable person would undo it** — when the code
looks like it could be simpler, and the reason it is not lives outside the file. Typically:

- Something verified against a real system that contradicts the obvious reading. "We tried the
  obvious thing and it silently did nothing" is the highest-value entry there is.
- A constraint one half imposes on the other, or on a tool neither owns.
- An option that was considered and rejected, where the rejected one is what someone will
  propose next.

Anything an agent can work out from the diff does not belong here. Neither does a preference.

## How

One file per decision, `NNNN-a-sentence-saying-what-was-decided.md`, numbered in order and
never renumbered. The title is the decision, in the present tense, as a claim — *"Row-level
security is on for every table, and forced"*, not *"RLS decision"*. Then the reasoning, a
**Considered options** section naming what was rejected and why, and a **Consequences** section
for what this costs and what it makes impossible.

**Cite nothing by section number, and count nothing that lives elsewhere.** Name the thing —
the route, the function, the rule, the invariant — and let the reader grep. A section number
points into one revision of one document, and a decision outlives the document whose structure
it borrowed. A count of how many rules or files or questions exist somewhere else is wrong the
first time that number changes, and nothing fails when it does.

A decision is never quietly edited to match a change of mind. Write a new one that supersedes
it, or amend it with a dated note saying what changed and what forced it. The old reasoning is
the part with the value: it says what was believed at the time, which is exactly what someone
reopening the question needs.
