"""An explicit source-native demonstration contract, never a live transport grant."""

from typing import Literal

from cutback.core.models import StrictModel

NATIVE_FIXTURE_CONTRACT: Literal["native-source-fixture-v1"] = "native-source-fixture-v1"


class NativeFixtureExecution(StrictModel):
    version: Literal["native-source-fixture-v1"] = NATIVE_FIXTURE_CONTRACT

    def public_summary(self):
        return {
            "version": self.version,
            "transport": "scripted_test_only",
            "real_model_quality_verified": False,
            "real_customer_savings_verified": False,
        }
