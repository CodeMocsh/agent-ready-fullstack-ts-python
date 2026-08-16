import { createRootRoute, createRoute, createRouter, Outlet } from "@tanstack/react-router";
import { TaskList } from "@/components/task-list";

const rootRoute = createRootRoute({ component: Outlet });

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: TaskList,
});

export const router = createRouter({
  routeTree: rootRoute.addChildren([indexRoute]),
  basepath: import.meta.env.BASE_URL,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
