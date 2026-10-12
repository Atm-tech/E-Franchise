"""Verify a backup and restore to a NEW database path; never replaces live data."""
import argparse
from pathlib import Path
from backup_database import backup


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args()
    print(backup(args.backup,args.destination))
    print('Verified restore created. Stop the app, then set E_FRANCHISE_DB to this path and restart.')
