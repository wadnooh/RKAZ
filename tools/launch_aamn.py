#!/usr/bin/env python3
"""Provision Rekaz data models in reportrek; do not store API credentials.

Schema provisioning does not port backend business rules or migrate data.
Data application requires an explicitly selected production SQLite snapshot.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import getpass
import json
import os
import re
from pathlib import Path
import sys
import xmlrpc.client

import rekaz_odoo_sync as sync
import pull_rekaz_production

TARGET = 'https://reportrek.odoo.com'
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--setup', action='store_true')
    modes.add_argument('--cost-menu-move', action='store_true',
                       help='Move existing cost-center menus to Accounting without changing financial data')
    modes.add_argument('--task-tabs-publish', action='store_true',
                       help='Show existing contract and claims tabs without changing workflow or financial records')
    modes.add_argument('--contract-tab-move', action='store_true',
                       help='Move the contract selector from BOQ to transaction numbers')
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--documents-copy', action='store_true', help='Copy source files into Odoo Documents linked to Projects')
    modes.add_argument('--inventory-copy', action='store_true',
                       help='Disabled for this deployment; does not import historical stock movements')
    modes.add_argument('--inventory-setup', action='store_true',
                       help='Install per-transaction receipts, issues, returns, scrap, and live balances')
    modes.add_argument('--inventory-live-update', action='store_true',
                       help='Read material indicators live from native movements without changing stock quantities')
    modes.add_argument('--inventory-dashboard-cleanup', action='store_true',
                       help='Hide duplicate damage cards from the inventory overview only')
    modes.add_argument('--inventory-warehouse-audit', action='store_true',
                       help='Read-only audit of company warehouses, stock, and picking history')
    modes.add_argument('--inventory-migrate', action='store_true',
                       help='Create reviewable draft transfers for verified current transaction stock balances')
    modes.add_argument('--projects-copy', action='store_true', help='Copy live Rekaz records into native Odoo Projects and tasks')
    modes.add_argument('--finance-setup', action='store_true', help='Install custom Rekaz financial linking automations')
    modes.add_argument('--claims-setup', action='store_true', help='Install native Project claim validation and finance follow-up')
    modes.add_argument('--claims-accounting-setup', action='store_true', help='Install explicit draft journal entries linked to claims')
    modes.add_argument('--boq-setup', action='store_true', help='Install contract catalogue and quantity entry screens')
    modes.add_argument('--boq-copy', action='store_true', help='Import verified contract items and quantities from the production snapshot')
    modes.add_argument('--diagnose', action='store_true', help='Read-only diagnosis of target conflicts and source links')
    modes.add_argument('--recover', action='store_true', help='Audit duplicates, retain identical redundant copies, audit again, then resume transfer')
    modes.add_argument('--check', action='store_true', help='Check authentication and company access without creating or transferring records')
    parser.add_argument('--sqlite', type=Path)
    parser.add_argument('--uploads', type=Path)
    parser.add_argument('--from-production', action='store_true',
                        help='Fetch current production data and attachments over SSH before applying')
    parser.add_argument('--ssh-key', type=Path, help='Path to an authorized production SSH key; used only with --from-production')
    parser.add_argument('--latest-production', action='store_true',
                        help='Apply the newest completed downloaded production snapshot and attachments')
    parser.add_argument('--allow-renamed', action='store_true',
                        help='Allow duplicate recovery to retain records whose data fields match but display name differs')
    args = parser.parse_args()
    if args.inventory_copy:
        parser.error('--inventory-copy is disabled to preserve historical stock data; use --inventory-setup for new transaction-linked movements only')
    if (args.documents_copy or args.boq_copy) and not args.latest_production:
        parser.error('--documents-copy and --boq-copy require --latest-production')
    if args.allow_renamed and not args.recover:
        parser.error('--allow-renamed requires --recover')
    if args.inventory_migrate and not (args.sqlite or args.latest_production or args.from_production):
        parser.error('--inventory-migrate requires an explicit production snapshot via --sqlite, --latest-production, or --from-production')
    if args.from_production and (not (args.apply or args.projects_copy or args.inventory_migrate)
                                 or args.sqlite or args.uploads):
        parser.error('--from-production requires --apply, --projects-copy, or --inventory-migrate and selects its own SQLite/uploads source')
    if args.ssh_key and not args.from_production:
        parser.error('--ssh-key requires --from-production')
    if args.recover and not args.latest_production:
        parser.error('--recover requires --latest-production')
    if args.latest_production:
        if not (args.apply or args.recover or args.diagnose or args.projects_copy or args.inventory_copy or args.inventory_migrate or args.documents_copy or args.boq_copy) or args.from_production or args.sqlite or args.uploads:
            parser.error('--latest-production requires a compatible transfer mode and selects its own downloaded source')
        folder = ROOT / 'instance' / 'odoo_production_snapshots'
        candidates = sorted((p for p in folder.iterdir() if p.is_dir()
                             and (p / 'production.json').is_file()
                             and (p / 'rakaz.db').is_file()), reverse=True) if folder.is_dir() else []
        if not candidates:
            raise ValueError('No completed production snapshot is available')
        chosen = candidates[0]
        meta = json.loads((chosen / 'production.json').read_text(encoding='utf-8'))
        if meta.get('host') != '191.101.2.59' or meta.get('database') != '/opt/rekaz/data/rakaz.db':
            raise ValueError('Production snapshot origin does not match the authorized Rekaz server')
        if meta.get('s3_missing_refs') and not args.inventory_migrate:
            raise ValueError('Production snapshot has missing S3 files; resolve these before transferring all data')
        args.sqlite, args.uploads = chosen / 'rakaz.db', chosen / 'uploads'
        print('Production snapshot: ' + str(chosen))
    if args.from_production:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        args.sqlite, args.uploads = pull_rekaz_production.pull(
            ROOT / 'instance' / 'odoo_production_snapshots' / stamp, args.ssh_key)
    if args.apply and not args.sqlite:
        parser.error('--apply requires --sqlite with the verified production snapshot')
    read_only_inventory_audit = args.inventory_warehouse_audit
    no_source = args.inventory_live_update or args.inventory_dashboard_cleanup
    source = (args.sqlite or ROOT / 'instance' / 'rakaz.db').resolve(
        strict=not (no_source or args.check or args.inventory_setup or args.cost_menu_move or args.task_tabs_publish or args.contract_tab_move or read_only_inventory_audit))
    if args.uploads and not args.inventory_migrate and not args.uploads.is_dir():
        parser.error('--uploads must be an existing attachment directory')
    with (sync.m.snapshot(source) if not (
            no_source or args.check or args.inventory_setup or args.cost_menu_move or args.task_tabs_publish or args.contract_tab_move or read_only_inventory_audit)
            else contextlib.nullcontext()) as conn:
        if not (no_source or args.check or args.inventory_setup or args.cost_menu_move or args.task_tabs_publish or args.contract_tab_move or read_only_inventory_audit):
            sync.m.catalog(conn)
        if args.apply or args.recover or args.inventory_migrate:
            if args.inventory_migrate and conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Inventory migration source snapshot integrity check failed')
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(tickets)')}
            for column in ['ticket_no', 'rekaz_code']:
                if column in columns and conn.execute(
                    'SELECT 1 FROM tickets WHERE upper(trim("' + column + '")) '
                    'LIKE ? LIMIT 1', ('TEST%',)
                ).fetchone():
                    raise ValueError('Source contains TEST ticket identities; select a clean verified production snapshot')
            if args.latest_production:
                if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Production snapshot integrity check failed')
                if not args.inventory_migrate:
                    specs = sync.m.catalog(conn)
                    refs = {ref for table, spec in specs.items()
                            for row in sync.m.source_rows(conn, table, spec)
                            for ref in sync.m.media_refs(row)}
                    missing = sum(sync.m.local_media(args.uploads, ref) is None for ref in refs)
                    if missing:
                        raise ValueError('Production snapshot is missing ' + str(missing) + ' attachment files; transfer was not started')
    print('Target: ' + TARGET)
    print('Mode: inventory dashboard cleanup' if args.inventory_dashboard_cleanup else 'Mode: live inventory indicators' if args.inventory_live_update else 'Mode: move contract to transaction numbers' if args.contract_tab_move else 'Mode: publish existing task tabs' if args.task_tabs_publish else 'Mode: move cost menus to Accounting' if args.cost_menu_move else 'Mode: authentication check only' if args.check else (
        'Mode: read-only inventory warehouse audit' if args.inventory_warehouse_audit else
        ('Mode: transaction inventory setup' if args.inventory_setup else
        ('Mode: transaction stock balance migration to reviewable drafts' if args.inventory_migrate else
        ('Mode: schema only' if args.setup else 'Mode: production snapshot transfer')))))
    database = input('Odoo database name [reportrek] (not a URL): ').strip() or 'reportrek'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', database):
        raise ValueError('Enter the database name only, for example reportrek; do not enter https://reportrek.odoo.com/')
    login = input('Authorized Odoo login/email: ').strip()
    if not login:
        raise ValueError('Authorized Odoo login is required')
    secret = getpass.getpass('RPC API key (hidden; not stored): ').strip()
    if not all([database, login, secret]):
        raise ValueError('Database, login and API key are required')
    if any(char.isspace() for char in secret):
        raise ValueError('Paste only the API key value, without Authorization or Bearer prefixes')
    sync.m.socket.setdefaulttimeout(45)
    common = xmlrpc.client.ServerProxy(TARGET + '/xmlrpc/2/common', allow_none=True)
    major = common.version()['server_version_info'][0]
    if major == 20:
        client = sync.JSON2Client(TARGET, database, secret)
        users = client.call('res.users', 'search_read', [[('login', '=', login)]],
                            fields=['id', 'login', 'company_ids'], limit=2)
        if len(users) != 1 or users[0].get('login', '').strip().casefold() != login.casefold():
            raise ValueError('Authentication failed: verify the database name, exact user login, and an unexpired API key. No schema or data was changed.')
        user = users[0]
        uid = user['id']
        companies = client.call('res.company', 'search_read',
                                [[('id', 'in', user['company_ids'])]], fields=['name'], limit=False)
    else:
        uid = common.authenticate(database, login, secret, {})
        if not uid:
            raise ValueError('Authentication failed: verify the database name, exact user login, and an unexpired API key with RPC scope for that same user. No schema or data was changed.')
        objects = xmlrpc.client.ServerProxy(TARGET + '/xmlrpc/2/object', allow_none=True)
        user = objects.execute_kw(database, uid, secret, 'res.users', 'read', [[uid]],
                                  {'fields': ['company_ids']})[0]
        companies = objects.execute_kw(database, uid, secret, 'res.company', 'search_read',
                                       [[('id', 'in', user['company_ids'])]], {'fields': ['name'], 'limit': False})
    if not companies:
        raise ValueError('No authorized company found')
    if len(companies) == 1:
        company = companies[0]
    else:
        for item in companies:
            print(str(item['id']) + ': ' + item['name'])
        selected = int(input('Target company ID: '))
        company = next((item for item in companies if item['id'] == selected), None)
        if company is None:
            raise ValueError('Company is not assigned to this user')
    print('Company: ' + company['name'])
    if args.inventory_dashboard_cleanup:
        if database != 'reportrek':
            raise ValueError('Dashboard cleanup is restricted to database reportrek')
        import odoo_inventory_dashboard_cleanup
        api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
        with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
            return odoo_inventory_dashboard_cleanup.cleanup(api)
    if args.inventory_live_update:
        if database != 'reportrek':
            raise ValueError('Live inventory update is restricted to database reportrek')
        import odoo_inventory_live_update
        api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
        with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
            return odoo_inventory_live_update.install(api)
    if args.contract_tab_move:
        if database != 'reportrek':
            raise ValueError('Contract relocation is restricted to database reportrek')
        import odoo_publish_task_tabs
        api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
        with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
            return odoo_publish_task_tabs.move_contract(api)
    if args.task_tabs_publish:
        if database != 'reportrek':
            raise ValueError('Task tab publication is restricted to database reportrek')
        import odoo_publish_task_tabs
        api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
        with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
            odoo_publish_task_tabs.publish(api, apply=True)
        return 0
    if args.cost_menu_move:
        if database != 'reportrek':
            raise ValueError('Cost menu migration is restricted to database reportrek')
        import odoo_cost_accounting_menu
        api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
        with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
            return odoo_cost_accounting_menu.move(api)
    if args.check:
        print('Authentication and company read access verified. No schema or data was changed.')
        return 0
    if args.inventory_warehouse_audit:
        import odoo_project_inventory
        api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
        report = odoo_project_inventory.audit_warehouses(api)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    values = dict(ODOO_URL=TARGET, ODOO_DATABASE=database, ODOO_LOGIN=login,
                  ODOO_API_KEY=secret, ODOO_COMPANY_ID=str(company['id']),
                  ODOO_COMPANY_NAME=company['name'], REKAZ_SOURCE_KEY='rekaz-main',
                  ODOO_RPC_INTERVAL='2')
    old = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        if args.documents_copy:
            import odoo_documents_copy
            api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
            with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
                return odoo_documents_copy.copy(api, source, args.uploads)
        if args.inventory_setup:
            import odoo_project_inventory
            api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
            with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
                odoo_project_inventory.install(api)
            return 0
        if args.inventory_migrate:
            import odoo_project_inventory
            api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
            with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
                return odoo_project_inventory.migrate_current_transaction_stock(api, source)
        if args.inventory_copy:
            import odoo_project_inventory
            api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
            with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
                return odoo_project_inventory.copy(api, source)
        if args.projects_copy:
            import odoo_projects_copy
            api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
            with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
                odoo_projects_copy.copy(api, sqlite_source=source if (args.latest_production or args.from_production) else None)
            return 0
        if args.boq_setup or args.boq_copy:
            import odoo_contract_boq
            api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
            with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
                odoo_contract_boq.install(api)
                if args.boq_setup:
                    return 0
                return odoo_contract_boq.copy(api, source)
        if args.claims_accounting_setup:
            import odoo_claim_accounting
            api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
            with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
                odoo_claim_accounting.install(api)
            return 0
        if args.claims_setup:
            import odoo_native_claims
            api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
            with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
                odoo_native_claims.install(api)
            return 0
        if args.finance_setup:
            import odoo_finance_link
            api = sync.API(TARGET, database, login, secret, company['id'], company['name'])
            with sync.writer_lock(ROOT / 'instance' / 'odoo_sync.lock'):
                odoo_finance_link.install(api)
            return 0
        command = ['--setup' if args.setup else ('--diagnose' if args.diagnose else '--apply'), '--sqlite', str(source),
                   '--report', str(ROOT / 'outputs' / ('reportrek_diagnosis.json' if args.diagnose else ('reportrek_setup_report.json' if args.setup else 'reportrek_apply_report.json')))]
        if args.uploads:
            command += ['--uploads', str(args.uploads.resolve())]
        if args.recover:
            return recover_transfer(command, allow_renamed=args.allow_renamed)
        return sync.main(command)
    finally:
        for key, previous in old.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def recover_transfer(command, allow_renamed=False):
    """Never resume a transfer until a fresh duplicate audit is clean."""
    report_index = command.index('--report') + 1
    def run(mode, name, extra=None):
        step = list(command)
        step[0] = mode
        step[report_index] = str(ROOT / 'outputs' / name)
        if extra:
            step.extend(extra)
        return sync.main(step)
    print('Recovery: auditing target source identities', flush=True)
    result = run('--audit-duplicates', 'reportrek_duplicate_audit.json')
    if result not in (0, 2):
        return result
    if result == 2:
        print('Recovery: retaining identical duplicate records; edited copies will block recovery', flush=True)
        extra = ['--allow-renamed'] if allow_renamed else []
        result = run('--repair-duplicates', 'reportrek_duplicate_repair.json', extra=extra)
        if result:
            repair_report = ROOT / 'outputs' / 'reportrek_duplicate_repair.json'
            if repair_report.is_file():
                try:
                    rep_data = json.loads(repair_report.read_text(encoding='utf-8'))
                    skipped = rep_data.get('repair', {}).get('skipped', [])
                    only_name_diff = all(
                        all(d.get('reasons') == ['name_differs_from_canonical'] and not d.get('fields')
                            for d in s.get('differences', []))
                        for s in skipped
                    ) if skipped else False
                    if only_name_diff and not allow_renamed:
                        print('Note: Skipped duplicates only differ in display name with identical data fields.', flush=True)
                        print('You can resume recovery and transfer by adding --allow-renamed to your command:', flush=True)
                        print('  python "tools/launch_aamn.py" --recover --latest-production --allow-renamed', flush=True)
                except Exception:
                    pass
            print('Recovery stopped: review the repair report; transfer was not resumed', flush=True)
            return result
    result = run('--audit-duplicates', 'reportrek_duplicate_audit_after.json')
    if result:
        print('Recovery stopped: target duplicate audit is not clean; transfer was not resumed', flush=True)
        return result
    print('Recovery: duplicate audit clean; resuming transfer', flush=True)
    return sync.main(command)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (xmlrpc.client.Fault, xmlrpc.client.ProtocolError):
        print('Odoo rejected authentication/API/schema access. No remote fault or credentials printed.', file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, EOFError) as exc:
        print('Setup stopped: ' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
