# Changelog

What changed between template versions, and what a project generated from an earlier one gets
when it runs `copier update`.

**Copier resolves to the newest git tag, not to `main`.** A change that is merged but untagged
reaches nobody. Each heading below is a tag.

The titles are the ones the commits carry, because each states the change as a claim. Follow a
number to the pull request for the reasoning.

## Unreleased

## v0.5.0 — 2026-08-27

- A repository a stranger can contribute to, and a gate that meets them (#30)
- A record a reasonable person would not undo is an explanation (#29)
- A gate that accuses the wrong file is worse than no gate (#28)
- A check verified only where it passes is a check nobody has seen work (#27)
- A check that recognises nothing finds nothing and says so (#26)
- A document nobody names is a decision made twice (#25)
- A generated project runs its gate after a push as well as before a commit (#24)
- A route cannot be committed without resolving a tenant (#23)
- The docs carry what the code cannot, and nothing else (#22)
- The gate said eleven and ran nine, and a render outlives the check (#21)
- Mock mode leaves nothing behind, including what no mode turns off (#20)
- The gate meets the artifact a deployment ships (#19)
- The one-origin entrypoint is the edge, and now carries what one carries (#18)

## v0.4.0 — 2026-08-24

- The checks live before the commit, and now nowhere else (#17)
- No test decides whether to run, and the suite is sorted by what it needs (#16)
- Somewhere for a fix that can only be made somewhere else (#15)

## v0.3.0 — 2026-08-21

- One process can carry both halves, and a refusal says what it was (#14)
- The e2e ports follow the environment too, and one run never borrows another's server (#13)
- Three more conformance rules, and somewhere for a stroke weight to point (#12)

## v0.2.2 — 2026-08-19

- Copier 9.17.1, in all eleven places, and a gate that keeps them there (#11)
- Both ratchets are two-sided, pulled from the repos that own them (#10)

## v0.2.1 — 2026-08-17

- The layer is one thing again: the comment gate is upstream and byte-identical (#9)
- Both ports come from the environment, so two checkouts can run at once (#8)
- The contract suite, against a backend that is really on Postgres (#7)

## v0.2.0 — 2026-08-17

- Say that a generated project can receive later template changes (#6)
- Nothing made a new table be a tenant table, so a later one would not be (#5)
- A real store seam, two substrates, and tenant isolation the database enforces (#4)
- Put the gate before the commit, and mechanise the rule that never held (#3)
- Stop `make dev` leaking the backend, and teach the checks to catch the rest (#2)

## v0.1.1 — 2026-08-16

- Stop `make dev` from stranding uvicorn, and gate the bug that did it (#1)

## v0.1.0 — 2026-08-16

The first template: a React frontend and a FastAPI backend that agree on a generated OpenAPI
contract, the mock and live modes, the decision records, and the three-way sync `copier update`
performs.
