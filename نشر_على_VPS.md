# نشر نظام ركاز على VPS (الاستضافة الرسمية)

**الرابط الرسمي:** https://report.ralenjaz.com

| البند | القيمة |
|-------|--------|
| السيرفر | نفس VPS الخاص بـ RTCREPORT (`191.101.2.59`) — دون المساس بـ `/opt/weeklyreport` |
| مسار التطبيق | `/opt/rekaz` |
| قاعدة البيانات | `/opt/rekaz/data/rakaz.db` (`RAKAZ_DATA_DIR=/opt/rekaz/data`) |
| الخدمة | `systemd` → `rekaz.service` (Waitress على `127.0.0.1:8010`) |
| الواجهة | nginx + Let's Encrypt → `report.ralenjaz.com` (ملف موقع منفصل عن `report.rtcco.org`) |
| الدومين القديم | `rekaz.wadnooh.com` → تحويل 301 إلى الرابط الرسمي |
| النسخ الاحتياطي | محلي تحت `/opt/rekaz/data/backups` + رفع تلقائي إلى Amazon S3 |

المستودع: https://github.com/wadnooh/RKAZ

---

## فحص سريع بعد النشر

```bash
curl -sS https://report.ralenjaz.com/health
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

لا تلمس `/opt/weeklyreport` ولا خدمة `weeklyreport` ولا موقع nginx `report.rtcco.org`.

---

## متغيرات البيئة (`/etc/rekaz.env`)

أهم المفاتيح (القيم السرية تُدار على السيرفر فقط):

```text
SECRET_KEY=...
RAKAZ_DATA_DIR=/opt/rekaz/data
RAKAZ_CLOUD=1
TRIAL_MODE=1
SESSION_COOKIE_SECURE=1
FORCE_HTTPS=1
PREFERRED_URL_SCHEME=https
APP_BASE_URL=https://report.ralenjaz.com
AUTO_BACKUP=1
AUTO_BACKUP_HOURS=2
AUTO_BACKUP_ACTIVITY_SECONDS=45
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=rekaz-alenjaz-backups
AWS_S3_REGION=eu-north-1
AWS_S3_PREFIX=rekaz-backups
AWS_S3_AUTO_RESTORE=1
PROGRAMMER_BOOTSTRAP_CODE=...   # تسجيل الجهاز الرئيسي للمبرمج
PROGRAMMER_CHANGE_PIN=...       # تحقق صارم من جهاز ثانوي
PROGRAMMER_EMAILS=wadnooh@gmail.com,wadnooh@wadnooh.com
SMTP_HOST=...
SMTP_PORT=...
SMTP_USER=...
SMTP_PASS=...
SMTP_FROM=...
```

`TRIAL_MODE=1` لا يلغي ثبات القرص (`RAKAZ_DATA_DIR`). الحفظ تلقائي صامت بدون واجهة للمستخدم.
بعد الاعتماد النهائي يمكن ضبط `TRIAL_MODE=0`.

حماية التحكم الإداري (جهاز المبرمج + OTP بريد): انظر [`للعميل/حماية_جهاز_المبرمج.md`](للعميل/حماية_جهاز_المبرمج.md).
توليد رمز موافقة احتياطي (عند تعذّر البريد):

```bash
cd /opt/rekaz
sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/programmer_approve.py
```

إعادة تعيين الأجهزة عند القفل:

```bash
sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/programmer_reset_devices.py --yes
```

---

## استعادة طارئة (CLI — بدون واجهة)

```bash
cd /opt/rekaz
sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/restore_backup.py --list
sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/restore_backup.py --list-s3
sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/restore_backup.py --export-zip /tmp/rekaz-latest.zip
sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/restore_backup.py --s3-latest --yes
```

سجل الحفظ التلقائي: `/opt/rekaz/data/backups/.auto_backup.log`

---

## DNS

| النوع | الاسم | القيمة | TTL |
|-------|--------|--------|-----|
| A | `report` | `191.101.2.59` | 300 أو الافتراضي |

النطاق `ralenjaz.com` يُدار من حساب Hostinger الخاص بالعميل (ليس حساب `wadnooh.com`).
بعد انتشار DNS نفّذ على السيرفر:

```bash
bash /opt/rekaz/tools/finish_report_ralenjaz_ssl.sh
```

هذا يُصدر شهادة Let's Encrypt ويحوّل `rekaz.wadnooh.com` إلى `report.ralenjaz.com`.

---

## Render (متقاعد)

الاستضافة انتقلت عن Render. الخدمة القديمة `rekaz-alenjaz` معلّقة ومُعطّل عليها النشر التلقائي.
الرابط القديم `https://rekaz-alenjaz.onrender.com` لم يعد رسمياً.

مرجع أرشيف فقط: [`نشر_على_Render.md`](نشر_على_Render.md)
