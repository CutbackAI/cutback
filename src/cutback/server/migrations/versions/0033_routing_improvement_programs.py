"""Freeze source-routing programs and their scoped native-call ownership."""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE routing_improvement_programs (
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
        reserved_usd NUMERIC(24,  12) DEFAULT '0' NOT NULL,
        spent_usd NUMERIC(24,  12) DEFAULT '0' NOT NULL,
        requests_used INTEGER DEFAULT '0' NOT NULL,
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
        CONSTRAINT uq_routing_program_scope UNIQUE (id,  project_id,  environment_id),
        CONSTRAINT uq_routing_program_request UNIQUE (project_id,  request_id),
        CONSTRAINT fk_routing_program_environment FOREIGN KEY(environment_id,  project_id)
        REFERENCES environments (id,  project_id) ON DELETE CASCADE,
        CONSTRAINT fk_routing_program_dataset FOREIGN KEY(dataset_id,  project_id) REFERENCES
        datasets (id,  project_id),
        CONSTRAINT fk_routing_program_template FOREIGN KEY(template_plan_id,  project_id)
        REFERENCES experiment_plans (id,  project_id),
        CONSTRAINT fk_routing_program_snapshot FOREIGN KEY(snapshot_id,  project_id,
        environment_id) REFERENCES task_cost_snapshots (id,  project_id,  environment_id),
        CONSTRAINT fk_routing_program_policy FOREIGN KEY(policy_id,  project_id) REFERENCES
        capture_policies (id,  project_id),
        CONSTRAINT fk_routing_program_payload FOREIGN KEY(payload_id,  project_id) REFERENCES
        payload_samples (id,  project_id),
        CONSTRAINT fk_routing_program_fit FOREIGN KEY(fit_id,  project_id) REFERENCES
        payload_samples (id,  project_id),
        CONSTRAINT ck_routing_program_money CHECK (reserved_usd >= 0 AND spent_usd >= 0),
        CONSTRAINT ck_routing_program_requests CHECK (requests_used >= 0 AND requests_used <=
        10000 AND known_cost_requests >= 0 AND known_cost_requests <= requests_used),
        CONSTRAINT ck_routing_program_status CHECK (status IN ('READY_FOR_APPROVAL', 'QUEUED',
        'WAITING_STATIC', 'WAITING_TRAINING', 'WAITING_ROUTED', 'WAITING_CONFIRMATION',
        'COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED', 'ABSTAINED')),
        CONSTRAINT ck_routing_program_approval CHECK (approval_status IN ('pending',
        'consumed')),
        FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_routing_program_project ON routing_improvement_programs (project_id,
        environment_id,  created_at,  id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_routing_program_queue ON routing_improvement_programs (status,
        available_at,  created_at,  id)
        """
    )
    op.execute(
        """
        CREATE TABLE routing_program_runs (
        id UUID NOT NULL,
        project_id UUID NOT NULL,
        environment_id UUID NOT NULL,
        program_id UUID NOT NULL,
        run_id UUID NOT NULL,
        phase VARCHAR(24) NOT NULL,
        program_sha256 VARCHAR(64) NOT NULL,
        plan_sha256 VARCHAR(64) NOT NULL,
        derivation JSON NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_routing_program_run_owner UNIQUE (run_id),
        CONSTRAINT uq_routing_program_phase UNIQUE (program_id,  phase),
        CONSTRAINT uq_routing_program_run_scope UNIQUE (run_id,  program_id,  project_id,
        environment_id),
        CONSTRAINT fk_routing_program_run_parent FOREIGN KEY(program_id,  project_id,
        environment_id) REFERENCES routing_improvement_programs (id,  project_id,
        environment_id) ON DELETE CASCADE,
        CONSTRAINT fk_routing_program_run_experiment FOREIGN KEY(run_id,  project_id) REFERENCES
        experiment_runs (id,  project_id),
        CONSTRAINT ck_routing_program_phase CHECK (phase IN ('static_validation', 'training',
        'routed_validation', 'confirmation')),
        FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_routing_program_run_project ON routing_program_runs (project_id,
        program_id,  id)
        """
    )
    op.execute(
        """
        CREATE TABLE routing_program_selections (
        id UUID NOT NULL,
        project_id UUID NOT NULL,
        environment_id UUID NOT NULL,
        program_id UUID NOT NULL,
        validation_run_id UUID NOT NULL,
        candidate_key VARCHAR(100) NOT NULL,
        strategy VARCHAR(24) NOT NULL,
        configuration_sha256 VARCHAR(64) NOT NULL,
        program_sha256 VARCHAR(64) NOT NULL,
        selection_sha256 VARCHAR(64) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_routing_program_selection UNIQUE (program_id),
        CONSTRAINT fk_routing_program_selection_parent FOREIGN KEY(program_id,  project_id,
        environment_id) REFERENCES routing_improvement_programs (id,  project_id,
        environment_id) ON DELETE CASCADE,
        CONSTRAINT fk_routing_program_selection_validation FOREIGN KEY(validation_run_id,
        program_id,  project_id,  environment_id) REFERENCES routing_program_runs (run_id,
        program_id,  project_id,  environment_id),
        CONSTRAINT ck_routing_program_strategy CHECK (strategy IN ('static_native',
        'learned_input_size')),
        FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
        )
        """
    )


def downgrade():
    connection = op.get_bind()
    if any(
        connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})"))
        for table in (
            "routing_program_selections",
            "routing_program_runs",
            "routing_improvement_programs",
        )
    ):
        raise RuntimeError("EXPORT_AND_EXPLICITLY_DELETE_ROUTING_PROGRAM_DATA_BEFORE_DOWNGRADE")
    op.drop_table("routing_program_selections")
    op.drop_table("routing_program_runs")
    op.drop_table("routing_improvement_programs")
