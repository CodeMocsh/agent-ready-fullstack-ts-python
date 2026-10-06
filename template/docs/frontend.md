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
  src/client-events.ts reports a failure the browser saw to the backend's log
  tests/              mirrors src/
  e2e/                Playwright; *.live.spec.ts needs a running backend
  e2e/signed-in.ts    how a live spec signs in -- yours
  devtools/           the frontend's own gates, and the scanner they share
```

`src/mocks/` is the fixture layer every component test runs against. Keep it when the backend
feels real enough.

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
