import { HttpResponse } from "msw";

const holding = new Set<() => void>();

export function heldUntilTheTestEnds(): Promise<Response> {
  return new Promise((release) => {
    holding.add(() => release(HttpResponse.error()));
  });
}

export function releaseEveryHeldRequest(): void {
  for (const release of holding) {
    release();
  }
  holding.clear();
}
