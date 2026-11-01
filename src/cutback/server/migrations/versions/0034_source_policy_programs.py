"""Scoped common-policy envelopes, immutable selections and explicit payload ownership."""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE source_policy_programs (
        id UUID NOT NULL,
        project_id UUID NOT NULL,
        environment_id UUID NOT NULL,
        request_id UUID NOT NULL,
        dataset_id UUID NOT NULL,
        template_plan_id UUID NOT NULL,
        snapshot_id UUID NOT NULL,
        policy_id UUID NOT NULL,
        payload_id UUID NOT NULL,
        fit_id UUID,
        request_sha256 VARCHAR(64) NOT NULL,
        template_plan_sha256 VARCHAR(64) NOT NULL,
        snapshot_sha256 VARCHAR(64) NOT NULL,
        approval_sha256 VARCHAR(64) NOT NULL,
        specification JSON NOT NULL,
        economic_envelope JSON NOT NULL,
        status VARCHAR(40) NOT NULL,
        approval_status VARCHAR(20) NOT NULL,
        approved_by VARCHAR(100),
        reserved_usd NUMERIC(24, 12) DEFAULT '0' NOT NULL,
        spent_usd NUMERIC(24, 12) DEFAULT '0' NOT NULL,
        requests_used INTEGER DEFAULT '0' NOT NULL,
        current_round INTEGER DEFAULT '0' NOT NULL,
        known_cost_requests INTEGER DEFAULT '0' NOT NULL,
        lease_token UUID,
        lease_until TIMESTAMP WITH TIME ZONE,
        available_at TIMESTAMP WITH TIME ZONE,
        started_at TIMESTAMP WITH TIME ZONE,
        deadline_at TIMESTAMP WITH TIME ZONE,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        completed_at TIMESTAMP WITH TIME ZONE,
        summary JSON NOT NULL,
        actor VARCHAR(100) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_source_policy_program_scope UNIQUE (id, project_id, environment_id),
        CONSTRAINT uq_source_policy_program_request UNIQUE (project_id, request_id),
        CONSTRAINT fk_source_policy_program_environment FOREIGN KEY(environment_id, project_id)
        REFERENCES environments (id, project_id) ON DELETE CASCADE,
        CONSTRAINT fk_source_policy_program_dataset FOREIGN KEY(dataset_id, project_id)
        REFERENCES datasets (id, project_id),
        CONSTRAINT fk_source_policy_program_template FOREIGN KEY(template_plan_id, project_id)
        REFERENCES experiment_plans (id, project_id),
        CONSTRAINT fk_source_policy_program_snapshot FOREIGN KEY(snapshot_id, project_id,
        environment_id) REFERENCES task_cost_snapshots (id, project_id, environment_id),
        CONSTRAINT fk_source_policy_program_policy FOREIGN KEY(policy_id, project_id) REFERENCES
        capture_policies (id, project_id),
        CONSTRAINT fk_source_policy_program_payload FOREIGN KEY(payload_id, project_id)
        REFERENCES payload_samples (id, project_id),
        CONSTRAINT fk_source_policy_program_fit FOREIGN KEY(fit_id, project_id) REFERENCES
        payload_samples (id, project_id),
        CONSTRAINT ck_source_policy_program_money CHECK (reserved_usd >= 0 AND spent_usd >= 0),
        CONSTRAINT ck_source_policy_program_requests CHECK (requests_used >= 0 AND requests_used
        <= 10000 AND known_cost_requests >= 0 AND known_cost_requests <= requests_used),
        CONSTRAINT ck_source_policy_program_status CHECK (status IN
        ('READY_FOR_APPROVAL','QUEUED','WAITING_STATIC','WAITING_TRAINING','WAITING_GENERATION','WAITING_GENERATED','WAITING_ROUTED','WAITING_CONFIRMATION','COMPLETED','FAILED','CANCELLED','EXPIRED','ABSTAINED')),
        CONSTRAINT ck_source_policy_program_approval CHECK (approval_status IN
        ('pending','consumed')),
        CONSTRAINT ck_source_policy_program_current_round CHECK (current_round >= 0 AND
        current_round <= 3),
        FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_source_policy_program_project ON source_policy_programs (project_id,
        environment_id, created_at, id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_source_policy_program_queue ON source_policy_programs (status,
        available_at, created_at, id)
        """
    )
    op.execute(
        """
        CREATE TABLE source_policy_program_runs (
        id UUID NOT NULL,
        project_id UUID NOT NULL,
        environment_id UUID NOT NULL,
        program_id UUID NOT NULL,
        run_id UUID NOT NULL,
        phase VARCHAR(24) NOT NULL,
        round_index INTEGER DEFAULT '0' NOT NULL,
        program_sha256 VARCHAR(64) NOT NULL,
        plan_sha256 VARCHAR(64) NOT NULL,
        derivation JSON NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_source_policy_program_run_owner UNIQUE (run_id),
        CONSTRAINT uq_source_policy_program_phase UNIQUE (program_id, phase, round_index),
        CONSTRAINT uq_source_policy_program_run_scope UNIQUE (run_id, program_id, project_id,
        environment_id),
        CONSTRAINT fk_source_policy_program_run_parent FOREIGN KEY(program_id, project_id,
        environment_id) REFERENCES source_policy_programs (id, project_id, environment_id) ON
        DELETE CASCADE,
        CONSTRAINT fk_source_policy_program_run_experiment FOREIGN KEY(run_id, project_id)
        REFERENCES experiment_runs (id, project_id),
        CONSTRAINT ck_source_policy_program_phase CHECK (phase IN
        ('static_validation','generation','generated_validation','training','routed_validation','confirmation')),
        CONSTRAINT ck_source_policy_program_round CHECK (round_index >= 0 AND round_index <= 3),
        CONSTRAINT ck_source_policy_program_phase_round CHECK ((phase IN
        ('generation','generated_validation') AND round_index >= 1) OR (phase NOT IN
        ('generation','generated_validation') AND round_index = 0)),
        FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_source_policy_program_run_project ON source_policy_program_runs
        (project_id, program_id, id)
        """
    )
    op.execute(
        """
        CREATE TABLE source_policy_program_selections (
        id UUID NOT NULL,
        project_id UUID NOT NULL,
        environment_id UUID NOT NULL,
        program_id UUID NOT NULL,
        validation_run_id UUID NOT NULL,
        candidate_key VARCHAR(200) NOT NULL,
        strategy VARCHAR(24) NOT NULL,
        configuration_sha256 VARCHAR(64) NOT NULL,
        program_sha256 VARCHAR(64) NOT NULL,
        selection_sha256 VARCHAR(64) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_source_policy_program_selection UNIQUE (program_id),
        CONSTRAINT fk_source_policy_program_selection_parent FOREIGN KEY(program_id, project_id,
        environment_id) REFERENCES source_policy_programs (id, project_id, environment_id) ON
        DELETE CASCADE,
        CONSTRAINT fk_source_policy_program_selection_validation FOREIGN KEY(validation_run_id,
        program_id, project_id, environment_id) REFERENCES source_policy_program_runs (run_id,
        program_id, project_id, environment_id),
        CONSTRAINT ck_source_policy_program_strategy CHECK (strategy IN
        ('static','generated','learned')),
        FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE source_policy_program_payloads (
        id UUID NOT NULL,
        project_id UUID NOT NULL,
        environment_id UUID NOT NULL,
        program_id UUID NOT NULL,
        payload_id UUID NOT NULL,
        content_sha256 VARCHAR(64) NOT NULL,
        kind VARCHAR(24) NOT NULL,
        ordinal INTEGER NOT NULL,
        program_sha256 VARCHAR(64) NOT NULL,
        derivation JSON NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_source_policy_program_payload UNIQUE (program_id, payload_id),
        CONSTRAINT uq_source_policy_program_payload_ordinal UNIQUE (program_id, ordinal),
        CONSTRAINT fk_source_policy_program_payload_parent FOREIGN KEY(program_id, project_id,
        environment_id) REFERENCES source_policy_programs (id, project_id, environment_id) ON
        DELETE CASCADE,
        CONSTRAINT fk_source_policy_program_payload_content FOREIGN KEY(payload_id, project_id)
        REFERENCES payload_samples (id, project_id) ON DELETE CASCADE,
        CONSTRAINT ck_source_policy_program_payload_ordinal CHECK (ordinal >= 0 AND ordinal <=
        64),
        CONSTRAINT ck_source_policy_program_payload_kind CHECK (kind IN
        ('reference','static','generated','learned','generation_input','generation_output','fit')),
        FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_source_policy_program_payload_project ON source_policy_program_payloads
        (project_id, program_id, id)
        """
    )


def downgrade():
    connection = op.get_bind()
    tables = (
        "source_policy_program_selections",
        "source_policy_program_payloads",
        "source_policy_program_runs",
        "source_policy_programs",
    )
    if any(
        connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")) for table in tables
    ):
        raise RuntimeError(
            "EXPORT_AND_EXPLICITLY_DELETE_SOURCE_POLICY_PROGRAM_DATA_BEFORE_DOWNGRADE"
        )
    for table in tables:
        op.drop_table(table)
