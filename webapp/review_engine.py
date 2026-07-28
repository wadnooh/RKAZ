"""وجهات القفز السريع وخدمات مساعدة خفيفة للعمليات."""

from __future__ import annotations


def jump_destinations():
    """قائمة وجهات نافذة القفز السريع."""
    return [
        {"title": "الإنشاءات - التنفيذ", "path": "/constructions", "group": "أقسام", "keywords": "إنشاءات"},
        {"title": "لوحة العمليات", "path": "/ops", "group": "عمليات", "keywords": "لوحة dashboard ops"},
        {"title": "الأعطال", "path": "/tickets", "group": "عمليات", "keywords": "عطل أعطال ticket fault طوارئ"},
        {"title": "عطل جديد", "path": "/tickets/new", "group": "عمليات", "keywords": "إضافة عطل جديد"},
        {"title": "فرق المهام", "path": "/teams", "group": "عمليات", "keywords": "فرقة فريق"},
        {"title": "إجراءات العمل SOP", "path": "/sop", "group": "عمليات", "keywords": "sop إجراء"},
        {"title": "الكميات (من داخل العطل)", "path": "/module/quantities", "group": "سجلات مرتبطة", "keywords": "كميات boq"},
        {"title": "قائمة الصور (من داخل العطل)", "path": "/module/photos", "group": "سجلات مرتبطة", "keywords": "صور"},
        {"title": "التمتير (من داخل العطل)", "path": "/module/metering", "group": "سجلات مرتبطة", "keywords": "تمتير مستخلص"},
        {"title": "القوائم المرجعية", "path": "/lists", "group": "إدارة", "keywords": "قوائم"},
        {"title": "المقاولين", "path": "/contractors", "group": "أقسام", "keywords": "مقاول"},
        {"title": "التنسيقات والجودة", "path": "/quality", "group": "أقسام", "keywords": "تنسيق جودة"},
        {"title": "السلامة", "path": "/safety", "group": "أقسام", "keywords": "سلامة تصريح"},
        {"title": "المستودعات", "path": "/warehouses", "group": "أقسام", "keywords": "مستودع مواد"},
        {"title": "أرصدة المواد", "path": "/warehouses/balances", "group": "أقسام", "keywords": "رصيد"},
        {"title": "المشتريات والعهد", "path": "/external-purchases", "group": "أقسام", "keywords": "شراء عهدة"},
        {"title": "المتابعات المالية", "path": "/financial", "group": "مالية", "keywords": "مالية مستخلص"},
        {"title": "المستخلصات و SAP", "path": "/module/invoices", "group": "مالية", "keywords": "فاتورة sap"},
        {"title": "التدفق النقدي", "path": "/cashflow", "group": "مالية", "keywords": "سيولة نقدي"},
        {"title": "الورشة", "path": "/maintenance", "group": "أقسام", "keywords": "سيارة معدة ورشة"},
        {"title": "الموارد البشرية", "path": "/hr", "group": "أقسام", "keywords": "موظف موارد"},
        {"title": "إدارة العقود", "path": "/contracts-admin", "group": "أقسام", "keywords": "عقد"},
        {"title": "المستخدمون", "path": "/users/list", "group": "إدارة", "keywords": "مستخدم"},
        {"title": "الإعدادات", "path": "/settings", "group": "إدارة", "keywords": "إعدادات"},
        {"title": "المزامنة التلقائية", "path": "/admin/backups", "group": "إدارة", "keywords": "مزامنة backup حفظ تلقائي"},
        {"title": "سجل النشاط", "path": "/admin/audit-log/view", "group": "إدارة", "keywords": "سجل audit"},
        {"title": "البحث العام", "path": "/search", "group": "إدارة", "keywords": "بحث search"},
    ]
