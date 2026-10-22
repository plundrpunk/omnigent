"""Tests for the migration that admits the ``work_receipt`` item type (code 12)."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
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
    _run_migrations,
    clear_engine_cache,
    get_or_create_engine,
)

_PRE_REVISION = "nf12ab34cd56"
_HEAD_REVISION = "f8d2e3a4b5c6"
_CHECK_NAME = "ck_conversation_items_type"


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
    assert _CHECK_NAME in {
        c["name"] for c in sa.inspect(engine).get_check_constraints("conversation_items")
    }
    command.upgrade(_build_alembic_config(db_url), "head")
    assert "11, 12)" in _type_check_sql(engine)
    assert _index_sql(engine) == indexes_at_head
    assert _CHECK_NAME in {
        c["name"] for c in sa.inspect(engine).get_check_constraints("conversation_items")
    }


@pytest.mark.parametrize("check_history", ["wide", "narrow", "renamed"])
def test_upgrade_is_idempotent_on_check_histories(db_url: str, check_history: str) -> None:
    engine = sa.create_engine(db_url)
    migration = import_module(
        "omnigent.db.migrations.versions.f8d2e3a4b5c6_add_work_receipt_item_type"
    )
    try:
        if check_history == "narrow":
            command.downgrade(_build_alembic_config(db_url), _PRE_REVISION)
        elif check_history == "renamed":
            with engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)) as operations:
                    with operations.batch_alter_table("conversation_items") as batch_op:
                        batch_op.drop_constraint(_CHECK_NAME, type_="check")
                        batch_op.create_check_constraint(
                            "ck_conversation_items_type_work_receipt",
                            "type IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)",
                        )
        indexes = _index_sql(engine)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        assert [
            check
            for check in sa.inspect(engine).get_check_constraints("conversation_items")
            if check["name"] != "ck_conversation_items_status"
        ] == [{"name": _CHECK_NAME, "sqltext": "type IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)"}]
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


@pytest.mark.parametrize("parent", ["base", "m1a2b3c4d5e6", _PRE_REVISION])
def test_boot_upgrades_each_history_with_canonical_check(tmp_path: Path, parent: str) -> None:
    db_path = tmp_path / "upgrade.db"
    url = f"sqlite:///{db_path}"
    config = _build_alembic_config(url)
    command.upgrade(config, parent)
    engine = sa.create_engine(url)
    try:
        if parent == _PRE_REVISION:
            with engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)) as operations:
                    with operations.batch_alter_table("conversation_items") as batch_op:
                        batch_op.drop_constraint(_CHECK_NAME, type_="check")
                        batch_op.create_check_constraint(
                            _CHECK_NAME, "type IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)"
                        )
        _initialize_or_verify_schema(engine, url)
        assert _get_current_db_revision(engine) == _HEAD_REVISION
        assert [
            check
            for check in sa.inspect(engine).get_check_constraints("conversation_items")
            if check["name"] != "ck_conversation_items_status"
        ] == [{"name": _CHECK_NAME, "sqltext": "type IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)"}]
        before = db_path.read_bytes()
        _initialize_or_verify_schema(engine, url)
        command.upgrade(config, "head")
        assert db_path.read_bytes() == before
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)) as operations:
                with operations.batch_alter_table("conversation_items") as batch_op:
                    batch_op.drop_constraint(_CHECK_NAME, type_="check")
    finally:
        engine.dispose()


@pytest.mark.parametrize("parent", ["e5d9bc8ac650", "head"])
@pytest.mark.parametrize("entrypoint", [_initialize_or_verify_schema, _run_migrations])
def test_boot_rejects_reused_revision_without_changing_database(
    tmp_path: Path, parent: str, entrypoint
) -> None:
    db_path = tmp_path / "reused-revision.db"
    url = f"sqlite:///{db_path}"
    command.upgrade(_build_alembic_config(url), parent)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("UPDATE alembic_version SET version_num = 'f7c1d2e3a4b5'")
        before = db_path.read_bytes()
        with pytest.raises(RuntimeError, match="found revision 'f7c1d2e3a4b5'") as exc_info:
            entrypoint(engine, url)
        assert db_path.read_bytes() == before
        assert _get_current_db_revision(engine) == "f7c1d2e3a4b5"
        repair_parent = "e5d9bc8ac650" if parent == "e5d9bc8ac650" else _PRE_REVISION
        message = str(exc_info.value)
        repair_command = message.split("`")[1]
        assert repair_command == (
            f"alembic -c omnigent/db/alembic.ini stamp --purge {repair_parent}"
        )
        assert "Upgrade Omnigent" not in message
        assert "then restart Omnigent" in message
        if parent == "e5d9bc8ac650":
            assert not sa.inspect(engine).has_table("connections")
        subprocess.run(
            [sys.executable, "-m", *shlex.split(repair_command)],
            cwd=Path(__file__).resolve().parents[2],
            env={**os.environ, "OMNIGENT_DB_URL": url},
            check=True,
            capture_output=True,
            text=True,
        )
        assert _get_current_db_revision(engine) == repair_parent
        _initialize_or_verify_schema(engine, url)
        assert _get_current_db_revision(engine) == _HEAD_REVISION
        assert sa.inspect(engine).has_table("connections")
        assert _CHECK_NAME in {
            check["name"]
            for check in sa.inspect(engine).get_check_constraints("conversation_items")
        }
    finally:
        engine.dispose()
