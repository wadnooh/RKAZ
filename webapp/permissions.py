"""نظام صلاحيات المستخدمين — أدوار ومصفوفة صلاحيات لكل التطبيق."""

from __future__ import annotations

from functools import wraps

from flask import flash, g, redirect, request, session, url_for

from webapp.modules_config import MODULES
from webapp.i18n import _ as i18n_phrase

# ---- أكواد الصلاحيات ----
PERM_LABELS = {
    'api.access': 'الوصول عبر API',
    'app.tabs.manage': 'إدارة التبويبات',
    'audit.read': 'سجل النشاط',
    'button.financial.amounts': 'إظهار المبالغ والإجماليات المالية',
    'button.logout': 'زر تسجيل الخروج',
    'button.module.boq_items.add': 'زر إضافة في دليل بنود العقد',
    'button.module.boq_items.delete': 'زر حذف في دليل بنود العقد',
    'button.module.boq_items.edit': 'زر تعديل في دليل بنود العقد',
    'button.module.boq_items.export': 'زر تصدير Excel في دليل بنود العقد',
    'button.module.boq_items.import': 'زر استيراد Excel في دليل بنود العقد',
    'button.module.construction_works.add': 'زر إضافة في أعمال الإنشاءات',
    'button.module.construction_works.delete': 'زر حذف في أعمال الإنشاءات',
    'button.module.construction_works.edit': 'زر تعديل في أعمال الإنشاءات',
    'button.module.construction_works.export': 'زر تصدير Excel في أعمال الإنشاءات',
    'button.module.construction_works.import': 'زر استيراد Excel في أعمال الإنشاءات',
    'button.module.contractor_supplies.add': 'زر إضافة في مواد موردة من مقاول',
    'button.module.contractor_supplies.delete': 'زر حذف في مواد موردة من مقاول',
    'button.module.contractor_supplies.edit': 'زر تعديل في مواد موردة من مقاول',
    'button.module.contractor_supplies.export': 'زر تصدير Excel في مواد موردة من مقاول',
    'button.module.contractor_supplies.import': 'زر استيراد Excel في مواد موردة من مقاول',
    'button.module.contractor_works.add': 'زر إضافة في أعمال المقاولين',
    'button.module.contractor_works.delete': 'زر حذف في أعمال المقاولين',
    'button.module.contractor_works.edit': 'زر تعديل في أعمال المقاولين',
    'button.module.contractor_works.export': 'زر تصدير Excel في أعمال المقاولين',
    'button.module.contractor_works.import': 'زر استيراد Excel في أعمال المقاولين',
    'button.module.contracts.add': 'زر إضافة في العقود',
    'button.module.contracts.delete': 'زر حذف في العقود',
    'button.module.contracts.edit': 'زر تعديل في العقود',
    'button.module.contracts.export': 'زر تصدير Excel في العقود',
    'button.module.contracts.import': 'زر استيراد Excel في العقود',
    'button.module.coordination.add': 'زر إضافة في التنسيقات الفنية',
    'button.module.coordination.delete': 'زر حذف في التنسيقات الفنية',
    'button.module.coordination.edit': 'زر تعديل في التنسيقات الفنية',
    'button.module.coordination.export': 'زر تصدير Excel في التنسيقات الفنية',
    'button.module.coordination.import': 'زر استيراد Excel في التنسيقات الفنية',
    'button.module.custody.add': 'زر إضافة في العهد',
    'button.module.custody.delete': 'زر حذف في العهد',
    'button.module.custody.edit': 'زر تعديل في العهد',
    'button.module.custody.export': 'زر تصدير Excel في العهد',
    'button.module.custody.import': 'زر استيراد Excel في العهد',
    'button.module.external_purchases.add': 'زر إضافة في المشتريات الخارجية',
    'button.module.external_purchases.delete': 'زر حذف في المشتريات الخارجية',
    'button.module.external_purchases.edit': 'زر تعديل في المشتريات الخارجية',
    'button.module.external_purchases.export': 'زر تصدير Excel في المشتريات الخارجية',
    'button.module.external_purchases.import': 'زر استيراد Excel في المشتريات الخارجية',
    'button.module.hr_employees.add': 'زر إضافة في الموظفون',
    'button.module.hr_employees.delete': 'زر حذف في الموظفون',
    'button.module.hr_employees.edit': 'زر تعديل في الموظفون',
    'button.module.hr_employees.export': 'زر تصدير Excel في الموظفون',
    'button.module.hr_employees.import': 'زر استيراد Excel في الموظفون',
    'button.module.invoices.add': 'زر إضافة في المستخلصات و SAP',
    'button.module.invoices.delete': 'زر حذف في المستخلصات و SAP',
    'button.module.invoices.edit': 'زر تعديل في المستخلصات و SAP',
    'button.module.invoices.export': 'زر تصدير Excel في المستخلصات و SAP',
    'button.module.invoices.import': 'زر استيراد Excel في المستخلصات و SAP',
    'button.module.issued_licenses.add': 'زر إضافة في الرخص المصدرة',
    'button.module.issued_licenses.delete': 'زر حذف في الرخص المصدرة',
    'button.module.issued_licenses.edit': 'زر تعديل في الرخص المصدرة',
    'button.module.issued_licenses.export': 'زر تصدير Excel في الرخص المصدرة',
    'button.module.issued_licenses.import': 'زر استيراد Excel في الرخص المصدرة',
    'button.module.metering.add': 'زر إضافة في التمتير',
    'button.module.metering.delete': 'زر حذف في التمتير',
    'button.module.metering.edit': 'زر تعديل في التمتير',
    'button.module.metering.export': 'زر تصدير Excel في التمتير',
    'button.module.metering.import': 'زر استيراد Excel في التمتير',
    'button.module.new_coordinations.add': 'زر إضافة في التنسيقات الجديدة',
    'button.module.new_coordinations.delete': 'زر حذف في التنسيقات الجديدة',
    'button.module.new_coordinations.edit': 'زر تعديل في التنسيقات الجديدة',
    'button.module.new_coordinations.export': 'زر تصدير Excel في التنسيقات الجديدة',
    'button.module.new_coordinations.import': 'زر استيراد Excel في التنسيقات الجديدة',
    'button.module.photos.add': 'زر إضافة في قائمة الصور',
    'button.module.photos.delete': 'زر حذف في قائمة الصور',
    'button.module.photos.edit': 'زر تعديل في قائمة الصور',
    'button.module.photos.export': 'زر تصدير Excel في قائمة الصور',
    'button.module.photos.import': 'زر استيراد Excel في قائمة الصور',
    'button.module.primary_team_orders.add': 'زر إضافة في الفرق الأولية',
    'button.module.primary_team_orders.delete': 'زر حذف في الفرق الأولية',
    'button.module.primary_team_orders.edit': 'زر تعديل في الفرق الأولية',
    'button.module.primary_team_orders.export': 'زر تصدير Excel في الفرق الأولية',
    'button.module.primary_team_orders.import': 'زر استيراد Excel في الفرق الأولية',
    'button.module.projects.add': 'زر إضافة في المشاريع',
    'button.module.projects.delete': 'زر حذف في المشاريع',
    'button.module.projects.edit': 'زر تعديل في المشاريع',
    'button.module.projects.export': 'زر تصدير Excel في المشاريع',
    'button.module.projects.import': 'زر استيراد Excel في المشاريع',
    'button.module.quality_clearances.add': 'زر إضافة في إخلاءات الأسفلت (فسوحات)',
    'button.module.quality_clearances.delete': 'زر حذف في إخلاءات الأسفلت (فسوحات)',
    'button.module.quality_clearances.edit': 'زر تعديل في إخلاءات الأسفلت (فسوحات)',
    'button.module.quality_clearances.export': 'زر تصدير Excel في إخلاءات الأسفلت (فسوحات)',
    'button.module.quality_clearances.import': 'زر استيراد Excel في إخلاءات الأسفلت (فسوحات)',
    'button.module.quality_inspections.add': 'زر إضافة في فحوصات الجودة',
    'button.module.quality_inspections.delete': 'زر حذف في فحوصات الجودة',
    'button.module.quality_inspections.edit': 'زر تعديل في فحوصات الجودة',
    'button.module.quality_inspections.export': 'زر تصدير Excel في فحوصات الجودة',
    'button.module.quality_inspections.import': 'زر استيراد Excel في فحوصات الجودة',
    'button.module.quantities.add': 'زر إضافة في الكميات / ورقة التمتير',
    'button.module.quantities.delete': 'زر حذف في الكميات / ورقة التمتير',
    'button.module.quantities.edit': 'زر تعديل في الكميات / ورقة التمتير',
    'button.module.quantities.export': 'زر تصدير Excel في الكميات / ورقة التمتير',
    'button.module.quantities.import': 'زر استيراد Excel في الكميات / ورقة التمتير',
    'button.module.reinforcement_departments.add': 'زر إضافة في أقسام التعزيز',
    'button.module.reinforcement_departments.delete': 'زر حذف في أقسام التعزيز',
    'button.module.reinforcement_departments.edit': 'زر تعديل في أقسام التعزيز',
    'button.module.reinforcement_departments.export': 'زر تصدير Excel في أقسام التعزيز',
    'button.module.reinforcement_departments.import': 'زر استيراد Excel في أقسام التعزيز',
    'button.module.reinforcement_works.add': 'زر إضافة في معاملات التعزيز / اسكيمات',
    'button.module.reinforcement_works.delete': 'زر حذف في معاملات التعزيز / اسكيمات',
    'button.module.reinforcement_works.edit': 'زر تعديل في معاملات التعزيز / اسكيمات',
    'button.module.reinforcement_works.export': 'زر تصدير Excel في معاملات التعزيز / اسكيمات',
    'button.module.reinforcement_works.import': 'زر استيراد Excel في معاملات التعزيز / اسكيمات',
    'button.module.safety_incidents.add': 'زر إضافة في بلاغات السلامة',
    'button.module.safety_incidents.delete': 'زر حذف في بلاغات السلامة',
    'button.module.safety_incidents.edit': 'زر تعديل في بلاغات السلامة',
    'button.module.safety_incidents.export': 'زر تصدير Excel في بلاغات السلامة',
    'button.module.safety_incidents.import': 'زر استيراد Excel في بلاغات السلامة',
    'button.module.safety_permits.add': 'زر إضافة في تصاريح العمل',
    'button.module.safety_permits.delete': 'زر حذف في تصاريح العمل',
    'button.module.safety_permits.edit': 'زر تعديل في تصاريح العمل',
    'button.module.safety_permits.export': 'زر تصدير Excel في تصاريح العمل',
    'button.module.safety_permits.import': 'زر استيراد Excel في تصاريح العمل',
    'button.module.warehouse_items.add': 'زر إضافة في أصناف المستودع',
    'button.module.warehouse_items.delete': 'زر حذف في أصناف المستودع',
    'button.module.warehouse_items.edit': 'زر تعديل في أصناف المستودع',
    'button.module.warehouse_items.export': 'زر تصدير Excel في أصناف المستودع',
    'button.module.warehouse_items.import': 'زر استيراد Excel في أصناف المستودع',
    'button.module.warehouse_tx.add': 'زر إضافة في معاملات المستودع',
    'button.module.warehouse_tx.delete': 'زر حذف في معاملات المستودع',
    'button.module.warehouse_tx.edit': 'زر تعديل في معاملات المستودع',
    'button.module.warehouse_tx.export': 'زر تصدير Excel في معاملات المستودع',
    'button.module.warehouse_tx.import': 'زر استيراد Excel في معاملات المستودع',
    'button.module.workshop_cars.add': 'زر إضافة في السيارات',
    'button.module.workshop_cars.delete': 'زر حذف في السيارات',
    'button.module.workshop_cars.edit': 'زر تعديل في السيارات',
    'button.module.workshop_cars.export': 'زر تصدير Excel في السيارات',
    'button.module.workshop_cars.import': 'زر استيراد Excel في السيارات',
    'button.module.workshop_equipment.add': 'زر إضافة في المعدات',
    'button.module.workshop_equipment.delete': 'زر حذف في المعدات',
    'button.module.workshop_equipment.edit': 'زر تعديل في المعدات',
    'button.module.workshop_equipment.export': 'زر تصدير Excel في المعدات',
    'button.module.workshop_equipment.import': 'زر استيراد Excel في المعدات',
    'button.quality.transfer': 'زر نقل التنسيق إلى متابعة التصاريح',
    'button.quality.workflow': 'أزرار مسار الجودة',
    'button.quick_jump': 'زر القفز السريع',
    'button.search': 'زر البحث العام',
    'button.tabs.add': 'زر إضافة تبويب',
    'button.tabs.delete': 'زر حذف تبويب',
    'button.tabs.edit': 'زر تعديل تبويب',
    'button.tabs.open': 'زر فتح تبويب',
    'button.ticket.export': 'زر تصدير الأعطال Excel',
    'button.ticket.new': 'زر إضافة عطل جديد',
    'button.users.add': 'زر إضافة مستخدم',
    'button.users.delete': 'زر حذف مستخدم',
    'button.users.edit': 'زر تعديل مستخدم',
    'button.users.toggle': 'زر تفعيل أو إيقاف مستخدم',
    'button.warehouse.issue': 'زر صرف مستودع',
    'cashflow.read': 'عرض التدفق النقدي',
    'cashflow.write': 'تعديل التدفق النقدي',
    'export': 'تصدير Excel',
    'modules.delete': 'حذف السجلات',
    'modules.read': 'عرض سجلات الأقسام',
    'modules.write': 'إضافة/تعديل السجلات',
    'notifications.manage': 'إدارة الإشعارات',
    'notifications.read': 'عرض الإشعارات',
    'ops.tabs.manage': 'إدارة تبويبات العمليات',
    'reports.view': 'عرض التقارير',
    'search': 'البحث العام',
    'section.constructions': 'الإنشاءات',
    'section.contractors': 'المقاولين',
    'section.contracts': 'إدارة العقود',
    'section.external': 'المشتريات والعهد',
    'section.financial': 'المتابعات المالية',
    'section.hr': 'الموارد البشرية',
    'section.maintenance': 'الورشة',
    'section.ops': 'العمليات والصيانة',
    'section.projects': 'المشاريع',
    'section.quality': 'التنسيقات والجودة',
    'section.reinforcement': 'التعزيز - اسكيمات',
    'section.safety': 'السلامة',
    'section.warehouses': 'المستودعات',
    'tab.audit': 'تبويب سجل النشاط',
    'tab.constructions': 'تبويب الإنشاءات',
    'tab.contractors': 'تبويب المقاولين',
    'tab.contracts': 'تبويب إدارة العقود',
    'tab.contracts.manage_tabs': 'تبويب إدارة التبويبات',
    'tab.external': 'تبويب المشتريات والعهد',
    'tab.financial': 'تبويب المتابعات المالية',
    'tab.hr': 'تبويب الموارد البشرية',
    'tab.maintenance': 'تبويب الورشة',
    'tab.module.boq_items': 'تبويب/قائمة دليل بنود العقد',
    'tab.module.construction_works': 'تبويب/قائمة أعمال الإنشاءات',
    'tab.module.contractor_supplies': 'تبويب/قائمة مواد موردة من مقاول',
    'tab.module.contractor_works': 'تبويب/قائمة أعمال المقاولين',
    'tab.module.contracts': 'تبويب/قائمة العقود',
    'tab.module.coordination': 'تبويب/قائمة التنسيقات الفنية',
    'tab.module.custody': 'تبويب/قائمة العهد',
    'tab.module.external_purchases': 'تبويب/قائمة المشتريات الخارجية',
    'tab.module.hr_employees': 'تبويب/قائمة الموظفون',
    'tab.module.invoices': 'تبويب/قائمة المستخلصات و SAP',
    'tab.module.issued_licenses': 'تبويب/قائمة الرخص المصدرة',
    'tab.module.metering': 'تبويب/قائمة التمتير',
    'tab.module.new_coordinations': 'تبويب/قائمة التنسيقات الجديدة',
    'tab.module.photos': 'تبويب/قائمة قائمة الصور',
    'tab.module.primary_team_orders': 'تبويب/قائمة الفرق الأولية',
    'tab.module.projects': 'تبويب/قائمة المشاريع',
    'tab.module.quality_clearances': 'تبويب/قائمة إخلاءات الأسفلت (فسوحات)',
    'tab.module.quality_inspections': 'تبويب/قائمة فحوصات الجودة',
    'tab.module.quantities': 'تبويب/قائمة الكميات / ورقة التمتير',
    'tab.module.reinforcement_departments': 'تبويب/قائمة أقسام التعزيز',
    'tab.module.reinforcement_works': 'تبويب/قائمة معاملات التعزيز / اسكيمات',
    'tab.module.safety_incidents': 'تبويب/قائمة بلاغات السلامة',
    'tab.module.safety_permits': 'تبويب/قائمة تصاريح العمل',
    'tab.module.warehouse_items': 'تبويب/قائمة أصناف المستودع',
    'tab.module.warehouse_tx': 'تبويب/قائمة معاملات المستودع',
    'tab.module.workshop_cars': 'تبويب/قائمة السيارات',
    'tab.module.workshop_equipment': 'تبويب/قائمة المعدات',
    'tab.ops': 'تبويب العمليات والصيانة',
    'tab.ops.primary_teams': 'تبويب العمليات: الفرق الأولية',
    'tab.ops.reinforcement': 'تبويب العمليات: التعزيز',
    'tab.ops.teams': 'تبويب العمليات: فرق المهام العاجلة',
    'tab.ops.tickets': 'تبويب العمليات: الأعطال',
    'tab.projects': 'تبويب المشاريع',
    'tab.quality': 'تبويب التنسيقات والجودة',
    'tab.reinforcement': 'تبويب التعزيز',
    'tab.safety': 'تبويب السلامة',
    'tab.users': 'تبويب المستخدمون',
    'tab.warehouse.balances': 'تبويب المستودعات: أرصدة المواد',
    'tab.warehouse.constructions': 'تبويب المستودعات: الإنشاءات',
    'tab.warehouse.contractors': 'تبويب المستودعات: مواد مقاول',
    'tab.warehouse.ops': 'تبويب المستودعات: العمليات والصيانة',
    'tab.warehouse.projects': 'تبويب المستودعات: المشاريع',
    'tab.warehouse.summary': 'تبويب المستودعات: إجمالي الكميات',
    'tab.warehouses': 'تبويب المستودعات',
    'teams.write': 'إدارة فرق المهام',
    'tickets.delete': 'حذف الأعطال',
    'tickets.read': 'عرض الأعطال',
    'tickets.write': 'إضافة/تعديل الأعطال والبنود',
    'users.manage': 'إدارة المستخدمين',
}

ALL_PERMS = set(PERM_LABELS)

# وحدات مرتبطة بالعطل: أي إضافة/تعديل/حذف لها يتطلب tickets.write (بالإضافة إلى modules.*)
TICKET_LINKED_WRITE_MODULES = frozenset({"quantities", "photos", "metering"})

# كل أقسام العرض
SECTION_PERMS = {
    "constructions": "section.constructions",
    "projects": "section.projects",
    "ops": "section.ops",
    "contractors": "section.contractors",
    "quality": "section.quality",
    "safety": "section.safety",
    "warehouses": "section.warehouses",
    "external": "section.external",
    "financial": "section.financial",
    "maintenance": "section.maintenance",
    "hr": "section.hr",
    "contracts": "section.contracts",
    "reinforcement": "section.reinforcement",
}

_READ_ALL_SECTIONS = set(SECTION_PERMS.values())

_ROLE_PERMS = {'admin': {'api.access',
           'app.tabs.manage',
           'audit.read',
           'button.financial.amounts',
           'button.logout',
           'button.module.boq_items.add',
           'button.module.boq_items.delete',
           'button.module.boq_items.edit',
           'button.module.boq_items.export',
           'button.module.boq_items.import',
           'button.module.construction_works.add',
           'button.module.construction_works.delete',
           'button.module.construction_works.edit',
           'button.module.construction_works.export',
           'button.module.construction_works.import',
           'button.module.contractor_supplies.add',
           'button.module.contractor_supplies.delete',
           'button.module.contractor_supplies.edit',
           'button.module.contractor_supplies.export',
           'button.module.contractor_supplies.import',
           'button.module.contractor_works.add',
           'button.module.contractor_works.delete',
           'button.module.contractor_works.edit',
           'button.module.contractor_works.export',
           'button.module.contractor_works.import',
           'button.module.contracts.add',
           'button.module.contracts.delete',
           'button.module.contracts.edit',
           'button.module.contracts.export',
           'button.module.contracts.import',
           'button.module.coordination.add',
           'button.module.coordination.delete',
           'button.module.coordination.edit',
           'button.module.coordination.export',
           'button.module.coordination.import',
           'button.module.custody.add',
           'button.module.custody.delete',
           'button.module.custody.edit',
           'button.module.custody.export',
           'button.module.custody.import',
           'button.module.external_purchases.add',
           'button.module.external_purchases.delete',
           'button.module.external_purchases.edit',
           'button.module.external_purchases.export',
           'button.module.external_purchases.import',
           'button.module.hr_employees.add',
           'button.module.hr_employees.delete',
           'button.module.hr_employees.edit',
           'button.module.hr_employees.export',
           'button.module.hr_employees.import',
           'button.module.invoices.add',
           'button.module.invoices.delete',
           'button.module.invoices.edit',
           'button.module.invoices.export',
           'button.module.invoices.import',
           'button.module.issued_licenses.add',
           'button.module.issued_licenses.delete',
           'button.module.issued_licenses.edit',
           'button.module.issued_licenses.export',
           'button.module.issued_licenses.import',
           'button.module.metering.add',
           'button.module.metering.delete',
           'button.module.metering.edit',
           'button.module.metering.export',
           'button.module.metering.import',
           'button.module.new_coordinations.add',
           'button.module.new_coordinations.delete',
           'button.module.new_coordinations.edit',
           'button.module.new_coordinations.export',
           'button.module.new_coordinations.import',
           'button.module.photos.add',
           'button.module.photos.delete',
           'button.module.photos.edit',
           'button.module.photos.export',
           'button.module.photos.import',
           'button.module.primary_team_orders.add',
           'button.module.primary_team_orders.delete',
           'button.module.primary_team_orders.edit',
           'button.module.primary_team_orders.export',
           'button.module.primary_team_orders.import',
           'button.module.projects.add',
           'button.module.projects.delete',
           'button.module.projects.edit',
           'button.module.projects.export',
           'button.module.projects.import',
           'button.module.quality_clearances.add',
           'button.module.quality_clearances.delete',
           'button.module.quality_clearances.edit',
           'button.module.quality_clearances.export',
           'button.module.quality_clearances.import',
           'button.module.quality_inspections.add',
           'button.module.quality_inspections.delete',
           'button.module.quality_inspections.edit',
           'button.module.quality_inspections.export',
           'button.module.quality_inspections.import',
           'button.module.quantities.add',
           'button.module.quantities.delete',
           'button.module.quantities.edit',
           'button.module.quantities.export',
           'button.module.quantities.import',
           'button.module.reinforcement_departments.add',
           'button.module.reinforcement_departments.delete',
           'button.module.reinforcement_departments.edit',
           'button.module.reinforcement_departments.export',
           'button.module.reinforcement_departments.import',
           'button.module.reinforcement_works.add',
           'button.module.reinforcement_works.delete',
           'button.module.reinforcement_works.edit',
           'button.module.reinforcement_works.export',
           'button.module.reinforcement_works.import',
           'button.module.safety_incidents.add',
           'button.module.safety_incidents.delete',
           'button.module.safety_incidents.edit',
           'button.module.safety_incidents.export',
           'button.module.safety_incidents.import',
           'button.module.safety_permits.add',
           'button.module.safety_permits.delete',
           'button.module.safety_permits.edit',
           'button.module.safety_permits.export',
           'button.module.safety_permits.import',
           'button.module.warehouse_items.add',
           'button.module.warehouse_items.delete',
           'button.module.warehouse_items.edit',
           'button.module.warehouse_items.export',
           'button.module.warehouse_items.import',
           'button.module.warehouse_tx.add',
           'button.module.warehouse_tx.delete',
           'button.module.warehouse_tx.edit',
           'button.module.warehouse_tx.export',
           'button.module.warehouse_tx.import',
           'button.module.workshop_cars.add',
           'button.module.workshop_cars.delete',
           'button.module.workshop_cars.edit',
           'button.module.workshop_cars.export',
           'button.module.workshop_cars.import',
           'button.module.workshop_equipment.add',
           'button.module.workshop_equipment.delete',
           'button.module.workshop_equipment.edit',
           'button.module.workshop_equipment.export',
           'button.module.workshop_equipment.import',
           'button.quality.transfer',
           'button.quality.workflow',
           'button.quick_jump',
           'button.search',
           'button.tabs.add',
           'button.tabs.delete',
           'button.tabs.edit',
           'button.tabs.open',
           'button.ticket.export',
           'button.ticket.new',
           'button.users.add',
           'button.users.delete',
           'button.users.edit',
           'button.users.toggle',
           'button.warehouse.issue',
           'cashflow.read',
           'cashflow.write',
           'export',
           'modules.delete',
           'modules.read',
           'modules.write',
           'notifications.manage',
           'notifications.read',
           'ops.tabs.manage',
           'reports.view',
           'search',
           'section.constructions',
           'section.contractors',
           'section.contracts',
           'section.external',
           'section.financial',
           'section.hr',
           'section.maintenance',
           'section.ops',
           'section.projects',
           'section.quality',
           'section.reinforcement',
           'section.safety',
           'section.warehouses',
           'tab.audit',
           'tab.constructions',
           'tab.contractors',
           'tab.contracts',
           'tab.contracts.manage_tabs',
           'tab.external',
           'tab.financial',
           'tab.hr',
           'tab.maintenance',
           'tab.module.boq_items',
           'tab.module.construction_works',
           'tab.module.contractor_supplies',
           'tab.module.contractor_works',
           'tab.module.contracts',
           'tab.module.coordination',
           'tab.module.custody',
           'tab.module.external_purchases',
           'tab.module.hr_employees',
           'tab.module.invoices',
           'tab.module.issued_licenses',
           'tab.module.metering',
           'tab.module.new_coordinations',
           'tab.module.photos',
           'tab.module.primary_team_orders',
           'tab.module.projects',
           'tab.module.quality_clearances',
           'tab.module.quality_inspections',
           'tab.module.quantities',
           'tab.module.reinforcement_departments',
           'tab.module.reinforcement_works',
           'tab.module.safety_incidents',
           'tab.module.safety_permits',
           'tab.module.warehouse_items',
           'tab.module.warehouse_tx',
           'tab.module.workshop_cars',
           'tab.module.workshop_equipment',
           'tab.ops',
           'tab.ops.primary_teams',
           'tab.ops.reinforcement',
           'tab.ops.teams',
           'tab.ops.tickets',
           'tab.projects',
           'tab.quality',
           'tab.reinforcement',
           'tab.safety',
           'tab.users',
           'tab.warehouse.balances',
           'tab.warehouse.constructions',
           'tab.warehouse.contractors',
           'tab.warehouse.ops',
           'tab.warehouse.projects',
           'tab.warehouse.summary',
           'tab.warehouses',
           'teams.write',
           'tickets.delete',
           'tickets.read',
           'tickets.write',
           'users.manage'},
 'مدخل بيانات': {'button.financial.amounts',
                 'button.logout',
                 'button.module.construction_works.add',
                 'button.module.construction_works.edit',
                 'button.module.construction_works.export',
                 'button.module.construction_works.import',
                 'button.module.contractor_supplies.add',
                 'button.module.contractor_supplies.edit',
                 'button.module.contractor_supplies.export',
                 'button.module.contractor_supplies.import',
                 'button.module.contractor_works.add',
                 'button.module.contractor_works.edit',
                 'button.module.contractor_works.export',
                 'button.module.contractor_works.import',
                 'button.module.coordination.add',
                 'button.module.coordination.edit',
                 'button.module.coordination.export',
                 'button.module.coordination.import',
                 'button.module.custody.add',
                 'button.module.custody.edit',
                 'button.module.custody.export',
                 'button.module.custody.import',
                 'button.module.external_purchases.add',
                 'button.module.external_purchases.edit',
                 'button.module.external_purchases.export',
                 'button.module.external_purchases.import',
                 'button.module.hr_employees.add',
                 'button.module.hr_employees.edit',
                 'button.module.hr_employees.export',
                 'button.module.hr_employees.import',
                 'button.module.invoices.add',
                 'button.module.invoices.edit',
                 'button.module.invoices.export',
                 'button.module.invoices.import',
                 'button.module.issued_licenses.add',
                 'button.module.issued_licenses.edit',
                 'button.module.issued_licenses.export',
                 'button.module.issued_licenses.import',
                 'button.module.metering.add',
                 'button.module.metering.edit',
                 'button.module.metering.export',
                 'button.module.metering.import',
                 'button.module.new_coordinations.add',
                 'button.module.new_coordinations.edit',
                 'button.module.new_coordinations.export',
                 'button.module.new_coordinations.import',
                 'button.module.photos.add',
                 'button.module.photos.edit',
                 'button.module.photos.export',
                 'button.module.photos.import',
                 'button.module.primary_team_orders.add',
                 'button.module.primary_team_orders.edit',
                 'button.module.primary_team_orders.export',
                 'button.module.primary_team_orders.import',
                 'button.module.projects.add',
                 'button.module.projects.edit',
                 'button.module.projects.export',
                 'button.module.projects.import',
                 'button.module.quality_clearances.add',
                 'button.module.quality_clearances.edit',
                 'button.module.quality_clearances.export',
                 'button.module.quality_clearances.import',
                 'button.module.quality_inspections.add',
                 'button.module.quality_inspections.edit',
                 'button.module.quality_inspections.export',
                 'button.module.quality_inspections.import',
                 'button.module.quantities.add',
                 'button.module.quantities.edit',
                 'button.module.quantities.export',
                 'button.module.quantities.import',
                 'button.module.reinforcement_departments.add',
                 'button.module.reinforcement_departments.edit',
                 'button.module.reinforcement_departments.export',
                 'button.module.reinforcement_departments.import',
                 'button.module.reinforcement_works.add',
                 'button.module.reinforcement_works.edit',
                 'button.module.reinforcement_works.export',
                 'button.module.reinforcement_works.import',
                 'button.module.safety_incidents.add',
                 'button.module.safety_incidents.edit',
                 'button.module.safety_incidents.export',
                 'button.module.safety_incidents.import',
                 'button.module.safety_permits.add',
                 'button.module.safety_permits.edit',
                 'button.module.safety_permits.export',
                 'button.module.safety_permits.import',
                 'button.module.warehouse_items.add',
                 'button.module.warehouse_items.edit',
                 'button.module.warehouse_items.export',
                 'button.module.warehouse_items.import',
                 'button.module.warehouse_tx.add',
                 'button.module.warehouse_tx.edit',
                 'button.module.warehouse_tx.export',
                 'button.module.warehouse_tx.import',
                 'button.module.workshop_cars.add',
                 'button.module.workshop_cars.edit',
                 'button.module.workshop_cars.export',
                 'button.module.workshop_cars.import',
                 'button.module.workshop_equipment.add',
                 'button.module.workshop_equipment.edit',
                 'button.module.workshop_equipment.export',
                 'button.module.workshop_equipment.import',
                 'button.quality.transfer',
                 'button.quality.workflow',
                 'button.quick_jump',
                 'button.search',
                 'button.ticket.export',
                 'button.ticket.new',
                 'button.warehouse.issue',
                 'cashflow.read',
                 'export',
                 'modules.read',
                 'modules.write',
                 'notifications.read',
                 'search',
                 'section.constructions',
                 'section.contractors',
                 'section.external',
                 'section.financial',
                 'section.hr',
                 'section.maintenance',
                 'section.ops',
                 'section.projects',
                 'section.quality',
                 'section.reinforcement',
                 'section.safety',
                 'section.warehouses',
                 'tab.constructions',
                 'tab.contractors',
                 'tab.external',
                 'tab.financial',
                 'tab.hr',
                 'tab.maintenance',
                 'tab.module.construction_works',
                 'tab.module.contractor_supplies',
                 'tab.module.contractor_works',
                 'tab.module.coordination',
                 'tab.module.custody',
                 'tab.module.external_purchases',
                 'tab.module.hr_employees',
                 'tab.module.invoices',
                 'tab.module.issued_licenses',
                 'tab.module.metering',
                 'tab.module.new_coordinations',
                 'tab.module.photos',
                 'tab.module.primary_team_orders',
                 'tab.module.projects',
                 'tab.module.quality_clearances',
                 'tab.module.quality_inspections',
                 'tab.module.quantities',
                 'tab.module.reinforcement_departments',
                 'tab.module.reinforcement_works',
                 'tab.module.safety_incidents',
                 'tab.module.safety_permits',
                 'tab.module.warehouse_items',
                 'tab.module.warehouse_tx',
                 'tab.module.workshop_cars',
                 'tab.module.workshop_equipment',
                 'tab.ops',
                 'tab.ops.primary_teams',
                 'tab.ops.reinforcement',
                 'tab.ops.teams',
                 'tab.ops.tickets',
                 'tab.projects',
                 'tab.quality',
                 'tab.reinforcement',
                 'tab.safety',
                 'tab.warehouse.balances',
                 'tab.warehouse.constructions',
                 'tab.warehouse.contractors',
                 'tab.warehouse.ops',
                 'tab.warehouse.projects',
                 'tab.warehouse.summary',
                 'tab.warehouses',
                 'tickets.read',
                 'tickets.write'},
 'مراقب': {'audit.read',
           'button.financial.amounts',
           'button.logout',
           'button.module.boq_items.export',
           'button.module.construction_works.export',
           'button.module.contractor_supplies.export',
           'button.module.contractor_works.export',
           'button.module.contracts.export',
           'button.module.coordination.export',
           'button.module.custody.export',
           'button.module.external_purchases.export',
           'button.module.hr_employees.export',
           'button.module.invoices.export',
           'button.module.issued_licenses.export',
           'button.module.metering.export',
           'button.module.new_coordinations.export',
           'button.module.photos.export',
           'button.module.primary_team_orders.export',
           'button.module.projects.export',
           'button.module.quality_clearances.export',
           'button.module.quality_inspections.export',
           'button.module.quantities.export',
           'button.module.reinforcement_departments.export',
           'button.module.reinforcement_works.export',
           'button.module.safety_incidents.export',
           'button.module.safety_permits.export',
           'button.module.warehouse_items.export',
           'button.module.warehouse_tx.export',
           'button.module.workshop_cars.export',
           'button.module.workshop_equipment.export',
           'button.quality.workflow',
           'button.quick_jump',
           'button.search',
           'button.ticket.export',
           'cashflow.read',
           'export',
           'modules.read',
           'notifications.read',
           'reports.view',
           'search',
           'section.constructions',
           'section.contractors',
           'section.contracts',
           'section.external',
           'section.financial',
           'section.hr',
           'section.maintenance',
           'section.ops',
           'section.projects',
           'section.quality',
           'section.reinforcement',
           'section.safety',
           'section.warehouses',
           'tab.audit',
           'tab.constructions',
           'tab.contractors',
           'tab.contracts',
           'tab.external',
           'tab.financial',
           'tab.hr',
           'tab.maintenance',
           'tab.module.boq_items',
           'tab.module.construction_works',
           'tab.module.contractor_supplies',
           'tab.module.contractor_works',
           'tab.module.contracts',
           'tab.module.coordination',
           'tab.module.custody',
           'tab.module.external_purchases',
           'tab.module.hr_employees',
           'tab.module.invoices',
           'tab.module.issued_licenses',
           'tab.module.metering',
           'tab.module.new_coordinations',
           'tab.module.photos',
           'tab.module.primary_team_orders',
           'tab.module.projects',
           'tab.module.quality_clearances',
           'tab.module.quality_inspections',
           'tab.module.quantities',
           'tab.module.reinforcement_departments',
           'tab.module.reinforcement_works',
           'tab.module.safety_incidents',
           'tab.module.safety_permits',
           'tab.module.warehouse_items',
           'tab.module.warehouse_tx',
           'tab.module.workshop_cars',
           'tab.module.workshop_equipment',
           'tab.ops',
           'tab.ops.primary_teams',
           'tab.ops.reinforcement',
           'tab.ops.teams',
           'tab.ops.tickets',
           'tab.projects',
           'tab.quality',
           'tab.reinforcement',
           'tab.safety',
           'tab.warehouse.balances',
           'tab.warehouse.constructions',
           'tab.warehouse.contractors',
           'tab.warehouse.ops',
           'tab.warehouse.projects',
           'tab.warehouse.summary',
           'tab.warehouses',
           'tickets.read'},
 'مشرف': {'api.access',
          'audit.read',
          'button.financial.amounts',
          'button.logout',
          'button.module.boq_items.add',
          'button.module.boq_items.delete',
          'button.module.boq_items.edit',
          'button.module.boq_items.export',
          'button.module.boq_items.import',
          'button.module.construction_works.add',
          'button.module.construction_works.delete',
          'button.module.construction_works.edit',
          'button.module.construction_works.export',
          'button.module.construction_works.import',
          'button.module.contractor_supplies.add',
          'button.module.contractor_supplies.delete',
          'button.module.contractor_supplies.edit',
          'button.module.contractor_supplies.export',
          'button.module.contractor_supplies.import',
          'button.module.contractor_works.add',
          'button.module.contractor_works.delete',
          'button.module.contractor_works.edit',
          'button.module.contractor_works.export',
          'button.module.contractor_works.import',
          'button.module.contracts.add',
          'button.module.contracts.delete',
          'button.module.contracts.edit',
          'button.module.contracts.export',
          'button.module.contracts.import',
          'button.module.coordination.add',
          'button.module.coordination.delete',
          'button.module.coordination.edit',
          'button.module.coordination.export',
          'button.module.coordination.import',
          'button.module.custody.add',
          'button.module.custody.delete',
          'button.module.custody.edit',
          'button.module.custody.export',
          'button.module.custody.import',
          'button.module.external_purchases.add',
          'button.module.external_purchases.delete',
          'button.module.external_purchases.edit',
          'button.module.external_purchases.export',
          'button.module.external_purchases.import',
          'button.module.hr_employees.add',
          'button.module.hr_employees.delete',
          'button.module.hr_employees.edit',
          'button.module.hr_employees.export',
          'button.module.hr_employees.import',
          'button.module.invoices.add',
          'button.module.invoices.delete',
          'button.module.invoices.edit',
          'button.module.invoices.export',
          'button.module.invoices.import',
          'button.module.issued_licenses.add',
          'button.module.issued_licenses.delete',
          'button.module.issued_licenses.edit',
          'button.module.issued_licenses.export',
          'button.module.issued_licenses.import',
          'button.module.metering.add',
          'button.module.metering.delete',
          'button.module.metering.edit',
          'button.module.metering.export',
          'button.module.metering.import',
          'button.module.new_coordinations.add',
          'button.module.new_coordinations.delete',
          'button.module.new_coordinations.edit',
          'button.module.new_coordinations.export',
          'button.module.new_coordinations.import',
          'button.module.photos.add',
          'button.module.photos.delete',
          'button.module.photos.edit',
          'button.module.photos.export',
          'button.module.photos.import',
          'button.module.primary_team_orders.add',
          'button.module.primary_team_orders.delete',
          'button.module.primary_team_orders.edit',
          'button.module.primary_team_orders.export',
          'button.module.primary_team_orders.import',
          'button.module.projects.add',
          'button.module.projects.delete',
          'button.module.projects.edit',
          'button.module.projects.export',
          'button.module.projects.import',
          'button.module.quality_clearances.add',
          'button.module.quality_clearances.delete',
          'button.module.quality_clearances.edit',
          'button.module.quality_clearances.export',
          'button.module.quality_clearances.import',
          'button.module.quality_inspections.add',
          'button.module.quality_inspections.delete',
          'button.module.quality_inspections.edit',
          'button.module.quality_inspections.export',
          'button.module.quality_inspections.import',
          'button.module.quantities.add',
          'button.module.quantities.delete',
          'button.module.quantities.edit',
          'button.module.quantities.export',
          'button.module.quantities.import',
          'button.module.reinforcement_departments.add',
          'button.module.reinforcement_departments.delete',
          'button.module.reinforcement_departments.edit',
          'button.module.reinforcement_departments.export',
          'button.module.reinforcement_departments.import',
          'button.module.reinforcement_works.add',
          'button.module.reinforcement_works.delete',
          'button.module.reinforcement_works.edit',
          'button.module.reinforcement_works.export',
          'button.module.reinforcement_works.import',
          'button.module.safety_incidents.add',
          'button.module.safety_incidents.delete',
          'button.module.safety_incidents.edit',
          'button.module.safety_incidents.export',
          'button.module.safety_incidents.import',
          'button.module.safety_permits.add',
          'button.module.safety_permits.delete',
          'button.module.safety_permits.edit',
          'button.module.safety_permits.export',
          'button.module.safety_permits.import',
          'button.module.warehouse_items.add',
          'button.module.warehouse_items.delete',
          'button.module.warehouse_items.edit',
          'button.module.warehouse_items.export',
          'button.module.warehouse_items.import',
          'button.module.warehouse_tx.add',
          'button.module.warehouse_tx.delete',
          'button.module.warehouse_tx.edit',
          'button.module.warehouse_tx.export',
          'button.module.warehouse_tx.import',
          'button.module.workshop_cars.add',
          'button.module.workshop_cars.delete',
          'button.module.workshop_cars.edit',
          'button.module.workshop_cars.export',
          'button.module.workshop_cars.import',
          'button.module.workshop_equipment.add',
          'button.module.workshop_equipment.delete',
          'button.module.workshop_equipment.edit',
          'button.module.workshop_equipment.export',
          'button.module.workshop_equipment.import',
          'button.quality.transfer',
          'button.quality.workflow',
          'button.quick_jump',
          'button.search',
          'button.ticket.export',
          'button.ticket.new',
          'button.warehouse.issue',
          'cashflow.read',
          'cashflow.write',
          'export',
          'modules.delete',
          'modules.read',
          'modules.write',
          'notifications.read',
          'search',
          'section.constructions',
          'section.contractors',
          'section.contracts',
          'section.external',
          'section.financial',
          'section.hr',
          'section.maintenance',
          'section.ops',
          'section.projects',
          'section.quality',
          'section.reinforcement',
          'section.safety',
          'section.warehouses',
          'tab.audit',
          'tab.constructions',
          'tab.contractors',
          'tab.contracts',
          'tab.external',
          'tab.financial',
          'tab.hr',
          'tab.maintenance',
          'tab.module.boq_items',
          'tab.module.construction_works',
          'tab.module.contractor_supplies',
          'tab.module.contractor_works',
          'tab.module.contracts',
          'tab.module.coordination',
          'tab.module.custody',
          'tab.module.external_purchases',
          'tab.module.hr_employees',
          'tab.module.invoices',
          'tab.module.issued_licenses',
          'tab.module.metering',
          'tab.module.new_coordinations',
          'tab.module.photos',
          'tab.module.primary_team_orders',
          'tab.module.projects',
          'tab.module.quality_clearances',
          'tab.module.quality_inspections',
          'tab.module.quantities',
          'tab.module.reinforcement_departments',
          'tab.module.reinforcement_works',
          'tab.module.safety_incidents',
          'tab.module.safety_permits',
          'tab.module.warehouse_items',
          'tab.module.warehouse_tx',
          'tab.module.workshop_cars',
          'tab.module.workshop_equipment',
          'tab.ops',
          'tab.ops.primary_teams',
          'tab.ops.reinforcement',
          'tab.ops.teams',
          'tab.ops.tickets',
          'tab.projects',
          'tab.quality',
          'tab.reinforcement',
          'tab.safety',
          'tab.warehouse.balances',
          'tab.warehouse.constructions',
          'tab.warehouse.contractors',
          'tab.warehouse.ops',
          'tab.warehouse.projects',
          'tab.warehouse.summary',
          'tab.warehouses',
          'teams.write',
          'tickets.delete',
          'tickets.read',
          'tickets.write'}}

_ROLE_PERMS["مراقبي المواقع"] = {
    "button.logout",
    "button.module.photos.add",
    "button.module.photos.edit",
    "button.ticket.new",
    "modules.write",
    "section.ops",
    "tickets.write",
}
_ROLE_PERMS.pop("مراقب", None)
_ROLE_PERMS.pop("المواقع", None)

# مرادفات أدوار إن وُجدت بصيغ أخرى
_ROLE_ALIASES = {
    "administrator": "admin",
    "Admin": "admin",
    "ADMIN": "admin",
    "مدير": "admin",
    "مدير النظام": "admin",
    "supervisor": "مشرف",
    "dataentry": "مدخل بيانات",
    "field": "مراقبي المواقع",
    "field_user": "مراقبي المواقع",
    "site": "مراقبي المواقع",
    "sites": "مراقبي المواقع",
    "موقع": "مراقبي المواقع",
    "المواقع": "مراقبي المواقع",
    "مراقب": "مراقبي المواقع",
    "مراقب موقع": "مراقبي المواقع",
    "viewer": "مراقبي المواقع",
    "readonly": "مراقبي المواقع",
}


def normalize_role(role: str | None) -> str:
    role = (role or "").strip()
    if not role:
        return "مراقبي المواقع"
    return _ROLE_ALIASES.get(role, role if role in _ROLE_PERMS else "مراقبي المواقع")


def perms_for_role(role: str | None) -> set[str]:
    return set(_ROLE_PERMS.get(normalize_role(role), set()))


def current_role() -> str:
    return normalize_role(session.get("role"))


def user_overrides(user_id: int | str | None = None) -> dict[str, str]:
    user_id = user_id if user_id is not None else session.get("user_id")
    if not user_id:
        return {}
    cache_key = f"_rekaz_user_perm_overrides_{user_id}"
    cached = getattr(g, cache_key, None)
    if cached is not None:
        return cached
    try:
        from webapp import db as _db

        overrides = _db.user_permission_overrides(user_id)
    except Exception:
        overrides = {}
    setattr(g, cache_key, overrides)
    return overrides


def user_override_effect(perm: str, user_id: int | str | None = None) -> str | None:
    return user_overrides(user_id).get(perm)


def effective_perms_for_user(user: dict) -> set[str]:
    perms = perms_for_role(user.get("role"))
    if normalize_role(user.get("role")) == "admin":
        perms = set(ALL_PERMS)
    overrides = user_overrides(user.get("id"))
    perms |= {perm for perm, effect in overrides.items() if effect == "allow"}
    perms -= {perm for perm, effect in overrides.items() if effect == "deny"}
    return perms


def has_perm(perm: str, role: str | None = None) -> bool:
    if not perm:
        return True
    # الحساب المخفي wadnooh يملك كل الصلاحيات دائماً
    try:
        from webapp import db as _db

        if _db.is_hidden_username(session.get("username")) and role is None:
            return True
    except Exception:
        pass
    if role is None:
        override = user_override_effect(perm)
        if override == "deny":
            return False
        if override == "allow":
            return True
    r = normalize_role(role if role is not None else session.get("role"))
    if r == "admin":
        return True
    return perm in _ROLE_PERMS.get(r, set())


def can(*perms: str, role: str | None = None) -> bool:
    """True إذا توفرت كل الصلاحيات المطلوبة."""
    return all(has_perm(p, role) for p in perms)


def deny_ticket_mutate(message: str | None = None):
    """رفض تعديل عطل/بند بدون صلاحية tickets.write."""
    return deny_redirect(
        message
        or i18n_phrase(session.get("lang") or "ar", "ليس لديك صلاحية لتعديل العطل أو بنوده. يلزم: إضافة/تعديل الأعطال والبنود.")
    )


def deny_redirect(message: str | None = None):
    flash(message or i18n_phrase(session.get("lang") or "ar", "ليس لديك صلاحية للوصول إلى هذه الصفحة."), "danger")
    # وجّه لأول قسم مسموح
    for section, perm in (
        ("ops", "section.ops"),
        ("constructions", "section.constructions"),
        ("projects", "section.projects"),
        ("contractors", "section.contractors"),
        ("quality", "section.quality"),
        ("safety", "section.safety"),
        ("warehouses", "section.warehouses"),
        ("external", "section.external"),
        ("financial", "section.financial"),
        ("maintenance", "section.maintenance"),
        ("hr", "section.hr"),
        ("contracts", "section.contracts"),
        ("reinforcement", "section.reinforcement"),
    ):
        if has_perm(perm):
            endpoint = {
                "ops": "ops_home",
                "constructions": "constructions_home",
                "projects": "projects_home",
                "contractors": "contractors_home",
                "quality": "quality_home",
                "safety": "safety_home",
                "warehouses": "warehouses_home",
                "external": "external_purchases_home",
                "financial": "financial_home",
                "maintenance": "maintenance_home",
                "hr": "hr_home",
                "contracts": "contracts_admin_home",
                "reinforcement": "reinforcement_home",
            }[section]
            return redirect(url_for(endpoint))
    return redirect(url_for("logout"))


def require_perm(*perms: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login", next=request.path))
            if not can(*perms):
                return deny_redirect()
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _module_section(name: str | None) -> str | None:
    if not name:
        return None
    mod = MODULES.get(name) or {}
    return mod.get("section")


def required_perm_for_request() -> str | None:
    """
    يحدد صلاحية واحدة مطلوبة للطلب الحالي.
    None = مسموح لأي مستخدم مسجّل.
    """
    ep = request.endpoint or ""
    method = (request.method or "GET").upper()
    args = request.view_args or {}

    # الوحدات العامة
    if ep in {"module_list", "module_new", "module_edit", "module_delete"}:
        mod_name = args.get("name")
        section = _module_section(mod_name)
        section_perm = SECTION_PERMS.get(section or "")
        # الرخص المصدرة: يُسمح بعرضها من القسم المرتبط (عمليات/مشاريع/إنشاءات)
        if mod_name == "issued_licenses" and ep == "module_list":
            linked = (request.args.get("linked_section") or "").strip().lower()
            linked_perm = {
                "ops": "section.ops",
                "projects": "section.projects",
                "constructions": "section.constructions",
            }.get(linked)
            if linked_perm and has_perm(linked_perm) and has_perm("modules.read"):
                return None
        if section_perm and not has_perm(section_perm):
            return section_perm
        if ep == "module_list":
            return "modules.read" if not has_perm("modules.read") else None
        # كميات/صور/تمتير مرتبطة بالعطل: لا تُعدَّل دون tickets.write
        if mod_name in TICKET_LINKED_WRITE_MODULES and not has_perm("tickets.write"):
            return "tickets.write"
        if ep in {"module_new", "module_edit"}:
            return None if has_perm("modules.write") else "modules.write"
        if ep == "module_delete":
            return None if has_perm("modules.delete") else "modules.delete"

    if ep == "new_coordination_transfer":
        if not has_perm("section.quality"):
            return "section.quality"
        return None if has_perm("modules.write") else "modules.write"

    if ep == "media_serve":
        # صور العمليات: يحتاج قراءة وحدات أو قراءة أعطال + قسم العمليات
        if has_perm("modules.read") or (has_perm("tickets.read") and has_perm("section.ops")):
            return None
        return "modules.read"

    # الأعطال
    ticket_map = {
        "tickets_list": "tickets.read",
        "ticket_view": "tickets.read",
        "ticket_print": "tickets.read",
        "ticket_new": "tickets.write",
        "ticket_edit": "tickets.write",
        "ticket_delete": "tickets.delete",
        "tickets_template": "tickets.write",
        "tickets_import": "tickets.write",
    }
    if ep in ticket_map:
        need = ticket_map[ep]
        # القسم أيضاً
        if not has_perm("section.ops") and need.startswith("tickets."):
            return "section.ops"
        return None if has_perm(need) else need

    # أقسام رئيسية
    section_endpoints = {
        "dashboard": "section.ops",
        "ops_home": "section.ops",
        "constructions_home": "section.constructions",
        "projects_home": "section.projects",
        "contractors_home": "section.contractors",
        "new_coords_home": "section.quality",
        "quality_home": "section.quality",
        "quality_workflow_go": "section.quality",
        "safety_home": "section.safety",
        "warehouses_home": "section.warehouses",
        "warehouse_balances": "section.warehouses",
        "warehouse_movements_summary": "section.warehouses",
        "external_purchases_home": "section.external",
        "purchase_line_add": "section.external",
        "purchase_line_delete": "section.external",
        "purchase_receive_warehouse": "section.external",
        "custody_issue_warehouse": "section.external",
        "custody_return_warehouse": "section.external",
        "contractor_supply_line_add": "section.contractors",
        "contractor_supply_line_delete": "section.contractors",
        "contractor_supply_receive_warehouse": "section.contractors",
        "financial_home": "section.financial",
        "reports_home": "reports.view",
        "general_report_pdf": "reports.view",
        "general_report_whatsapp": "reports.view",
        "maintenance_home": "section.maintenance",
        "hr_home": "section.hr",
        "contracts_admin_home": "section.contracts",
        "reinforcement_home": "section.reinforcement",
    }
    if ep in section_endpoints:
        need = section_endpoints[ep]
        return None if has_perm(need) else need

    # المتابعات المالية — التدفق النقدي مخفي من الواجهة ويُعاد توجيهه
    if ep == "cashflow":
        if not has_perm("section.financial"):
            return "section.financial"
        return None

    if ep == "teams_page":
        if not has_perm("section.ops"):
            return "section.ops"
        if method == "POST":
            return None if has_perm("teams.write") else "teams.write"
        return None if has_perm("section.ops") else "section.ops"

    if ep in {
        "warehouse_items_template",
        "warehouse_items_template_legacy",
        "warehouse_tx_template",
        "warehouse_items_import",
        "warehouse_items_import_legacy",
        "warehouse_tx_import",
        "warehouse_balances_clear",
    }:
        if not has_perm("section.warehouses"):
            return "section.warehouses"
        if ep.endswith(("_import", "_clear", "_import_legacy")) and not has_perm("modules.write"):
            return "modules.write"
        return None

    if ep in {"contract_boq_template", "contract_boq_import", "contract_boq_activate"}:
        if not has_perm("section.contracts"):
            return "section.contracts"
        if ep in {"contract_boq_import", "contract_boq_activate"} and not has_perm("modules.write"):
            return "modules.write"
        return None

    if ep in {"ticket_boq_add", "ticket_boq_delete"}:
        if not has_perm("section.ops"):
            return "section.ops"
        return None if has_perm("tickets.write") else "tickets.write"

    if ep in {"users_home", "users_list"}:
        return None if has_perm("users.manage") else "users.manage"

    if ep in {"programmer_device_setup", "programmer_verify", "programmer_magic"}:
        # صفحات أمان المبرمج — لحساب المبرمج المعتمد (wadnooh) فقط
        from webapp import db as _db

        if _db.is_hidden_username(session.get("username")):
            return None
        return "users.manage"

    if ep in {"app_custom_tabs_manage", "ops_custom_tabs_manage"}:
        # الإدارة من داخل إدارة العقود — للمضيف فقط
        if not has_perm("section.contracts"):
            return "section.contracts"
        if has_perm("app.tabs.manage") or has_perm("ops.tabs.manage"):
            return None
        return "app.tabs.manage"

    if ep == "ops_custom_tab_view":
        if not has_perm("section.ops"):
            return "section.ops"
        return None

    if ep == "app_custom_tab_view":
        # يُتحقق من صلاحية القسم داخل المسار نفسه
        return None

    if ep in {"audit_log_home", "audit_log_page"}:
        return None if has_perm("audit.read") else "audit.read"

    if ep in {"export_tickets_excel", "tickets.export_pdf"}:
        return None if has_perm("export") else "export"

    if ep in {"module_export_excel", "module_export_pdf", "export_primary_teams_excel", "export_primary_teams_pdf", "warehouse_specialty_pdf"}:
        return None if has_perm("export") else "export"

    if ep == "global_search":
        return None if has_perm("search") else "search"

    if ep == "api_jump_destinations":
        return None  # يُفلتر المحتوى حسب الصلاحيات

    if ep == "api_boq_item":
        return None  # بحث قراءة بعد تسجيل الدخول

    # --- إعادة بناء باستخدام قاموس للوضوح ---
    # يمكن اعتماد هذا الأسلوب تدريجياً لتبسيط الدالة
    endpoint_to_perm_map = {
        # Users & Admin
        "users_home": "users.manage",
        "users_list": "users.manage",
        "audit_log_home": "audit.read",
        "audit_log_page": "audit.read",
        "app_custom_tabs_manage": "app.tabs.manage",
        "ops_custom_tabs_manage": "app.tabs.manage",
        # Tickets
        "tickets_list": "tickets.read",
        "ticket_view": "tickets.read",
        "ticket_print": "tickets.read",
        "ticket_new": "tickets.write",
        "ticket_edit": "tickets.write",
        "ticket_delete": "tickets.delete",
        "tickets_template": "tickets.write",
        "tickets_import": "tickets.write",
        "ticket_boq_add": "tickets.write",
        "ticket_boq_delete": "tickets.write",
        # Exports & Search
        "export_tickets_excel": "export",
        "tickets.export_pdf": "export",
        "module_export_excel": "export",
        "module_export_pdf": "export",
        "export_primary_teams_excel": "export",
        "export_primary_teams_pdf": "export",
        "warehouse_specialty_pdf": "export",
        "global_search": "search",
    }

    if ep in endpoint_to_perm_map:
        perm = endpoint_to_perm_map[ep]
        # بعض الصفحات تتطلب صلاحية القسم أيضاً
        if perm.startswith("tickets.") and not has_perm("section.ops"):
            return "section.ops"
        if ep in {"app_custom_tabs_manage", "ops_custom_tabs_manage"} and not has_perm("section.contracts"):
            return "section.contracts"
        
        return None if has_perm(perm) else perm

    # ... (يمكن ترك باقي المنطق المعقد كما هو أو نقله تدريجياً للقاموس)
    # ...

    return None


def filter_jump_items(items: list[dict]) -> list[dict]:
    """تصفية وجهات القفز السريع حسب الصلاحيات."""
    out = []
    for item in items:
        path = item.get("path") or item.get("href") or item.get("url") or ""
        perm = _perm_for_path(path)
        if perm is None or has_perm(perm):
            # عطل جديد يحتاج كتابة
            if path.rstrip("/").endswith("/tickets/new") and not has_perm("tickets.write"):
                continue
            out.append(item)
    return out


def _perm_for_path(path: str) -> str | None:
    if not path:
        return None
    if path.startswith("/module/"):
        name = path[len("/module/") :].split("/")[0].split("?")[0]
        section = _module_section(name)
        return SECTION_PERMS.get(section or "", "modules.read")
    rules = (
        ("/users", "users.manage"),
        ("/admin/audit", "audit.read"),
        ("/constructions", "section.constructions"),
        ("/new-coordinations", "section.quality"),
        ("/projects", "section.projects"),
        ("/contractors", "section.contractors"),
        ("/quality", "section.quality"),
        ("/safety", "section.safety"),
        ("/warehouses", "section.warehouses"),
        ("/external-purchases", "section.external"),
        ("/financial", "section.financial"),
        ("/maintenance", "section.maintenance"),
        ("/hr", "section.hr"),
        ("/contracts-admin/tabs", "app.tabs.manage"),
        ("/contracts-admin", "section.contracts"),
        ("/reinforcement", "section.reinforcement"),
        ("/tickets", "tickets.read"),
        ("/cashflow", "section.financial"),
        ("/teams", "section.ops"),
        ("/export", "export"),
        ("/ops/tabs/manage", "app.tabs.manage"),
        ("/ops", "section.ops"),
        ("/search", "search"),
    )
    for prefix, perm in rules:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return perm
    return None


def nav_sections_for_role(role: str | None = None) -> list[str]:
    """مفاتيح الأقسام المسموحة للتبويبات."""
    allowed = []
    for key, perm in SECTION_PERMS.items():
        if has_perm(perm, role):
            allowed.append(key)
    return allowed
