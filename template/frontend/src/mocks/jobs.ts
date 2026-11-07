import type { Job } from "@/api/jobs";
import { http } from "@/mocks/http";

export interface JobStore {
  ended: (kind: string, outcome: Job["outcome"]) => Job;
  read: (id: string) => Job | null;
  reset: () => void;
}

export function createJobStore(): JobStore {
  let jobs = new Map<string, Job>();
  let nextId = 1;

  return {
    ended: (kind, outcome) => {
      const id = String(nextId);
      nextId += 1;
      jobs.set(id, { id, kind, state: "done", outcome, failure: null });
      return { id, kind, state: "pending", outcome: null, failure: null };
    },
    read: (id) => jobs.get(id) ?? null,
    reset: () => {
      jobs = new Map();
      nextId = 1;
    },
  };
}

export const jobStore = createJobStore();

export const jobHandlers = [
  http.get("/jobs/{id}", ({ params, response }) => {
    const job = jobStore.read(params.id);
    return job === null ? response(404).json({ detail: "Job not found" }) : response(200).json(job);
  }),
];
