"""Import clean local setup into an empty E-Franchise PostgreSQL schema.

Defaults to a rolled-back rehearsal. Credentials and backups stay under ignored data/.
Never imports orders, customers, financial history, or local test switches.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
import secrets
import struct
from decimal import Decimal

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.security import hash_password

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url-file', type=Path, required=True)
parser.add_argument('--source', type=Path, default=Path('data/e_franchise.db'))
parser.add_argument('--run-dir', type=Path, required=True)
parser.add_argument('--commit', action='store_true')
args = parser.parse_args()
folder = args.run_dir.resolve()
data_root = Path('data').resolve()
if not folder.is_relative_to(data_root):
    raise SystemExit('Run directory must be inside ignored data/.')
folder.mkdir(parents=True, exist_ok=True)
snapshot = folder / 'local-before-migration.sqlite'
if not snapshot.exists():
    with sqlite3.connect(f'file:{args.source.resolve().as_posix()}?mode=ro', uri=True) as original:
        with sqlite3.connect(snapshot) as backup:
            original.backup(backup)
source = sqlite3.connect(f'file:{snapshot.as_posix()}?mode=ro', uri=True)
source.row_factory = sqlite3.Row
def rows(table):
    return [dict(r) for r in source.execute('SELECT * FROM ' + table)]

setup_tables = ['outlets', 'franchisees', 'products', 'commission_rules',
                'promotions', 'target_programs', 'target_slabs']
empty_tables = setup_tables + ['orders', 'bills', 'dockets', 'wallet_ledger',
    'earnings_wallets', 'earnings_wallet_ledger', 'wallet_transfers',
    'franchise_target_progress', 'franchise_advances', 'franchise_customer_profiles',
    'inventory_balances', 'inventory_movements']
now = datetime.now(timezone.utc).isoformat()
url = args.url_file.read_text().strip()
report = {}
with psycopg.connect(url, connect_timeout=20) as target:
    target.execute('SET search_path TO e_franchise')
    if target.execute('SELECT current_schema()').fetchone()[0] != 'e_franchise':
        raise RuntimeError('E-Franchise schema missing')
    target.execute("SET LOCAL lock_timeout='10s'")
    target.execute(sql.SQL('LOCK TABLE {} IN EXCLUSIVE MODE').format(
        sql.SQL(',').join(sql.Identifier(t) for t in empty_tables + ['users', 'settings'])))
    for table in empty_tables:
        if target.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]:
            raise RuntimeError(f'Refusing to merge into nonempty {table}')
    existing_users = target.execute('SELECT phone,role FROM users').fetchall()
    if any(phone != 'admin' or role != 'ADMIN' for phone, role in existing_users):
        raise RuntimeError('Target has accounts beyond bootstrap admin; review required')
    backup_file = folder / 'render-before-migration.json'
    if not backup_file.exists():
        backup = {}
        tables = target.execute("SELECT tablename FROM pg_tables WHERE schemaname='e_franchise' ORDER BY tablename").fetchall()
        for (table,) in tables:
            result = target.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table)))
            columns = [c.name for c in result.description]
            backup[table] = [dict(zip(columns, row)) for row in result.fetchall()]
        backup_file.write_text(json.dumps(backup, default=str, indent=2), encoding='utf-8')
        (folder / 'backup-sha256.txt').write_text(hashlib.sha256(backup_file.read_bytes()).hexdigest())

    def insert(table, records):
        if records:
            columns = list(records[0])
            target.cursor().executemany(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
                sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, columns)),
                sql.SQL(',').join(sql.Placeholder() for _ in columns)),
                [[record[col] for col in columns] for record in records])
        report[table] = len(records)

    for table in setup_tables:
        records = rows(table)
        if table == 'franchisees':
            for record in records:
                record['wallet_balance'] = 0
        insert(table, records)
    local_users = rows('users')
    credential_file = folder / 'production-logins.json'
    if credential_file.exists():
        credentials = json.loads(credential_file.read_text())
    else:
        credentials = [{'role': r['role'], 'login': r['phone'], 'password': secrets.token_urlsafe(18)} for r in local_users]
        credential_file.write_text(json.dumps(credentials, indent=2), encoding='utf-8')
    passwords = {r['login']: r['password'] for r in credentials}
    next_id = target.execute('SELECT coalesce(max(id),0) FROM users').fetchone()[0] + 1
    for offset, record in enumerate(local_users):
        record['id'] = next_id + offset
        record['pin'] = hash_password(passwords[record['phone']])
        record['auth_version'] = int(record.get('auth_version') or 1) + 1
    insert('users', local_users)
    for record in rows('settings'):
        if record['key'] in {'profit_credit_mode', 'test_reset_version', 'test_wallet_topup_enabled', 'testing_quick_login_until'}:
            continue
        target.execute('INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (record['key'], record['value']))
    target.execute("INSERT INTO settings(key,value) VALUES('test_wallet_topup_enabled','0') ON CONFLICT(key) DO UPDATE SET value='0'")
    target.execute("INSERT INTO settings(key,value) VALUES('profit_credit_mode','delayed') ON CONFLICT(key) DO UPDATE SET value='delayed'")
    stock = rows('inventory_balances')
    for record in stock:
        if float(record['reserved']) != 0:
            raise RuntimeError('Source has reserved inventory; cannot import clean setup')
    insert('inventory_balances', stock)
    insert('inventory_movements', [{'outlet_id': r['outlet_id'], 'product_id': r['product_id'],
        'kind': 'OPENING', 'quantity_delta': r['on_hand'], 'reserved_delta': 0,
        'reference': 'SETUP-MIGRATION', 'reason': 'Opening stock imported from local setup',
        'created_by': None, 'created_at': now} for r in stock if float(r['on_hand']) != 0])
    insert('earnings_wallets', [{'franchisee_id': r['id'], 'balance': 0, 'updated_at': now} for r in rows('franchisees')])
    for table in setup_tables + ['users', 'inventory_movements']:
        sequence = target.execute('SELECT pg_get_serial_sequence(%s,%s)', ('e_franchise.' + table, 'id')).fetchone()[0]
        if sequence and args.commit:
            target.execute(sql.SQL('SELECT setval(%s,coalesce(max(id),1),count(*)>0) FROM {}').format(sql.Identifier(table)), (sequence,))
    for table in ['orders', 'wallet_ledger', 'earnings_wallet_ledger', 'franchise_customer_profiles', 'franchise_target_progress']:
        assert target.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0] == 0
    assert target.execute('SELECT coalesce(sum(wallet_balance),0) FROM franchisees').fetchone()[0] == 0
    assert target.execute('SELECT coalesce(sum(balance),0) FROM earnings_wallets').fetchone()[0] == 0
    for table in setup_tables:
        real_columns = {r[0] for r in target.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='e_franchise' AND table_name=%s AND data_type='real'", (table,))}
        for original in rows(table):
            fetched = target.execute(sql.SQL('SELECT {} FROM {} WHERE id=%s').format(
                sql.SQL(',').join(map(sql.Identifier, original)), sql.Identifier(table)), (original['id'],)).fetchone()
            expected = dict(original)
            if table == 'franchisees': expected['wallet_balance'] = 0
            for col in real_columns:
                if col in expected and expected[col] is not None:
                    expected[col] = struct.unpack('f', struct.pack('f', float(expected[col])))[0]
            normalized = [float(v) if isinstance(v, Decimal) else v for v in fetched]
            normalized = [struct.unpack('f', struct.pack('f', float(v)))[0]
                if col in real_columns and v is not None else v for col, v in zip(original, normalized)]
            mismatches = [(col, type(actual).__name__, type(wanted).__name__)
                for col, actual, wanted in zip(original, normalized, expected.values()) if actual != wanted]
            assert not mismatches, f'Value mismatch in {table}: {mismatches}'
    if args.commit:
        target.commit()
    else:
        target.rollback()
report['committed'] = args.commit
(folder / ('commit-report.json' if args.commit else 'dry-run-report.json')).write_text(json.dumps(report, indent=2))
print(json.dumps(report))
