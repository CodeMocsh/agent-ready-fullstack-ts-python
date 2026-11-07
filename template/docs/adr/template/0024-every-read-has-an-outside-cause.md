# 0024. Every read has an outside cause

Date: 2026-11-06

## Status

Accepted.

## Context

A project generated from this template lost its production service for three minutes. One
browser tab ran a bundle that was four hours old. A run that had ended was read again from a
cached list. The run settled, the settle invalidated the list, and the stale list adopted the run
again. Each time, the read of the list started again, so the fresh list never arrived. The tab sent
about 1,200 reads a minute. No timer was involved, and no person acted. The tab read because of
its own cache.

[0019](0019-the-gate-counts-the-reads-of-each-screen-s-first-draw.md) and
[0020](0020-a-read-is-current-until-the-next-navigation-focus-or-reconnect.md) hold what correct
code reads on a first draw. Nothing held what code reads after the draw. The server answered
`429` and `503` in the incident, and TanStack Query retried each read three times.

A read that answers its own cache looks correct in the code that writes it. Each line is an
ordinary invalidation in an ordinary callback. Only the running cycle shows it.

## Decision

- **A read has an outside cause.** These are the outside causes, and there are no others:
  - a navigation: each `onBeforeLoad` of the router, which `router.invalidate()` also emits;
  - a focus: the tab regains focus;
  - a reconnect: the tab comes back online;
  - a person's input: a `pointerdown`, a `pointerup`, a `keydown`, a `click`, a `submit`, a
    `change` or a `drop` anywhere in the tab. A person who leaves a field presses a key or a
    pointer first, so a save on blur is the person's mutation. A `focusout` is not an input: a
    script moves the focus as often as a person does;
  - a person's mutation: a mutation that starts before any read has answered since the last of
    the causes above. It permits a read of the keys in its `meta.invalidates`, when it settles;
  - a server event: `invalidateForServerEvent(queryClient, queryKeys)` in `src/query-client.ts`,
    in a stream under `src/streams/`. It permits a read of the keys it names;
  - a backstop: a query's `refetchInterval`, at its spacing.
- **A navigation, a focus, a reconnect and a person's input each permit one read of every key.** A
  person's mutation and a server event permit one read of the keys they name, and of no other key.
  Each one counts: eight mutations from one click, or two server events in one tick, permit eight or
  two reads. A permit that comes before the key's next read since the last cause stands in for that
  read's own permit. One mutation or event permits, and starts, one read of each active query its
  keys match, however many of its keys match that query. `tests/query-client-causes.test.tsx` holds
  this. `followNavigations` hears the causes. The default `persister` that `createQueryClient` sets
  checks each read of a key against the record of its last read before the read starts. It waits one
  microtask first, so that each listener of a focus or a reconnect hears the cause before the read
  that the event starts. A retry of a read that never reached the server is the same read.
- **The record of a key outlives its query, and not the next cause.** `src/query-client.ts` keeps
  the last read of each key by its hash, and forgets every record at each outside cause. A key
  whose query was removed, or collected, reads again only with an outside cause.
- **A current read starts no read.** A read is current until the next navigation, focus or
  reconnect, as [0020](0020-a-read-is-current-until-the-next-navigation-focus-or-reconnect.md)
  says. The default `staleTime` is infinite while the read is current, so a part that mounts, or
  a query that turns `enabled`, over that data starts no read, and so no `CycleStopped`. A
  project's own `staleTime` replaces the default; one that combines with `readIsCurrent` keeps
  this.
- **A new key is a new read.** A key that was never read reads under the cause that is current.
  So a dependent query that turns `enabled` and a query whose parameters change each read once.
  The next or the previous page of an infinite query is a new read too: a fetch whose
  `fetchMeta` names `fetchMore` reads a page that was not read.
- **A mutation that starts after an answer is not a cause.** An effect that writes when its data
  arrives is the client that answers its own cache. Its mutation still runs, and the read that
  its settle starts fails with `CycleStopped`. A write awaited after another write is not a cause
  either, because the first one's settle waits for the reads it starts: send both in one
  `mutationFn`, or start both with no `await` between them.
- **A backstop permits a read at its spacing, and no sooner.** A key with no new cause reads again
  only when its shortest `refetchInterval` has passed since its last read. TanStack restarts the
  interval timer of each observer with listeners at each update of its query, so after a read with a
  cause the next poll also comes a whole spacing later. `tests/query-client-backstop.test.tsx` holds
  this on TanStack's own timer. A test of a poll runs TanStack's timer, with the real clock, or with
  `Date` and the timers faked and advanced together. A test that fakes `Date` alone, or sends a poll
  with `refetch()`, sends a read the app never sends.
- **A cycle is stopped, drawn and reported.** The read does not start. The query fails with
  `CycleStopped`, which names the key. The screen draws the error. The query cache reports it as
  a client event of kind `query` and error `CycleStopped`. Nothing retries it, also not a
  `retry` that a query sets itself. The next outside cause reads the key again.
- **A read that failed keeps its failure under the same cause.** A key whose last read failed,
  and that reads again with no new cause, fails again with that failure. It sends nothing, and no
  retry of it sends anything, also when the failure was `Unreached`. A part that mounts over it
  then draws the failure the read met.
- **A test fails on a cycle.** `readsOf` in `tests/first-draw.tsx` fails a draw on each client
  event, so a screen that cycles fails the read-budget test with `CycleStopped` and its key.
  `renderWithQuery` in `tests/render.tsx` builds its client with `createQueryClient`, with its
  retry, and throws a `CycleStopped` out of the test. `tests/query-client.test.tsx` holds each
  cause, the cycle, a new key, a page, the record of a removed key, the backstop, the failure and
  the retry.
- **Only a mutation and a server event invalidate.** A mutation names the keys it makes stale in
  `meta.invalidates`. The mutation cache invalidates them when the mutation settles. A stream
  under `src/streams/` calls `invalidateForServerEvent`. `pnpm conformance` refuses
  `invalidateQueries`, `refetchQueries` and `resetQueries` everywhere except
  `src/query-client.ts`, and `invalidateForServerEvent` outside `src/streams/`. In a `useEffect`
  or a `useLayoutEffect`, it refuses a `mutate` or a `mutateAsync`, unless the effect registers a
  listener with `addEventListener`, and a `.click()`, a `requestSubmit()` or a `dispatchEvent()`,
  which fake a person's input.
- **Only a read that never reached the server is retried.** `request` in `src/api/client.ts`
  fails with `Unreached` when `fetch` rejects. The default `retry` retries `Unreached` up to three
  times, and nothing else: not an `ApiError`, not an answer that `request` cannot read, and not a
  `CycleStopped`.
- **A wait the server asks for holds every request.** `request` reads `Retry-After` on each
  refusal, as a number of seconds or as an HTTP date in any of the three forms RFC 9110 names:
  IMF-fixdate, RFC 850 and asctime. `src/api/http-date.ts` refuses a date that names no moment,
  such as 32 January. After a `429` or a `503`, until the wait ends, `request` refuses every
  request to the API itself with `Held`, an `ApiError` that carries the server's status and no
  request id. It sends nothing. A later refusal with a shorter wait keeps the longer one. A
  `Retry-After` in any other form fails as an `ApiError` with the refusal's own sentence and
  status, and holds nothing. A client event is sent during the wait, because
  `POST /client-events` is not admitted.
- **`devtools/check_template.sh` holds the stop to its purpose.** It adds two cycles to
  `TaskList`: a read that starts again each time its own answer arrives, and a mutation that
  starts each time the answer arrives and invalidates it. It expects the read-budget test to
  fail with `CycleStopped` and the key for each.

## Considered options

- **A limit of reads per key per second.** Any number is a guess. A low one stops a busy screen,
  and a high one lets the incident through at that rate.
- **A backoff on a key that reads too often.** It lowers the rate, and the cycle goes on. The
  screen shows old data, and nothing says why.
- **A cause from every event in the tab.** A timer, a message from a worker and a fetch that
  answers are events too. Each one would let the incident through.
- **A server event or a mutation as a cause for every key.** A stream that sends many events, or
  an effect that writes when its data arrives, would then let a cycle on any key run at that
  rate. Each permits a read of the keys it names.
- **Check only `isTrusted` input.** jsdom and Testing Library dispatch events that are not
  trusted, so no test could act as a person. Script that dispatches input to start a read is not
  a cycle that a person meets.
- **A switch that makes a cycle throw in tests and stop in production.** The read-budget test
  would then measure another client than the one the app runs. Each test client fails on the
  client event instead.
- **A conformance rule for an invalidation outside a mutation's callbacks.** A callback is often a
  function defined beside the mutation and passed to it, so a text scan cannot tell where it
  runs. `meta.invalidates` moves every invalidation into one module, which a scan can check.
- **Retry a `5xx`.** In the incident each retry was another request to an overloaded server. A
  `503` names its wait in `Retry-After`, and `request` holds every request for that wait.
- **Hold only the method and path that the server refused.** A `429` bounds the actor and a
  `503` the process, so every other path the tab sends earns another refusal.
- **Keep the record of a read on its query.** A removed or collected query would then forget its
  last read, and a cycle that removes the query each time would run.

## Consequences

- An invalidation in project code moves to `meta.invalidates`, or to `invalidateForServerEvent`
  in a stream under `src/streams/`. A call elsewhere fails `pnpm conformance`.
- A cause the list does not name is a false `CycleStopped` on a working screen. A read that a
  hover starts, such as a router's preload on intent, is not caused. It reads a key once, and
  a second hover with no other cause stops. A mouse wheel is not a person's input, so an
  infinite list reads its pages only through `fetchNextPage`.
- A mutation that starts after a read answered is not a cause. That includes a write awaited
  after another write, a write in another write's callback, and a write in a route's loader
  after it reads. Send the writes in one `mutationFn`, or start them with no `await` between.
- Three paths still let a cycle through, and no check sees them. A `navigate()` or a
  `router.invalidate()` in an effect counts as a navigation. A function that a module under
  `src/streams/` exports, and that calls `invalidateForServerEvent`, can be called from a
  component. An effect that registers a listener and also writes outside the listener passes
  `pnpm conformance`. Review each of them as a read with no outside cause.
- A cycle that reads a new key each time, such as a key that holds the time, is not stopped.
- A person who types or clicks permits one read of each key for each input. A cycle while the
  person types runs at the rate of their input.
- A client that follows no router refuses each read, as it refuses to mount a query over data.
- The stop rides TanStack Query's `persister` option. A query that sets its own `persister`
  replaces the default, and nothing checks its reads. A version of TanStack Query that changes
  the option changes the stop, and `tests/query-client.test.tsx` fails on it.
- A tab that already runs a bundle from before this decision gets none of it. Only a reload
  brings it.
- A request held by `Retry-After` fails at once with `Held`. The screen draws the error until
  the wait ends and an outside cause reads again.
