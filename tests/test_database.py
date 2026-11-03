from datetime import datetime
import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import GroupRsaTestRun, RsaTestRun, RsaUpload, StudentGroup, StudentGroupMembership, User


@pytest.fixture
def sqlite_session():
    # Cria engine SQLite em arquivo temporário para validar foreign keys e WAL
    engine = create_engine("sqlite:///test_temp.db", connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        import os
        for ext in ("", "-wal", "-shm"):
            p = f"test_temp.db{ext}"
            if os.path.exists(p):
                os.remove(p)


def test_sqlite_pragmas_enabled(sqlite_session):
    fk = sqlite_session.execute(text("PRAGMA foreign_keys")).scalar()
    assert fk == 1

    jm = sqlite_session.execute(text("PRAGMA journal_mode")).scalar()
    assert jm.lower() == "wal"


def test_user_creation_and_unique_constraint(sqlite_session):
    user1 = User(
        email="aluno1@universidade.edu.br",
        name="Aluno Um",
        password_hash="hash123",
        role="student",
    )
    sqlite_session.add(user1)
    sqlite_session.commit()

    user2 = User(
        email="aluno1@universidade.edu.br",
        name="Aluno Dois",
        password_hash="hash456",
        role="student",
    )
    sqlite_session.add(user2)
    with pytest.raises(IntegrityError):
        sqlite_session.commit()
    sqlite_session.rollback()


def test_sqlite_foreign_key_enforcement(sqlite_session):
    # Tenta criar grupo com leader_id inexistente (deve falhar por FK ativa)
    group = StudentGroup(name="Grupo Alpha", leader_id=9999)
    sqlite_session.add(group)
    with pytest.raises(IntegrityError):
        sqlite_session.commit()
    sqlite_session.rollback()


def test_rsa_upload_and_test_run(sqlite_session):
    user = User(
        email="aluno_test@universidade.edu.br",
        name="Aluno Teste",
        password_hash="hash123",
        role="student",
    )
    sqlite_session.add(user)
    sqlite_session.commit()

    upload = RsaUpload(
        user_id=user.id,
        original_filename="entrega.zip",
        stored_path="/data/uploads/fake.zip",
        sha256="abc123456",
    )
    sqlite_session.add(upload)
    sqlite_session.commit()

    test_run = RsaTestRun(
        upload_id=upload.id,
        status="passed",
        total=240,
        approved=240,
        failed=0,
        errored=0,
    )
    sqlite_session.add(test_run)
    sqlite_session.commit()

    retrieved = sqlite_session.scalar(select(RsaTestRun).where(RsaTestRun.upload_id == upload.id))
    assert retrieved is not None
    assert retrieved.approved == 240
    assert retrieved.status == "passed"

