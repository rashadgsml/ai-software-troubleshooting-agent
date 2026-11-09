import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.db.base import Base
from tests.integration.conftest import MIGRATION_DB, _ensure_database


def test_migrations_upgrade_and_downgrade_match_models():
    url = _ensure_database(MIGRATION_DB)
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "head")
    tables = set(sa.inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)

    # Migrations and models must not drift: autogenerate would find nothing to change.
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []

    command.downgrade(cfg, "base")
    assert set(sa.inspect(engine).get_table_names()) - {"alembic_version"} == set()
    engine.dispose()
