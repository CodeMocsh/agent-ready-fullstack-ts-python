import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { startClientEvents } from "@/client-events";
import { router } from "@/router";
import "@/index.css";

async function enableMocking(): Promise<void> {
  if (import.meta.env.VITE_ENABLE_MSW !== "true") {
    return;
  }
  const { worker } = await import("@/mocks/browser");
  await worker.start({
    onUnhandledRequest: "warn",
    serviceWorker: { url: `${import.meta.env.BASE_URL}mockServiceWorker.js` },
  });
}

const rootElement = document.getElementById("root");
if (rootElement === null) {
  throw new Error("index.html is missing the #root element");
}

const record = startClientEvents();
window.addEventListener("error", (event) => record("uncaught", event.error));
window.addEventListener("unhandledrejection", (event) => record("uncaught", event.reason));

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: (error) => record("query", error) }),
  mutationCache: new MutationCache({ onError: (error) => record("mutation", error) }),
});

await enableMocking();

createRoot(rootElement, { onCaughtError: (error) => record("caught", error) }).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
