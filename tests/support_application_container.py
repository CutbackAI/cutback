"""Explicit owned image selection for source check containment acceptance."""

import os

import pytest

from cutback.application_container import ApplicationContainer


@pytest.fixture
def container_image():
    image = os.environ.get("CUTBACK_TEST_APPLICATION_CHECK_IMAGE_ID")
    if not image:
        pytest.skip("explicit local application check image qualification is not enabled")
    return ApplicationContainer(
        image_id=image,
        python_path=os.environ.get(
            "CUTBACK_TEST_APPLICATION_CHECK_PYTHON_PATH", "/app/.venv/bin/python"
        ),
    )


@pytest.fixture
def container_node_image():
    image = os.environ.get("CUTBACK_TEST_APPLICATION_CHECK_NODE_IMAGE_ID")
    if not image:
        pytest.skip("explicit local Node application check image qualification is not enabled")
    return ApplicationContainer(image_id=image)
