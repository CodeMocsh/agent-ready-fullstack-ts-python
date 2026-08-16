import type { CreateTaskBody, Task, UpdateTaskBody } from "@/api/types";

const SEED: readonly Task[] = [
  { id: "1", title: "Read AGENTS.md", done: true },
  { id: "2", title: "Run the app in mock mode", done: false },
  { id: "3", title: "Replace this demo with a real feature", done: false },
];

export interface TaskStore {
  list: () => Task[];
  create: (body: CreateTaskBody) => Task;
  update: (id: string, body: UpdateTaskBody) => Task | null;
  remove: (id: string) => boolean;
  reset: () => void;
}

export function createTaskStore(seed: readonly Task[] = SEED): TaskStore {
  let tasks: Task[] = [...seed];
  let nextId = seed.length + 1;

  return {
    list: () => [...tasks],
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
