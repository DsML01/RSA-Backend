import hashlib
import io
import json
import stat
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .auth import create_access_token, get_current_user, hash_password, verify_password
from .config import settings
from .database import Base, SessionLocal, engine, get_db
from .models import GroupRsaTestRun, RsaTestRun, RsaUpload, StudentGroup, StudentGroupMembership, User
from .schemas import CustomRsaTestIn, GrantRoleIn, GroupCreateIn, GroupCustomRsaTestIn, GroupMemberIn, GroupRsaTestIn, LoginIn, RegisterIn
from .testing import GROUP_TOTAL_TESTS, TOTAL_TESTS, TestExecutionError, run_custom_test, run_group_custom_test, run_group_test_battery, run_test_battery


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="RSA Project API",
    version="0.3.0",
    description="Autenticação e envio individual de arquivos RSA em ZIP.",
    lifespan=lifespan,
)
cors_origins_list = [origin.strip().rstrip("/") for origin in settings.cors_origins.split(",") if origin.strip()]
if "*" in cors_origins_list:
    cors_origins_list = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def test_payload(test_run: RsaTestRun) -> dict:
    try:
        summary = json.loads(test_run.summary)
    except json.JSONDecodeError:
        summary = {}
    return {
        "id": test_run.id,
        "status": test_run.status,
        "total": test_run.total,
        "approved": test_run.approved,
        "failed": test_run.failed,
        "errored": test_run.errored,
        "score_percent": round((test_run.approved / test_run.total * 100) if test_run.total else 0, 2),
        "buckets": summary.get("buckets", {}),
        "last_error": summary.get("last_error", ""),
    }


def upload_payload(db: Session, upload: RsaUpload) -> dict:
    latest_test = db.scalar(
        select(RsaTestRun).where(RsaTestRun.upload_id == upload.id).order_by(desc(RsaTestRun.id))
    )
    payload = {
        "id": upload.id,
        "filename": upload.original_filename,
        "uploaded_at": upload.uploaded_at,
    }
    if latest_test:
        payload["latest_test"] = test_payload(latest_test)
    return payload


def latest_upload_for_group(db: Session, group_id: int) -> RsaUpload | None:
    """Return the newest submission made by any member of a group."""
    return db.scalar(
        select(RsaUpload)
        .join(StudentGroupMembership, StudentGroupMembership.user_id == RsaUpload.user_id)
        .where(StudentGroupMembership.group_id == group_id)
        .order_by(desc(RsaUpload.uploaded_at), desc(RsaUpload.id))
    )


def group_test_payload(db: Session, test_run: GroupRsaTestRun) -> dict:
    try:
        summary = json.loads(test_run.summary)
    except json.JSONDecodeError:
        summary = {}
    encoder_group = db.get(StudentGroup, test_run.encoder_group_id)
    decoder_group = db.get(StudentGroup, test_run.decoder_group_id)
    encoder_upload = db.get(RsaUpload, test_run.encoder_upload_id)
    decoder_upload = db.get(RsaUpload, test_run.decoder_upload_id)
    return {
        "id": test_run.id,
        "status": test_run.status,
        "total": test_run.total,
        "approved": test_run.approved,
        "failed": test_run.failed,
        "errored": test_run.errored,
        "score_percent": round((test_run.approved / test_run.total * 100) if test_run.total else 0, 2),
        "last_error": summary.get("last_error", ""),
        "created_at": test_run.created_at,
        "completed_at": test_run.completed_at,
        "encoder_group": {"id": encoder_group.id, "name": encoder_group.name} if encoder_group else None,
        "decoder_group": {"id": decoder_group.id, "name": decoder_group.name} if decoder_group else None,
        "encoder_upload": encoder_upload.original_filename if encoder_upload else None,
        "decoder_upload": decoder_upload.original_filename if decoder_upload else None,
    }


def user_payload(user: User) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}


def group_payload(db: Session, group: StudentGroup) -> dict:
    leader = db.get(User, group.leader_id)
    members = db.scalars(
        select(User)
        .join(StudentGroupMembership, StudentGroupMembership.user_id == User.id)
        .where(StudentGroupMembership.group_id == group.id)
        .order_by(User.name)
    ).all()
    return {
        "id": group.id,
        "name": group.name,
        "created_at": group.created_at,
        "leader": user_payload(leader) if leader else None,
        "members": [user_payload(member) for member in members],
    }


def require_student(user: User) -> None:
    if user.role != "student":
        raise HTTPException(status_code=403, detail="Esta ação é exclusiva para alunos.")


def require_teacher_role(user: User) -> None:
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Esta ação é exclusiva para professores.")


def execute_test_run(test_run_id: int) -> None:
    """Executa no processo de segundo plano da API, fora da requisição HTTP."""
    with SessionLocal() as db:
        test_run = db.get(RsaTestRun, test_run_id)
        if not test_run:
            return
        upload = db.get(RsaUpload, test_run.upload_id)
        if not upload:
            test_run.status = "error"
            test_run.summary = json.dumps({"last_error": "Arquivo enviado não encontrado."})
            test_run.completed_at = datetime.utcnow()
            db.commit()
            return
        test_run.status = "running"
        db.commit()
        try:
            result = run_test_battery(Path(upload.stored_path))
            test_run.total = result["total"]
            test_run.approved = result["approved"]
            test_run.failed = result["failed"]
            test_run.errored = result["errored"]
            test_run.summary = json.dumps({"buckets": result["buckets"], "last_error": result["last_error"]})
            test_run.status = "passed" if test_run.approved == test_run.total else "failed"
        except TestExecutionError as exc:
            test_run.status = "error"
            test_run.errored = TOTAL_TESTS
            test_run.summary = json.dumps({"last_error": str(exc)})
        except Exception:
            test_run.status = "error"
            test_run.errored = TOTAL_TESTS
            test_run.summary = json.dumps({"last_error": "Falha inesperada durante os testes."})
        test_run.completed_at = datetime.utcnow()
        db.commit()


def execute_group_test_run(test_run_id: int) -> None:
    """Execute a teacher's cross-group battery outside the HTTP request."""
    with SessionLocal() as db:
        test_run = db.get(GroupRsaTestRun, test_run_id)
        if not test_run:
            return
        encoder_upload = db.get(RsaUpload, test_run.encoder_upload_id)
        decoder_upload = db.get(RsaUpload, test_run.decoder_upload_id)
        if not encoder_upload or not decoder_upload:
            test_run.status = "error"
            test_run.errored = GROUP_TOTAL_TESTS
            test_run.summary = json.dumps({"last_error": "Arquivo enviado não encontrado."})
            test_run.completed_at = datetime.utcnow()
            db.commit()
            return
        test_run.status = "running"
        db.commit()
        try:
            result = run_group_test_battery(Path(encoder_upload.stored_path), Path(decoder_upload.stored_path))
            test_run.total = result["total"]
            test_run.approved = result["approved"]
            test_run.failed = result["failed"]
            test_run.errored = result["errored"]
            test_run.summary = json.dumps({"last_error": result["last_error"]})
            test_run.status = "passed" if test_run.approved == test_run.total else "failed"
        except TestExecutionError as exc:
            test_run.status = "error"
            test_run.errored = GROUP_TOTAL_TESTS
            test_run.summary = json.dumps({"last_error": str(exc)})
        except Exception:
            test_run.status = "error"
            test_run.errored = GROUP_TOTAL_TESTS
            test_run.summary = json.dumps({"last_error": "Falha inesperada durante os testes entre grupos."})
        test_run.completed_at = datetime.utcnow()
        db.commit()


def validate_rsa_zip(content: bytes) -> None:
    if len(content) > settings.max_zip_bytes:
        raise HTTPException(status_code=413, detail="O ZIP ultrapassa o limite de 1 MB.")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            extracted_size = 0
            c_files = 0
            for info in archive.infolist():
                path = Path(info.filename)
                mode = info.external_attr >> 16
                if path.is_absolute() or ".." in path.parts or stat.S_ISLNK(mode):
                    raise HTTPException(status_code=422, detail="O ZIP contém um caminho inseguro.")
                if info.is_dir():
                    continue
                extracted_size += info.file_size
                if extracted_size > settings.max_unzipped_bytes:
                    raise HTTPException(status_code=422, detail="O conteúdo descompactado excede 5 MB.")
                if path.suffix.lower() == ".c":
                    c_files += 1
            if c_files == 0:
                raise HTTPException(status_code=422, detail="O ZIP deve conter ao menos um arquivo .c.")
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=422, detail="Envie um arquivo ZIP válido.") from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, db: Session = Depends(get_db)) -> dict:
    """Cadastro público: toda nova conta é um aluno."""
    email = data.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="E-mail já cadastrado.")

    user = User(name=data.name, email=email, password_hash=hash_password(data.password), role="student")
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}


@app.post("/api/v1/auth/login")
def login(data: LoginIn, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")
    return {"access_token": create_access_token(user), "token_type": "bearer"}


@app.get("/api/v1/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}


@app.get("/api/v1/users/students")
def list_students_for_teacher(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict]:
    """List students, indicating whether they already belong to a group."""
    require_teacher_role(user)
    memberships = dict(db.execute(select(StudentGroupMembership.user_id, StudentGroupMembership.group_id)).all())
    students = db.scalars(select(User).where(User.role == "student").order_by(User.name)).all()
    return [{**user_payload(student), "group_id": memberships.get(student.id)} for student in students]


@app.patch("/api/v1/users/{user_id}/role")
def grant_teacher_role(
    user_id: int,
    data: GrantRoleIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    require_teacher_role(user)
    student = db.get(User, user_id)
    if not student or student.role != "student":
        raise HTTPException(status_code=404, detail="Aluno não encontrado.")
    if db.scalar(select(StudentGroupMembership).where(StudentGroupMembership.user_id == student.id)):
        raise HTTPException(status_code=409, detail="O aluno deve sair do grupo antes de receber o papel de professor.")
    student.role = data.role
    db.commit()
    db.refresh(student)
    return user_payload(student)


@app.get("/api/v1/groups")
def list_groups_for_teacher(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict]:
    require_teacher_role(user)
    groups = db.scalars(select(StudentGroup).order_by(StudentGroup.name)).all()
    return [group_payload(db, group) for group in groups]


@app.post("/api/v1/groups", status_code=status.HTTP_201_CREATED)
def create_group(
    data: GroupCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    require_teacher_role(user)
    name = data.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="O nome do grupo deve ter pelo menos dois caracteres.")
    if db.scalar(select(StudentGroup).where(StudentGroup.name == name)):
        raise HTTPException(status_code=409, detail="Já existe um grupo com esse nome.")
    leader = db.get(User, data.leader_id)
    if not leader or leader.role != "student":
        raise HTTPException(status_code=422, detail="O líder deve ser um aluno existente.")
    if db.scalar(select(StudentGroupMembership).where(StudentGroupMembership.user_id == leader.id)):
        raise HTTPException(status_code=409, detail="O aluno escolhido já pertence a um grupo.")

    group = StudentGroup(name=name, leader_id=leader.id)
    db.add(group)
    db.flush()
    db.add(StudentGroupMembership(group_id=group.id, user_id=leader.id))
    db.commit()
    db.refresh(group)
    return group_payload(db, group)


@app.get("/api/v1/groups/available-leaders")
def list_available_group_leaders(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict]:
    """Return only students who are not already in any group, including as leader."""
    require_teacher_role(user)
    member_ids = set(db.scalars(select(StudentGroupMembership.user_id)).all())
    students = db.scalars(select(User).where(User.role == "student").order_by(User.name)).all()
    return [user_payload(student) for student in students if student.id not in member_ids]


@app.get("/api/v1/groups/{group_id}/available-students")
def list_available_students_for_group(
    group_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    require_student(user)
    group = db.get(StudentGroup, group_id)
    if not group or group.leader_id != user.id:
        raise HTTPException(status_code=403, detail="Somente o líder deste grupo pode consultar alunos disponíveis.")
    member_ids = set(db.scalars(select(StudentGroupMembership.user_id)).all())
    students = db.scalars(select(User).where(User.role == "student").order_by(User.name)).all()
    return [user_payload(student) for student in students if student.id not in member_ids]


@app.post("/api/v1/groups/{group_id}/members", status_code=status.HTTP_201_CREATED)
def add_group_member(
    group_id: int,
    data: GroupMemberIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    require_student(user)
    group = db.get(StudentGroup, group_id)
    if not group or group.leader_id != user.id:
        raise HTTPException(status_code=403, detail="Somente o líder deste grupo pode adicionar integrantes.")
    student = db.get(User, data.student_id)
    if not student or student.role != "student":
        raise HTTPException(status_code=422, detail="Selecione um aluno existente.")
    if db.scalar(select(StudentGroupMembership).where(StudentGroupMembership.user_id == student.id)):
        raise HTTPException(status_code=409, detail="Este aluno já pertence a um grupo.")
    db.add(StudentGroupMembership(group_id=group.id, user_id=student.id))
    db.commit()
    return group_payload(db, group)


def group_uploads_for_test(db: Session, encoder_group_id: int, decoder_group_id: int) -> tuple[StudentGroup, StudentGroup, RsaUpload, RsaUpload]:
    if encoder_group_id == decoder_group_id:
        raise HTTPException(status_code=422, detail="Escolha grupos diferentes para cifrar e decifrar.")
    encoder_group = db.get(StudentGroup, encoder_group_id)
    decoder_group = db.get(StudentGroup, decoder_group_id)
    if not encoder_group or not decoder_group:
        raise HTTPException(status_code=404, detail="Grupo não encontrado.")
    encoder_upload = latest_upload_for_group(db, encoder_group.id)
    decoder_upload = latest_upload_for_group(db, decoder_group.id)
    if not encoder_upload:
        raise HTTPException(status_code=422, detail=f"O grupo {encoder_group.name} ainda não possui um arquivo enviado.")
    if not decoder_upload:
        raise HTTPException(status_code=422, detail=f"O grupo {decoder_group.name} ainda não possui um arquivo enviado.")
    return encoder_group, decoder_group, encoder_upload, decoder_upload


@app.get("/api/v1/group-tests")
def list_group_tests(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict]:
    require_teacher_role(user)
    runs = db.scalars(select(GroupRsaTestRun).order_by(desc(GroupRsaTestRun.created_at)).limit(30)).all()
    return [group_test_payload(db, run) for run in runs]


@app.post("/api/v1/group-tests", status_code=status.HTTP_202_ACCEPTED)
def start_group_test(
    data: GroupRsaTestIn,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Queue 50 texts across 2-to-9-digit primes for the latest uploads of two groups."""
    require_teacher_role(user)
    encoder_group, decoder_group, encoder_upload, decoder_upload = group_uploads_for_test(
        db, data.encoder_group_id, data.decoder_group_id
    )
    active_run = db.scalar(
        select(GroupRsaTestRun).where(
            GroupRsaTestRun.encoder_group_id == encoder_group.id,
            GroupRsaTestRun.decoder_group_id == decoder_group.id,
            GroupRsaTestRun.status.in_(("queued", "running")),
        )
    )
    if active_run:
        raise HTTPException(status_code=409, detail="Esta combinação de grupos já está sendo testada.")
    test_run = GroupRsaTestRun(
        encoder_group_id=encoder_group.id,
        decoder_group_id=decoder_group.id,
        encoder_upload_id=encoder_upload.id,
        decoder_upload_id=decoder_upload.id,
        status="queued",
        total=GROUP_TOTAL_TESTS,
    )
    db.add(test_run)
    db.commit()
    db.refresh(test_run)
    background_tasks.add_task(execute_group_test_run, test_run.id)
    return group_test_payload(db, test_run)


@app.post("/api/v1/group-tests/custom")
def custom_group_test(
    data: GroupCustomRsaTestIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Run one teacher-selected interoperability case using the newest group uploads."""
    require_teacher_role(user)
    encoder_group, decoder_group, encoder_upload, decoder_upload = group_uploads_for_test(
        db, data.encoder_group_id, data.decoder_group_id
    )
    try:
        result = run_group_custom_test(
            Path(encoder_upload.stored_path), Path(decoder_upload.stored_path), data.message, data.p, data.q, data.e
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TestExecutionError as exc:
        return {"status": "error", "ciphertext": [], "decrypted_message": None, "decoder_output": "", "decoder_stderr": "", "error": str(exc), "public_key": {"n": data.p * data.q, "e": data.e}}
    return {
        **result,
        "public_key": {"n": data.p * data.q, "e": data.e},
        "encoder_group": {"id": encoder_group.id, "name": encoder_group.name},
        "decoder_group": {"id": decoder_group.id, "name": decoder_group.name},
    }


@app.get("/api/v1/my-group")
def get_my_group(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    require_student(user)
    membership = db.scalar(select(StudentGroupMembership).where(StudentGroupMembership.user_id == user.id))
    if not membership:
        return {"group": None, "is_leader": False}
    group = db.get(StudentGroup, membership.group_id)
    if not group:
        return {"group": None, "is_leader": False}
    return {"group": group_payload(db, group), "is_leader": group.leader_id == user.id}


@app.delete("/api/v1/my-group")
def leave_my_group(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    require_student(user)
    membership = db.scalar(select(StudentGroupMembership).where(StudentGroupMembership.user_id == user.id))
    if not membership:
        raise HTTPException(status_code=404, detail="Você não pertence a um grupo.")
    group = db.get(StudentGroup, membership.group_id)
    if group and group.leader_id == user.id:
        member_count = db.scalar(
            select(func.count()).select_from(StudentGroupMembership).where(StudentGroupMembership.group_id == group.id)
        )
        if member_count > 1:
            raise HTTPException(status_code=409, detail="O líder não pode sair enquanto houver outros integrantes no grupo.")
        db.delete(membership)
        db.delete(group)
        db.commit()
        return {"detail": "Você saiu e o grupo vazio foi removido."}

    db.delete(membership)
    db.commit()
    return {"detail": "Você saiu do grupo."}


@app.get("/api/v1/rsa-uploads")
def list_my_uploads(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    if user.role != "student":
        raise HTTPException(status_code=403, detail="Somente alunos possuem envios de RSA nesta etapa.")
    uploads = db.scalars(
        select(RsaUpload).where(RsaUpload.user_id == user.id).order_by(desc(RsaUpload.uploaded_at))
    ).all()
    return [upload_payload(db, upload) for upload in uploads]


@app.post("/api/v1/rsa-uploads", status_code=status.HTTP_201_CREATED)
async def upload_rsa_file(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if user.role != "student":
        raise HTTPException(status_code=403, detail="Somente alunos podem enviar arquivos RSA nesta etapa.")
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Envie um arquivo com extensão .zip.")

    content = await file.read(settings.max_zip_bytes + 1)
    validate_rsa_zip(content)
    storage_path = settings.upload_dir / f"{uuid.uuid4().hex}.zip"
    storage_path.write_bytes(content)

    upload = RsaUpload(
        user_id=user.id,
        original_filename=Path(file.filename).name,
        stored_path=str(storage_path),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload_payload(db, upload)


@app.post("/api/v1/rsa-uploads/{upload_id}/test", status_code=status.HTTP_202_ACCEPTED)
def test_rsa_upload(
    upload_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if user.role != "student":
        raise HTTPException(status_code=403, detail="Somente alunos podem testar arquivos RSA nesta etapa.")
    upload = db.get(RsaUpload, upload_id)
    if not upload or upload.user_id != user.id:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    active_run = db.scalar(
        select(RsaTestRun).where(
            RsaTestRun.upload_id == upload.id,
            RsaTestRun.status.in_(("queued", "running")),
        )
    )
    if active_run:
        raise HTTPException(status_code=409, detail="Este arquivo já está sendo testado.")

    test_run = RsaTestRun(upload_id=upload.id, status="queued")
    db.add(test_run)
    db.commit()
    db.refresh(test_run)
    background_tasks.add_task(execute_test_run, test_run.id)
    return test_payload(test_run)


@app.post("/api/v1/rsa-uploads/{upload_id}/custom-test")
def custom_test_rsa_upload(
    upload_id: int,
    data: CustomRsaTestIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Run one manually configured RSA round trip against the student's ZIP."""
    if user.role != "student":
        raise HTTPException(status_code=403, detail="Somente alunos podem testar arquivos RSA nesta etapa.")
    upload = db.get(RsaUpload, upload_id)
    if not upload or upload.user_id != user.id:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    try:
        result = run_custom_test(Path(upload.stored_path), data.message, data.p, data.q, data.e)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TestExecutionError as exc:
        return {"status": "error", "ciphertext": [], "decrypted_message": None, "decoder_output": "", "decoder_stderr": "", "error": str(exc)}

    return {
        **result,
        "public_key": {"n": data.p * data.q, "e": data.e},
    }
