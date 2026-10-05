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

One file per decision: `NNNN-a-sentence-saying-what-was-decided.md`. The template's own records
go in this directory. The records a generated project gets live in `template/docs/adr/`, under the
same rules.

Every record has the same parts:

- `# NNNN. The decision as a claim`, in the present tense. *"Copier over a bespoke CLI"*, not
  *"generator decision"*.
- A `Date:` line.
- `## Status`: accepted or not.
- `## Context`: the forces, and why the obvious design does not work here.
- `## Decision`: the rules. Each rule names the code that holds it: a function, a file, a test.
- `## Considered options`: only the options somebody will propose again, and why not.
- `## Consequences`: what the decision costs, and what it makes impossible.

**Write in Simplified Technical English.** Put one idea in each sentence. Keep most sentences to 20
words or fewer. Use the active voice and the present tense. Use the words in `CONTEXT.md`, and use
one word for one meaning. Do not use idioms or metaphors.

**A record states the decision as it is now.** When the decision changes, change the record. Git
holds the earlier text. Do not add dated amendments, and do not tell the history of the decision.

**Numbers are a reading order.** Records are numbered from 0001 with no gaps, in the order a
newcomer should read them. When records merge, go away or move, renumber them, and update every
citation in the tree in the same change. A citation in git history, an old commit or a transcript
may then point at a different record. That is accepted: the tree is the source of truth. If two
branches add or move records at once, the later merge renumbers.

**Cite nothing by section number, and count nothing that lives elsewhere.** Name the thing: the
script, the target, the rule or the invariant. A section number points into one revision of one
document. A count of things that live somewhere else is wrong the first time that number changes.
