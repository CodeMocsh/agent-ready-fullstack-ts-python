# 0030. Server events merge and share a read budget

Date: 2026-11-08

## Status

Accepted.

## Context

An event names state that changed. It does not require a separate copy of that state.
Repeated invalidation can cancel a query without canceling its transport.
The browser then sends reads whose answers the query cache no longer uses.
Grouping reads from one browser task does not merge events from later tasks.
One active read per query still permits excessive traffic when reads finish quickly.
One event can also change many query keys.

## Decision

`src/event-refresh.ts` keeps one pending change and one shared completion promise per query.
It waits for an active read before it starts another read.
A new observer can consume a pending event cause while respecting the same spacing policy.
An event after the next read starts leaves a pending change for a trailing read.
An event during the trailing read does the same.
Overlapping keys match each query once.
An inactive query stays stale and holds one event permit for its next read.
A next-page read does not consume the event cause for a full refresh.
If pagination cancels a mount refresh with no pending drain, the query stays stale.
The next full read consumes its remaining permit.
Removing a query discards its pending work.

`src/query-client.ts` spends one event permit when a read starts.
It does not accumulate permits for repeated events.
Completing a read grants no permit.
A failed refresh rejects the completion promise when no newer event remains.
A newer event can cause another read after a failure. The query cache reports the failure.
The existing bounded retry policy still applies to a request that never reached the server.

`src/api/read-budget.ts` holds a separate budget for each query client.
Its policy limits event read grants over a sliding interval and spaces refreshes of each query.
Unused capacity does not accumulate at interval boundaries.
An independent outside cause releases reads already waiting for event admission. Navigation,
focus, reconnect and input must not wait behind old event traffic. Those queued reads now have
that outside cause. This does not add budget for later events, or start a read by itself.
`createQueryClient` accepts a policy as its second argument.
The default policy leaves capacity below the reported platform incident rate for other work.
It is a starting policy, not proof of sufficient capacity for a deployment.

Each query function forwards its `AbortSignal` to each API read.
The transport charges requests before grouping. Each retry, page, and fallback also uses the budget.
Duplicate paths can consume extra budget before grouping merges them.
Reads without an event cause and calls without the signal remain outside this event budget.
The server still enforces actor admission across clients and tabs.

`src/api/client.ts` keeps signaled reads eligible for grouping.
Canceling one consumer rejects that consumer and preserves the other consumers.
Canceling every consumer aborts shared transport.
Consumers remain attached until their response bodies finish.
Event groups from different query clients stay separate.
Cancellation does not become `Unreached` and does not trigger a network retry.
Explicit cancellation without a newer cause rejects the refresh promise, even with old cached data.
A replacement read can complete the same pending refresh. A newer event remains a cause.
Its event cause can resume once after an admitted read is canceled. Canceling that admitted
continuation needs a new cause. A wait canceled before budget admission retains its allowance;
it has sent no read. Continuation identity distinguishes that retained cause from a newer event.
Removing the query discards its pending refresh instead of reporting cancellation.
Stopping `followNavigations` aborts pending event work and its scoped transport.
It restores active event queries to idle without reporting cancellation as a query failure.
Session teardown also clears the old query client.

Streams own event mapping. Completion and reconnect supply explicit causes through the same API.
Streams handle the returned promise's rejection.
`tests/event-refresh.test.ts` checks final state, concurrency, failures, and aggregate event reads.
`tests/api/read-cancellation.test.ts` checks shared cancellation and fallback accounting.

## Considered options

- `cancelRefetch: false` alone loses changes that occur during a read.
- A timer without a pending change can lose final completion or create reads without a cause.
- One active read per query does not limit fast reads or changes across many keys.
- Counting outer HTTP requests hides the admission cost of grouped parts.
- A client budget cannot enforce a shared actor limit across tabs and processes.

## Consequences

Event refresh can wait behind other event reads.
Large lists and screens with many affected keys take longer to refresh.
Projects must forward query signals and measure all admitted traffic under their server limits.
Local tests prove the mechanism. Platform integration tests must prove event mapping and deployment capacity.
