"""Tests for the migration that admits the ``work_receipt`` item type (code 12)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.engine import Engine

from omnigent.db.enum_codecs import ITEM_TYPE
from omnigent.db.utils import _build_alembic_config, clear_engine_cache, get_or_create_engine

_PRE_REVISION = "nf12ab34cd56"


@pytest.fixture
def db_url(tmp_path: Path) -> Iterator[str]:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    get_or_create_engine(url)
    try:
        yield url
    finally:
        clear_engine_cache()


def _type_check_sql(engine: Engine) -> str:
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='conversation_items'"
            )
        ).scalar_one()


def _index_sql(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return sorted(
            row[0]
            for row in conn.execute(
                sa.text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='conversation_items' AND sql IS NOT NULL"
                )
            )
        )


def test_work_receipt_code_is_admitted_at_head(db_url: str) -> None:
    assert ITEM_TYPE["work_receipt"] == 12
    engine = sa.create_engine(db_url)
    assert "11, 12)" in _type_check_sql(engine)


def test_downgrade_restores_narrow_check_and_keeps_indexes(db_url: str) -> None:
    engine = sa.create_engine(db_url)
    indexes_at_head = _index_sql(engine)
    command.downgrade(_build_alembic_config(db_url), _PRE_REVISION)
    assert "11, 12)" not in _type_check_sql(engine)
    assert _index_sql(engine) == indexes_at_head
    command.upgrade(_build_alembic_config(db_url), "head")
    assert "11, 12)" in _type_check_sql(engine)
    assert _index_sql(engine) == indexes_at_head
