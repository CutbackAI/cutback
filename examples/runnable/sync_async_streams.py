"""Run with python -m examples.runnable.sync_async_streams. No network/provider costs."""

import asyncio

from examples.runnable.native_clients import anthropic_client, openai_client


def synchronous():
    with openai_client() as client:
        response = client.responses.create(model="gpt-4.1-mini", input="Owned synthetic example")
        assert response.output_text == "synthetic answer"
        with client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": "Owned synthetic example"}],
            stream=True,
            stream_options={"include_usage": True},
        ) as stream:
            text = "".join(
                chunk.choices[0].delta.content or "" for chunk in stream if chunk.choices
            )
            assert text == "synthetic answer"
    with anthropic_client() as client:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=32,
            messages=[{"role": "user", "content": "Owned synthetic example"}],
        )
        assert message.content[0].text == "synthetic answer"
        with client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=32,
            messages=[{"role": "user", "content": "Owned synthetic example"}],
            stream=True,
        ) as stream:
            text = "".join(
                event.delta.text
                for event in stream
                if event.type == "content_block_delta" and event.delta.type == "text_delta"
            )
            assert text == "synthetic answer"


async def asynchronous():
    async with openai_client(asynchronous=True) as client:
        response = await client.responses.create(
            model="gpt-4.1-mini", input="Owned synthetic example"
        )
        assert response.output_text == "synthetic answer"
        async with await client.responses.create(
            model="gpt-4.1-mini", input="Owned synthetic example", stream=True
        ) as stream:
            text = "".join(
                [
                    event.delta
                    async for event in stream
                    if event.type == "response.output_text.delta"
                ]
            )
            assert text == "synthetic answer"
    async with anthropic_client(asynchronous=True) as client:
        message = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=32,
            messages=[{"role": "user", "content": "Owned synthetic example"}],
        )
        assert message.content[0].text == "synthetic answer"
        async with await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=32,
            messages=[{"role": "user", "content": "Owned synthetic example"}],
            stream=True,
        ) as stream:
            text = "".join(
                [
                    event.delta.text
                    async for event in stream
                    if event.type == "content_block_delta" and event.delta.type == "text_delta"
                ]
            )
            assert text == "synthetic answer"


if __name__ == "__main__":
    synchronous()
    asyncio.run(asynchronous())
    print(
        "SYNTHETIC: native sync, async and streaming examples passed. No provider network requests."
    )
