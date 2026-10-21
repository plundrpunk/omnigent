"""admit the work_receipt conversation item type

Revision ID: f7c1d2e3a4b5
Revises: e5d9bc8ac650
Create Date: 2026-10-21 00:00:00.000000

Widens ``ck_conversation_items_type`` to admit code 12 (``work_receipt``,
see ``omnigent.db.enum_codecs.ITEM_TYPE``). Databases that ran the SMALLINT
conversion before code 12 existed carry the narrower CHECK.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f7c1d2e3a4b5"
down_revision: str | None = "e5d9bc8ac650"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "ck_conversation_items_type"
_CODES_WITH_RECEIPT = "type IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)"
_CODES_WITHOUT_RECEIPT = "type IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)"


def _replace_check(predicate: str) -> None:
    with op.batch_alter_table("conversation_items") as batch_op:
        batch_op.drop_constraint(_CHECK_NAME, type_="check")
        batch_op.create_check_constraint(_CHECK_NAME, predicate)


def upgrade() -> None:
    """Admit work_receipt (code 12) in conversation_items.type."""
    _replace_check(_CODES_WITH_RECEIPT)


def downgrade() -> None:
    """Drop work_receipt rows, then restore the 11-code CHECK."""
    op.execute("DELETE FROM conversation_items WHERE type = 12")
    _replace_check(_CODES_WITHOUT_RECEIPT)
