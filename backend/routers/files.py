"""Files router — upload, list endpoints."""
import secrets
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

router = APIRouter()

from core.auth_middleware import require_auth
from core.paths import UPLOAD_DIR


@router.post("/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    session: dict = Depends(require_auth)
):
    """Subir archivo general (PDF, TXT, etc)"""
    try:
        file_extension = Path(file.filename).suffix
        saved_file = UPLOAD_DIR / f"{secrets.token_hex(8)}_{file.filename}"

        with saved_file.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        file_info = {
            "filename": file.filename,
            "path": str(saved_file),
            "size": saved_file.stat().st_size,
            "extension": file_extension
        }

        if file_extension.lower() in ['.txt', '.md', '.py', '.js', '.json', '.yaml', '.yml', '.csv']:
            try:
                with saved_file.open("r", encoding="utf-8") as f:
                    file_info["content"] = f.read(10000)
                    file_info["readable"] = True
            except Exception:
                file_info["readable"] = False
        else:
            file_info["readable"] = False

        return {"success": True, "file_info": file_info}

    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/files/list")
async def list_uploaded_files(session: dict = Depends(require_auth)):
    """Listar archivos subidos"""
    try:
        files = []
        for file_path in UPLOAD_DIR.glob("*"):
            if file_path.is_file():
                files.append({
                    "filename": file_path.name,
                    "size": file_path.stat().st_size,
                    "modified": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
                })

        return {"success": True, "files": files, "count": len(files)}
    except Exception as e:
        return {"success": False, "error": str(e)}
