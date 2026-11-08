import { setupServer } from "msw/node";
import { handlers } from "@/mocks/handlers";
import { answersReads } from "@/mocks/reads";

export const server = setupServer(
  ...handlers,
  answersReads(() => server.listHandlers()),
);
