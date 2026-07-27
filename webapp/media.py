"""رفع وعرض صور سجل الصور — S3 إن وُجد، وإلا قرص محلي."""

from __future__ import annotations

import io
import os
import re
import uuid
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from webapp import backup as backup_svc
from webapp import db

# أنواع مقبولة وحد أقصى ~6 ميجا للصورة الواحدة
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_MIME = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}
MAX_IMAGE_BYTES = 6 * 1024 * 1024

PHOTO_FIELDS = (
    "before_shot",
    "during_shot",
    "after_shot",
    "quantities_shot",
    "location_shot",
)

_SAFE = re.compile(r"[^\w\-]+", re.UNICODE)


def uploads_root() -> Path:
    root = db.DB_PATH.parent / "uploads" / "photos"
    root.mkdir(parents=True, exist_ok=True)
    return root


def photos_s3_prefix() -> str:
    return (os.environ.get("AWS_S3_PHOTOS_PREFIX", "").strip() or "rekaz-photos").strip("/")


def storage_backend() -> str:
    return "s3" if backup_svc.s3_configured() else "local"


def is_media_ref(value: str | None) -> bool:
    v = (value or "").strip()
    return v.startswith("/media/s3/") or v.startswith("/media/local/")


def photo_field_filled(value: str | None) -> bool:
    """مكتمل إن وُجدت صورة مرفوعة، أو قيمة قديمة «نعم»."""
    v = (value or "").strip()
    if not v or v == "لا":
        return False
    if v == "نعم":
        return True
    return is_media_ref(v)


def photos_complete(row: dict) -> bool:
    return all(photo_field_filled(row.get(k)) for k in PHOTO_FIELDS)


def media_url(value: str | None) -> str | None:
    """رابط عرض للصورة إن كانت مرفوعة."""
    v = (value or "").strip()
    return v if is_media_ref(v) else None


def _ext_for(file: FileStorage) -> str:
    name = secure_filename(file.filename or "") or "image"
    ext = Path(name).suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext in ALLOWED_EXT:
        return ext
    mime = (file.mimetype or "").lower().strip()
    if mime in ("image/jpeg", "image/jpg"):
        return ".jpg"
    if mime == "image/png":
        return ".png"
    if mime == "image/webp":
        return ".webp"
    raise ValueError("صيغة الصورة غير مدعومة (jpg / png / webp)")


def validate_image(file: FileStorage) -> bytes:
    if not file or not (file.filename or "").strip():
        raise ValueError("لم يُختر ملف")
    mime = (file.mimetype or "").lower().strip()
    if mime and mime not in ALLOWED_MIME and not mime.startswith("image/"):
        raise ValueError("الملف ليس صورة صالحة")
    data = file.read()
    if not data:
        raise ValueError("الملف فارغ")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("حجم الصورة يتجاوز 6 ميجابايت")
    # إعادة المؤشر إن احتيج لاحقاً
    try:
        file.stream.seek(0)
    except Exception:
        pass
    _ext_for(file)  # يتحقق من الامتداد
    # تحقق بسيط من التوقيع
    if not (
        data[:3] == b"\xff\xd8\xff"  # jpeg
        or data[:8] == b"\x89PNG\r\n\x1a\n"  # png
        or (len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    ):
        raise ValueError("محتوى الملف ليس صورة jpg/png/webp")
    return data


def _safe_ticket(ticket_no: str | None) -> str:
    t = _SAFE.sub("-", (ticket_no or "general").strip())[:80]
    return t or "general"


def save_photo(
    file: FileStorage,
    *,
    field: str,
    ticket_no: str | None = None,
) -> str:
    """يحفظ الصورة ويعيد مسار عرض `/media/...` للتخزين في الحقل."""
    if field not in PHOTO_FIELDS:
        raise ValueError("حقل صورة غير معروف")
    data = validate_image(file)
    ext = _ext_for(file)
    rel = f"{_safe_ticket(ticket_no)}/{field}_{uuid.uuid4().hex}{ext}"
    content_type = {
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")

    if backup_svc.s3_configured():
        key = f"{photos_s3_prefix()}/{rel}".replace("\\", "/")
        client = backup_svc._s3_client()
        cfg = backup_svc.s3_settings()
        client.put_object(
            Bucket=cfg["bucket"],
            Key=key,
            Body=data,
            ContentType=content_type,
            ContentDisposition=f'inline; filename="{Path(rel).name}"',
        )
        return f"/media/s3/{key}"

    dest = uploads_root() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return f"/media/local/{rel.replace(chr(92), '/')}"


def load_media(storage: str, key: str) -> tuple[io.BytesIO, str, str]:
    """يُرجع (stream, mime, download_name)."""
    storage = (storage or "").strip().lower()
    key = (key or "").replace("\\", "/").lstrip("/")
    if ".." in key.split("/"):
        raise ValueError("مسار غير صالح")
    name = Path(key).name or "image"
    ext = Path(name).suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")

    if storage == "s3":
        if not backup_svc.s3_configured():
            raise FileNotFoundError("S3 غير مُعد")
        # أمان: المفاتيح يجب أن تكون تحت بادئة الصور
        prefix = photos_s3_prefix() + "/"
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
            raise FileNotFoundError("الصورة غير موجودة")
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
    - clear_flags[field]=True لمسح الصورة
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
