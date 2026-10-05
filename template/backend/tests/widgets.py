"""A resource of the tests' own, mounted on an app a test builds, so a test of what surrounds a
route -- the log, the spans, the metrics -- needs nothing the project serves."""

from typing import final

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from app.refusal import ApiError, responses


class Widget(BaseModel):
    name: str


@final
class NoSuchWidget(ApiError):
    status = 404
    description = "No such widget"


router = APIRouter()


@router.get("/widgets")
async def list_widgets() -> list[Widget]:
    return []


@router.post("/widgets", status_code=201)
async def create_widget(body: Widget) -> Widget:
    return body


@router.get("/widgets/{id}", responses=responses(NoSuchWidget))
async def read_widget(id: str) -> Widget:
    raise NoSuchWidget(f"No widget is called {id}")


def with_widgets(app: FastAPI) -> FastAPI:
    """`app`, also serving `/widgets`. Every `/widgets/{id}` is refused with a `404`."""
    app.include_router(router)
    return app
