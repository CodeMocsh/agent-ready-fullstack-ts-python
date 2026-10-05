# Decisions

This is where an architectural decision lives, as the test below defines one. `AGENTS.md` bans
comments and sends rationale to the commit message, and most decisions need nothing more.
**A decision record is for a decision, not for an explanation.**

## What belongs here

**The test is the cost of change.** That is the line Grady Booch draws between architecture and
the rest of design: a decision is significant when reversing it would be expensive. Michael
Nygard, who introduced this format in 2011, names where the expense shows up — the structure, a
quality the system is held to, a dependency, an interface, or the way the thing is built. A
choice that touches none of those is an implementation detail, however hard it was to get right.

A decision is architectural, and earns a file, only if it does at least one of these:

- **It fixes structure** across modules, or across the two halves.
- **It fixes a quality the system is held to**: tenancy and security, availability, cost, or
  how the system is released and deployed.
- **It takes on a dependency or a contract** that would take a quarter to swap out.

These do not, however much thought they took:

- UI layout, copy, and the name of a screen, a field or a facet.
- How one component or one module behaves, or the shape inside it.
- A convention a gate already enforces. The gate is the record.
- A workaround for a bug in a tool.

Those go in the commit message, plus a docstring or a test where they constrain how something
may be used. Under the comment ban the reason for a choice always lives outside the file, so
"the reason is not in the code" is never enough on its own. **If unsure, it is a commit
message.**

## How

One file per decision, `NNNN-a-sentence-saying-what-was-decided.md`, numbered in order and
never renumbered. Yours go in this directory. `template/` holds the decisions that came with
the template and is numbered on its own, so a template update never lands a number beside one
of yours; cite one of those as `docs/adr/template/NNNN`. `docs/adr/template/0016` says why.

The title is the decision, in the present tense, as a claim — *"Row-level security is on for
every table, and forced"*, not *"RLS decision"*. Then the reasoning, a
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
