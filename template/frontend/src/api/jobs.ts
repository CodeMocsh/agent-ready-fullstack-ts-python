import { request } from "@/api/client";
import type { components } from "@/api/schema";

export type Job = components["schemas"]["Job"];

export const jobsApi = {
  read: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}`),
};
