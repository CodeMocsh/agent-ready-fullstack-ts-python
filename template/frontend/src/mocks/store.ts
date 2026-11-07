import type { CreateTaskBody, Task, TaskPage, UpdateTaskBody } from "@/api/tasks";

export const PAGE_MAX = 100;

const SEED: readonly Task[] = [
  { id: "1", title: "Read AGENTS.md", done: true },
  { id: "2", title: "Run the app in mock mode", done: false },
  { id: "3", title: "Replace this demo with a real feature", done: false },
];

export interface TaskStore {
  list: (after: string | null) => TaskPage | null;
  create: (body: CreateTaskBody) => Task;
  update: (id: string, body: UpdateTaskBody) => Task | null;
  remove: (id: string) => boolean;
  reset: () => void;
}

export function createTaskStore(seed: readonly Task[] = SEED): TaskStore {
  let tasks: Task[] = [...seed];
  let nextId = seed.length + 1;

  return {
    list: (after) => {
      const newest = [...tasks].reverse();
      const named = after === null ? -1 : newest.findIndex((task) => task.id === after);
      if (after !== null && named === -1) {
        return null;
      }
      const page = newest.slice(named + 1, named + 1 + PAGE_MAX);
      const last = page.at(-1);
      const more = named + 1 + PAGE_MAX < newest.length;
      return { tasks: page, next: more && last !== undefined ? last.id : null };
    },
    create: ({ title }) => {
      const task: Task = { id: String(nextId), title, done: false };
      nextId += 1;
      tasks = [...tasks, task];
      return task;
    },
    update: (id, { done }) => {
      const existing = tasks.find((task) => task.id === id);
      if (existing === undefined) {
        return null;
      }
      const updated: Task = { ...existing, done };
      tasks = tasks.map((task) => (task.id === id ? updated : task));
      return updated;
    },
    remove: (id) => {
      const before = tasks.length;
      tasks = tasks.filter((task) => task.id !== id);
      return tasks.length < before;
    },
    reset: () => {
      tasks = [...seed];
      nextId = seed.length + 1;
    },
  };
}

export const taskStore = createTaskStore();
