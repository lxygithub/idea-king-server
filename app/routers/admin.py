import json
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

_tpl_dir = os.path.join(os.path.dirname(__file__), "..", "templates", "admin")
_tpl_env = Environment(
    loader=FileSystemLoader(_tpl_dir),
    cache_size=0,
)


def _get_admin_id(request: Request) -> int | None:
    return request.session.get("admin_user_id")


async def _require_admin(request: Request, db: AsyncSession = Depends(get_db)):
    uid = _get_admin_id(request)
    if uid is None:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return user


def _render(name: str, **ctx):
    tpl = _tpl_env.get_template(name)
    return tpl.render(**ctx)

def _get_next_url(request: Request) -> str:
    referer = request.headers.get("referer", "")
    if "/admin/users/" in referer:
        import re
        m = re.search(r'(/admin/users/\d+)', referer)
        if m:
            return m.group(1)
    return "/admin/files"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    uid = _get_admin_id(request)
    if uid:
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    return HTMLResponse(_render("login.html", request=request))


@router.post("/login")
async def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return RedirectResponse(url="/admin/login?error=1", status_code=303)
    request.session["admin_user_id"] = user.id
    request.session["admin_username"] = user.username
    request.session["admin_is_admin"] = user.is_admin
    raise HTTPException(status_code=303, headers={"Location": "/admin/login"})


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    raise HTTPException(status_code=303, headers={"Location": "/admin/login"})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(_require_admin),
):
    uid = request.session.get("admin_user_id")
    is_admin = request.session.get("admin_is_admin", False)
    if is_admin:
        user_count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        file_count = 0
        all_users = (await db.execute(select(User))).scalars().all()
        for u in all_users:
            file_count += await file_service.count_user_files(db, u.id)
        return HTMLResponse(
            _render("dashboard.html", request=request, user_count=user_count, file_count=file_count,
                    admin_username=request.session.get("admin_username"), is_admin=True, user_id=uid)
        )
    else:
        my_count = await file_service.count_user_files(db, uid)
        return HTMLResponse(
            _render("dashboard.html", request=request, file_count=my_count,
                    admin_username=request.session.get("admin_username"), is_admin=False, user_id=uid)
        )


@router.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request, db: AsyncSession = Depends(get_db), _=Depends(_require_admin)
):
    if not request.session.get("admin_is_admin"):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return HTMLResponse(
        _render("users.html", request=request, users=users,
                admin_username=request.session.get("admin_username"),
                admin_user_id=request.session.get("admin_user_id"))
    )


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(
    request: Request, user_id: int,
    db: AsyncSession = Depends(get_db), _=Depends(_require_admin)
):
    result = await db.execute(select(User).where(User.id == user_id))
    view_user = result.scalar_one_or_none()
    if not view_user:
        raise HTTPException(status_code=404, detail="User not found")
    files = await file_service.get_user_files(db, user_id)
    return HTMLResponse(
        _render("user_detail.html", request=request, view_user=view_user, files=files,
                admin_username=request.session.get("admin_username"))
    )


@router.get("/files", response_class=HTMLResponse)
async def files_list(
    request: Request, db: AsyncSession = Depends(get_db), _=Depends(_require_admin)
):
    if not request.session.get("admin_is_admin"):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    files = await file_service.get_all_files(db)
    return HTMLResponse(
        _render("files.html", request=request, files=files,
                admin_username=request.session.get("admin_username"))
    )


@router.post("/files/{file_id}/update")
async def update_file(request: Request,
    file_id: str, name: str = Form(None), tags: str = Form(None),
    db: AsyncSession = Depends(get_db), _=Depends(_require_admin)
):
    # Convert comma-separated tags to JSON array
    tags_json = None
    if tags is not None:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        tags_json = json.dumps(tag_list, ensure_ascii=False)
    try:
        await file_service.update_file_meta(db, file_id, name=name, tags=tags_json)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise
    return RedirectResponse(url=_get_next_url(request), status_code=303)



@router.post("/files/batch-delete")
async def batch_delete_files(
    request: Request,
    file_ids: str = Form(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(_require_admin),
):
    """Delete multiple files by comma-separated IDs."""
    ids = [f.strip() for f in file_ids.split(",") if f.strip()]
    for fid in ids:
        await file_service.delete_file_as_admin(db, fid)
    next_url = _get_next_url(request)
    sep = "&" if "?" in next_url else "?"
    return RedirectResponse(url=next_url + sep + "deleted=" + str(len(ids)), status_code=303)


@router.post("/files/{file_id}/delete")
async def delete_file(
    request: Request, file_id: str,
    db: AsyncSession = Depends(get_db), _=Depends(_require_admin)
):
    await file_service.delete_file_as_admin(db, file_id)
    return RedirectResponse(url=_get_next_url(request), status_code=303)


@router.post("/users/create")
async def create_user(
    request: Request, username: str = Form(...), password: str = Form(...),
    is_admin: str | None = Form(None),
    db: AsyncSession = Depends(get_db), _=Depends(_require_admin)
):
    from app.services.auth_service import hash_password
    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none():
        users = (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
        return HTMLResponse(
            _render("users.html", request=request, users=users,
                    admin_username=request.session.get("admin_username"),
                    admin_user_id=request.session.get("admin_user_id"), error="用户名已存在")
        )
    user = User(username=username, password_hash=hash_password(password), is_admin=(is_admin == "on"))
    db.add(user)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/delete")
async def delete_user(
    request: Request, user_id: int,
    db: AsyncSession = Depends(get_db), _=Depends(_require_admin)
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == request.session.get("admin_user_id"):
        return RedirectResponse(url="/admin/users?error=self", status_code=303)
    await db.delete(user)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/change-password")
async def admin_change_password(
    user_id: int, new_password: str = Form(...),
    db: AsyncSession = Depends(get_db), _=Depends(_require_admin)
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
    user_id: int, new_username: str = Form(...),
    db: AsyncSession = Depends(get_db), _=Depends(_require_admin)
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_admin:
        return RedirectResponse(url=f"/admin/users/{user_id}?error=admin_cannot_change_username", status_code=303)
    dup = await db.execute(select(User).where(User.username == new_username))
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")
    user.username = new_username
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=303)


@router.api_route("/{path:path}", methods=["GET", "POST", "HEAD"])
async def admin_catch_all(path: str, request: Request):
    """Catch-all: redirect unknown paths to dashboard or login."""
    if path.startswith("static/"):
        raise HTTPException(status_code=404)
    if _get_admin_id(request):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    return RedirectResponse(url="/admin/login", status_code=303)

