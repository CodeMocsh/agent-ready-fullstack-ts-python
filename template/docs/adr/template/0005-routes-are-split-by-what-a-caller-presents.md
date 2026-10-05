# 0005. Routes are split by what a caller presents, not by resource

Date: 2026-10-05

## Status

Accepted.

## Context

The requirement is the one property of a route that is expensive to get wrong. A route under the
wrong requirement answers a caller that it must refuse. It looks the same as a route that works.

The obvious design is one `routes.py`. In that file, the line between two requirements is which
router name a decorator uses. It is also the first file that a project grows past. A project
splits it by resource, because a file listing shows resources. Each resource module then needs
every router, and the requirement is again a choice on each decorator.

A project grows more than two requirements: none, any verified credential, membership of the
organization in the path, and operator. When they share one file, the routes for one requirement
move apart, and nothing brings them together again. When each requirement is a module, the
module name states the requirement.

A module that declares its own dependencies can be included in a different place. Its routes
then leave the identity seam with it.

## Decision

- `app/routes/` holds one module or package per requirement. `app/routes/public.py` requires
  nothing. `app/routes/tenant/` requires a tenant.
- A resource is a module inside the requirement that it is under, for example
  `app/routes/tenant/tasks.py`. It declares a bare `APIRouter()`.
- The package's `__init__.py` owns the requirement. In `app/routes/tenant/__init__.py` the router
  carries `Depends(resolved_tenant)` and includes every module under the package. A route gets
  the requirement because it is in the directory.
- `create_app` in `app/main.py` includes one router per requirement and nothing else.
- `tests/routes/test_guarantee.py` reads the routes off the assembled app. It fails on a route
  that answers without a tenant and is not in `PUBLIC_ROUTES`. A module included in the wrong
  place fails the gate.

## Considered options

- **One `routes.py`.** Every project replaces it. A template file that every project rewrites is
  a merge conflict on every `copier update`.
- **A module per resource.** The requirement becomes a choice on each decorator again.
- **A `@public` marker on each route.** [0004](0004-a-route-cannot-escape-the-identity-seam.md)
  rejects it: a marker travels with the commit that makes the mistake.

## Consequences

- A new requirement, such as a role, a membership or an operator, is a new module or package
  beside `public.py` and `tenant/`, with its own router. It is never a dependency added to one
  route.
- A resource with routes under two requirements has two modules. This is intended: each module
  name states one requirement.
- A route cannot change its requirement in its own decorator. To change it, move the route to a
  different module.
