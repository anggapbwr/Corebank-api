"""ledger append-only: trigger DB tolak UPDATE & DELETE (BR-04)

Revision ID: 0002_ledger_append_only
Revises: 0001_initial
Create Date: 2026-01-01

Kenapa di DATABASE, bukan cuma di aplikasi?
Aplikasi bisa buggy, migrasi bisa salah, admin bisa telanjur query.
Trigger adalah lapisan terakhir yang tidak bisa dilewati siapa pun —
persis yang diminta auditor (immutability catatan finansial).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_ledger_append_only"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_ledger_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'ledger_entries is APPEND-ONLY (BR-04): % tidak diizinkan', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ledger_append_only
        BEFORE UPDATE OR DELETE ON ledger_entries
        FOR EACH ROW EXECUTE FUNCTION forbid_ledger_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_ledger_append_only ON ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS forbid_ledger_mutation()")
