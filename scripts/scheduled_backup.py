"""Daily online backup runner for Task Scheduler and cron; never deletes backups."""
import argparse
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from backup_database import backup


def run(source, folder):
    stamp = datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y%m%d-%H%M%S-%f')
    return backup(source, folder / f'e_franchise-{stamp}.db')


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    data = Path(os.getenv('E_FRANCHISE_DATA_DIR', str(root / 'data')))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(os.getenv('E_FRANCHISE_DB', str(data / 'e_franchise.db'))))
    parser.add_argument('--folder', type=Path, default=data / 'backups')
    args = parser.parse_args()
    print(run(args.source, args.folder))
