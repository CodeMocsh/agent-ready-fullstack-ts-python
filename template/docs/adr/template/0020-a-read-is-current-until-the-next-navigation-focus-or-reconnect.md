# 0020. A read is current until the next navigation, focus or reconnect

Date: 2026-10-30

## Status

Accepted.

## Context

`src/query-client.ts` keeps a `staleTime` of 0. Without a stream, a read at mount or on a focus is
the only way a tab learns of a write it did not make. A `staleTime` of 1 s or 30 s made a project
generated from this template show its cached copy after another writer's change. 8 of its 20
browser specs failed. The screen did not show that it was late.

With a `staleTime` of 0, each query that mounts after its key's first answer reads again. A query
that mounts before the answer shares the request that is in flight. How many mount late depends
on load. In that project, one screen sent the same read up to 8 times in one draw. Its read
budgets ([0019](0019-the-gate-counts-the-reads-of-each-screen-s-first-draw.md)) failed under load
and passed when the machine was idle.

## Decision

- **A query that mounts reads again only when its data arrived before the last navigation, focus
  or reconnect, or when this tab invalidated it.** `createQueryClient` sets this as the default
  `refetchOnMount`. A second `useQuery` call of a key that the draw already read shares that read
  when it mounts.
- **`followNavigations(router, client)` records the time.** It records it when it is called, when a
  navigation starts while no load is open, when the tab regains focus, and when the tab comes back
  online. A load opens at `onBeforeLoad`. A load closes at `onResolved`. The router's `status` at an
  event is not the same in each version of `@tanstack/router-core`. Version 1.171.15 stops emitting
  `onBeforeNavigate` after a redirect. `followNavigations` reads neither the `status` nor
  `onBeforeNavigate`. A redirect starts before the load that throws it closes, and so does a
  navigation that replaces one that loads. Neither records the time, so a read before it stays
  current for the screen it ends on. `router.invalidate()` counts as a navigation. `src/main.tsx`
  calls `followNavigations` once. `readsOf` in `tests/first-draw.tsx` calls it for each draw, so the
  count measures what the app does. The function it returns stops the following and forgets the
  time.
- **A client that follows no router refuses to mount a query over data.** The default
  `refetchOnMount` throws, and names `followNavigations`. A client that already follows a router
  refuses a second one.
- **The rest is unchanged.** The `staleTime` stays 0. A focus reads again every mounted query.
  A write invalidates the keys it made stale. A `refetchInterval` is still a backstop.
- **The time of a read is when its answer arrives.** A read that starts before a navigation and
  ends after it is current. A read in the same millisecond as the navigation is current too:
  `Date.now()` has a resolution of 1 ms, and a mocked answer can arrive in that millisecond.
- `tests/query-client.test.tsx` holds each rule but the one for a navigation that replaces one that
  loads. It renders the router, because some versions of
  the router emit `onResolved` only when it renders.

## Considered options

- **A `staleTime` above 0.** It hides another writer's change for that time, on every screen.
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
  the router never close the first load, and no navigation after it records the time.
- A `useQuery` call that turns `enabled`, or changes its key, reads again in the same draw.
- `renderWithQuery` in `tests/render.tsx` creates its own `QueryClient`, so a component test
  runs TanStack's default.
- A route that preloads its data reads it again when the person navigates there, because the
  preload's answer arrived before that navigation.
