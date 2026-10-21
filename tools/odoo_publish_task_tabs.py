"""Publish existing Rekaz BOQ and claims tab visibility changes."""

import sys
import re
import argparse
import copy
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from odoo_contract_boq import task_boq_view_arch
from odoo_native_claims import claim_page_arch


def move_contract(api):
    """Relocate the existing selector, retaining its domain and permissions."""
    found = {}
    for marker in ('rekaz_boq', 'rekaz_task_numbers'):
        rows = api.call('ir.ui.view', 'search_read', [[
            ('model', '=', 'project.task'), ('active', '=', True),
            ('arch_db', 'ilike', marker),
        ]], fields=['id', 'arch_db'], limit=False)
        matches = []
        for row in rows:
            root = ET.fromstring(row['arch_db'], parser=ET.XMLParser(
                target=ET.TreeBuilder(insert_comments=True)))
            pages = root.findall(".//page[@name='" + marker + "']")
            if len(pages) == 1:
                matches.append((row, root, pages[0]))
        if len(matches) != 1:
            raise ValueError('Expected one active task page: ' + marker)
        found[marker] = matches[0]
    source_row, source_root, source = found['rekaz_boq']
    target_row, target_root, target = found['rekaz_task_numbers']
    if source_row['id'] == target_row['id']:
        target_root = source_root
        target = source_root.find(".//page[@name='rekaz_task_numbers']")
    old = source.findall(".//field[@name='x_rekaz_boq_contract']")
    new = target.findall(".//field[@name='x_rekaz_boq_contract']")
    if len(old) > 1 or len(new) > 1 or not (old or new):
        raise ValueError('Contract selector is missing or ambiguous; no views changed')
    if not old:
        print('Contract selector already in transaction numbers. No changes needed.')
        return 0
    if not new:
        group = target.find('group')
        if group is None:
            raise ValueError('Transaction numbers group is absent; no views changed')
        group.insert(0, copy.deepcopy(old[0]))
    for parent in source.iter():
        if old[0] in list(parent):
            parent.remove(old[0])
            break
    changes = [(target_row, target_root)]
    if source_row['id'] != target_row['id']:
        changes.append((source_row, source_root))
    for row, root in changes:
        arch = ET.tostring(root, encoding='unicode')
        api.call('ir.ui.view', 'write', [[row['id']], {'arch_db': arch}])
        check = api.call('ir.ui.view', 'read', [[row['id']]], fields=['arch_db'])
        if len(check) != 1 or ET.tostring(ET.fromstring(check[0]['arch_db'])) != ET.tostring(ET.fromstring(arch)):
            raise ValueError('Contract relocation verification failed: ' + str(row['id']))
    print('Contract selector moved to transaction numbers and verified. Contract values, BOQ lines and workflow unchanged.')
    return 0


def publish(api, apply=False):
    View = "ir.ui.view"

    targets = [
        ("rekaz_boq", "العقد وبنود الكميات"),
        ("rekaz_claim", "المستخلص والمالية"),
    ]

    proposed = []

    for marker, title in targets:
        rows = api.call(
            View,
            "search_read",
            [[
                ("model", "=", "project.task"),
                ("arch_db", "ilike", marker),
            ]],
            fields=["id", "name", "arch_db"],
            limit=False,
        )

        matches = []

        for row in rows:
            arch = row.get("arch_db") or ""

            pattern = (
                r'<page\b[^>]*\bname=["\']'
                + re.escape(marker)
                + r'["\'][^>]*>'
            )

            found = list(re.finditer(pattern, arch))

            if len(found) == 1:
                matches.append((row, found[0]))

        if len(matches) != 1:
            raise RuntimeError(
                f"{title}: expected one existing view; "
                f"found {len(matches)}. No changes applied."
            )

        row, match = matches[0]
        arch = row["arch_db"]
        tag = match.group(0)

        if marker == "rekaz_boq":
            expected = task_boq_view_arch()
            if 'name="rekaz_boq" invisible=' in expected:
                raise RuntimeError(
                    "Local BOQ source still hides the tab."
                )
        else:
            expected = claim_page_arch(
                details_visibility=True
            )
            if "x_rekaz_boq_details_visible" in expected:
                raise RuntimeError(
                    "Local claims source still hides the tab."
                )

        new_tag = re.sub(
            r'\s+invisible=["\']not '
            r'x_rekaz_boq_details_visible["\']',
            "",
            tag,
        )

        new_arch = (
            arch[:match.start()]
            + new_tag
            + arch[match.end():]
        )

        proposed.append({
            "id": row["id"],
            "title": title,
            "old": arch,
            "new": new_arch,
        })

    for item in proposed:
        print(
            f"View {item['id']}: {item['title']} - "
            + (
                "CHANGE REQUIRED"
                if item["old"] != item["new"]
                else "ALREADY VISIBLE"
            )
        )

    if not apply:
        print("PREVIEW ONLY: no Odoo data changed.")
        return

    if all(x["old"] == x["new"] for x in proposed):
        print("All target tabs are already configured.")
        return

    for item in proposed:
        if item["old"] == item["new"]:
            continue

        api.call(
            View,
            "write",
            [[item["id"]], {
                "arch_db": item["new"]
            }],
        )

        check = api.call(
            View,
            "read",
            [[item["id"]]],
            fields=["arch_db"],
        )

        if (
            len(check) != 1
            or check[0]["arch_db"] != item["new"]
        ):
            raise RuntimeError(
                f"View verification failed: {item['id']}"
            )

        print(f"UPDATED: {item['title']}")

    print("Task tab visibility update completed.")
