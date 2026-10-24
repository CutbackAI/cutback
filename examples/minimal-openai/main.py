"""Scan-only example; importing needs OpenAI. Running inference requires explicit user approval."""

from openai import OpenAI

client = OpenAI()
MODEL = "gpt-4.1-mini"


def summarize(text: str):
    return client.responses.create(model=MODEL, input=text)
