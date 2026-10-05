# Decisions

**An ADR records an architectural decision: one a principal architect would want to review,
because it is expensive to reverse. It fixes the system's structure, a quality the system is held
to, or a dependency or contract the system is bound to.** Everything else is a commit message.

## What belongs here

A decision earns a file only if it does at least one of these:

- It fixes structure across modules, or across the two halves.
- It fixes a quality the system is held to: tenancy and security, availability, cost, or how
  the system is released and deployed.
- It takes on a dependency or a contract that would take a quarter to swap.

Naming, copy, one module's internals and anything a gate already enforces do not. Their reason
goes in the commit message, and a constraint on how something may be used goes in a docstring or
a test. Under the comment ban a reason always lives outside the code, so that alone never earns a
record. **If unsure, it is a commit message.**

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
