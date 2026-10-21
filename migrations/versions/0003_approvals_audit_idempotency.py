"""approval_requests, audit_logs, idempotency_keys + trigger append-only audit

Revision ID: 0003_approvals_audit_idempotency
Revises: 0002_ledger_append_only
Create Date: 2026-01-01

- approval_requests : work queue & eskalasi (WORKFLOWS.md)
- audit_logs        : jejak semua aksi penting — juga APPEND-ONLY (NFR-08)
- idempotency_keys  : lapisan persisted untuk Idempotency-Key (BR-03), fallback
                      kalau Redis mati; response_snapshot menyimpan respons asli
                      supaya retry menghasilkan jawaban identik
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_approvals_audit_idempotency"
down_revision: str | None = "0002_ledger_append_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "object_type",
            sa.Enum("transfer", "account", name="approval_object_type"),
            nullable=False,
        ),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column(
            "current_role",
            # type user_role sudah dibuat di 0001 → referensikan saja (create_type=False)
            postgresql.ENUM(
                "nasabah",
                "teller",
                "cs",
                "supervisor",
                "manager",
                "ops_risk",
                "auditor",
                name="user_role",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("PENDING", "ESCALATED", "APPROVED", "REJECTED", name="approval_status"),
            nullable=False,
        ),
        sa.Column("escalation_level", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        # BR-10: salin approval matrix yang berlaku saat keputusan diambil
        sa.Column("matrix_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    # Work queue query: WHERE status IN (...) AND current_role = :role
    op.create_index(
        "ix_approval_requests_queue",
        "approval_requests",
        ["status", "current_role"],
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("object_type", sa.String(30), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_logs_object", "audit_logs", ["object_type", "object_id"])

    # Trigger generik: tabel audit_logs juga append-only (fungsi sama dengan
    # yang dipakai ledger_entries, dibuat generik memakai TG_TABLE_NAME)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_mutation_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                '% adalah APPEND-ONLY: % tidak diizinkan', TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION forbid_mutation_append_only();
        """
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("key", name="uq_idempotency_keys_key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_append_only ON audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("approval_requests")
    op.execute("DROP FUNCTION IF EXISTS forbid_mutation_append_only()")
    sa.Enum(name="approval_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="approval_object_type").drop(op.get_bind(), checkfirst=True)
