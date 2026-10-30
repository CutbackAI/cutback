"""Lazy commands; core scanner installation never imports runtime guard dependencies."""

from pathlib import Path
from uuid import UUID

import typer


def register_guardrails(app, output, request, fail):
    group = typer.Typer(no_args_is_help=True, help="Review optional bounded runtime guardrails.")
    app.add_typer(group, name="guardrails")

    @group.command("demo")
    def demo():
        """Run the independent app and actual checker process, with no vendor calls."""
        try:
            from cutback.sample_app.runtime_guardrails import run_guardrail_sample

            output(run_guardrail_sample())
        except ImportError:
            fail(
                "GUARDRAIL_EXTRA_REQUIRED", "Install cutback[guardrails] from the reviewed source."
            )
        except (ValueError, OSError):
            fail(
                "GUARDRAIL_DEMO_UNAVAILABLE",
                "Check optional dependencies and owned loopback process permissions; "
                "private details omitted.",
            )

    @group.command("validate")
    def validate(policy_file: Path):
        """Parse a private policy offline and print only its public review metadata."""
        from cutback.cli_checks import read
        from cutback.core.guardrails import GuardrailPolicy

        try:
            value = read(policy_file)
            if not isinstance(value, dict) or "content_nonce" not in value:
                raise ValueError("GUARDRAIL_FROZEN_CONTENT_NONCE_REQUIRED")
            policy = GuardrailPolicy.model_validate(value)
        except (ValueError, OSError, TypeError):
            fail(
                "GUARDRAIL_POLICY_INVALID",
                "Provide a finite consented policy with a frozen content_nonce UUID; "
                "content omitted.",
            )
        output(
            {
                "policy": policy.public_metadata(),
                "execution": "not_executed",
                "approval": "not_granted",
            }
        )

    @group.command("register")
    def register_policy(registration_file: Path, project: UUID, endpoint: str):
        """Send a reviewed private registration to the authorized encrypted collector."""
        from cutback.cli_checks import read

        try:
            body = read(registration_file)
        except (ValueError, OSError, TypeError):
            fail(
                "GUARDRAIL_REGISTRATION_INVALID",
                "Provide a reviewed private registration file; content omitted.",
            )
        output(request("POST", endpoint, f"/v1/projects/{project}/runtime-guardrails", json=body))
