"""Read a consistent production snapshot and local attachments over authorized SSH."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import zipfile

HOST = 'root@191.101.2.59'
REMOTE = r'''
import hashlib, json, pathlib, sqlite3, subprocess, sys, tempfile, zipfile
if subprocess.run(['systemctl','is-active','--quiet','rekaz']).returncode:
    raise SystemExit(10)
source=pathlib.Path('/opt/rekaz/data/rakaz.db')
uploads=source.parent/'uploads'
if not source.is_file():
    raise SystemExit(11)
with tempfile.TemporaryDirectory(prefix='rekaz-odoo-') as temp:
    snapshot=pathlib.Path(temp)/'rakaz.db'
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src:
        with sqlite3.connect(snapshot) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                raise SystemExit(12)
    archive=pathlib.Path(temp)/'snapshot.zip'
    refs=set()
    excluded={'users','settings','audit_log','user_permission_overrides','whatsapp_logs',
              'programmer_devices','programmer_approve_codes','sqlite_sequence'}
    media_columns={'attachments','before_shot','during_shot','after_shot','quantities_shot','location_shot'}
    with sqlite3.connect(snapshot) as db:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if table in excluded:
                continue
            quoted='"'+table.replace('"','""')+'"'
            columns={r[1] for r in db.execute('PRAGMA table_info('+quoted+')')}
            for column in columns & media_columns:
                for (raw,) in db.execute('SELECT "'+column+'" FROM '+quoted):
                    if not isinstance(raw,str):
                        continue
                    try:
                        values=json.loads(raw)
                    except ValueError:
                        values=[raw]
                    if isinstance(values,str):
                        values=[values]
                    if isinstance(values,list):
                        refs.update(v for v in values if isinstance(v,str) and v.startswith('/media/s3/'))
    materialized={}
    missing=[]
    if refs:
        # Use the running service's existing AWS settings only inside this process.
        pid=subprocess.check_output(['systemctl','show','rekaz','-p','MainPID','--value'],text=True).strip()
        env={}
        for item in pathlib.Path('/proc/'+pid+'/environ').read_bytes().split(b'\0'):
            name,sep,value=item.partition(b'=')
            if sep and name.startswith(b'AWS_'):
                env[name.decode()]=value.decode()
        import boto3
        bucket=env.get('AWS_S3_BUCKET','').strip()
        if not bucket or not env.get('AWS_ACCESS_KEY_ID') or not env.get('AWS_SECRET_ACCESS_KEY'):
            raise SystemExit(14)
        prefix=(env.get('AWS_S3_PHOTOS_PREFIX','').strip() or 'rekaz-photos').strip('/')+'/'
        client=boto3.client('s3',region_name=env.get('AWS_S3_REGION','').strip() or 'eu-north-1',
                            aws_access_key_id=env['AWS_ACCESS_KEY_ID'].strip(),
                            aws_secret_access_key=env['AWS_SECRET_ACCESS_KEY'].strip())
        for ref in sorted(refs):
            key=ref.removeprefix('/media/s3/')
            if not key.startswith(prefix) or '..' in key.split('/'):
                raise SystemExit(15)
            rel='s3/'+hashlib.sha256(ref.encode()).hexdigest()+pathlib.PurePosixPath(key).suffix
            target=pathlib.Path(temp)/rel
            target.parent.mkdir(exist_ok=True)
            try:
                size=client.head_object(Bucket=bucket,Key=key)['ContentLength']
                if size>25*1024*1024:
                    raise SystemExit(17)
                client.download_file(bucket,key,str(target))
                materialized[ref]=rel
            except client.exceptions.ClientError as error:
                if error.response.get('Error',{}).get('Code') in {'404','NoSuchKey','NotFound'}:
                    missing.append(ref)
                else:
                    raise SystemExit(16)
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        z.write(snapshot,'rakaz.db')
        for file in uploads.rglob('*'):
            if file.is_file():
                resolved=file.resolve()
                if not resolved.is_relative_to(uploads.resolve()):
                    raise SystemExit(13)
                z.write(file,'uploads/'+file.relative_to(uploads).as_posix())
        for ref,rel in materialized.items():
            z.write(pathlib.Path(temp)/rel,'uploads/'+rel)
        z.writestr('uploads/.rekaz_media_manifest.json',json.dumps(materialized))
        z.writestr('production.json',json.dumps({'host':'191.101.2.59',
                   'database':str(source),'service':'rekaz','uploads':str(uploads),
                   'local_uploads_available':uploads.is_dir(),
                   's3_downloaded':len(materialized),'s3_missing_refs':missing}))
    with archive.open('rb') as f:
        import shutil
        shutil.copyfileobj(f,sys.stdout.buffer)
'''


def unpack(archive: Path, destination: Path):
    """Validate every member before writing any; never accept paths outside destination."""
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        names = [item.filename for item in members]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate snapshot paths')
        if sum(item.file_size for item in members) > 4 * 1024**3:
            raise ValueError('Production snapshot exceeds 4 GiB')
        for item in members:
            target = (root / item.filename).resolve()
            if ('\\' in item.filename or ':' in item.filename or '..' in item.filename.split('/')
                    or not target.is_relative_to(root)
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError('Unsafe snapshot path')
        meta = json.loads(bundle.read('production.json'))
        if meta.get('host') != '191.101.2.59' or meta.get('database') != '/opt/rekaz/data/rakaz.db':
            raise ValueError('Unexpected production source')
        if 'rakaz.db' not in names:
            raise ValueError('Missing production database')
        for item in members:
            target = root / item.filename
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
    (root / 'uploads').mkdir(exist_ok=True)
    return root / 'rakaz.db', root / 'uploads'


def pull(destination: Path, key: Path | None = None):
    if key is None:
        verified = Path.home() / '.ssh' / 'weeklyreport_client_ed25519_20260930b'
        key = verified if verified.is_file() else Path.home() / '.ssh' / 'weeklyreport_client_ed25519'
    if not key.is_file():
        raise ValueError('Authorized production SSH key is unavailable')
    destination.mkdir(parents=True, exist_ok=False)
    print('Reading production SQLite backup and attachments; source is unchanged.', flush=True)
    command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
               '-o', 'ConnectTimeout=30', '-o', 'ConnectionAttempts=2',
               '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
               '-i', str(key), HOST, '/opt/rekaz/.venv/bin/python -']
    with tempfile.TemporaryDirectory(prefix='rekaz-source-') as temp:
        archive = Path(temp) / 'snapshot.zip'
        try:
            with archive.open('wb') as output:
                result = subprocess.run(command, input=REMOTE.encode('utf-8'),
                                        stdout=output, stderr=subprocess.PIPE, timeout=1800)
        except subprocess.TimeoutExpired:
            raise ValueError('Production SSH timed out; Odoo transfer was not started') from None
        if result.returncode:
            detail = result.stderr.decode('utf-8', errors='replace').lower()
            reason = 'remote snapshot or SSH failure'
            if 'permission denied' in detail:
                reason = 'SSH authentication rejected: the configured production key is not authorized on the server'
            elif 'timed out' in detail or 'connection timeout' in detail:
                reason = 'SSH network timeout: the production server did not respond'
            elif 'host key verification failed' in detail:
                reason = 'SSH host identity verification failed; verify the server identity before reconnecting'
            elif 'connection refused' in detail:
                reason = 'SSH connection refused by the production server'
            raise ValueError('Production snapshot failed (SSH exit ' + str(result.returncode)
                             + '): ' + reason + '; Odoo transfer was not started')
        try:
            return unpack(archive, destination)
        except (zipfile.BadZipFile, KeyError):
            raise ValueError('Production snapshot archive is incomplete; Odoo transfer was not started') from None
