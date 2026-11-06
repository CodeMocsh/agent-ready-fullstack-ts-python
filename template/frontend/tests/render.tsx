import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { onTestFinished } from "vitest";
import { AppProviders } from "@/app-providers";
import { CycleStopped, createQueryClient, followNavigations } from "@/query-client";

function failTheTestOnACycle(_kind: string, error: unknown): void {
  if (error instanceof CycleStopped) {
    queueMicrotask(() => {
      throw error;
    });
  }
}

export function renderWithQuery(ui: ReactNode) {
  const queryClient = createQueryClient(failTheTestOnACycle);
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory() });
  onTestFinished(followNavigations(router, queryClient));
  return render(
    <QueryClientProvider client={queryClient}>
      <AppProviders>{ui}</AppProviders>
    </QueryClientProvider>,
  );
}
