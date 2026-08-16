from app.models import CreateTaskBody, Task, UpdateTaskBody

SEED = (
    ("1", "Read AGENTS.md", True),
    ("2", "Run the app in mock mode", False),
    ("3", "Replace this demo with a real feature", False),
)


class TaskStore:
    _tasks: list[Task]
    _next_id: int

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._tasks = [Task(id=id, title=title, done=done) for id, title, done in SEED]
        self._next_id = len(SEED) + 1

    def list(self) -> list[Task]:
        return list(self._tasks)

    def create(self, body: CreateTaskBody) -> Task:
        task = Task(id=str(self._next_id), title=body.title, done=False)
        self._next_id += 1
        self._tasks.append(task)
        return task

    def update(self, id: str, body: UpdateTaskBody) -> Task | None:
        for task in self._tasks:
            if task.id == id:
                task.done = body.done
                return task
        return None

    def remove(self, id: str) -> bool:
        for index, task in enumerate(self._tasks):
            if task.id == id:
                del self._tasks[index]
                return True
        return False


task_store = TaskStore()
