import os

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services import file_service
from app.services.auth_service import verify_password

router = APIRouter(prefix="/admin", tags=["admin"])

# Use absolute path for templates to avoid cwd issues
_tpl_dir = os.path.join(os.path.dirname(__file__), "..", "templates", "admin")
_tpl_env = Environment(
    loader=FileSystemLoader(_tpl_dir),
    cache_size=0,  # disable cache to avoid unhashable-type issue
)


# ---- Session helpers ----

def _get_admin_id(request: Request) -> int | None:
    return request.session.get("admin_user_id")


async def _require_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    uid = _get_admin_id(request)
    if uid is None:
        raise HTTPException(status_code=303, detail="Redirecting...")
    result = await db.execute(
        select(User).where(User.id == uid, User.is_admin == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        request.session.clear()
        raise HTTPException(status_code=303)
    return user


def _render(name: str, **ctx) -> str:
    """Render a Jinja2 template to HTML string."""
    tpl = _tpl_env.get_template(name)
    return tpl.render(**ctx)


# ---- Routes ----

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return HTMLResponse(_render("login.html", request=request))


@router.post("/login")
async def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.username == username)
    )
    user = result.scalar_one_or_none()
    if (
        not user
        or not verify_password(password, user.password_hash)
        or not user.is_admin
    ):
        return RedirectResponse(url="/admin/login?error=1", status_code=303)
    request.session["admin_user_id"] = user.id
    request.session["admin_username"] = user.username
    return RedirectResponse(url="/admin/dashboard", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(_require_admin),
):
    user_count = (
        await db.execute(select(func.count()).select_from(User))
    ).scalar_one()
    # Count files across all per-user tables
    file_count = 0
    all_users = (await db.execute(select(User))).scalars().all()
    for u in all_users:
        file_count += await file_service.count_user_files(db, u.id)
    return HTMLResponse(
        _render(
            "dashboard.html",
            request=request,
            user_count=user_count,
            file_count=file_count,
            admin_username=request.session.get("admin_username"),
        )
    )


@router.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(_require_admin),
):
    result = await db.execute(
        select(User).order_by(User.created_at.desc())
    )
    users = result.scalars().all()
    return HTMLResponse(
        _render(
            "users.html",
            request=request,
            users=users,
            admin_username=request.session.get("admin_username"),
            admin_user_id=request.session.get("admin_user_id"),
        )
    )


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(_require_admin),
):
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    view_user = result.scalar_one_or_none()
    if not view_user:
        raise HTTPException(status_code=404, detail="User not found")

    files = await file_service.get_user_files(db, user_id)

    return HTMLResponse(
        _render(
            "user_detail.html",
            request=request,
            view_user=view_user,
            files=files,
            admin_username=request.session.get("admin_username"),
        )
    )


@router.get("/files", response_class=HTMLResponse)
async def files_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(_require_admin),
):
    files = await file_service.get_all_files(db)
    return HTMLResponse(
        _render(
            "files.html",
            request=request,
            files=files,
            admin_username=request.session.get("admin_username"),
        )
    )


@router.post("/files/{file_id}/delete")
async def delete_file(
    request: Request,
    file_id: str,
    db: AsyncSession = Depends(get_db),
    _=Depends(_require_admin),
):
    from app.services.file_service import delete_file_as_admin

    await delete_file_as_admin(db, file_id)
    return RedirectResponse(url="/admin/files", status_code=303)


@router.post("/users/create")
async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    is_admin: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    _=Depends(_require_admin),
):
    from app.services.auth_service import hash_password

    # Check duplicate
    result = await db.execute(
        select(User).where(User.username == username)
    )
    if result.scalar_one_or_none():
        return HTMLResponse(
            _render(
                "users.html",
                request=request,
                users=(await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all(),
                admin_username=request.session.get("admin_username"),
                admin_user_id=request.session.get("admin_user_id"),
                error="用户名已存在",
            )
        )
    user = User(
        username=username,
        password_hash=hash_password(password),
        is_admin=(is_admin == "on"),
    )
    db.add(user)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/delete")
async def delete_user(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(_require_admin),
):
    """Delete user and all their files (cascade)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == request.session.get("admin_user_id"):
        # Cannot delete yourself
        return RedirectResponse(url="/admin/users?error=self", status_code=303)
    await db.delete(user)  # cascade deletes files
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/change-password")
async def admin_change_password(
    user_id: int,
    new_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(_require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    from app.services.auth_service import hash_password as _hash
    user.password_hash = _hash(new_password)
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/change-username")
async def admin_change_username(
    user_id: int,
    new_username: str = Form(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(_require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_admin:
        return RedirectResponse(url=f"/admin/users/{user_id}?error=admin_cannot_change_username", status_code=303)
    # Check duplicate
    dup = await db.execute(select(User).where(User.username == new_username))
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")
    user.username = new_username
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=303)
