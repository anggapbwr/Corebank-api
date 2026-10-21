"""initial schema: users, customers, accounts, transactions, ledger_entries

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01

Semua tabel finansial dibuat append-only-friendly sejak awal;
ledger_entries diberi trigger di revision 0002.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "nasabah",
                "teller",
                "cs",
                "supervisor",
                "manager",
                "ops_risk",
                "auditor",
                name="user_role",
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("ktp_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("account_number", sa.String(20), nullable=False),
        sa.Column("type", sa.Enum("savings", "checking", name="account_type"), nullable=False),
        sa.Column("balance", sa.Numeric(19, 4), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING_APPROVAL", "ACTIVE", "FROZEN", "CLOSED", name="account_status"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_accounts_customer_id", "accounts", ["customer_id"])
    op.create_index("ix_accounts_account_number", "accounts", ["account_number"], unique=True)

    op.create_table(
        "transactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "type", sa.Enum("transfer", "adjustment", name="transaction_type"), nullable=False
        ),
        sa.Column("source_account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("dest_account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING_APPROVAL", "EXECUTED", "REJECTED", "FAILED", name="transaction_status"
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        # idempotency layer 2 (setelah Redis) — jaring pengaman akhir (ERD §4)
        sa.UniqueConstraint("idempotency_key", name="uq_transactions_idempotency_key"),
    )
    op.create_index("ix_transactions_source_account_id", "transactions", ["source_account_id"])

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("transaction_id", sa.Uuid(), sa.ForeignKey("transactions.id"), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("direction", sa.Enum("DEBIT", "CREDIT", name="ledger_direction"), nullable=False),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("balance_after", sa.Numeric(19, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ledger_entries_transaction_id", "ledger_entries", ["transaction_id"])
    op.create_index("ix_ledger_entries_account_id", "ledger_entries", ["account_id"])


def downgrade() -> None:
    op.drop_table("ledger_entries")
    op.drop_table("transactions")
    op.drop_table("accounts")
    op.drop_table("customers")
    op.drop_table("users")
    sa.Enum(name="ledger_direction").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="transaction_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="transaction_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="account_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="account_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
