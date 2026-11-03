from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class User(Base):
    """Conta da aplicação na etapa atual: aluno ou professor."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="student")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StudentGroup(Base):
    """Grupo de alunos, sempre criado com um líder aluno."""

    __tablename__ = "rsa_student_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    leader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StudentGroupMembership(Base):
    """Associação única: um aluno pode participar de apenas um grupo."""

    __tablename__ = "rsa_student_group_memberships"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("rsa_student_groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RsaUpload(Base):
    """Arquivo ZIP enviado por um aluno, sem vínculo com atividade ou grupo."""

    __tablename__ = "rsa_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RsaTestRun(Base):
    """Resultado da bateria de 240 testes de um ZIP enviado pelo aluno."""

    __tablename__ = "rsa_test_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("rsa_uploads.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    total: Mapped[int] = mapped_column(Integer, default=240)
    approved: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errored: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class GroupRsaTestRun(Base):
    """Avaliação de interoperabilidade entre o encoder e o decoder de dois grupos."""

    __tablename__ = "rsa_group_test_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    encoder_group_id: Mapped[int] = mapped_column(ForeignKey("rsa_student_groups.id"), index=True)
    decoder_group_id: Mapped[int] = mapped_column(ForeignKey("rsa_student_groups.id"), index=True)
    encoder_upload_id: Mapped[int] = mapped_column(ForeignKey("rsa_uploads.id"), index=True)
    decoder_upload_id: Mapped[int] = mapped_column(ForeignKey("rsa_uploads.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    total: Mapped[int] = mapped_column(Integer, default=400)
    approved: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errored: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
