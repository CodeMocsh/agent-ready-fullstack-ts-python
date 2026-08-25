# The one-origin entrypoint is the edge, and what it refuses is not the contract

`app.serve` sets response security headers and caps request bodies. `app.main` does neither,
and must not start. The refusals the edge issues — `400`, `411`, `413` — are deliberately
absent from `openapi.json`.

Three decisions, one reason: **a reverse proxy is not only a router.** It caps bodies, sets
the headers that decide what an injected string may become, and terminates TLS. A service
deployed behind one inherits all of that and should declare none of it. `app.serve` exists for
the deployment that has no such thing in front of it, so it is the only place those belong.

## Why `app.main` must not set them too

The obvious simplification is to move the headers into `create_app()` so every topology gets
them. It is wrong in the topology that matters most.

A deployment behind nginx, an ALB, Cloud Run or an identity-aware proxy already has an edge
setting these, tuned to that deployment. A second copy from the application is at best
redundant and at worst weaker — and when two `Content-Security-Policy` headers disagree, a
browser enforces **the intersection**, so the stricter-looking application header silently
narrows what the operator's edge allowed, in a way nothing in either config explains. One
policy, set by whatever is actually at the edge, is the only arrangement that stays legible.

## Why the refusals are not in the contract

`AGENTS.md` requires every status code a route can return to be declared, and `openapi.json`
is the contract both halves are generated from. The edge's refusals break that rule on
purpose.

They are not route behaviour. `app.main` cannot emit them: run it behind a proxy and the
`413` comes from nginx, not from Python, and nginx's limit is the one that applies. Declaring
them on the routes would put a deployment's configuration into a document that describes the
API, and it would be false for every deployment that is not `app.serve` — which is most of
them. `create_server` passes `openapi_url=None` for the same reason: the wrapper is a
deployment, not an API.

The consequence is accepted rather than hidden: `src/api/client.ts` will throw the `detail`
sentence from a refusal the generated types never mentioned. That is the same thing it would
do with a proxy's `413`, which no spec mentions either.

## The policy's two concessions

**`style-src` allows `'unsafe-inline'`.** Radix — which every `shadcn` overlay is built on —
positions popovers and dialogs with inline `style` attributes. A strict `style-src` breaks
each one added after generation, and breaks it as *misplacement* rather than as an error, so
it is found by eye or not at all. Inline CSS cannot execute, so the concession is narrow.
`script-src 'self'` carries the weight instead, and it holds only while the build emits no
inline script: verified against a real vite build, whose `index.html` is one external module
script and one stylesheet. Something that needs an inline script gets a nonce or a hash, never
a widened directive.

**`strict-transport-security` names no subdomains.** `includeSubDomains` from a template is a
promise about hosts the deployment has never seen, and it would take a plaintext sibling on
the same apex offline from a header nobody remembers setting. `preload` is the same mistake
and is close to irreversible. Both are correct to add once you own every name under the domain
— which is a fact about a deployment, not about this code.

`cross-origin-opener-policy: same-origin` is included though it was not asked for, because
this process serves HTML with a session on it. It severs `window.opener`, so a popup-based
OAuth flow completes, closes, and never hands its result back. Redirect flows are unaffected
and are the ones to prefer; a project that needs the popup drops this line and loses the
cross-window isolation with it.

## Why the body cap reads framing rather than the method

A body may ride any method. Keying the requirement on `POST`/`PUT`/`PATCH` let a chunked
`DELETE` of any size through — an actual defect, not a hypothetical one. What makes a body
unmeasurable is the absence of a length, so that is what is checked: a `transfer-encoding`
with no `content-length` is refused whatever the verb, and so is any request on a transport
that does not make framing headers compulsory. Under HTTP/1.1 a request with neither header
has no body at all, which is the only reason the remaining method list is safe.

## Considered options

- **Headers in `app.main`, so every topology gets them.** Rejected above: two policies
  intersect, and the proxied deployment is the common one.
- **Declare `400`/`411`/`413` on every route.** Rejected: false for every deployment that is
  not `app.serve`, and it would put topology into the contract artifact.
- **A separate `app/edge.py`.** Rejected for now. The headers, the cap and the bundle
  fallback are one idea — *what a proxy would have done* — and splitting them yields a module
  with exactly one caller. Revisit if a second entrypoint ever needs the same behaviour.
- **Counting bytes instead of reading `content-length`.** Rejected: a body counted is a body
  already received, and the request worth refusing is the one it would have cost something to
  read.

## Consequences

- A generated project that deploys behind a proxy runs `app.main` and gets none of this, which
  is correct and is also easy to mistake for a missing feature.
- `openapi.json` does not describe every status a caller of `app.serve` can see. A client
  generated from it must still handle a refusal it was not told about — which was already true
  of any deployment with a proxy in front.
- Raising `MAX_BODY_BYTES` for one uploading endpoint raises it for every route. An endpoint
  that streams is the reason to bound that endpoint itself instead.
- The policy is a named constant, not configuration. Changing it is a code edit and a diff,
  which is the point.
