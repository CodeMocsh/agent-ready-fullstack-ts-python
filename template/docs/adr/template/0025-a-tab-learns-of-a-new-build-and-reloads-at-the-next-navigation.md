# 0025. A tab learns of a new build and reloads at the next navigation

Date: 2026-11-06

## Status

Accepted.

## Context

A tab runs the code it loaded until it loads again. A person can keep one tab open for days.
In a project generated from this template, one tab ran a build that was 4 hours old. Its old code
read the same list again and again, about 1,200 times a minute, until the backend stopped. The
deployed build had a fix for that read. The tab did not load it.

Before this decision, only a missing chunk made a tab load again: `src/missing-chunk.ts` reloads
once when a deploy removed a chunk that the tab asks for. A deploy that removes no chunk the tab
asks for leaves the tab on its old build.

The obvious design puts the build in each response of the API, so the tab compares it with its
own. That design fails when the two halves deploy apart. The backend does not know which
frontend build is live. A static host, a CDN or another process can serve the bundle. Under
`app.serve` the two are the same, and in every other topology they are not.

## Decision

- **The build identity is the file name of the bundle's entry chunk.** Its name carries a hash
  that Vite computes over the code of the bundle and the styles it loads. So a change to any code
  or style changes the identity, and two builds of the same tree share it. The identity holds no
  time. `devtools/check_template.sh` changes one style token, builds again, and fails when the
  identity stays the same.
- **`build.json` names the deployed build.** The `name-the-build` plugin in `vite.config.ts`
  writes it beside `index.html`, as `{"entry": "assets/index-<hash>.js"}`. It refuses a bundle
  that does not have exactly one entry chunk. `devtools/check_template.sh` builds the bundle and
  fails when `build.json` does not name the script that `index.html` loads.
- **The tab knows its own build from `import.meta.url` in `src/main.tsx`**, which is the URL of
  the entry chunk. Nothing writes the identity into the bundle, because a hash of the bundle
  cannot be inside the bundle.
- **The tab reads `build.json` on a focus and when a navigation to another path starts.** `followTheDeployedBuild`
  in `src/build.ts` does this. Both are outside causes: the person did something. The read is
  not a read of the API, so it is outside each read budget. It asks with `cache: "no-cache"`.
- **A new build reloads the tab at the next navigation to another path.** A navigation is a
  moment when the person leaves what they were doing. The router has already written the new URL,
  so the reload loads the screen the person asked for. A navigation that keeps the path, such as
  `router.invalidate()` or a change of search parameters while the person types in a filter, does
  not reload.
- **A deploy that goes back to the running build cancels the reload.** A tab that learns that
  `build.json` names its own build again forgets the new build it learned of.
- **A tab reloads into a build once.** When it reloads, the session storage records the build
  the tab reloads into. A tab that starts and runs another build does not reload into that build
  again. A host that still serves the old `index.html` after a reload therefore cannot make the
  tab reload for ever. A tab that starts and runs the build it reloaded into goes on to follow
  each later deploy, so a deploy that goes forward, back and forward again reloads it each time.
  `src/missing-chunk.ts` records the build it reloads from on a missing chunk in the same way.
- **A build not loaded is reported once.** A tab that reloaded into a build and still runs
  another one reports `BuildNotLoaded`, a client event of the kind `build`: the host served an
  old `index.html`. Its error class is its own, so the report is not lost among the events of a
  `build.json` that does not answer.
- **A `build.json` that does not answer, or names no entry, is a client event of the kind
  `build`.** The tab does not reload. A status of 400 or more, or a body that is not JSON,
  is such a failure.
- **The development loop reads no `build.json`.** Vite's dev server serves no bundle, and it
  replaces each module that changes. `src/main.tsx` does not start `followTheDeployedBuild` when
  `import.meta.env.DEV` is true.
- **`build.json` is asked for again on each load.** `app.serve` gives it `no-cache`, as it gives
  every file outside `assets/`. `tests/serve/test_serve.py` holds this. Another host must do the
  same.
- `tests/build.test.tsx` holds each rule of `followTheDeployedBuild`. Nothing tests that
  `src/main.tsx` starts it only outside the development loop.

## Considered options

- **The build in each response of the API, as a header.** The backend does not know the frontend
  build when the halves deploy apart. Under `app.serve` it would know, and the template would then
  have one mechanism for one topology.
- **A hash of every file in the bundle, written into the bundle.** The hash would have to be in
  the content it hashes. A build would have to replace a placeholder after the hash, and the
  names of the files would then not match their content.
- **A time or a commit in `build.json`.** Two builds of the same tree would differ, and each
  rebuild would reload every tab. A commit also needs git at build time.
- **Reload as soon as the tab learns of a new build.** It discards what the person was typing.
- **Read `build.json` on a timer.** A timer is a cause that the tab makes for itself. A focus and
  a navigation already come from the person, and a tab that nobody uses does not need new code.
- **Check `index.html` in place of a file of its own.** The tab would have to parse HTML to find
  the entry, and a host that rewrites the HTML changes the answer.

## Consequences

- A tab that is already old does not have this code. It runs its old build until it loads
  again. Only the backend's own bounds protect against such a tab.
- A tab that nobody navigates in keeps its old build. It reads `build.json` on each focus, and
  reloads at its first navigation after that.
- A screen with no navigation inside it, such as a single-page tool, reloads only through a
  missing chunk or by hand.
- A static host that caches `build.json` hides each new build from each tab, and nothing in the
  tab can tell. `docs/deployment.md` says what the host must set.
- A failed read of `build.json` on each focus is one client event for each route, because
  `startClientEvents` sends each kind, error, route and status once. `BuildUnknown` carries no
  status, so a `404` and a `503` from the host send the same event.
- A deploy under a path prefix keeps `build.json` under the same prefix, because the tab reads
  it relative to `import.meta.env.BASE_URL`.
