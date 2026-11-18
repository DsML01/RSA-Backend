from typing import Literal

from pydantic import BaseModel, EmailStr, Field

TestMode = Literal["auto", "both", "encoder_only", "decoder_only"]


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RsaTestIn(BaseModel):
    mode: TestMode = "auto"


class BaseCustomRsaTestIn(BaseModel):
    p: int = Field(ge=10, le=999_999_999)
    q: int = Field(ge=10, le=999_999_999)
    e: int = Field(ge=2)
    message: str = Field(min_length=1, max_length=1_000)


class CustomRsaTestIn(BaseCustomRsaTestIn):
    mode: TestMode = "auto"


class GrantRoleIn(BaseModel):
    role: Literal["teacher"]


class GroupCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    leader_id: int = Field(gt=0)


class GroupMemberIn(BaseModel):
    student_id: int = Field(gt=0)


class GroupRsaTestIn(BaseModel):
    encoder_group_id: int = Field(gt=0)
    decoder_group_id: int = Field(gt=0)
    encoder_upload_id: int | None = Field(default=None, gt=0)
    decoder_upload_id: int | None = Field(default=None, gt=0)


class GroupCustomRsaTestIn(BaseCustomRsaTestIn):
    encoder_group_id: int = Field(gt=0)
    decoder_group_id: int = Field(gt=0)
    encoder_upload_id: int | None = Field(default=None, gt=0)
    decoder_upload_id: int | None = Field(default=None, gt=0)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: str
