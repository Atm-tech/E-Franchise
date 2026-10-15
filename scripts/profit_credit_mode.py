"""Switch local profit crediting without disabling the background worker."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['immediate', 'delayed'])
args = parser.parse_args()
if os.getenv('APP_ENV', '').lower() == 'production' or os.getenv('DATABASE_URL', '').strip():
    raise SystemExit('This switch is for local testing only.')
with main.get_conn() as conn:
    cur = conn.cursor()
    main.upsert_setting(cur, 'profit_credit_mode', args.mode)
    released = main.release_matured_commissions(cur) if args.mode == 'immediate' else 0
    main.audit(cur, None, 'PROFIT_CREDIT_MODE', 'SETTING', None, args.mode)
print(f'Profit mode: {args.mode}. Released {released} eligible orders. Background worker remains enabled.')
