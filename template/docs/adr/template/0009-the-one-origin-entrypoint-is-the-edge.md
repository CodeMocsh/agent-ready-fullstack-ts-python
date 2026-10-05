# 0009. The one-origin entrypoint is the edge, and what it refuses is not the contract

Date: 2026-10-05

## Status

Accepted.

## Context

A reverse proxy does more than route. It caps request bodies. It sets the response headers that
decide what an injected string can become in a browser. It terminates TLS. A service behind a
proxy gets all of this from the proxy, and declares none of it.

`app.serve` puts both halves on one origin, for a deployment with nothing in front of it. In
that deployment, nothing else does the work of a proxy.

The obvious design sets the security headers in `create_app()`, so that every topology gets them.
That design fails in the most common topology. A deployment behind nginx, an ALB, Cloud Run or
an identity-aware proxy already has an edge that sets these headers for that deployment. When two
`Content-Security-Policy` headers disagree, a browser enforces their intersection. The
application's policy then narrows what the operator's edge allows, and no configuration shows
why.

The obvious contract declares every status a route can return, as
[0008](0008-the-spec-describes-what-the-service-actually-does.md) requires. The edge's refusals
are not route behaviour. Behind a proxy, the proxy sends the `413`, and the proxy's limit
applies. A route that declares the `413` describes a deployment, and that description is false
for every deployment that does not run `app.serve`.

## Decision

- `app.serve` is the edge. `_confined` in `app/serve.py` adds `SECURITY_HEADERS` to every
  response that leaves the process, a `500` included. It keeps a header that a route already
  set. `tests/serve/test_serve.py::test_a_server_error_still_carries_the_headers` holds this.
- `app.main` sets no security header and caps no body.
  `tests/serve/test_serve.py::test_the_service_on_its_own_sets_none_of_them` holds this.
- `_refuse_oversized` in `app/serve.py` reads `content-length` and refuses before it reads the
  body. It answers `413` above `MAX_BODY_BYTES` and `400` for a length that is not a number. It
  answers `411` for a request that can carry a body and declares no length.
- `400`, `411` and `413` are not in `openapi.json`. `create_server` passes `openapi_url=None`,
  because the wrapper is a deployment and not an API.
- The policy is the constant `SECURITY_HEADERS`, not configuration. A change to it is a code
  change with a diff.
- `script-src 'self'` carries the policy. It holds only while the build emits no inline script.
  `frontend/index.html.jinja` loads one external module script. Something that needs an inline
  script gets a nonce or a hash, never a wider directive.
- `style-src` allows `'unsafe-inline'`. Radix positions every `shadcn` overlay with inline
  `style` attributes. A strict `style-src` misplaces each overlay and reports no error, so only a
  person who looks at the page finds it. Inline CSS cannot run script, so the concession is
  narrow.
- `strict-transport-security` names no subdomains and asks for no preload. `includeSubDomains`
  is a promise about hosts the template has never seen. It takes a plaintext sibling on the same
  apex domain offline. `preload` is the same promise, and it is almost impossible to reverse. A
  deployment adds both when it owns every name under the domain.
  `tests/serve/test_serve.py::test_the_transport_policy_does_not_claim_subdomains` holds this.
- `cross-origin-opener-policy: same-origin` is on, because this process serves HTML to a
  session. It removes `window.opener`, so a popup OAuth flow completes and cannot return its
  result. Redirect flows are not affected, and they are the flows to use. A project that needs
  the popup removes this header and loses the cross-window isolation.

## Considered options

- **Headers in `app.main`, so that every topology gets them.** Two policies intersect, and the
  deployment behind a proxy is the common one.
- **Declare `400`, `411` and `413` on every route.** The declaration is false for every
  deployment that does not run `app.serve`, and it puts topology into the contract.
- **A separate `app/edge.py`.** The headers, the cap and the bundle fallback are one idea: what a
  proxy does. A separate module has one caller. A second entrypoint that needs the same
  behaviour is the reason to split.
- **Count bytes instead of reading `content-length`.** A counted body is a body the process has
  already received. The request worth refusing is the one that costs something to read.

## Consequences

- A project behind a proxy runs `app.main` and gets none of this. That is correct, and it looks
  like a missing feature.
- `openapi.json` does not name every status a caller of `app.serve` can see.
  `frontend/src/api/client.ts` throws the `detail` sentence of a refusal that the generated types
  do not name. It does the same for a proxy's `413`, which no spec names either.
- `MAX_BODY_BYTES` applies to every route. An endpoint that takes an upload streams and bounds
  itself. It does not raise the cap for every other route.
- Nothing checks the built `index.html` for an inline script. A build plugin that adds one
  breaks the page under `app.serve` only.
