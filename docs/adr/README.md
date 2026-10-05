# Decisions

This is where an architectural decision about **the template itself** lives — the generator, the shape
of what it renders, and the way this repo is checked. Decisions about what a *generated project*
does live in `template/docs/adr/`, and ship to every project made from it.

## What belongs here

**An ADR records an architectural decision: one a principal architect would want to review,
because it is expensive to reverse. It fixes the shape of the generator or of what it renders,
how the template is checked, released or updated, or a dependency or contract the template is
bound to.** Everything else is a commit message. Detail an agent can work out from the diff goes
in [../constraints.md](../constraints.md).

A decision earns a file only if it does at least one of these:

- It fixes the shape of the generator, or of what every generated project gets.
- It fixes how the template is checked, released or updated.
- It takes on a dependency or a contract that would take a quarter to swap.

Naming, wording, one script's behaviour and anything a gate already enforces do not. **If
unsure, it is a commit message.**

## How

One file per decision, `NNNN-a-sentence-saying-what-was-decided.md`, numbered in order and
never renumbered. The title is the decision, in the present tense, as a claim — *"Copier over a
bespoke CLI"*, not *"generator decision"*.

The entries here carry **Status**, **Context**, **Decision** and **Consequences**, with the
rejected options argued inside Context. `template/docs/adr/` states the rejected options under
their own heading instead. Either shape is fine and neither is worth a migration; what both owe
the reader is the option that was turned down and the price of the one that was not.

**Cite nothing by section number, and count nothing that lives elsewhere.** Name the thing — the
script, the target, the rule, the invariant — and let the reader grep. A section number points
into one revision of one document, and a decision outlives the document whose structure it
borrowed. A count of how many questions or checks or files exist somewhere else is wrong the
first time that number changes, and nothing fails when it does.

A decision is never quietly edited to match a change of mind. Write a new one that supersedes
it, or amend it with a dated note saying what changed and what forced it. The old reasoning is
the part with the value: it says what was believed at the time, which is exactly what someone
reopening the question needs.
