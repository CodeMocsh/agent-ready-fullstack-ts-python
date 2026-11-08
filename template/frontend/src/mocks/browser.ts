import { setupWorker } from "msw/browser";
import { handlers } from "@/mocks/handlers";
import { answersReads } from "@/mocks/reads";

export const worker = setupWorker(
  ...handlers,
  answersReads(() => worker.listHandlers()),
);
