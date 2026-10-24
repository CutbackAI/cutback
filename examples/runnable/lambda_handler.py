"""Owned synthetic Lambda-style handler; local execution is not AWS freeze acceptance."""

import functools

import cutback
from cutback.instrumentation import workflow
from examples.runnable.native_clients import openai_client


def bounded_invocation(handler, observer):
    @functools.wraps(handler)
    def invoke(event, context):
        try:
            return handler(event, context)
        finally:
            try:
                remaining = context.get_remaining_time_in_millis()
                observer.flush(min(0.1, max(0, (remaining - 50) / 1000)))
            except Exception:
                pass

    return invoke


def application_handler(event, context):
    with openai_client() as client, workflow("synthetic-lambda-classify"):
        result = client.responses.create(model="gpt-4.1-mini", input="Owned synthetic fixture")
        return {"synthetic": True, "answer": result.output_text}


# Explicitly disabled for this standalone fixture. For authorized staging use the
# scoped bootstrap in examples/aws-lambda; replace synthetic transport only after approval.
handler = bounded_invocation(
    application_handler,
    cutback.init(service_name="synthetic-lambda", environment="staging", enabled=False),
)


if __name__ == "__main__":

    class LocalContext:
        def get_remaining_time_in_millis(self):
            return 1000

    print(handler({}, LocalContext()))
