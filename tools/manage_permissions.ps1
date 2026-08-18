param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("list", "add-permission", "grant-role", "revoke-role", "set-user-role", "list-users", "list-user-permissions", "allow-user-permission", "deny-user-permission", "clear-user-permission")]
    [string]$Action,

    [string]$Permission,
    [string]$Label,
    [string]$Role,
    [string]$User,
    [string]$Root = "",
    [string]$DbPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-PythonCommand {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return "python" }
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) { return "py" }
    throw "Python is required. Install Python or add it to PATH."
}

function Invoke-RekazPython {
    param(
        [Parameter(Mandatory = $true)] [string]$Code,
        [string[]]$Args = @()
    )
    $tmp = Join-Path $env:TEMP ("rekaz-permissions-{0}.py" -f ([guid]::NewGuid().ToString("N")))
    Set-Content -LiteralPath $tmp -Value $Code -Encoding UTF8
    try {
        $python = Get-PythonCommand
        & $python $tmp @Args
        if ($LASTEXITCODE -ne 0) { throw "Python command failed with exit code $LASTEXITCODE" }
    }
    finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Normalize-PermissionKey {
    param([string]$Value)
    if ($null -eq $Value) { return "" }
    $key = $Value.Trim().ToLowerInvariant()
    $key = $key -replace "\s+", "_"
    $key = $key -replace "[^a-z0-9_.:-]", ""
    return $key
}

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
} else {
    $Root = (Resolve-Path -LiteralPath $Root).Path
}
$PermissionsFile = Join-Path $Root "webapp\permissions.py"
if (-not (Test-Path -LiteralPath $PermissionsFile)) {
    throw "Cannot find webapp\permissions.py under $Root"
}
if (-not $DbPath) {
    $DbPath = Join-Path $Root "instance\rakaz.db"
}

$code = @'
import ast
import importlib
import sqlite3
import sys
from pathlib import Path

def arg(index):
    value = sys.argv[index] if len(sys.argv) > index else ""
    return "" if value == "__EMPTY__" else value


root = Path(arg(1))
action = arg(2)
perm = arg(3)
label = arg(4)
role = arg(5)
user = arg(6)
db_path = Path(arg(7))
permissions_path = root / "webapp" / "permissions.py"

text = permissions_path.read_text(encoding="utf-8")
module = ast.parse(text)


def literal_for(name):
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except Exception:
                        sys.path.insert(0, str(root))
                        mod = importlib.import_module("webapp.permissions")
                        return getattr(mod, name)
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == name:
                try:
                    return ast.literal_eval(node.value)
                except Exception:
                    sys.path.insert(0, str(root))
                    mod = importlib.import_module("webapp.permissions")
                    return getattr(mod, name)
    raise SystemExit(f"Cannot find {name} in permissions.py")


def replace_assignment(source, name, value_text):
    tree = ast.parse(source)
    target_node = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    target_node = node
                    break
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == name:
                target_node = node
        if target_node:
            break
    if not target_node:
        raise SystemExit(f"Cannot find {name}")
    lines = source.splitlines(keepends=True)
    start = target_node.lineno - 1
    end = target_node.end_lineno
    replacement = f"{name} = {value_text}\n"
    return "".join(lines[:start]) + replacement + "".join(lines[end:])


def q(value):
    return repr(str(value))


def format_perm_labels(labels):
    rows = ["{"]
    for key in sorted(labels):
        rows.append(f"    {q(key)}: {q(labels[key])},")
    rows.append("}")
    return "\n".join(rows)


def format_role_perms(role_perms):
    rows = ["{"]
    for role_name in sorted(role_perms):
        rows.append(f"    {q(role_name)}: {{")
        for key in sorted(role_perms[role_name]):
            rows.append(f"        {q(key)},")
        rows.append("    },")
    rows.append("}")
    return "\n".join(rows)


def save(labels=None, role_perms=None):
    global text
    if labels is not None:
        text = replace_assignment(text, "PERM_LABELS", format_perm_labels(labels))
    if role_perms is not None:
        text = replace_assignment(text, "_ROLE_PERMS", format_role_perms(role_perms))
    permissions_path.write_text(text, encoding="utf-8")


def ensure_permission(labels, key, lbl):
    if not key:
        raise SystemExit("Permission key is required")
    if key not in labels:
        labels[key] = lbl or key


def ensure_user_permission_overrides_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_permission_overrides (
            user_id INTEGER NOT NULL,
            perm TEXT NOT NULL,
            effect TEXT NOT NULL CHECK(effect IN ('allow','deny')),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, perm)
        )
        """
    )


def find_user(conn, value):
    if not value:
        raise SystemExit("User is required")
    if str(value).isdigit():
        row = conn.execute("SELECT * FROM users WHERE id=?", (int(value),)).fetchone()
    else:
        row = conn.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (value,)).fetchone()
    if not row:
        raise SystemExit(f"User not found: {value}")
    return row



if action == "list":
    labels = literal_for("PERM_LABELS")
    role_perms = literal_for("_ROLE_PERMS")
    print("Permissions:")
    for key in sorted(labels):
        print(f"  {key}: {labels[key]}")
    print("\nRoles:")
    for role_name in sorted(role_perms):
        print(f"  {role_name}: {len(role_perms[role_name])} permissions")

elif action == "add-permission":
    labels = literal_for("PERM_LABELS")
    ensure_permission(labels, perm, label)
    save(labels=labels)
    print(f"Added permission: {perm}")

elif action in {"grant-role", "revoke-role"}:
    labels = literal_for("PERM_LABELS")
    role_perms = literal_for("_ROLE_PERMS")
    if not role:
        raise SystemExit("Role is required")
    ensure_permission(labels, perm, label)
    role_perms.setdefault(role, set())
    role_perms[role] = set(role_perms[role])
    if action == "grant-role":
        role_perms[role].add(perm)
        print(f"Granted {perm} to role {role}")
    else:
        role_perms[role].discard(perm)
        print(f"Revoked {perm} from role {role}")
    save(labels=labels, role_perms=role_perms)

elif action == "list-users":
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_user_permission_overrides_table(conn)
        for row in conn.execute(
            """
            SELECT u.id, u.username, u.full_name, u.role, u.active,
                   SUM(CASE WHEN o.effect='allow' THEN 1 ELSE 0 END) AS allow_count,
                   SUM(CASE WHEN o.effect='deny' THEN 1 ELSE 0 END) AS deny_count
            FROM users u
            LEFT JOIN user_permission_overrides o ON o.user_id=u.id
            GROUP BY u.id
            ORDER BY u.username
            """
        ):
            print(f"{row['id']}\t{row['username']}\t{row['full_name']}\t{row['role']}\tactive={row['active']}\tallow={row['allow_count'] or 0}\tdeny={row['deny_count'] or 0}")
    finally:
        conn.close()

elif action == "list-user-permissions":
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_user_permission_overrides_table(conn)
        target = find_user(conn, user)
        print(f"User: {target['username']} ({target['role']})")
        rows = conn.execute(
            "SELECT perm, effect FROM user_permission_overrides WHERE user_id=? ORDER BY perm",
            (target["id"],),
        ).fetchall()
        if not rows:
            print("No user-specific permission overrides.")
        for row in rows:
            print(f"{row['effect']}\t{row['perm']}")
    finally:
        conn.close()

elif action in {"allow-user-permission", "deny-user-permission", "clear-user-permission"}:
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    labels = literal_for("PERM_LABELS")
    if action != "clear-user-permission":
        ensure_permission(labels, perm, label)
        if perm not in labels:
            raise SystemExit(f"Permission not found: {perm}")
    elif not perm:
        pass
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_user_permission_overrides_table(conn)
        target = find_user(conn, user)
        if action == "clear-user-permission":
            if perm:
                conn.execute("DELETE FROM user_permission_overrides WHERE user_id=? AND perm=?", (target["id"], perm))
                print(f"Cleared user override {perm} for {target['username']}")
            else:
                conn.execute("DELETE FROM user_permission_overrides WHERE user_id=?", (target["id"],))
                print(f"Cleared all user permission overrides for {target['username']}")
        else:
            effect = "allow" if action == "allow-user-permission" else "deny"
            save(labels=labels)
            conn.execute(
                """
                INSERT INTO user_permission_overrides(user_id, perm, effect)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, perm) DO UPDATE SET effect=excluded.effect
                """,
                (target["id"], perm, effect),
            )
            print(f"{effect} {perm} for user {target['username']}")
        conn.commit()
    finally:
        conn.close()

elif action == "set-user-role":
    if not user or not role:
        raise SystemExit("User and Role are required")
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("UPDATE users SET role=? WHERE lower(username)=lower(?)", (role, user))
        conn.commit()
        if cur.rowcount == 0:
            raise SystemExit(f"User not found: {user}")
        print(f"Changed user {user} role to {role}")
    finally:
        conn.close()
'@

$permissionKey = Normalize-PermissionKey $Permission
function ArgOrEmpty {
    param([string]$Value)
    if ([string]::IsNullOrEmpty($Value)) { return "__EMPTY__" }
    return $Value
}
$argsForPython = @(
    (ArgOrEmpty $Root),
    (ArgOrEmpty $Action),
    (ArgOrEmpty $permissionKey),
    (ArgOrEmpty $Label),
    (ArgOrEmpty $Role),
    (ArgOrEmpty $User),
    (ArgOrEmpty $DbPath)
)
Invoke-RekazPython -Code $code -Args $argsForPython
