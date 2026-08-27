# Decisions

This is where a design decision about **the template itself** lives — the generator, the shape
of what it renders, and the way this repo is checked. Decisions about what a *generated project*
does live in `template/docs/adr/`, and ship to every project made from it.

## What belongs here

**The test is the cost of change.** That is the line Grady Booch draws between architecture and
the rest of design: a decision is significant when reversing it would be expensive. Michael
Nygard, who introduced this format in 2011, names where the expense shows up — the structure, a
quality the system is held to, a dependency, an interface, or the way the thing is built. A
choice that touches none of those is an implementation detail, however hard it was to get right.

In practice a decision earns a file when **a reasonable person would undo it** — when the setup
looks like it could be simpler, and the reason it is not lives outside the file. The
highest-value entry is something verified against a real system that contradicts the obvious
reading: we tried the obvious thing and it silently did nothing. A rejected option belongs here
too, especially the one someone will propose next.

If you could choose differently next week and nothing outside that file would notice, the
reasoning goes in the commit message. Detail an agent can work out from the diff goes in
[../constraints.md](../constraints.md) instead.

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
