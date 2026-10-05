# A refusal is a class, declared once

Every way a route answers no is a class in `app/errors.py` that carries its own status and
sentence. A route raises the class, and names it in `responses(...)` on its decorator, which
builds the OpenAPI declaration from the same class. Refusals that share a status are one
declaration whose description names each. `tests/errors/test_errors.py` reads the
source and fails when a route raises a refusal it does not declare, when a refusal is declared
and no route can raise it, and when anything outside `app/errors.py` builds an
`HTTPException` itself.

## Why

The obvious shape writes a refusal twice: `raise HTTPException(404, "Task not found")` where it
happens, and a hand-kept `{404: {...}}` dictionary on the decorator. Nothing compares the two.
In the sibling project this template was extracted alongside, raises far outnumbered the
dictionaries. Some refusals were never declared, and some declarations described
responses no route could produce. Both survived for months.

The declaration is the half that reaches the other side. `openapi.json` is generated from it,
`schema.ts` from that, and the mock handlers are typed from `schema.ts`. A status that is raised
and not declared is a status the mocks cannot return, so the contract suite passes against a
service that answers differently.

The tests read the source rather than drive the app because a raise on a branch no test reaches
is still wrong, and that branch is the one that fails in production.

## Considered options

- **Hand-kept dictionaries, checked by a test.** The test would compare two spellings of one
  fact. Generating one from the other leaves nothing to compare.
- **Driving every route to find its refusals.** It finds only the branches a test can reach.
- **Declaring `401` the same way.** Rejected in
  [0008](0008-a-route-cannot-escape-the-identity-seam.md): the shipped seam never raises it.

## Consequences

- A refusal's text is part of the contract. Changing a `description` changes `openapi.json`.
- `raise NoSuchTask` is the whole of a refusal at the raise site. A refusal that needs to say
  more in its body passes a detail; one that needs a different status is a different class.
- A refusal raised outside the contract — `NoSuchAsset`, from `app/serve.py` — is named in
  `OUTSIDE_THE_CONTRACT` in the test, as a reviewable line.
