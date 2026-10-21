# Rekaz transaction cost centers

Target: `https://reportrek.odoo.com`, company `ركاز الانجاز`.

## Verified live on 2026-10-19

- Dedicated optional analytic plan, preserving the native Project Plan.
- 190 task analytic accounts linked by `project.task.x_rekaz_cost_center` and `account.analytic.account.x_rekaz_task`.
- Task create/name/partner/company automation creates and maintains the center.
- Native center list action 1348.
- Admin-only operational cost ledger `x_rekaz_cost_entry`, action 1350. No salary values were read or imported.
- Approved-only pivot action 1351. Drafts are excluded from this report.
- Category, actual quantity/rate, employee/equipment interval, evidence, source and accounting-reference fields.
- Approval requires evidence and calculation basis; resource intervals may not overlap approved resource costs.
- Database uniqueness for company/source and reversal; database trigger freezes approved costs against update/delete.
- Exact negative reversal of an approved original is supported once; no rewriting the original.
- Task page shows approved costs and their sum to system administrators.
- Owner electricity materials retain zero purchase-cost treatment. No stock movements, journal entries or wages were generated.

## Live rollback verification

The native server action verified quantity 3 × rate 2 = 6, rejected approval without evidence, rejected duplicate source at database level, and rejected SQL modification of an approved cost. All test rows were rolled back. Evidence: `outputs/rekaz_cost_integrity_verified.png`.

## Not complete / do not advertise as implemented

- Fully loaded effective-dated employee and owned/rented equipment rate profiles, overtime and productive-time denominators.
- Team participation history, work/item/stage allocation and approved native timesheet integration.
- Automatic financial analytic distribution, posted actual cost/revenue/invoice/collection reconciliation and double-count exclusion across all native sources.
- Many-to-many claim allocation across tasks; the legacy claim remains single-task and must be bridged with reviewed reconciliation.
- Shared expense pools with validated allocation totals/bases, period closing, budget approval, complete audit/reopening workflows.
- Final profitability/completeness calculation, planned-vs-actual BOQ profitability, equipment/team reports and PDF reports.
- Security roles for named users. Do not expand existing users' access to wages or cost rates implicitly.
- Expenses app (`hr_expense`) is not installed; user was informed it is needed for employee-expense integration. Payroll, assets, analytic accounting, timesheets, purchase, fleet, maintenance and documents were found installed.

## Installer sources

`tools/odoo_cost_centers_install.py`, `tools/odoo_cost_ledger_install.py`, `tools/odoo_cost_integrity_install.py`, `tools/odoo_cost_task_view_install.py`, `tools/odoo_cost_verify.py` are Odoo Execute Code bodies using the supplied `env`; they are not standalone CLI programs. Apply in that order, then run verification. Current server action 1346 is an admin-only setup surface and has no contextual binding.

Do not use the ledger's operational total plus the same referenced accounting expense as a combined cost. Native analytic-account balances remain accounting indicators and are not populated by these operational ledger rows. Missing costs must never imply a verified profit.
