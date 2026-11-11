# Frontend

Everything `pnpm` touches. [AGENTS.md](../AGENTS.md) holds the rules nothing checks; this is the
detail behind them and behind the rules the gates enforce.

## Layout

```
frontend/
  src/api/            the contract: base.ts, client.ts, types.ts, schema.ts and parts.ts (generated)
  src/mocks/          the second implementation of that contract -- permanent, not a scaffold
  src/components/     UI; ui/ is vendored shadcn
  src/index.css       the @theme block
  src/router.tsx      routes
  src/app-providers.tsx the providers around the router -- yours
  src/query-client.ts the query client
  src/client-events.ts reports a failure the browser saw to the backend's log
  src/missing-chunk.ts reloads the tab once when a deploy removed a chunk it asks for
  src/build.ts        reloads the tab at the next navigation once a new build is deployed
  tests/              mirrors src/
  tests/read-budgets.ts the reads each screen's first draw may send -- yours
  tests/signed-in.ts  how the read-budget test signs in -- yours
  e2e/                Playwright; *.live.spec.ts needs a running backend
  e2e/signed-in.ts    how a live spec signs in -- yours
  devtools/           the frontend's own gates, and the scanner they share
```

`src/mocks/` is the fixture layer every component test runs against. Keep it when the backend
feels real enough.

Test files in one worker share their modules and one jsdom window. `vitest.config.ts` sets
`isolate: false`. Without it, each file loads again the modules that `tests/setup.ts` imports.
When a worker runs many files, that load is most of the CPU the tests use. `tests/setup.ts`
itself still runs once for each file. A registration it makes on a shared module therefore goes
in `beforeAll` and comes off after the file's last test, or it accumulates once for each file.
After each file, `tests/setup.ts` puts back the shared state that tests commonly change. A file
that changes other shared state puts it back after its last test.

## Reading server state

The rules are in [AGENTS.md](../AGENTS.md). This is how the project meets them today.

- **A read stays current until the next navigation, focus or reconnect.** A `useQuery` call that
  mounts, or turns `enabled`, reads again only when its data arrived before the last navigation,
  focus or reconnect, or when this tab invalidated it. The default `staleTime` says the same:
  infinite while the read is current. `followNavigations` notes each of these events, by answers
  rather than by time. `src/main.tsx` calls it once. A navigation that starts while another one
  loads, such as a redirect, starts no new period. `router.invalidate()` counts as a navigation. A
  tab that regains focus reads again every mounted query. Without a stream, these reads are the only
  way a tab learns of a write it did not make. Another person, another tab and a job outside a
  request make such writes. With a fixed `staleTime`, the screen shows their change late. The screen
  does not show that it is late. A section that opens without a navigation can show data as old as
  the last navigation, focus or reconnect. `tests/query-client.test.tsx` holds this.
  [adr/template/0020](adr/template/0020-a-read-is-current-until-the-next-navigation-focus-or-reconnect.md)
  says why.
- **A second `useQuery` call of a key shares the read only when it mounts.** A call that turns
  `enabled`, or changes its key, reads again. Give a child the data of the screen's query.
- **A screen's first draw is one request.** `request` in `src/api/client.ts` holds each GET to a
  part, and sends the ones that start in one task as one `GET /reads`. Each call gets the answer
  it would get alone. A GET route is a part when it declares `openapi_extra=PART`, and
  `make openapi` writes the parts to `src/api/parts.ts`. A read that needs another read's answer
  is a second request, and its budget names that reason in `because`. A read that needs only the
  route's parameters starts in the route's `loader`, in the same task as the others. A section the
  person opens later mounts its own query when it opens. `tests/first-draw.test.tsx` draws each
  screen and expects its requests to equal `tests/read-budgets.ts`, then counts again on a focus.
  [adr/template/0029](adr/template/0029-the-reads-that-start-in-one-task-are-one-request.md)
  and
  [adr/template/0019](adr/template/0019-the-gate-counts-the-reads-of-each-screen-s-first-draw.md)
  say why.
- **A list is read a page at a time.** `TaskList` reads the first page, newest first, in its
  first draw. It reads the page that `next` names when the person clicks "Load more". After a
  read of a page fails, "Load more" reads every page again from the first. A focus, and a
  mutation of the key, read again each page the screen holds, one after another. The read budget
  counts only the first draw, so it does not count these. A list read never asks for every row.
  `src/mocks/store.ts` pages as the backend does, and its `PAGE_MAX` is the `maxItems` of
  `TaskPage.tasks` in `openapi.json`. [adr/template/0026](adr/template/0026-every-store-read-is-a-page.md) says why.
- **A burst costs more than its requests.** Each query that a draw or a focus starts reaches the
  backend at the same moment. An instance serves the ones it takes in turn.
  [deployment.md](deployment.md#a-burst-of-requests) says what that costs on a host that adds
  instances.
- **A read has an outside cause.** `src/query-client.ts` checks each read against the outside
  causes. A navigation, a focus, a reconnect and a person's input each permit one read of each
  key. A person's mutation and a server event permit one read of the keys they name. A key that
  reads again with no cause fails with `CycleStopped`, which names the key. The screen draws it, a
  client event reports it, and nothing retries it. `TaskList` names its key in each mutation's
  `meta.invalidates`, and the mutation cache invalidates it. A stream under `src/streams/` calls
  `invalidateForServerEvent`. `pnpm conformance` refuses an invalidation anywhere else, and a
  mutation that an effect starts.
  [adr/template/0024](adr/template/0024-every-read-has-an-outside-cause.md) says why.
- **Server events merge.** `invalidateForServerEvent` marks matching queries stale. It waits for
  an active read before it starts a trailing read. Events during the trailing read remain pending.
  Inactive queries stay stale until they mount. Completion and reconnect events use the same function.
  An ordinary read canceled before its answer may resume once under the same cause.
  A second cancellation needs a new cause. Mutation grants do not gain a spare continuation.
  Read completion alone permits no read. The returned promise settles when pending active reads settle.
  Handle its rejection at the stream boundary. The query cache also reports query failures.
- **Event reads share a budget.** Pass each query function's `signal` to every API read it starts.
  `tasksApi.list(after, signal)` shows the contract. The transport keeps signaled reads in groups.
  Each event read request, retry, page, and fallback uses the client's budget.
  Duplicate paths may consume extra budget before the transport merges them.
  The default policy lives in `src/api/read-budget.ts`. Pass a policy as the second argument to
  `createQueryClient` when deployment measurements require different limits.
  The interval also sets the minimum time between event refreshes of one query.
  Navigation, focus, reconnect and input release reads already waiting for event admission.
  Those reads consume that outside cause. Later events keep the event budget.
  Reads without an event cause do not use the event budget.
  Calls that omit the signal do not use it either. Test actual admitted parts across the whole screen.
  The budget belongs to one query client. It cannot reserve capacity across tabs or actors.
  Keep capacity for other traffic in the server admission limit.
  A canceled refresh rejects with `EventReadCanceled`. Stopping the client rejects with
  `EventRefreshStopped`. A stream may handle these lifecycle reasons without reporting a failure.
  Other errors, including transport aborts, remain failures.
  An admitted event read can resume once after cancellation. A wait canceled before admission
  keeps its allowance. Canceling an admitted continuation needs a new cause.
  Removing the query discards its pending refresh.
  Stop `followNavigations` and clear the old query client when a session ends.
  [adr/template/0030](adr/template/0030-server-events-merge-and-share-a-read-budget.md) states the contract.
- **A failure shows its `said`, and nothing else.** A `Failure` holds a `cause` and the sentence
  the backend declares for it. Pick a mark or a tone by `cause`. Never show an error's own text.
  [adr/template/0031](adr/template/0031-a-stored-failure-reaches-a-person-as-a-declared-cause.md).
- **A refusal that names a wait holds every request.** `request` in `src/api/client.ts` reads
  `Retry-After` and refuses every request to the API itself with `Held` until the wait ends. A
  client event still goes out. Only a read that never reached the server is retried.
- **This project has no stream yet.** A mutation invalidates the keys it made stale, in the tab
  that wrote it. Another tab sees the change when it mounts the screen or regains focus. A screen that
  must show another writer's change sooner needs a stream. A `refetchInterval` under 30 s is not a
  substitute.
- **A backstop is a timed read again.** It catches what a stream did not say. It is never the
  way a screen learns of a change.

## Vendored and generated code — do not tidy

- `src/components/ui/**`, `src/lib/utils.ts` and `public/mockServiceWorker.js` were written by a
  tool. You may change what they *do*; do not reformat them, strip their comments, or restyle
  them to match house rules.
- `src/api/schema.ts` is regenerate-only, like `openapi.json`. A hand edit is a bug.
- `pnpm dlx shadcn add` does not put everything in `src/components/ui`. A block, a chart, a hook
  or a lib file lands in ordinary scanned source and fails `make lint` in shadcn's idiom.
  [AGENTS.md](../AGENTS.md) says what to do.

## Conventions

- **The theme is the only place a colour, a type size or a spacing step is defined.** A hex
  literal, a palette step like `bg-blue-500`, a `text-[13px]`, a `p-[7px]` or an opacity
  modifier like `bg-primary/80` fails `pnpm conformance`. Add a token to `src/index.css`.
  A font size, family, line height or letter spacing is a token too: `pnpm conformance` refuses
  one in a `style={{}}` attribute or a raw declaration in a stylesheet.
- **Icon stroke comes from `--icon-stroke`**, not from the call site.
- **A `useEffect` does not fetch.** Server state comes from TanStack Query hooks against
  `src/api/client.ts`. Effects are for synchronising with something outside React.
  `pnpm conformance` refuses a `fetch`, an `await` or a `.then` written in an effect.
- **A component file is named for what it exports**, in kebab-case: `task-list.tsx` exports
  `TaskList`. The file name is how anything finds a component without reading it.
- **Export a function, not an arrow.** `export function load()`, never
  `export const load = () => …`. A declaration names itself; an assignment does not.
- **A file holds no more non-blank lines than `complexity.maxFileLines`**, JSX included, and
  `pnpm file-length` refuses one past it. Split by what the parts require of a caller. A file
  that must stay whole goes in `complexity.overCap` by name. Biome's own file-length rule cannot
  see JSX and is not the gate.
- Use the `@/` alias for anything under `src/`. TypeScript is `strict`, plus
  `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`.
- **The worker registration, `src/api/base.ts` and the router basepath all derive from
  `import.meta.env.BASE_URL`**, so the app works under any path prefix.
- **There is no `.env.production`.** Production mode loads no env file, so mock mode is off
  because nothing turns it on. `public/` is copied into every build whatever the mode, so the
  `strip-mock-worker` plugin in `vite.config.ts` covers what no mode can; without it a production
  deploy ships `mockServiceWorker.js` to its own origin.
- **A tab runs the deployed build.** The `name-the-build` plugin in `vite.config.ts` writes
  `build.json` into each build. `src/build.ts` reads it on a focus and when a navigation to
  another path starts, and reloads the tab at the next such navigation when it names another
  build. The development loop does not read it. Do not reload in the middle of what the person
  does: a navigation is the safe moment.
  [adr/template/0025](adr/template/0025-a-tab-learns-of-a-new-build-and-reloads-at-the-next-navigation.md)
  says why.
- **Record a failure through the function `startClientEvents` returns**, never through
  `console`, which biome refuses in `src/`. A client event names a kind, a route template and an
  error class, and never carries a message: an error's message is where a user's input ends up.
- The agent guard blocks writes to any `.env*` file. If one needs to change, say so and let a
  human edit it.
