"""حفظ تلقائي صامت للبيانات (محلي + S3) — بدون واجهة للمستخدم."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from webapp import db
from webapp.modules_config import MODULES, SECTION_META

log = logging.getLogger("rekaz.backup")

SAFE_LABEL = re.compile(r"[^\w\u0600-\u06FF\-]+", re.UNICODE)

PURPOSE_CHOICES = [
    ("manual", "حفظ يدوي"),
    ("auto", "حفظ تلقائي"),
    ("dev", "نقطة تطوير"),
    ("milestone", "مرحلة إنجاز"),
    ("before_change", "قبل تعديل كبير"),
    ("daily", "حفظ يومي"),
    ("trial", "حفظ تجربة سحابية"),
]

_auto_lock = threading.Lock()
_backup_io_lock = threading.RLock()
_scheduler_started = False
_silent_timer = None
_silent_lock = threading.Lock()

# رقم العطل التجريبي الذي يُزرع محلياً — لا يُعتبر بيانات مستخدم على السحابة
SEED_TICKET_NO = "43656357"
# يجب كتابتها حرفياً في نموذج الاستعادة لتأكيد النية
RESTORE_CONFIRM_PHRASE = "استعادة"
# حد أقصى لحجم ZIP المرفوع (بايت) — يحمي من ملفات ضخمة بالخطأ
MAX_UPLOAD_ZIP_BYTES = 512 * 1024 * 1024
# تحذير إن تجاوز عمر آخر حفظة هذا المضاعف من دورة الحفظ
STALE_BACKUP_MULTIPLIER = 2.5
MIN_FREE_DISK_BYTES = 64 * 1024 * 1024


def is_cloud() -> bool:
    return bool(os.environ.get("RENDER") or os.environ.get("RAKAZ_CLOUD", "").strip())


def is_ephemeral_hosting() -> bool:
    """سحابة بلا قرص دائم (مثل Render Free) — البيانات تُفقد عند إعادة النشر/السكون."""
    return is_cloud() and not bool(os.environ.get("RAKAZ_DATA_DIR", "").strip())


def is_trial_free() -> bool:
    """إصدار التجربة للعميل — القرص قد يكون دائماً مع ذلك عبر RAKAZ_DATA_DIR."""
    flag = os.environ.get("TRIAL_MODE", "").strip().lower()
    if flag in {"0", "false", "no", "off", "paid"}:
        return False
    if flag in {"1", "true", "yes", "on", "trial", "free"}:
        return True
    # افتراضي: سحابة بلا قرص دائم = تجربة
    return is_ephemeral_hosting()


def hosting_info() -> dict:
    trial = is_trial_free()
    data_dir = os.environ.get("RAKAZ_DATA_DIR", "").strip()
    ephemeral = is_ephemeral_hosting()
    if trial and data_dir:
        plan_label = "إصدار تجربة (VPS + SQLite دائم)"
        hint = (
            "القرص على السيرفر دائم — بياناتك لا تُمسح بإعادة التشغيل. "
            "الحفظ التلقائي يعمل في الخلفية محلياً وإلى Amazon S3 دون تدخل منك."
        )
    elif trial:
        plan_label = "تجربة سحابية مؤقتة"
        hint = (
            "القرص غير دائم — الحفظ التلقائي يرفع النسخ إلى Amazon S3 في الخلفية. "
            "اطلب نسخة ZIP من مسؤول التشغيل عند الحاجة."
        )
    elif data_dir:
        plan_label = "سحابي دائم (VPS + SQLite)"
        hint = "الحفظ التلقائي يعمل على قرص السيرفر وإلى Amazon S3 دون واجهة للمستخدم."
    else:
        plan_label = "محلي"
        hint = "الحفظات تُنشأ تلقائياً في مجلد البيانات المحلي."
    return {
        "is_cloud": is_cloud(),
        "is_trial_free": trial,
        "is_ephemeral": ephemeral,
        "plan_label": plan_label,
        # القرص الدائم يعتمد على RAKAZ_DATA_DIR فقط — لا يُلغى بوضع التجربة
        "data_persistent": bool(data_dir),
        "client_backup_mandatory": False,
        "data_root": str(data_root()),
        "backups_root": str(backups_root()),
        "hint": hint,
        "restore_confirm_phrase": RESTORE_CONFIRM_PHRASE,
    }


def local_keep_count() -> int:
    raw = os.environ.get("BACKUP_KEEP_LOCAL", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return max(5, min(int(raw), 500))
    return 30


def s3_keep_count() -> int:
    raw = os.environ.get("BACKUP_KEEP_S3", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return max(5, min(int(raw), 500))
    return 40


def free_disk_bytes(path: Path | None = None) -> int | None:
    try:
        usage = shutil.disk_usage(str(path or data_root()))
        return int(usage.free)
    except Exception:
        return None


def ensure_disk_space(min_bytes: int = MIN_FREE_DISK_BYTES) -> None:
    free = free_disk_bytes()
    if free is not None and free < min_bytes:
        raise OSError(
            f"مساحة القرص غير كافية للحفظ (المتبقي {human_size(free)}، المطلوب على الأقل {human_size(min_bytes)})."
        )


def validate_sqlite_file(path: Path) -> dict:
    """تحقق أن الملف SQLite صالح ويمرّ integrity_check."""
    path = Path(path)
    if not path.exists() or path.stat().st_size < 100:
        raise ValueError("ملف قاعدة البيانات فارغ أو غير موجود")
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except Exception:
        conn = sqlite3.connect(str(path))
    try:
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        row = conn.execute("PRAGMA integrity_check").fetchone()
        ok = bool(row and str(row[0]).lower() == "ok")
        if not ok:
            raise ValueError(f"فحص سلامة القاعدة فشل: {row[0] if row else 'unknown'}")
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "tickets" not in tables and "users" not in tables:
            raise ValueError("الملف لا يشبه قاعدة ركاز (لا جداول أساسية)")
        return {"ok": True, "tables": len(tables), "size": path.stat().st_size}
    finally:
        conn.close()


def confirm_restore_phrase(provided: str | None) -> bool:
    text = (provided or "").strip()
    return text == RESTORE_CONFIRM_PHRASE


def data_root() -> Path:
    return db.DB_PATH.parent


def backups_root() -> Path:
    root = data_root() / "backups"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _slug(text: str, fallback: str = "حفظ") -> str:
    text = (text or "").strip() or fallback
    text = SAFE_LABEL.sub("-", text).strip("-_")
    return (text[:60] or fallback)


def progress_snapshot(conn: sqlite3.Connection | None = None) -> dict:
    """ملخص أعداد السجلات لمعرفة كيف يسير العمل."""
    own = conn is None
    if own:
        conn = db.connect()
    try:
        def count(table: str) -> int:
            try:
                return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except Exception:
                return 0

        tickets_by_status = {}
        try:
            rows = conn.execute(
                "SELECT COALESCE(status,'') AS status, COUNT(*) AS n FROM tickets GROUP BY COALESCE(status,'')"
            ).fetchall()
            tickets_by_status = {r["status"] or "بدون حالة": r["n"] for r in rows}
        except Exception:
            tickets_by_status = {}

        modules = {}
        for key, mod in MODULES.items():
            modules[key] = {
                "title": mod["title"],
                "section": mod.get("section"),
                "count": count(mod["table"]),
            }

        sections = {}
        for sec_key, meta in SECTION_META.items():
            sections[sec_key] = {
                "title": meta["title"],
                "count": sum(m["count"] for m in modules.values() if m.get("section") == sec_key),
            }

        return {
            "tickets_total": count("tickets"),
            "tickets_by_status": tickets_by_status,
            "teams": count("teams"),
            "users": count("users"),
            "followups_open": count("followups"),
            "reviews": count("reviews"),
            "audit_events": count("audit_log"),
            "modules": modules,
            "sections": sections,
            "db_path": str(db.DB_PATH),
            "db_size_bytes": db.DB_PATH.stat().st_size if db.DB_PATH.exists() else 0,
        }
    finally:
        if own:
            conn.close()


def create_backup(
    *,
    label: str = "",
    note: str = "",
    purpose: str = "manual",
    user_name: str = "",
) -> dict:
    """
    يحفظ نسخة من قاعدة البيانات في مسار منظم:
    instance/backups/YYYY/MM/DD/HHMMSS__purpose__label/
      rakaz.db
      meta.json
    """
    if not db.DB_PATH.exists():
        raise FileNotFoundError("قاعدة البيانات غير موجودة بعد.")

    with _backup_io_lock:
        ensure_disk_space()
        now = datetime.now()
        purpose = purpose if purpose in {p for p, _ in PURPOSE_CHOICES} else "manual"
        purpose_ar = dict(PURPOSE_CHOICES).get(purpose, purpose)
        folder_name = f"{now.strftime('%H%M%S')}__{purpose}__{_slug(label or purpose_ar)}"
        dest = backups_root() / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d") / folder_name
        dest.mkdir(parents=True, exist_ok=False)

        dest_db = dest / "rakaz.db"
        try:
            # نسخة آمنة أثناء التشغيل
            src = sqlite3.connect(str(db.DB_PATH))
            try:
                dst = sqlite3.connect(str(dest_db))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            validate_sqlite_file(dest_db)
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise

        progress = progress_snapshot()
        meta = {
            "id": f"{now.strftime('%Y/%m/%d')}/{folder_name}",
            "created_at": now.isoformat(timespec="seconds"),
            "purpose": purpose,
            "purpose_label": purpose_ar,
            "label": (label or "").strip(),
            "note": (note or "").strip(),
            "user_name": user_name or "نظام",
            "app": "rekaz",
            "path": str(dest.relative_to(data_root())).replace("\\", "/"),
            "db_file": "rakaz.db",
            "progress": progress,
        }
        (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # فهرس سريع لآخر الحفظات
        _append_index(meta)
        return meta


def _index_path() -> Path:
    return backups_root() / "index.jsonl"


def _append_index(meta: dict) -> None:
    line = json.dumps(
        {
            "id": meta["id"],
            "created_at": meta["created_at"],
            "purpose": meta["purpose"],
            "purpose_label": meta["purpose_label"],
            "label": meta["label"],
            "note": meta["note"],
            "user_name": meta["user_name"],
            "path": meta["path"],
            "tickets_total": meta["progress"].get("tickets_total", 0),
            "db_size_bytes": meta["progress"].get("db_size_bytes", 0),
            "sections": {k: v.get("count", 0) for k, v in (meta["progress"].get("sections") or {}).items()},
        },
        ensure_ascii=False,
    )
    with _index_path().open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def list_backups(limit: int = 100) -> list[dict]:
    """قائمة الحفظات من الأحدث للأقدم."""
    items: list[dict] = []
    root = backups_root()
    for meta_file in root.rglob("meta.json"):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["_dir"] = str(meta_file.parent)
            meta["_rel"] = str(meta_file.parent.relative_to(root)).replace("\\", "/")
            items.append(meta)
        except Exception:
            continue
    items.sort(key=lambda m: m.get("created_at") or "", reverse=True)
    return items[:limit]


def get_backup(rel_path: str) -> dict | None:
    rel_path = (rel_path or "").replace("\\", "/").strip().lstrip("/")
    if not rel_path or ".." in rel_path:
        return None
    folder = backups_root() / rel_path
    meta_file = folder / "meta.json"
    db_file = folder / "rakaz.db"
    if not meta_file.exists() or not db_file.exists():
        return None
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["_dir"] = str(folder)
    meta["_rel"] = rel_path
    meta["_db"] = str(db_file)
    return meta


def build_backup_zip(rel_path: str) -> Path:
    meta = get_backup(rel_path)
    if not meta:
        raise FileNotFoundError("الحفظة غير موجودة")
    folder = Path(meta["_dir"])
    db_file = folder / "rakaz.db"
    meta_file = folder / "meta.json"
    if not db_file.exists():
        raise FileNotFoundError("ملف قاعدة الحفظة غير موجود")
    # مجلد تنزيل منفصل حتى لا نعيد ضغط الملف أثناء الإرسال
    out_dir = backups_root() / "_download"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._\-]+", "-", rel_path.replace("/", "-")).strip("-")[:140]
    zip_path = out_dir / f"rekaz-backup-{safe_name or 'latest'}.zip"
    if zip_path.exists():
        try:
            zip_path.unlink()
        except OSError:
            pass
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.write(db_file, arcname="rakaz.db")
        if meta_file.exists():
            zf.write(meta_file, arcname="meta.json")
    return zip_path


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    """استخراج آمن يمنع Zip Slip."""
    dest = dest.resolve()
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if not name or name.endswith("/"):
            continue
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in Path(name).parts:
            raise ValueError(f"مسار غير آمن داخل ZIP: {info.filename}")
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest) + os.sep) and target != dest:
            raise ValueError(f"مسار غير آمن داخل ZIP: {info.filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def restore_backup(rel_path: str, *, user_name: str = "", confirm_phrase: str | None = None) -> dict:
    """استعادة ذرّية: تحقق سلامة → حفظة أمان → نسخ مؤقت → استبدال."""
    if confirm_phrase is not None and not confirm_restore_phrase(confirm_phrase):
        raise ValueError(f"للتأكيد اكتب كلمة «{RESTORE_CONFIRM_PHRASE}» حرفياً.")

    meta = get_backup(rel_path)
    if not meta:
        raise FileNotFoundError("الحفظة غير موجودة")

    src_db = Path(meta["_db"])
    validate_sqlite_file(src_db)

    with _backup_io_lock:
        ensure_disk_space(min_bytes=max(MIN_FREE_DISK_BYTES, src_db.stat().st_size * 3))
        safety = create_backup(
            label="قبل الاستعادة",
            note=f"حفظ أمان قبل استعادة: {meta.get('id')}",
            purpose="before_change",
            user_name=user_name or "نظام",
        )

        tmp = db.DB_PATH.with_suffix(".restore.tmp")
        bak = db.DB_PATH.with_suffix(".restore.bak")
        try:
            if tmp.exists():
                tmp.unlink()
            shutil.copy2(src_db, tmp)
            validate_sqlite_file(tmp)
            # احتفظ بنسخة لحظية من القاعدة الحية قبل الاستبدال
            if db.DB_PATH.exists():
                if bak.exists():
                    bak.unlink()
                shutil.copy2(db.DB_PATH, bak)
            tmp.replace(db.DB_PATH)
            db.ensure_schema()
            if bak.exists():
                try:
                    bak.unlink()
                except OSError:
                    pass
        except Exception:
            # حاول الرجوع من .bak إن فشلت الاستعادة بعد الاستبدال
            if bak.exists() and (not db.DB_PATH.exists() or db.DB_PATH.stat().st_size < 100):
                try:
                    bak.replace(db.DB_PATH)
                except Exception:
                    pass
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise

        return {"restored": meta, "safety": safety}


def import_backup_zip(file_storage, *, user_name: str = "", also_restore: bool = False, confirm_phrase: str | None = None) -> dict:
    """
    رفع حفظة ZIP (rakaz.db + meta.json اختيارياً) من جهاز محلي إلى السيرفر.
    مفيد في التجربة لإرجاع البيانات عند الحاجة.
    """
    raw = file_storage.read()
    if not raw:
        raise ValueError("الملف فارغ")
    if len(raw) > MAX_UPLOAD_ZIP_BYTES:
        raise ValueError(f"ملف ZIP أكبر من الحد المسموح ({human_size(MAX_UPLOAD_ZIP_BYTES)})")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        zip_path = tmp / "upload.zip"
        zip_path.write_bytes(raw)
        extract_dir = tmp / "extracted"
        extract_dir.mkdir()
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                if zf.testzip() is not None:
                    raise ValueError("ملف ZIP تالف")
                _safe_extract_zip(zf, extract_dir)
        except zipfile.BadZipFile as exc:
            raise ValueError("الملف ليس ZIP صالحاً") from exc

        db_file = None
        for candidate in extract_dir.rglob("rakaz.db"):
            db_file = candidate
            break
        if not db_file or not db_file.exists():
            raise ValueError("ملف ZIP لا يحتوي على rakaz.db")

        try:
            validate_sqlite_file(db_file)
        except Exception as exc:
            raise ValueError(f"ملف قاعدة غير صالح: {exc}") from exc

        ensure_disk_space(min_bytes=max(MIN_FREE_DISK_BYTES, db_file.stat().st_size * 3))
        now = datetime.now()
        folder_name = f"{now.strftime('%H%M%S')}__trial__رفع-من-جهاز"
        dest = backups_root() / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d") / folder_name
        dest.mkdir(parents=True, exist_ok=False)
        shutil.copy2(db_file, dest / "rakaz.db")

        uploaded_meta = None
        for candidate in extract_dir.rglob("meta.json"):
            try:
                uploaded_meta = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                uploaded_meta = None
            break

        tmp_conn = sqlite3.connect(str(dest / "rakaz.db"))
        tmp_conn.row_factory = sqlite3.Row
        try:
            progress = {
                "tickets_total": int(tmp_conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
                if tmp_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='tickets'"
                ).fetchone()
                else 0,
                "db_size_bytes": (dest / "rakaz.db").stat().st_size,
                "sections": {},
                "modules": {},
                "tickets_by_status": {},
            }
        finally:
            tmp_conn.close()

        meta = {
            "id": f"{now.strftime('%Y/%m/%d')}/{folder_name}",
            "created_at": now.isoformat(timespec="seconds"),
            "purpose": "trial",
            "purpose_label": "حفظ تجربة سحابية",
            "label": (uploaded_meta or {}).get("label") or "رفع من جهاز",
            "note": (uploaded_meta or {}).get("note")
            or "حفظة مرفوعة من جهاز العميل أثناء التجربة",
            "user_name": user_name or "نظام",
            "app": "rekaz",
            "path": str(dest.relative_to(data_root())).replace("\\", "/"),
            "db_file": "rakaz.db",
            "progress": progress,
            "imported_from_zip": True,
        }
        (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        _append_index(meta)

        result = {"imported": meta, "restored": None}
        if also_restore:
            result["restored"] = restore_backup(
                str(dest.relative_to(backups_root())).replace("\\", "/"),
                user_name=user_name,
                confirm_phrase=confirm_phrase if confirm_phrase is not None else RESTORE_CONFIRM_PHRASE,
            )
        return result


def progress_timeline(limit: int = 30) -> list[dict]:
    """خط زمني مبسّط لتقدّم العمل من الحفظات."""
    rows = []
    for item in list_backups(limit=limit):
        prog = item.get("progress") or {}
        rows.append(
            {
                "id": item.get("id"),
                "created_at": item.get("created_at"),
                "purpose_label": item.get("purpose_label"),
                "label": item.get("label"),
                "note": item.get("note"),
                "user_name": item.get("user_name"),
                "tickets_total": prog.get("tickets_total", 0),
                "sections": {k: v.get("count", 0) for k, v in (prog.get("sections") or {}).items()},
                "tickets_by_status": prog.get("tickets_by_status") or {},
                "rel": item.get("_rel"),
                "db_size_bytes": prog.get("db_size_bytes", 0),
            }
        )
    return rows


def human_size(n: int) -> str:
    n = float(n or 0)
    for unit in ("بايت", "ك.ب", "م.ب", "ج.ب"):
        if n < 1024 or unit == "ج.ب":
            return f"{n:.0f} {unit}" if unit == "بايت" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ج.ب"


def _auto_state_path() -> Path:
    return backups_root() / ".auto_state.json"


def load_auto_state() -> dict:
    path = _auto_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_auto_state(state: dict) -> None:
    path = _auto_state_path()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def auto_interval_minutes() -> int:
    raw = os.environ.get("AUTO_BACKUP_INTERVAL_MINUTES", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    hours = os.environ.get("AUTO_BACKUP_HOURS", "").strip()
    if hours.isdigit() and int(hours) > 0:
        return int(hours) * 60
    # افتراضي: كل 6 ساعات
    return 6 * 60


def auto_backup_enabled() -> bool:
    flag = os.environ.get("AUTO_BACKUP", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def get_sync_token() -> str:
    env = os.environ.get("BACKUP_SYNC_TOKEN", "").strip()
    if env:
        return env
    settings = db.get_settings()
    token = str(settings.get("backup_sync_token") or "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(24)
    db.save_settings({"backup_sync_token": token})
    return token


def regenerate_sync_token() -> str:
    token = secrets.token_urlsafe(24)
    db.save_settings({"backup_sync_token": token})
    return token


def token_matches(provided: str | None) -> bool:
    expected = get_sync_token()
    if not expected or not provided:
        return False
    return secrets.compare_digest(str(provided).strip(), expected)


def latest_backup(purpose: str | None = None) -> dict | None:
    for item in list_backups(limit=200):
        if purpose and item.get("purpose") != purpose:
            continue
        return item
    return None


def s3_configured() -> bool:
    return bool(
        os.environ.get("AWS_S3_BUCKET", "").strip()
        and os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
        and os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
    )


def s3_settings() -> dict:
    return {
        "bucket": os.environ.get("AWS_S3_BUCKET", "").strip(),
        "region": os.environ.get("AWS_S3_REGION", "").strip() or "eu-north-1",
        "prefix": (os.environ.get("AWS_S3_PREFIX", "").strip() or "rekaz-backups").strip("/"),
        "configured": s3_configured(),
    }


def _s3_client():
    import boto3

    cfg = s3_settings()
    return boto3.client(
        "s3",
        region_name=cfg["region"],
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "").strip(),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip(),
    )


def probe_aws_link() -> dict:
    """فحص ربط Render ↔ Amazon S3."""
    cfg = s3_settings()
    if not cfg["configured"]:
        return {
            "ok": False,
            "linked": False,
            "provider": "aws-s3",
            "reason": "مفاتيح AWS أو اسم الـ Bucket غير مضبوطة في بيئة التشغيل",
            "s3": cfg,
        }
    try:
        client = _s3_client()
        client.head_bucket(Bucket=cfg["bucket"])
        listed = list_s3_backups(limit=3)
        return {
            "ok": True,
            "linked": True,
            "provider": "aws-s3",
            "s3": cfg,
            "objects_sample": len(listed),
            "latest_key": (listed[0]["key"] if listed else None),
            "latest_modified": (listed[0]["last_modified"] if listed else None),
        }
    except Exception as exc:
        return {
            "ok": False,
            "linked": False,
            "provider": "aws-s3",
            "s3": cfg,
            "error": str(exc),
        }


def list_s3_backups(limit: int = 40) -> list[dict]:
    """قائمة حفظات Amazon S3 من الأحدث للأقدم.

    مهم: list_objects_v2 يرتّب حسب المفتاح (أبجدياً) وليس LastModified.
    مع بادئة وقت ISO يأتي الأقدم أولاً — لذا يجب جلب كل الكائنات ثم الفرز،
    وإلا استعادة الإقلاع تختار أقدم حفظة وتمسح بيانات أحدث.
    """
    result = list_s3_backups_detailed(limit=limit)
    return result.get("items") or []


def list_s3_backups_detailed(limit: int = 40) -> dict:
    """مثل list_s3_backups مع رسالة خطأ واضحة عند الفشل."""
    if not s3_configured():
        return {"items": [], "ok": False, "error": "S3 غير مضبوط"}
    cfg = s3_settings()
    try:
        try:
            import boto3  # noqa: F401
        except ImportError:
            return {"items": [], "ok": False, "error": "حزمة boto3 غير مثبتة على السيرفر"}
        client = _s3_client()
        prefix = f"{cfg['prefix']}/"
        items = []
        token = None
        while True:
            kwargs: dict = {
                "Bucket": cfg["bucket"],
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if token:
                kwargs["ContinuationToken"] = token
            resp = client.list_objects_v2(**kwargs)
            for obj in resp.get("Contents") or []:
                key = obj.get("Key") or ""
                if not key.endswith(".zip"):
                    continue
                lm = obj.get("LastModified")
                items.append(
                    {
                        "key": key,
                        "name": key.split("/")[-1],
                        "size": int(obj.get("Size") or 0),
                        "size_label": human_size(int(obj.get("Size") or 0)),
                        "last_modified": lm.isoformat() if hasattr(lm, "isoformat") else str(lm or ""),
                        "uri": f"s3://{cfg['bucket']}/{key}",
                    }
                )
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
            if not token:
                break
        items.sort(key=lambda x: x.get("last_modified") or "", reverse=True)
        trimmed = items[: max(1, min(int(limit or 40), 500))]
        return {"items": trimmed, "ok": True, "total": len(items)}
    except Exception as exc:
        _record_backup_error(f"تعذر قراءة أرشيف S3: {exc}")
        return {"items": [], "ok": False, "error": str(exc)}


def _backup_log_path() -> Path:
    return backups_root() / ".auto_backup.log"


def append_backup_log(message: str, *, level: str = "INFO") -> None:
    """سجل تشغيلي صامت تحت مجلد الحفظات (بدون واجهة)."""
    line = f"{datetime.now().isoformat(timespec='seconds')} [{level}] {message}"
    try:
        with _backup_log_path().open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if level == "ERROR":
        log.error(message)
    elif level == "WARNING":
        log.warning(message)
    else:
        log.info(message)


def _record_backup_error(message: str) -> None:
    try:
        state = load_auto_state()
        state["last_error"] = str(message)[:500]
        state["last_error_at"] = datetime.now().isoformat(timespec="seconds")
        save_auto_state(state)
    except Exception:
        pass
    append_backup_log(str(message), level="ERROR")


def prune_local_backups(keep: int | None = None) -> dict:
    """الإبقاء على أحدث N حفظات محلية وحذف الأقدم بأمان."""
    keep = int(keep if keep is not None else local_keep_count())
    keep = max(5, keep)
    items = list_backups(limit=5000)
    if len(items) <= keep:
        return {"ok": True, "kept": len(items), "deleted": 0}
    to_delete = items[keep:]
    deleted = 0
    errors = []
    root = backups_root().resolve()
    for meta in to_delete:
        try:
            folder = Path(meta.get("_dir") or "")
            if not folder.exists():
                continue
            folder = folder.resolve()
            if not str(folder).startswith(str(root) + os.sep):
                continue
            # لا تحذف مجلدات النظام
            if folder.name.startswith("_") or folder.name.startswith("."):
                continue
            shutil.rmtree(folder, ignore_errors=False)
            deleted += 1
        except Exception as exc:
            errors.append(str(exc))
    # نظّف المجلدات اليومية الفارغة بلطف
    try:
        for year_dir in sorted(root.glob("20*")):
            if not year_dir.is_dir():
                continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir():
                    continue
                for day_dir in month_dir.iterdir():
                    if day_dir.is_dir() and not any(day_dir.iterdir()):
                        day_dir.rmdir()
                if month_dir.is_dir() and not any(month_dir.iterdir()):
                    month_dir.rmdir()
            if year_dir.is_dir() and not any(year_dir.iterdir()):
                year_dir.rmdir()
    except Exception:
        pass
    return {"ok": not errors, "kept": keep, "deleted": deleted, "errors": errors[:5]}


def prune_s3_backups(keep: int | None = None) -> dict:
    """حذف أقدم حفظات S3 مع الإبقاء على أحدث N."""
    if not s3_configured():
        return {"ok": True, "skipped": True, "reason": "S3 غير مضبوط"}
    keep = int(keep if keep is not None else s3_keep_count())
    keep = max(5, keep)
    detailed = list_s3_backups_detailed(limit=500)
    items = detailed.get("items") or []
    if not detailed.get("ok"):
        return {"ok": False, "error": detailed.get("error"), "deleted": 0}
    if len(items) <= keep:
        return {"ok": True, "kept": len(items), "deleted": 0}
    to_delete = items[keep:]
    deleted = 0
    errors = []
    cfg = s3_settings()
    try:
        client = _s3_client()
        # احذف على دفعات صغيرة
        batch: list[dict] = []
        for obj in to_delete:
            batch.append({"Key": obj["key"]})
            if len(batch) >= 50:
                resp = client.delete_objects(Bucket=cfg["bucket"], Delete={"Objects": batch, "Quiet": True})
                deleted += len(batch) - len(resp.get("Errors") or [])
                for err in resp.get("Errors") or []:
                    errors.append(str(err))
                batch = []
        if batch:
            resp = client.delete_objects(Bucket=cfg["bucket"], Delete={"Objects": batch, "Quiet": True})
            deleted += len(batch) - len(resp.get("Errors") or [])
            for err in resp.get("Errors") or []:
                errors.append(str(err))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "deleted": deleted}
    return {"ok": not errors, "kept": keep, "deleted": deleted, "errors": errors[:5]}


def apply_retention() -> dict:
    """تطبيق سياسة الاحتفاظ محلياً وعلى S3."""
    local = prune_local_backups()
    remote = prune_s3_backups()
    state = load_auto_state()
    state["last_prune"] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "local": local,
        "s3": remote,
    }
    save_auto_state(state)
    return {"local": local, "s3": remote}


def download_s3_backup(key: str) -> Path:
    """تنزيل حفظة من S3 إلى مجلد مؤقت محلي."""
    key = (key or "").strip()
    if not key or ".." in key or not key.endswith(".zip"):
        raise ValueError("مفتاح S3 غير صالح")
    cfg = s3_settings()
    if not key.startswith(cfg["prefix"] + "/") and key != cfg["prefix"]:
        # اسمح فقط داخل بادئة الحفظات
        raise ValueError("المفتاح خارج مجلد الحفظات")
    dest_dir = backups_root() / "_s3_inbox"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(key).name
    client = _s3_client()
    client.download_file(cfg["bucket"], key, str(dest))
    return dest


def restore_from_s3(key: str, *, user_name: str = "نظام AWS") -> dict:
    """استعادة قاعدة البيانات من حفظة موجودة على Amazon S3."""
    zip_path = download_s3_backup(key)

    class _ZipFile:
        def __init__(self, path: Path):
            self._path = path
            self.filename = path.name

        def read(self):
            return self._path.read_bytes()

    imported = import_backup_zip(_ZipFile(zip_path), user_name=user_name, also_restore=True)
    state = load_auto_state()
    state["last_s3_restore"] = {
        "key": key,
        "at": datetime.now().isoformat(timespec="seconds"),
        "user": user_name,
        "backup_id": (imported.get("imported") or {}).get("id"),
    }
    save_auto_state(state)
    return imported


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def local_db_is_blank_or_seed() -> bool:
    """
    True إذا كانت القاعدة غير موجودة أو مجرّد بذرة init_db بدون عمل مستخدم.
    مهم: لا نعتمد على حجم الملف — البذرة وحدها ~180 ك.ب وقد تمنع الاستعادة خطأً.
    """
    if not db.DB_PATH.exists():
        return True
    conn = db.connect()
    try:
        tickets = _table_count(conn, "tickets")
        if tickets == 0:
            return True
        if tickets == 1:
            row = conn.execute("SELECT ticket_no FROM tickets LIMIT 1").fetchone()
            if row and str(row[0]) == SEED_TICKET_NO:
                extras = (
                    _table_count(conn, "quantities")
                    + _table_count(conn, "warehouse_tx")
                    + _table_count(conn, "photos")
                    + _table_count(conn, "metering")
                    + _table_count(conn, "invoices")
                    + _table_count(conn, "projects")
                    + _table_count(conn, "contract_boq_items")
                    + _table_count(conn, "contractor_works")
                    + _table_count(conn, "hr_employees")
                    + _table_count(conn, "audit_log")
                )
                return extras == 0
        return False
    finally:
        conn.close()


def maybe_restore_from_s3_on_boot() -> dict:
    """
    عند الإقلاع: إن كانت القاعدة فارغة/بذرة وS3 فيه حفظات،
    استعد أحدث نسخة تلقائياً (Newest by LastModified عبر list_s3_backups).
    لا نستبدل قاعدة فيها عمل مستخدم حقيقي — حتى لا نمسح تعديلات لم تُرفع بعد.
    """
    flag = os.environ.get("AWS_S3_AUTO_RESTORE", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return {"restored": False, "skipped": True, "reason": "الاستعادة التلقائية متوقفة"}
    if not s3_configured():
        return {"restored": False, "skipped": True, "reason": "S3 غير مربوط"}

    if not local_db_is_blank_or_seed():
        return {"restored": False, "skipped": True, "reason": "القاعدة تحتوي بيانات"}

    remote = list_s3_backups(limit=1)
    if not remote:
        return {"restored": False, "skipped": True, "reason": "لا توجد حفظات على S3"}

    key = remote[0]["key"]
    try:
        result = restore_from_s3(key, user_name="إقلاع تلقائي←AWS")
        db.ensure_schema()
        return {"restored": True, "key": key, "result": result}
    except Exception as exc:
        return {"restored": False, "error": str(exc), "key": key}


def upload_backup_to_s3(meta: dict, zip_path: Path) -> dict:
    """رفع الحفظة تلقائياً إلى Amazon S3 (بديل الجهاز الدائم)."""
    if not s3_configured():
        return {"ok": False, "skipped": True, "reason": "S3 غير مُعد"}
    try:
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return {"ok": False, "error": "حزمة boto3 غير مثبتة"}

    cfg = s3_settings()
    stamp = (meta.get("created_at") or datetime.now().isoformat(timespec="seconds"))
    stamp = re.sub(r"[^0-9T\-]", "-", stamp.replace(":", "-"))[:40]
    bid = re.sub(r"[^A-Za-z0-9_./\-]", "-", str(meta.get("id") or "backup")).replace("/", "-")
    bid = re.sub(r"-+", "-", bid).strip("-")[:120] or "backup"
    key = f"{cfg['prefix']}/{stamp}__{bid}.zip"
    try:
        client = _s3_client()
        # S3 metadata يجب أن تكون ASCII فقط
        bid_raw = str(meta.get("id") or "")
        bid_ascii = re.sub(r"[^A-Za-z0-9_./\-]", "-", bid_raw)[:200]
        stamp_ascii = re.sub(r"[^0-9T\-]", "", str(meta.get("created_at") or ""))[:32]
        extra = {
            "ContentType": "application/zip",
            "Metadata": {
                "app": "rekaz",
                "backup-id": bid_ascii or "backup",
                "created-at": stamp_ascii or "unknown",
                "tickets": str((meta.get("progress") or {}).get("tickets_total", 0)),
            },
        }
        client.upload_file(str(zip_path), cfg["bucket"], key, ExtraArgs=extra)
        state = load_auto_state()
        state["last_s3_upload"] = {
            "key": key,
            "at": datetime.now().isoformat(timespec="seconds"),
            "uri": f"s3://{cfg['bucket']}/{key}",
            "size": zip_path.stat().st_size if zip_path.exists() else 0,
        }
        save_auto_state(state)
        return {
            "ok": True,
            "bucket": cfg["bucket"],
            "region": cfg["region"],
            "key": key,
            "uri": f"s3://{cfg['bucket']}/{key}",
            "size": zip_path.stat().st_size if zip_path.exists() else 0,
        }
    except (BotoCoreError, ClientError, Exception) as exc:
        return {"ok": False, "error": str(exc)}


def post_backup_webhook(meta: dict, download_url: str = "") -> dict:
    url = os.environ.get("BACKUP_WEBHOOK_URL", "").strip()
    if not url:
        return {"ok": False, "skipped": True, "reason": "لا يوجد webhook"}
    payload = json.dumps(
        {
            "app": "rekaz",
            "event": "backup.created",
            "backup_id": meta.get("id"),
            "created_at": meta.get("created_at"),
            "purpose": meta.get("purpose"),
            "tickets_total": (meta.get("progress") or {}).get("tickets_total", 0),
            "download_url": download_url,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"ok": True, "status": getattr(resp, "status", 200)}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": str(exc)}


def deliver_backup(meta: dict, *, download_url: str = "") -> dict:
    """إرسال الحفظة: S3 أولاً + webhook اختياري + جاهز للسحب المحلي."""
    result = {"s3": None, "webhook": None, "ready_for_pull": True}
    if not meta.get("_rel"):
        path = meta.get("path") or ""
        if path.startswith("backups/"):
            rel = path[len("backups/") :]
        else:
            rel = path
        meta["_rel"] = rel
    try:
        zip_path = build_backup_zip(meta["_rel"])
    except Exception as exc:
        return {"ready_for_pull": False, "error": str(exc)}

    result["s3"] = upload_backup_to_s3(meta, zip_path)
    result["webhook"] = post_backup_webhook(meta, download_url=download_url)
    result["zip_size"] = zip_path.stat().st_size if zip_path.exists() else 0
    return result


def create_auto_backup(*, force: bool = False, user_name: str = "نظام تلقائي") -> dict:
    """
    إنشاء حفظة تلقائية إن حان موعدها، رفعها إلى S3 عند التوفر، ثم تقليم الأرشيف.
    """
    with _auto_lock:
        state = load_auto_state()
        now = datetime.now()
        interval = auto_interval_minutes()
        last_raw = state.get("last_backup_at")
        if not force and last_raw:
            try:
                last = datetime.fromisoformat(last_raw)
                if now - last < timedelta(minutes=interval):
                    return {
                        "created": False,
                        "skipped": True,
                        "reason": "لم يحن موعد الحفظ بعد",
                        "next_due_minutes": max(
                            0, int(interval - (now - last).total_seconds() / 60)
                        ),
                        "last_backup_at": last_raw,
                        "latest": latest_backup(),
                    }
            except Exception:
                pass

        if not db.DB_PATH.exists():
            append_backup_log("تعذر الحفظ: قاعدة البيانات غير موجودة", level="ERROR")
            return {"created": False, "error": "قاعدة البيانات غير موجودة"}

        purpose = "auto"
        try:
            meta = create_backup(
                label="حفظ تلقائي",
                note=f"حفظ تلقائي كل {interval} دقيقة — محلي + S3 عند التوفر",
                purpose=purpose,
                user_name=user_name,
            )
        except Exception as exc:
            append_backup_log(f"فشل إنشاء الحفظة: {exc}", level="ERROR")
            raise
        meta["_rel"] = backup_rel_from_meta(meta)
        delivery = deliver_backup(meta)
        prune = {}
        try:
            prune = apply_retention()
        except Exception as exc:
            append_backup_log(f"فشل تقليم الأرشيف: {exc}", level="WARNING")
            prune = {"error": str(exc)}
        # أعد التحميل بعد الرفع حتى لا نمسح last_s3_upload الذي كتبه upload_backup_to_s3
        state = load_auto_state()
        state.update(
            {
                "last_backup_at": now.isoformat(timespec="seconds"),
                "last_backup_id": meta.get("id"),
                "last_backup_rel": meta.get("_rel"),
                "last_delivery": delivery,
                "interval_minutes": interval,
                "last_prune": prune,
            }
        )
        s3_info = (delivery or {}).get("s3") or {}
        if s3_info.get("ok") and s3_info.get("key"):
            state["last_s3_upload"] = {
                "key": s3_info.get("key"),
                "at": now.isoformat(timespec="seconds"),
                "uri": s3_info.get("uri"),
                "size": s3_info.get("size") or 0,
            }
            append_backup_log(
                f"حفظة {meta.get('id')} — محلي + S3={s3_info.get('key')} "
                f"| prune local={(prune.get('local') or {}).get('deleted', 0)} "
                f"s3={(prune.get('s3') or {}).get('deleted', 0)}"
            )
        else:
            reason = s3_info.get("error") or s3_info.get("reason") or "بدون S3"
            append_backup_log(
                f"حفظة {meta.get('id')} — محلي فقط ({reason}) "
                f"| prune local={(prune.get('local') or {}).get('deleted', 0)}",
                level="WARNING" if s3_configured() else "INFO",
            )
            if s3_configured() and not s3_info.get("ok"):
                state["last_error"] = f"S3: {reason}"
                state["last_error_at"] = now.isoformat(timespec="seconds")
        save_auto_state(state)
        return {
            "created": True,
            "backup": meta,
            "delivery": delivery,
            "prune": prune,
            "interval_minutes": interval,
        }


def auto_status(*, include_s3_list: bool = False) -> dict:
    state = load_auto_state()
    latest = latest_backup(purpose="auto") or latest_backup()
    interval = auto_interval_minutes()
    next_due = None
    last_raw = state.get("last_backup_at")
    if last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
            due = last + timedelta(minutes=interval)
            next_due = max(0, int((due - datetime.now()).total_seconds() / 60))
        except Exception:
            next_due = None
    aws = probe_aws_link()
    s3_items = list_s3_backups(limit=15) if include_s3_list and s3_configured() else []
    return {
        "enabled": auto_backup_enabled(),
        "interval_minutes": interval,
        "last_backup_at": last_raw,
        "last_backup_id": state.get("last_backup_id"),
        "last_backup_rel": state.get("last_backup_rel"),
        "next_due_minutes": next_due,
        "s3_configured": s3_configured(),
        "s3": s3_settings(),
        "aws_link": aws,
        "s3_backups": s3_items,
        "last_s3_upload": state.get("last_s3_upload"),
        "last_s3_restore": state.get("last_s3_restore"),
        "last_prune": state.get("last_prune"),
        "last_error": state.get("last_error"),
        "last_error_at": state.get("last_error_at"),
        "webhook_configured": bool(os.environ.get("BACKUP_WEBHOOK_URL", "").strip()),
        "sync_token_ready": bool(get_sync_token()),
        "latest": {
            "id": (latest or {}).get("id"),
            "created_at": (latest or {}).get("created_at"),
            "purpose": (latest or {}).get("purpose"),
            "rel": (latest or {}).get("_rel"),
            "tickets_total": ((latest or {}).get("progress") or {}).get("tickets_total"),
        }
        if latest
        else None,
        "last_delivery": state.get("last_delivery"),
    }


def start_auto_backup_scheduler(app=None) -> None:
    """خيط خلفي ينشئ حفظة عند حلول الموعد (يعمل مع Waitress أيضاً)."""
    global _scheduler_started
    if _scheduler_started or not auto_backup_enabled():
        return
    _scheduler_started = True

    def _loop():
        # أول تشغيل بعد دقيقة من الإقلاع
        import time

        append_backup_log("بدأ مجدول الحفظ التلقائي")
        time.sleep(60)
        while True:
            try:
                if auto_backup_enabled():
                    if app is not None:
                        with app.app_context():
                            create_auto_backup(force=False)
                    else:
                        create_auto_backup(force=False)
            except Exception as exc:
                try:
                    state = load_auto_state()
                    state["last_error"] = str(exc)
                    state["last_error_at"] = datetime.now().isoformat(timespec="seconds")
                    save_auto_state(state)
                except Exception:
                    pass
                append_backup_log(f"خطأ في دورة الحفظ التلقائي: {exc}", level="ERROR")
            # افحص كل 5 دقائق
            time.sleep(300)

    t = threading.Thread(target=_loop, name="rekaz-auto-backup", daemon=True)
    t.start()


def activity_backup_delay_seconds() -> int:
    """مهلة التجميع قبل الرفع بعد تعديل. على الاستضافة المؤقتة تُختصر حتى لا تُفقد البيانات."""
    raw_s = os.environ.get("AUTO_BACKUP_ACTIVITY_SECONDS", "").strip()
    if raw_s.isdigit():
        delay = max(5, int(raw_s))
    else:
        raw_m = os.environ.get("AUTO_BACKUP_ACTIVITY_MINUTES", "").strip()
        if raw_m.isdigit():
            delay = max(5, int(raw_m) * 60)
        elif is_ephemeral_hosting() or is_cloud():
            delay = 45
        else:
            delay = 30 * 60
    # قرص غير دائم: ارفع خلال 90 ثانية كحد أقصى
    if is_ephemeral_hosting():
        delay = min(delay, 90)
    return delay


def backup_rel_from_meta(meta: dict) -> str:
    """استخراج المسار النسبي للحفظة داخل مجلد backups."""
    if meta.get("_rel"):
        return str(meta["_rel"]).replace("\\", "/")
    path = str(meta.get("path") or "").replace("\\", "/")
    if path.startswith("backups/"):
        return path[len("backups/") :]
    return path


def silent_backup_after_change() -> None:
    """حفظ صامت Debounced بعد تعديل — يرفع إلى S3 عند توفر المفاتيح."""
    if not auto_backup_enabled():
        return

    delay = activity_backup_delay_seconds()

    def _run():
        try:
            create_auto_backup(force=True, user_name="حفظ بعد تعديل")
        except Exception as exc:
            try:
                state = load_auto_state()
                state["last_error"] = str(exc)
                state["last_error_at"] = datetime.now().isoformat(timespec="seconds")
                save_auto_state(state)
            except Exception:
                pass

    global _silent_timer
    with _silent_lock:
        if _silent_timer is not None:
            try:
                _silent_timer.cancel()
            except Exception:
                pass
        _silent_timer = threading.Timer(float(delay), _run)
        _silent_timer.daemon = True
        _silent_timer.name = "rekaz-silent-backup"
        _silent_timer.start()
