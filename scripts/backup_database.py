"""Create and verify an online SQLite backup. Never overwrites an existing file."""
import argparse
from contextlib import closing
import sqlite3
from datetime import datetime
from pathlib import Path


def backup(source: Path, destination: Path):
    source=source.resolve();destination=destination.resolve()
    if not source.is_file(): raise ValueError('Source database does not exist')
    if source==destination or destination.exists(): raise ValueError('Destination must be a new backup file')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original:
        with closing(sqlite3.connect(destination)) as copy:
            original.backup(copy)
            if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('Backup integrity check failed')
    return destination


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=root/'data/e_franchise.db')
    parser.add_argument('--destination',type=Path,default=root/'data/backups'/f'e_franchise-{datetime.now():%Y%m%d-%H%M%S-%f}.db')
    args=parser.parse_args()
    print(backup(args.source,args.destination))
