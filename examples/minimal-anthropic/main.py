"""Scan-only example; not executed by Cutback."""

import anthropic as ant

client = ant.AsyncAnthropic()


async def categorize(messages):
    return await client.messages.create(
        model="claude-sonnet-4-5", max_tokens=100, messages=messages
    )
