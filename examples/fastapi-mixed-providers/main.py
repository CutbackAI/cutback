"""Synthetic application source for offline scanning; no customer inputs."""

import os

from clients import claude, openai_client


async def shared_ai(prompt):
    return await openai_client.responses.create(model=os.getenv("APP_MODEL"), input=prompt)


async def summarize_document(document):
    return await shared_ai(document)


async def categorize_document(document):
    return await shared_ai(document)


def extract_label(messages):
    return claude.messages.create(model="claude-sonnet-4-5", max_tokens=100, messages=messages)
