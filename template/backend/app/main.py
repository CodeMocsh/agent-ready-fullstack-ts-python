from fastapi import FastAPI

from app.routes import router

app = FastAPI(title="Tasks API", version="0.1.0", separate_input_output_schemas=False)
app.router.redirect_slashes = False
app.include_router(router)
