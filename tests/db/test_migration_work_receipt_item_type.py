"""Tests for the migration that admits the ``work_receipt`` item type (code 12)."""

from __future__ import annotations

from collections.abc import Iterator
from importlib import import_module
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Engine

from omnigent.db.enum_codecs import ITEM_TYPE
from omnigent.db.utils import (
    _build_alembic_config,
    _get_current_db_revision,
    _get_head_db_revision,
    _initialize_or_verify_schema,
    clear_engine_cache,
    get_or_create_engine,
)

_PRE_REVISION = "nf12ab34cd56"
_HEAD_REVISION = "f8d2e3a4b5c6"


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
    assert _get_head_db_revision(db_url) == _HEAD_REVISION
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


@pytest.mark.parametrize("narrow_check", [False, True])
def test_upgrade_is_idempotent_on_both_check_histories(db_url: str, narrow_check: bool) -> None:
    engine = sa.create_engine(db_url)
    migration = import_module(
        "omnigent.db.migrations.versions.f8d2e3a4b5c6_add_work_receipt_item_type"
    )
    try:
        if narrow_check:
            command.downgrade(_build_alembic_config(db_url), _PRE_REVISION)
        indexes = _index_sql(engine)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        check_name = (
            "ck_conversation_items_type_work_receipt"
            if narrow_check
            else "ck_conversation_items_type"
        )
        assert [
            check
            for check in sa.inspect(engine).get_check_constraints("conversation_items")
            if check["name"] != "ck_conversation_items_status"
        ] == [{"name": check_name, "sqltext": "type IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)"}]
        statements: list[str] = []

        def capture_sql(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        sa.event.listen(engine, "before_cursor_execute", capture_sql)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        assert not any(
            statement.lstrip().upper().startswith(("ALTER", "CREATE", "DROP"))
            for statement in statements
        )
        assert _index_sql(engine) == indexes
    finally:
        engine.dispose()


@pytest.mark.parametrize("parent", ["e5d9bc8ac650", "head"])
def test_boot_rejects_reused_revision_without_changing_database(
    tmp_path: Path, parent: str
) -> None:
    db_path = tmp_path / "reused-revision.db"
    url = f"sqlite:///{db_path}"
    command.upgrade(_build_alembic_config(url), parent)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("UPDATE alembic_version SET version_num = 'f7c1d2e3a4b5'")
        before = db_path.read_bytes()
        with pytest.raises(RuntimeError, match="found revision 'f7c1d2e3a4b5'"):
            _initialize_or_verify_schema(engine, url)
        assert db_path.read_bytes() == before
        assert _get_current_db_revision(engine) == "f7c1d2e3a4b5"
        if parent == "e5d9bc8ac650":
            assert not sa.inspect(engine).has_table("connections")
    finally:
        engine.dispose()
