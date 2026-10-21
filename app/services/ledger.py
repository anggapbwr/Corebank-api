"""Ledger service — jantung double-entry.

Konvensi arah (sisi rekening nasabah):
- CREDIT = uang MASUK ke rekening (saldo bertambah)
- DEBIT  = uang KELUAR dari rekening (saldo berkurang)

Aturan yang dijaga di sini (bukan di route — supaya semua pemanggil, termasuk
job internal, tunduk pada aturan yang sama):
- BR-01 saldo tidak boleh negatif
- BR-05 Σ DEBIT = Σ KREDIT per transaksi
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.ledger import LedgerDirection, LedgerEntry


class LedgerError(ValueError):
    pass


class LedgerUnbalancedError(LedgerError):
    """BR-05 — total debit tidak sama dengan kredit."""


class NonPositiveAmountError(LedgerError):
    pass


class InsufficientBalanceError(LedgerError):
    """BR-01 — saldo akan menjadi negatif."""


# (rekening, arah, nominal)
Movement = tuple[Account, LedgerDirection, Decimal]


def assert_balanced(movements: list[Movement]) -> None:
    if len(movements) < 2:
        raise LedgerError("double-entry butuh minimal 2 baris")
    for _, _, amount in movements:
        if amount <= 0:
            raise NonPositiveAmountError("nominal harus > 0; arah ditentukan DEBIT/CREDIT")
    total_debit = sum(a for _, d, a in movements if d == LedgerDirection.DEBIT)
    total_credit = sum(a for _, d, a in movements if d == LedgerDirection.CREDIT)
    if total_debit != total_credit:
        raise LedgerUnbalancedError(f"debit {total_debit} != kredit {total_credit}")


async def post_journal(
    db: AsyncSession,
    *,
    transaction_id: UUID,
    movements: list[Movement],
) -> list[LedgerEntry]:
    """Tulis ledger + update saldo. Pemanggil berkewajiban commit/rollback (atomicity BR-02).

    Baris rekening di-lock (SELECT ... FOR UPDATE) agar dua transfer paralel
    tidak saling menimpa saldo (lost update).
    """
    assert_balanced(movements)
    entries: list[LedgerEntry] = []
    for account, direction, amount in movements:
        locked = await db.scalar(select(Account).where(Account.id == account.id).with_for_update())
        delta = amount if direction == LedgerDirection.CREDIT else -amount
        new_balance = Decimal(locked.balance) + delta
        if new_balance < 0:
            raise InsufficientBalanceError(
                f"{locked.account_number}: saldo {locked.balance} tidak cukup ({delta})"
            )
        locked.balance = new_balance
        entries.append(
            LedgerEntry(
                transaction_id=transaction_id,
                account_id=locked.id,
                direction=direction,
                amount=amount,
                balance_after=new_balance,
            )
        )
    db.add_all(entries)
    return entries
