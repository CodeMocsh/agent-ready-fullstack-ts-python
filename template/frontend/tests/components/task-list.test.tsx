import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { TaskList } from "@/components/task-list";
import { server } from "@/mocks/node";
import { PAGE_MAX, taskStore } from "@/mocks/store";
import { heldUntilTheTestEnds } from "../held.ts";
import { renderWithQuery } from "../render.tsx";

describe("TaskList", () => {
  it("renders the tasks the API returns", async () => {
    renderWithQuery(<TaskList />);

    expect(await screen.findByText("Read AGENTS.md")).toBeInTheDocument();
    expect(screen.getByText("Run the app in mock mode")).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });

  it("says which half is answering", async () => {
    renderWithQuery(<TaskList />);

    expect(await screen.findByText(/Mock mode/)).toBeInTheDocument();
    expect(screen.queryByText(/Live mode/)).not.toBeInTheDocument();
  });

  it("adds a task and shows it in the table", async () => {
    const user = userEvent.setup();
    renderWithQuery(<TaskList />);
    await screen.findByText("Read AGENTS.md");

    await user.type(screen.getByLabelText("New task title"), "Ship the template");
    await user.click(screen.getByRole("button", { name: "Add" }));

    expect(await screen.findByText("Ship the template")).toBeInTheDocument();
  });

  it("removes a task", async () => {
    const user = userEvent.setup();
    renderWithQuery(<TaskList />);
    await screen.findByText("Read AGENTS.md");

    await user.click(screen.getByRole("button", { name: 'Delete "Read AGENTS.md"' }));

    await waitFor(() => {
      expect(screen.queryByText("Read AGENTS.md")).not.toBeInTheDocument();
    });
  });

  it("marks a task done", async () => {
    const user = userEvent.setup();
    renderWithQuery(<TaskList />);
    const row = (await screen.findByText("Run the app in mock mode")).closest("tr");
    expect(row).not.toBeNull();

    await user.click(within(row as HTMLElement).getByRole("checkbox"));

    await waitFor(() => {
      expect(within(row as HTMLElement).getByRole("checkbox")).toBeChecked();
    });
  });

  it("shows loading and cancels its unanswered request on unmount", async () => {
    let received: Request | undefined;
    server.use(
      http.get("/api/tasks", ({ request }) => {
        received = request;
        return heldUntilTheTestEnds();
      }),
    );
    const view = renderWithQuery(<TaskList />);

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    await waitFor(() => expect(received).toBeDefined());
    view.unmount();
    expect(received?.signal.aborted).toBe(true);
  });

  it("surfaces a failing request", async () => {
    server.use(http.get("/api/tasks", () => new HttpResponse(null, { status: 500 })));
    renderWithQuery(<TaskList />);

    expect(await screen.findByText(/failed with 500/)).toBeInTheDocument();
  });
});

describe("TaskList pages", () => {
  it("reads the next page when the person asks for more", async () => {
    const user = userEvent.setup();
    for (let n = 1; n <= PAGE_MAX; n++) {
      taskStore.create({ title: `Task ${n}` });
    }
    renderWithQuery(<TaskList />);
    expect(await screen.findByText(`Task ${PAGE_MAX}`)).toBeInTheDocument();
    expect(screen.queryByText("Read AGENTS.md")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Read AGENTS.md")).toBeInTheDocument();
    expect(screen.getByText("Task 1")).toBeInTheDocument();
  });

  it("offers no more after the last page", async () => {
    const user = userEvent.setup();
    for (let n = 1; n <= PAGE_MAX; n++) {
      taskStore.create({ title: `Task ${n}` });
    }
    renderWithQuery(<TaskList />);
    await user.click(await screen.findByRole("button", { name: "Load more" }));

    await screen.findByText("Read AGENTS.md");
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("offers no more when every task is on the first page", async () => {
    renderWithQuery(<TaskList />);

    expect(await screen.findByText("Read AGENTS.md")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("reads from the first page again when the page it asked for starts after a removed task", async () => {
    const user = userEvent.setup();
    const oldest = taskStore.create({ title: "Task 1" });
    for (let n = 2; n <= PAGE_MAX; n++) {
      taskStore.create({ title: `Task ${n}` });
    }
    renderWithQuery(<TaskList />);
    await screen.findByText(`Task ${PAGE_MAX}`);
    taskStore.remove(oldest.id);

    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Task not found")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Replace this demo with a real feature")).toBeInTheDocument();
    expect(screen.queryByText("Task not found")).not.toBeInTheDocument();
  });
});
