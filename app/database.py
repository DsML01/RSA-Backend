from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

is_sqlite = settings.database_url.startswith("sqlite")

connect_args = {}
if is_sqlite:
    connect_args = {
        "check_same_thread": False,
        "timeout": 30,
    }
    # Cria os diretórios pais se a URL apontar para um arquivo com caminho relativo ou absoluto
    raw_path = settings.database_url.replace("sqlite:///", "", 1)
    if raw_path and raw_path != ":memory:":
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        # Garante suporte a chaves estrangeiras no SQLite
        cursor.execute("PRAGMA foreign_keys=ON")
        # Habilita WAL (Write-Ahead Logging) para maior concorrência entre threads e background tasks
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema_columns(target_engine=engine) -> None:
    """Adiciona colunas novas em bancos SQLite já existentes sem exigir recriação manual."""
    inspector = inspect(target_engine)
    table_names = set(inspector.get_table_names())
    migrations: list[str] = []

    if "rsa_uploads" in table_names:
        upload_columns = {col["name"] for col in inspector.get_columns("rsa_uploads")}
        if "group_id" not in upload_columns:
            migrations.append("ALTER TABLE rsa_uploads ADD COLUMN group_id INTEGER REFERENCES rsa_student_groups(id)")
        if "has_encoder" not in upload_columns:
            migrations.append("ALTER TABLE rsa_uploads ADD COLUMN has_encoder BOOLEAN NOT NULL DEFAULT true")
        if "has_decoder" not in upload_columns:
            migrations.append("ALTER TABLE rsa_uploads ADD COLUMN has_decoder BOOLEAN NOT NULL DEFAULT true")

    if "rsa_test_runs" in table_names:
        test_columns = {col["name"] for col in inspector.get_columns("rsa_test_runs")}
        if "mode" not in test_columns:
            migrations.append("ALTER TABLE rsa_test_runs ADD COLUMN mode VARCHAR(20) NOT NULL DEFAULT 'both'")

    if migrations:
        with target_engine.begin() as conn:
            for statement in migrations:
                conn.execute(text(statement))
