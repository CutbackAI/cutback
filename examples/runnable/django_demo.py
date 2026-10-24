"""Owned synthetic Django view. --serve binds loopback; no provider network requests."""

import argparse
import secrets

import django
from django.conf import settings
from django.http import JsonResponse
from django.urls import path

from cutback.instrumentation import workflow
from examples.runnable.native_clients import openai_client


def synthetic_view(request):
    with openai_client() as client, workflow("synthetic-django-classify"):
        result = client.responses.create(model="gpt-4.1-mini", input="Owned synthetic example")
        return JsonResponse({"synthetic": True, "answer": result.output_text})


urlpatterns = [path("synthetic", synthetic_view)]


def configure():
    if not settings.configured:
        settings.configure(
            SECRET_KEY=secrets.token_urlsafe(48),
            DEBUG=False,
            ALLOWED_HOSTS=["127.0.0.1", "localhost", "testserver"],
            ROOT_URLCONF=__name__,
            MIDDLEWARE=[
                "django.middleware.security.SecurityMiddleware",
                "django.middleware.csrf.CsrfViewMiddleware",
            ],
            INSTALLED_APPS=[],
        )
    django.setup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    options = parser.parse_args()
    configure()
    if options.serve:
        from django.core.management import execute_from_command_line

        execute_from_command_line(["synthetic-demo", "runserver", "127.0.0.1:8001", "--noreload"])
    else:
        from django.test import Client

        print("SYNTHETIC Django view:", Client().get("/synthetic").json())
