from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    is_admin: bool
    created_at: str | None = None


class RegisterResponse(BaseModel):
    message: str
    user: UserResponse


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    is_admin: bool


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=128)


class ChangeUsernameRequest(BaseModel):
    new_username: str = Field(..., min_length=3, max_length=100)


class AdminChangePasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=128)


class AdminChangeUsernameRequest(BaseModel):
    new_username: str = Field(..., min_length=3, max_length=100)
