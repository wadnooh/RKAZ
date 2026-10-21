"""Resume the authorized production export without fetching S3 files again."""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import zipfile

from pull_rekaz_production import HOST, unpack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remote', required=True)
    parser.add_argument('--partial', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'/opt/rekaz/data/backups/odoo_export_[0-9TZ]+\.zip', args.remote):
        raise ValueError('Export must be an authorized Rekaz backup archive')
    key = Path.home() / '.ssh' / 'weeklyreport_client_ed25519_20260930b'
    offset = args.partial.stat().st_size if args.partial.exists() else 0
    code = ('import shutil,sys\nwith open(' + repr(args.remote) + ',"rb") as source:\n'
            + ' source.seek(' + str(offset) + ')\n shutil.copyfileobj(source,sys.stdout.buffer)\n')
    command = ['ssh','-T','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
               '-o','ConnectTimeout=30','-o','ConnectionAttempts=2',
               '-o','ServerAliveInterval=10','-o','ServerAliveCountMax=2',
               '-i',str(key),HOST,'python3 -']
    print('Resuming export after ' + str(offset) + ' bytes.', flush=True)
    args.partial.parent.mkdir(parents=True, exist_ok=True)
    with args.partial.open('ab') as output:
        try:
            result = subprocess.run(command,input=code.encode(),stdout=output,
                                    stderr=subprocess.PIPE,timeout=1800)
        except subprocess.TimeoutExpired:
            raise ValueError('Export download timed out; the received prefix is retained for resumption') from None
    if result.returncode:
        raise ValueError('Export download failed; the received prefix is retained for resumption')
    with zipfile.ZipFile(args.partial) as bundle:
        if bundle.testzip() is not None:
            raise ValueError('Export archive checksum verification failed')
    db, uploads = unpack(args.partial, args.destination)
    print('Production SQLite: ' + str(db))
    print('Production attachments: ' + str(uploads))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__,file=sys.stderr)
        sys.exit(1)
