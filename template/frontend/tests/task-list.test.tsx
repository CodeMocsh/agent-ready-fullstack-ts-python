import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { TaskList } from "@/components/task-list";
import { server } from "@/mocks/node";
import { renderWithQuery } from "./render.tsx";

describe("TaskList", () => {
  it("renders the tasks the API returns", async () => {
    renderWithQuery(<TaskList />);

    expect(await screen.findByText("Read AGENTS.md")).toBeInTheDocument();
    expect(screen.getByText("Run the app in mock mode")).toBeInTheDocument();
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

  it("surfaces a failing request", async () => {
    server.use(http.get("/api/tasks", () => new HttpResponse(null, { status: 500 })));
    renderWithQuery(<TaskList />);

    expect(await screen.findByText(/failed with 500/)).toBeInTheDocument();
  });
});
