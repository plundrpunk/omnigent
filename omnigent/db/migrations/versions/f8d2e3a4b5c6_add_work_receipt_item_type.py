"""admit the work_receipt conversation item type

Revision ID: f8d2e3a4b5c6
Revises: nf12ab34cd56
Create Date: 2026-10-21 00:00:00.000000

Widens ``ck_conversation_items_type`` to admit code 12 (``work_receipt``,
see ``omnigent.db.enum_codecs.ITEM_TYPE``). Databases that ran the SMALLINT
conversion before code 12 existed carry the narrower CHECK.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8d2e3a4b5c6"
down_revision: str | None = "nf12ab34cd56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "ck_conversation_items_type"
_RECEIPT_CHECK_NAME = "ck_conversation_items_type_work_receipt"
_CODES_WITH_RECEIPT = "type IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)"
_CODES_WITHOUT_RECEIPT = "type IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)"


def _type_check() -> tuple[str, bool]:
    checks = sa.inspect(op.get_bind()).get_check_constraints("conversation_items")
    for check in checks:
        if check["name"] in (_CHECK_NAME, _RECEIPT_CHECK_NAME):
            return check["name"], re.search(r"\b12\b", check["sqltext"]) is not None
    raise RuntimeError("Missing conversation_items type CHECK constraint")


def _replace_check(old_name: str, new_name: str, predicate: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "cockroachdb" and old_name == new_name:
        # Publish the drop before reusing its name; this makes the CRDB run non-atomic.
        with op.batch_alter_table("conversation_items") as batch_op:
            batch_op.drop_constraint(old_name, type_="check")
        bind.commit()
        bind.execute(sa.text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
        with op.batch_alter_table("conversation_items") as batch_op:
            batch_op.create_check_constraint(new_name, predicate)
        return
    with op.batch_alter_table("conversation_items") as batch_op:
        batch_op.drop_constraint(old_name, type_="check")
        batch_op.create_check_constraint(new_name, predicate)


def upgrade() -> None:
    """Admit work_receipt (code 12) in conversation_items.type."""
    name, admits_receipt = _type_check()
    if not admits_receipt or name != _CHECK_NAME:
        _replace_check(name, _CHECK_NAME, _CODES_WITH_RECEIPT)


def downgrade() -> None:
    """Drop work_receipt rows, then restore the 11-code CHECK."""
    op.execute("DELETE FROM conversation_items WHERE type = 12")
    name, admits_receipt = _type_check()
    if admits_receipt:
        _replace_check(name, _CHECK_NAME, _CODES_WITHOUT_RECEIPT)
