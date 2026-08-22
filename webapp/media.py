"""رفع وعرض مرفقات سجل الصور — صور أو PDF — عبر S3 إن وُجد، وإلا قرص محلي."""

from __future__ import annotations

import io
import json
import os
import re
import uuid
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from webapp import backup as backup_svc
from webapp import db

# أنواع مقبولة وحد أقصى ~10 ميجا للملف الواحد
ALLOWED_EXT = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".zip",
}
ALLOWED_MIME = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
    "application/zip",
    "application/x-zip-compressed",
}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# توافق مع الاستيرادات القديمة
MAX_IMAGE_BYTES = MAX_UPLOAD_BYTES

PHOTO_FIELDS = (
    "before_shot",
    "during_shot",
    "after_shot",
    "quantities_shot",
    "location_shot",
)
ATTACHMENT_FIELD = "attachments"

_SAFE = re.compile(r"[^\w\-]+", re.UNICODE)

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".zip": "application/zip",
}


def uploads_root() -> Path:
    root = db.DB_PATH.parent / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def storage_backend() -> str:
    return "s3" if backup_svc.s3_configured() else "local"


def is_media_ref(value: str | None) -> bool:
    v = (value or "").strip()
    return v.startswith("/media/s3/") or v.startswith("/media/local/")


def is_pdf_ref(value: str | None) -> bool:
    v = (value or "").strip()
    if not is_media_ref(v):
        return False
    return Path(v.split("?")[0]).suffix.lower() == ".pdf"


def is_image_ref(value: str | None) -> bool:
    v = (value or "").strip().lower()
    if not is_media_ref(v):
        return False
    return Path(v.split("?")[0]).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}


def photo_field_filled(value: str | None) -> bool:
    """مكتمل إن وُجد مرفق مرفوع، أو قيمة قديمة «نعم»."""
    v = (value or "").strip()
    if not v or v == "لا":
        return False
    if v == "نعم":
        return True
    return is_media_ref(v)


def photos_complete(row: dict) -> bool:
    return all(photo_field_filled(row.get(k)) for k in PHOTO_FIELDS)


def media_url(value: str | None) -> str | None:
    """رابط عرض للمرفق إن كان مرفوعاً."""
    v = (value or "").strip()
    return v if is_media_ref(v) else None


def _kind_from_bytes(data: bytes) -> str | None:
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def attachment_refs(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if is_media_ref(str(v).strip())]
    raw = str(value or "").strip()
    if not raw:
        return []
    if is_media_ref(raw):
        return [raw]
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if isinstance(parsed, list):
        return [str(v).strip() for v in parsed if is_media_ref(str(v).strip())]
    return []


def encode_attachment_refs(refs: list[str]) -> str:
    clean = [str(v).strip() for v in refs or [] if is_media_ref(str(v).strip())]
    return json.dumps(clean, ensure_ascii=False)


def media_filename(value: str | None) -> str:
    v = (value or "").strip().split("?")[0]
    return Path(v).name or "file"


def _ext_for(file: FileStorage, *, kind: str | None = None) -> str:
    name = secure_filename(file.filename or "") or "file"
    ext = Path(name).suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext in ALLOWED_EXT:
        return ext
    mime = (file.mimetype or "").lower().strip()
    if mime in ("image/jpeg", "image/jpg") or kind == "jpeg":
        return ".jpg"
    if mime == "image/png" or kind == "png":
        return ".png"
    if mime == "image/webp" or kind == "webp":
        return ".webp"
    if mime == "application/pdf" or kind == "pdf":
        return ".pdf"
    raise ValueError("صيغة الملف غير مدعومة (jpg / png / webp / pdf)")


def validate_image(file: FileStorage) -> bytes:
    """يتحقق من صورة أو PDF ويعيد البايتات."""
    if not file or not (file.filename or "").strip():
        raise ValueError("لم يُختر ملف")
    mime = (file.mimetype or "").lower().strip()
    if mime and mime not in ALLOWED_MIME and not mime.startswith("image/") and mime != "application/pdf":
        raise ValueError("نوع الملف غير مدعوم (صور أو PDF فقط)")
    data = file.read()
    if not data:
        raise ValueError("الملف فارغ")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("حجم الملف يتجاوز 25 ميجابايت")
    try:
        file.stream.seek(0)
    except Exception:
        pass
    kind = _kind_from_bytes(data)
    if not kind:
        raise ValueError("محتوى الملف ليس صورة jpg/png/webp أو PDF")
    _ext_for(file, kind=kind)
    return data


def validate_attachment(file: FileStorage) -> bytes:
    if not file or not (file.filename or "").strip():
        raise ValueError("لم يُختر ملف")
    name = secure_filename(file.filename or "") or "file"
    ext = Path(name).suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in ALLOWED_EXT:
        raise ValueError("صيغة الملف غير مدعومة")
    data = file.read()
    if not data:
        raise ValueError("الملف فارغ")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("حجم الملف يتجاوز 10 ميجابايت")
    try:
        file.stream.seek(0)
    except Exception:
        pass
    if ext in {".jpg", ".png", ".webp", ".pdf"} and not _kind_from_bytes(data):
        raise ValueError("محتوى الملف لا يطابق صيغته")
    return data


def _safe_ticket(ticket_no: str | None) -> str:
    t = _SAFE.sub("-", (ticket_no or "general").strip())[:80]
    return t or "general"


def _save_media(data: bytes, rel_path: str, content_type: str, filename: str) -> str:
    """Saves media data to S3 or local disk and returns the media URL."""
    if backup_svc.s3_configured():
        client = backup_svc._s3_client()
        cfg = backup_svc.s3_settings()
        key = f"{cfg.get('photos_prefix', 'rekaz-photos')}/{rel_path}".replace("\\", "/")
        client.put_object(
            Bucket=cfg["bucket"],
            Key=key,
            Body=data,
            ContentType=content_type,
            ContentDisposition=f'inline; filename="{filename}"',
        )
        return f"/media/s3/{key}"

    dest = uploads_root() / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return f"/media/local/{rel_path.replace(chr(92), '/')}"


def save_photo(
    file: FileStorage,
    *,
    field: str,
    ticket_no: str | None = None,
) -> str:
    """يحفظ الصورة أو PDF ويعيد مسار عرض `/media/...` للتخزين في الحقل."""
    if field not in PHOTO_FIELDS:
        raise ValueError("حقل صورة غير معروف")
    data = validate_image(file)
    kind = _kind_from_bytes(data)
    ext = _ext_for(file, kind=kind)
    photo_rel = f"{_safe_ticket(ticket_no)}/{field}_{uuid.uuid4().hex}{ext}"
    rel = f"photos/{photo_rel}"
    content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")
    return _save_media(data, rel, content_type, Path(photo_rel).name)


def save_attachment(
    file: FileStorage,
    *,
    scope: str = "general",
    record_ref: str | None = None,
) -> str:
    data = validate_attachment(file)
    name = secure_filename(file.filename or "") or "file"
    ext = Path(name).suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    content_type = _CONTENT_TYPES.get(ext, (file.mimetype or "application/octet-stream"))
    stem = _SAFE.sub("-", Path(name).stem).strip("-")[:60] or "file"
    rel = f"attachments/{_safe_ticket(scope)}/{_safe_ticket(record_ref)}/{uuid.uuid4().hex}_{stem}{ext}"
    return _save_media(data, rel, content_type, Path(rel).name)


def load_media(storage: str, key: str) -> tuple[io.BytesIO, str, str]:
    """يُرجع (stream, mime, download_name)."""
    storage = (storage or "").strip().lower()
    key = (key or "").replace("\\", "/").lstrip("/")
    if ".." in key.split("/"):
        raise ValueError("مسار غير صالح")
    name = Path(key).name or "file"
    ext = Path(name).suffix.lower()
    mime = _CONTENT_TYPES.get(ext, "application/octet-stream")

    if storage == "s3":
        if not backup_svc.s3_configured():
            raise FileNotFoundError("S3 غير مُعد")
        prefix = (backup_svc.s3_settings().get("photos_prefix") or "rekaz-photos") + "/"
        if not key.startswith(prefix) and not key.startswith("rekaz-photos/"):
            raise PermissionError("مفتاح S3 خارج مجلد الصور")
        client = backup_svc._s3_client()
        cfg = backup_svc.s3_settings()
        obj = client.get_object(Bucket=cfg["bucket"], Key=key)
        body = obj["Body"].read()
        mime = obj.get("ContentType") or mime
        return io.BytesIO(body), mime, name

    if storage == "local":
        path = (uploads_root() / key).resolve()
        root = uploads_root().resolve()
        if not str(path).startswith(str(root)) or not path.is_file():
            raise FileNotFoundError("الملف غير موجود")
        return io.BytesIO(path.read_bytes()), mime, name

    raise ValueError("نوع تخزين غير معروف")


def apply_photo_uploads(
    form_data: dict,
    files,
    *,
    ticket_no: str | None = None,
    clear_flags: dict | None = None,
) -> None:
    """
    يدمج ملفات الرفع مع بيانات النموذج.
    - ملف الرفع: file_<field>
    - clear_flags[field]=True لمسح المرفق
    """
    tno = ticket_no or (form_data.get("ticket_no") if form_data else None)
    clears = clear_flags or {}
    for field in PHOTO_FIELDS:
        existing = (form_data.get(field) or "").strip()
        uploaded = None
        if files is not None:
            uploaded = files.get(f"file_{field}") or files.get(field)
        has_file = bool(uploaded and (uploaded.filename or "").strip())
        if clears.get(field) and not has_file:
            form_data[field] = ""
            continue
        if has_file:
            form_data[field] = save_photo(uploaded, field=field, ticket_no=tno)
            continue
        form_data[field] = existing


def apply_attachment_uploads(
    form_data: dict,
    files,
    *,
    field: str = ATTACHMENT_FIELD,
    scope: str = "general",
    record_ref: str | None = None,
    clear: bool = False,
) -> None:
    existing = [] if clear else attachment_refs(form_data.get(field))
    uploaded_files = []
    if files is not None:
        uploaded_files = files.getlist(f"file_{field}") or files.getlist(field)
    for uploaded in uploaded_files:
        if uploaded and (uploaded.filename or "").strip():
            existing.append(save_attachment(uploaded, scope=scope, record_ref=record_ref))
    form_data[field] = encode_attachment_refs(existing)
