"""The routes, one module or package per requirement: what a caller must present.

`public` requires nothing. `tenant` requires a tenant. A new requirement is a new module or
package here with its own router, never a dependency added to one route. `docs/adr/template/0005`.
"""
