# 0020. A read is current until the next navigation, focus or reconnect

Date: 2026-10-30

## Status

Accepted.

## Context

Without a stream, a read at mount or on a focus is the only way a tab learns of a write it did
not make. A `staleTime` of 1 s or 30 s made a project
generated from this template show its cached copy after another writer's change. 8 of its 20
browser specs failed. The screen did not show that it was late.

With a `staleTime` of 0, each query that mounts after its key's first answer reads again. A query
that mounts before the answer shares the request that is in flight. How many mount late depends
on load. In that project, one screen sent the same read up to 8 times in one draw. Its read
budgets ([0019](0019-the-gate-counts-the-reads-of-each-screen-s-first-draw.md)) failed under load
and passed when the machine was idle.

## Decision

- **A read is current from its answer until the next navigation, focus or reconnect, unless this
  tab invalidates it.** `readIsCurrent` in `src/query-client.ts` holds the rule. A query that
  mounts reads again only when its read is not current: `createQueryClient` sets this as the
  default `refetchOnMount`. Data whose read is current is fresh: the default `staleTime` is
  infinite while the read is current and 0 after, so a query that turns `enabled` over it starts no
  read either. A second `useQuery` call of a key that the draw already read shares that read.
- **Each navigation, focus and reconnect starts a period, and a period notes answers, not time.**
  `followNavigations(router, client)` starts a period when it is called, when a navigation starts
  while no load is open, when the tab regains focus, and when the tab comes back online. Each answer
  in the period, a write through `setQueryData` included, notes its query. A read is current when
  its query is noted in the period. An answer that arrives after the event is current, and one that
  arrived before it is not, whatever the clock says. A load opens at `onBeforeLoad`. A load closes
  at `onResolved`. The router's `status` at an event is not the same in each version of
  `@tanstack/router-core`. Version 1.171.15 stops emitting `onBeforeNavigate` after a redirect.
  `followNavigations` reads neither the `status` nor `onBeforeNavigate`. A redirect starts before
  the load that throws it closes, and so does a navigation that replaces one that loads. Neither
  starts a period, so a read before it stays current for the screen it ends on.
  `router.invalidate()` counts as a navigation. `src/main.tsx` calls `followNavigations` once.
  `readsOf` in `tests/first-draw.tsx` calls it for each draw, so the count measures what the app
  does. The function it returns stops the following and forgets the period.
- **A client that follows no router refuses to build a query.** The default `staleTime` and
  `refetchOnMount` throw, and name `followNavigations`. A client that already follows a router
  refuses a second one.
- **A project's own `staleTime` replaces the default.** A query that may stay fresh for longer
  combines its value with `readIsCurrent`, so that a current read stays fresh.
- **The rest is unchanged.** A focus reads again every mounted query.
  A mutation invalidates the keys it made stale. A `refetchInterval` is still a backstop.
  [0024](0024-every-read-has-an-outside-cause.md) says what may start a read.
- **A read is current from its answer.** A read that starts before a navigation and ends after
  it is current. A write through `setQueryData` is an answer too.
- `tests/query-client.test.tsx` and `tests/query-client-causes.test.tsx` hold each rule but the one
  for a navigation that replaces one that loads. It renders the router, because some versions of
  the router emit `onResolved` only when it renders.

## Considered options

- **A fixed `staleTime` above 0.** It hides another writer's change for that time, on every
  screen.
- **Compare the time of the data with the time of the event.** `Date.now()` has a resolution of
  1 ms. A focus and its own answer, or a navigation and a redirect's read, can fall in one
  millisecond, and then the order is lost.
- **Give each child the data of the screen's query.** It is the right shape for a new screen.
  In a project with many screens, each duplicate is found only after it causes a burst.
- **`refetchOnMount: false`.** A screen that a navigation draws again then shows its old copy.
- **Record only navigations, not a focus or a reconnect.** A part that closed before a focus,
  and opens after it, then shows data older than the focus.

## Consequences

- A part that opens without a navigation can show data as old as the last navigation, focus or
  reconnect. A screen that must show another writer's change sooner needs a stream.
- A project that creates its own `QueryClient` does not get this default, and its read budgets
  measure a different client from the one the app uses.
- A query option `refetchOnMount` on one query replaces the default for that query.
- `followNavigations` needs a router that a `RouterProvider` renders. Without one, some versions of
  the router never close the first load, and no navigation after it starts a period.
- A `useQuery` call that changes its key reads again in the same draw. One that turns `enabled`
  over data whose read is current does not.
- `fetchQuery`, `ensureQueryData` and `prefetchQuery` answer from the cache while its read is
  current. A read after a person's input invalidates the key, or calls `refetch()`.
- `renderWithQuery` in `tests/render.tsx` builds its client with `createQueryClient`, and
  follows a router that it never renders. No navigation starts a period there.
- A route that preloads its data reads it again when the person navigates there, because the
  preload's answer arrived before that navigation.
