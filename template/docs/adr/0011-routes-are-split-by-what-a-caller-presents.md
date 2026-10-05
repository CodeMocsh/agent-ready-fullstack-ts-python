# Routes are split by what a caller presents, not by resource

`app/routes/` is a package with one module or package per *requirement*: `public.py` asks a
caller for nothing, and `tenant/` asks for a tenant. A resource is a module inside the
requirement it lives under — `tenant/tasks.py` — and declares a bare `APIRouter()`. The
package's `__init__.py` owns the requirement: its router carries the dependency, and every
module under it is included there.

## Why the requirement is the seam

It is the one property of a route that is expensive to get wrong. A route on the wrong side
answers somebody it should refuse, and it looks exactly like a route that works.

A single `routes.py` holds both sides in one file, and the line between them is which of two
router names a decorator happens to use. That file is also the first one every project grows
past. When it splits, it splits by resource, because resource is what a file listing shows —
and then each resource module needs both routers, and the line is back to a choice made per
decorator.

The sibling project this template was extracted alongside grew four requirements in one file:
none, any verified credential, membership of the organization in the path, and operator. Routes
meant for one requirement drifted apart from each other in that file, and nothing could pull
them back. Splitting by requirement made every module's name say which one it was.

## Why a bare `APIRouter()` in each module

A module that declares its own dependencies can be included somewhere else, and its routes leave
the seam with it. Declaring the requirement once, on the router in `__init__.py`, means a route
reaches it by being in the directory. `tests/routes/test_guarantee.py` reads the assembled app
and fails on any route that answers without a tenant, so a module included in the wrong place
fails the gate.

## Considered options

- **One `routes.py`.** It is what shipped, and every project replaced it. A template file every
  project rewrites is a merge conflict on every `copier update`.
- **A module per resource.** Rejected above: the requirement becomes a per-decorator choice
  again.
- **A `@public` marker on each route.** Rejected in [0008](0008-a-route-cannot-escape-the-identity-seam.md):
  a marker travels with the commit that made the mistake.

## Consequences

- A new requirement — a role, a membership, an operator — is a new module or package beside
  `public.py` and `tenant/`, with its own router. It is never a dependency added to one route.
- A resource that has routes under two requirements has two modules. That is the point.
- `main.py` includes one router per requirement and nothing else.
