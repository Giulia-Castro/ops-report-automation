"""create ops_report and pipeline_failure

Revision ID: 0001
Revises:
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("createdat", sa.DateTime(timezone=True), nullable=False),
        sa.Column("createdby", sa.String(100), nullable=False),
        sa.Column("updatedat", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updatedby", sa.String(100), nullable=False),
        sa.Column("rowversion", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "ops_report",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("reportdate", sa.Date(), nullable=False),
        sa.Column("reportstatus", sa.String(20), nullable=False),
        sa.Column("healthstatus", sa.String(10), nullable=False),
        sa.Column("summarysource", sa.String(10), nullable=True),
        sa.Column("totalrequests", sa.Integer(), nullable=False),
        sa.Column("servererrors", sa.Integer(), nullable=False),
        sa.Column("errorrate", sa.Numeric(7, 4), nullable=False),
        sa.Column("p95latencyms", sa.Integer(), nullable=False),
        sa.Column("anomalycount", sa.Integer(), nullable=False),
        sa.Column("metricsjson", sa.Text(), nullable=False),
        sa.Column("anomaliesjson", sa.Text(), nullable=False),
        sa.Column("summaryjson", sa.Text(), nullable=True),
        sa.Column("llmmodel", sa.String(60), nullable=True),
        sa.Column("inputtokens", sa.Integer(), nullable=True),
        sa.Column("outputtokens", sa.Integer(), nullable=True),
        sa.Column("costusd", sa.Numeric(12, 6), nullable=True),
        sa.Column("generationms", sa.Integer(), nullable=False),
        sa.Column("generatedat", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deliveredat", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.UniqueConstraint("reportdate", name="uq_ops_report_date"),
    )
    op.create_index("ix_ops_report_status", "ops_report", ["reportstatus"])

    op.create_table(
        "pipeline_failure",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflowname", sa.String(100), nullable=False),
        sa.Column("nodename", sa.String(100), nullable=True),
        sa.Column("failuremessage", sa.String(500), nullable=True),
        sa.Column("executionid", sa.String(64), nullable=True),
        sa.Column("occurredat", sa.DateTime(timezone=True), nullable=False),
        *_audit_columns(),
    )
    op.create_index("ix_pipeline_failure_at", "pipeline_failure", ["occurredat"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_failure_at", table_name="pipeline_failure")
    op.drop_table("pipeline_failure")
    op.drop_index("ix_ops_report_status", table_name="ops_report")
    op.drop_table("ops_report")
