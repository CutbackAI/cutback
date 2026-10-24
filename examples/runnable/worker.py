"""Bounded worker example using owned local provider fixtures."""

from concurrent.futures import ThreadPoolExecutor

import cutback
from cutback.instrumentation import workflow
from examples.runnable.native_clients import openai_client


def classify_fixture(index):
    with openai_client() as client, workflow("synthetic-worker-classify"):
        response = client.responses.create(
            model="gpt-4.1-mini", input=f"Owned synthetic fixture {index}"
        )
        return response.output_text


def main():
    observer = cutback.init(service_name="synthetic-worker", environment="staging", enabled=False)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(classify_fixture, range(4)))
        assert results == ["synthetic answer"] * 4
        print("SYNTHETIC: four bounded worker tasks passed; no provider network requests.")
    finally:
        observer.close(timeout_seconds=0.1)


if __name__ == "__main__":
    main()
