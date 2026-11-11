# 0019. The gate counts the reads of each screen's first draw

Date: 2026-10-30

## Status

Accepted.

## Context

`AGENTS.md` says that a screen's first draw is one request. A screen that breaks the rule still
works on a laptop. On a host that adds instances, the reads of one draw reach the backend in the
same moment. A tab that regains focus sends them again.
[deployment.md](../../deployment.md#a-burst-of-requests) says what that costs.

In a project generated from this template, bursts of this kind started new instances, and a
request that waited for one waited the whole cold start. Each screen was fixed after its
incident, and nothing stopped the next screen.

The reads of a draw are not visible in the source. A child mounts after its parent's response
and then starts its own query. A lazy route loads its component first. A `beforeLoad` reads
before anything renders. Only a draw shows all of them.

## Decision

- **Each screen has a read budget.** `tests/read-budgets.ts` gives each screen the path it
  visits and the GET requests to the API that its first draw sends. A budget above 1 names
  `because`, and the type of `ReadBudget` asks for it. A screen is a route
  with no children. The project owns the file, and an update does not touch it. A screen that
  only redirects names in `lands` the screen its visit draws. The budget then counts that whole
  draw. A screen behind sign-in never lands on the sign-in screen: `signedIn` signs it in.
- **The budgets name every screen and nothing else.** `tests/first-draw.test.tsx` fails when the
  paths in `tests/read-budgets.ts` differ from the screens that `src/router.tsx` serves. A new
  screen fails the gate until it has a budget.
- **A draw is counted in vitest, against `src/mocks/`.** `readsOf` in `tests/first-draw.tsx`
  builds a new router at the path, with the options of `src/router.tsx`, and a new query client
  from `src/query-client.ts`. The new query client replaces one in the router's context. It
  counts from before the router loads, so a `beforeLoad` read counts. Each check first flushes
  React in `act`. The draw has settled when the router is idle, no query fetches, and no request
  is open, for 150 ms after such a flush. A child that mounts after a response therefore counts,
  also after a render that React slices.
- **The count is exact, and one URL is read once.** The first draw fails when it sends more requests
  or fewer than its budget. The count is the same on each run
  ([0020](0020-a-read-is-current-until-the-next-navigation-focus-or-reconnect.md)), so a change that
  removes a read changes its budget too. It fails when it reads one URL, its query included, in two
  requests. `readsOf` lists the parts of each `GET /reads`.
  [0029](0029-the-reads-that-start-in-one-task-are-one-request.md) sends the reads that start in one
  task as one request.
- **A draw holds one row a page.** `readsOf` calls `pageAtMost(1)` from `src/mocks/store.ts`
  before it draws. A screen that reads the next page by itself then reads one page for each row,
  and its count passes its budget, whatever the seed holds. At `PAGE_MAX` rows a page, the seed
  fits one page, and the same screen passes. Each mock of a list reads `pageRows()`.
  `tests/api/pages.test.ts` asks each list in `PAGES` of `src/api/parts.ts` at one row a page, and
  fails on a mock that answers more.
- **A draw has the providers the app has.** `src/main.tsx` and `readsOf` both put the router
  inside `AppProviders` from `src/app-providers.tsx`, inside the query client's provider. The
  project owns that file. A provider that the project adds there is in each draw too.
- **`signedIn` wraps each draw.** `signedIn` in `tests/signed-in.ts` wraps each draw, and
  gets the path of the screen. The project owns the file. A screen such as a sign-in screen is
  drawn signed out. A project that signs in stores a credential before the draw, and removes it
  in a `finally`. `tests/setup.ts` clears the browser storage only after each file, so a draw
  that throws would otherwise leave the credential for the next draw.
- **A focus reads within the same budget.** After the first draw, `readsOf` reports a focus to
  TanStack Query and counts again. The focus may send fewer requests than the budget.
- **A draw that fails is a failure, not a count.** An error in a route, in a render or in a
  query fails the test with that error. A request that no handler in `src/mocks/` answers, or
  that a handler answers with a status of 400 or more, fails it with the method and the path. A
  visit that draws the not-found screen fails it too.
- **A stream is not a read.** `streams` in `tests/read-budgets.ts` lists patterns of the paths
  that hold a stream open. `readsOf` does not count those requests, and does not wait for them.
  A stream's handler in `src/mocks/` answers with a body that stays open. A handler that never
  answers fails `tests/setup.ts`, which waits for each request a test starts.
- **`devtools/check_template.sh` holds the count to its purpose.** It adds a second query to
  `TaskList` that waits for the first answer, and expects the test to fail on the count. 0029
  names its other mutations.

## Considered options

- **A Playwright spec that visits each route.** It counts in a real browser. The gate runs no
  browser, so the spec would run nowhere.
- **A conformance rule that allows a fetching query only in a route or in `src/api/`.** It
  controls where a query is written, not how many requests a draw sends. A hook in `src/api/`
  that a child calls passes the rule and sends the same request.
- **A component that reads the cache and never fetches.** With no data in the cache it waits
  for ever, and shows nothing. Give a child the data of the screen's query instead.

## Consequences

- A new screen needs an entry in `tests/read-budgets.ts`, and a screen with parameters needs
  data in `src/mocks/` for the path it visits.
- A screen must draw in jsdom. A browser API that jsdom does not have needs a stand-in in the
  setup of the tests.
- A budget above one is visible in one file, with its reason. A reviewer sees it.
- A read that is not a GET does not count. A screen that reads with a POST is outside the budget.
- A screen is a route with no children. A parent route that draws at its own path, with no index
  route, has no budget.
- A child that waits more than 150 ms before it reads, for example behind `React.lazy`, does not
  count.
- The count does not see a section the person opens later. Such a section reads its own data, and
  the rule allows that.
