# نشر نظام ركاز على VPS (الاستضافة الرسمية)

**الرابط الرسمي:** https://rekaz.wadnooh.com

| البند | القيمة |
|-------|--------|
| السيرفر | نفس VPS الخاص بـ RTCREPORT (`191.101.2.59`) — دون المساس بـ `/opt/weeklyreport` |
| مسار التطبيق | `/opt/rekaz` |
| قاعدة البيانات | `/opt/rekaz/data/rakaz.db` (`RAKAZ_DATA_DIR=/opt/rekaz/data`) |
| الخدمة | `systemd` → `rekaz.service` (Waitress على `127.0.0.1:8010`) |
| الواجهة | nginx + Let's Encrypt → `rekaz.wadnooh.com` |
| النسخ الاحتياطي | محلي تحت `/opt/rekaz/data/backups` + رفع تلقائي إلى Amazon S3 |

المستودع: https://github.com/wadnooh/RKAZ

---

## فحص سريع بعد النشر

```bash
curl -sS https://rekaz.wadnooh.com/health
systemctl status rekaz --no-pager
nginx -t
```

تأكد من `/health`:

- `ok` = true
- `storage.data_persistent` = true
- `storage.db_path` = `/opt/rekaz/data/rakaz.db`
- `aws.ok` = true و`auto_backup.last_s3_ok` = true

---

## تحديث الكود على السيرفر

```bash
cd /opt/rekaz
sudo -u rekazapp git pull origin main
sudo -u rekazapp /opt/rekaz/.venv/bin/pip install -r requirements.txt
sudo systemctl restart rekaz
```

لا تلمس `/opt/weeklyreport` ولا خدمة `weeklyreport`.

---

## متغيرات البيئة (`/etc/rekaz.env`)

أهم المفاتيح (القيم السرية تُدار على السيرفر فقط):

```text
SECRET_KEY=...
RAKAZ_DATA_DIR=/opt/rekaz/data
RAKAZ_CLOUD=1
TRIAL_MODE=0
SESSION_COOKIE_SECURE=1
FORCE_HTTPS=1
AUTO_BACKUP=1
AUTO_BACKUP_HOURS=2
AUTO_BACKUP_ACTIVITY_SECONDS=45
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=rekaz-alenjaz-backups
AWS_S3_REGION=eu-north-1
AWS_S3_PREFIX=rekaz-backups
AWS_S3_AUTO_RESTORE=1
```

---

## DNS

سجل A: `rekaz.wadnooh.com` → IP الـ VPS.

---

## Render (متقاعد)

الاستضافة انتقلت عن Render. الخدمة القديمة `rekaz-alenjaz` معلّقة ومُعطّل عليها النشر التلقائي.
الرابط القديم `https://rekaz-alenjaz.onrender.com` لم يعد رسمياً.

مرجع أرشيف فقط: [`نشر_على_Render.md`](نشر_على_Render.md)
