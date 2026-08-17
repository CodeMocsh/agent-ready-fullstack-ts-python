import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { tasksApi } from "@/api/client";
import type { Task } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const TASKS_KEY = ["tasks"] as const;

const SERVED_BY =
  import.meta.env.VITE_ENABLE_MSW === "true"
    ? "Mock mode — Mock Service Worker is answering. No backend is running."
    : "Live mode — the backend is answering.";

export function TaskList() {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");

  const tasks = useQuery({ queryKey: TASKS_KEY, queryFn: tasksApi.list });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: TASKS_KEY });

  const create = useMutation({
    mutationFn: tasksApi.create,
    onSuccess: async () => {
      setTitle("");
      await invalidate();
    },
  });

  const toggle = useMutation({
    mutationFn: ({ id, done }: Pick<Task, "id" | "done">) => tasksApi.update(id, { done }),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: tasksApi.remove,
    onSuccess: invalidate,
  });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = title.trim();
    if (trimmed.length > 0) {
      create.mutate({ title: trimmed });
    }
  };

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-6 p-8">
      <header className="flex flex-col gap-1">
        <h1 className="font-heading text-2xl font-semibold">Tasks</h1>
        <p className="text-muted-foreground text-sm">{SERVED_BY}</p>
      </header>

      <form onSubmit={submit} className="flex gap-2">
        <Input
          aria-label="New task title"
          placeholder="What needs doing?"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
        <Button type="submit" disabled={create.isPending}>
          Add
        </Button>
      </form>

      {tasks.isPending ? <p className="text-muted-foreground text-sm">Loading…</p> : null}
      {tasks.isError ? <p className="text-destructive text-sm">{tasks.error.message}</p> : null}

      {tasks.data ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">Done</TableHead>
              <TableHead>Title</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {tasks.data.map((task) => (
              <TableRow key={task.id}>
                <TableCell>
                  <input
                    type="checkbox"
                    aria-label={`Mark "${task.title}" as done`}
                    checked={task.done}
                    onChange={(event) => toggle.mutate({ id: task.id, done: event.target.checked })}
                  />
                </TableCell>
                <TableCell className={task.done ? "text-muted-foreground line-through" : ""}>
                  {task.title}
                </TableCell>
                <TableCell>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Delete "${task.title}"`}
                    onClick={() => remove.mutate(task.id)}
                  >
                    <Trash2 />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </main>
  );
}
