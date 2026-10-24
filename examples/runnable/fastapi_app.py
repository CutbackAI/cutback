"""Runnable loopback synthetic FastAPI application. No provider key or real inference."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

from cutback.instrumentation import workflow
from examples.runnable.native_clients import anthropic_client, openai_client


@asynccontextmanager
async def lifespan(app):
    async with (
        openai_client(asynchronous=True) as openai,
        anthropic_client(asynchronous=True) as anthropic,
    ):
        app.state.openai, app.state.anthropic = openai, anthropic
        yield


app = FastAPI(title="Cutback synthetic example", lifespan=lifespan, docs_url=None, redoc_url=None)


class ExampleInput(BaseModel):
    text: str = Field(min_length=1, max_length=1024)


@app.post("/classify")
async def classify(body: ExampleInput):
    with workflow("example-fastapi-classify"):
        response = await app.state.openai.responses.create(model="gpt-4.1-mini", input=body.text)
        message = await app.state.anthropic.messages.create(
            model="claude-haiku-4-5",
            max_tokens=32,
            messages=[{"role": "user", "content": body.text}],
        )
    return {"synthetic": True, "openai": response.output_text, "anthropic": message.content[0].text}
