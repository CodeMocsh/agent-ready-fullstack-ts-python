# Frontend

Everything `pnpm` touches. [AGENTS.md](../AGENTS.md) holds the rules nothing checks; this is the
detail behind them and behind the rules the gates enforce.

## Layout

```
frontend/
  src/api/            the contract: base.ts, client.ts, types.ts, schema.ts (generated)
  src/mocks/          the second implementation of that contract -- permanent, not a scaffold
  src/components/     UI; ui/ is vendored shadcn
  src/index.css       the @theme block
  src/router.tsx      routes
  src/app-providers.tsx the providers around the router -- yours
  src/query-client.ts the query client
  src/client-events.ts reports a failure the browser saw to the backend's log
  src/missing-chunk.ts reloads the tab once when a deploy removed a chunk it asks for
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

- **Data is stale as soon as it arrives.** `src/query-client.ts` keeps TanStack's default
  `staleTime` of `0`. A screen that mounts reads again what it shows. A tab that regains focus
  reads again every mounted query. Without a stream, that read is the only way a tab learns of a
  write it did not make. Another person, another tab and a job outside a request make such
  writes. With a longer `staleTime`, the screen shows their change late. The screen does not
  show that it is late.
  `tests/query-client.test.ts` holds this.
  Because `staleTime` is 0, a child that mounts after the response and calls `useQuery` with the
  same key reads again. Give the child the data of the screen's query.
- **A screen's first draw is one query, and so one request.** Its response holds everything the
  screen draws first. A part the person opens later mounts its own query when it opens.
  `TaskList` in `src/components/task-list.tsx` draws from one query.
  `tests/first-draw.test.tsx` draws each screen and counts its reads against
  `tests/read-budgets.ts`, then counts again on a focus.
  [adr/template/0019](adr/template/0019-the-gate-counts-the-reads-of-each-screen-s-first-draw.md)
  says why.
- **A burst costs more than its requests.** Each query that a draw or a focus starts reaches the
  backend at the same moment. An instance serves the ones it takes in turn.
  [deployment.md](deployment.md#a-burst-of-requests) says what that costs on a host that adds
  instances.
- **This project has no stream yet.** A write invalidates the keys it made stale, in the tab that
  wrote it. Another tab sees the change when it mounts the screen or regains focus. A screen that
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
- **Record a failure through the function `startClientEvents` returns**, never through
  `console`, which biome refuses in `src/`. A client event names a kind, a route template and an
  error class, and never carries a message: an error's message is where a user's input ends up.
- The agent guard blocks writes to any `.env*` file. If one needs to change, say so and let a
  human edit it.
