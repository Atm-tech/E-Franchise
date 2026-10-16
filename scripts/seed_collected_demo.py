"""Seed five clearly marked demo orders and one docket, without wallet/stock changes.

Use the configured DATABASE_URL or local SQLite. Existing demo orders are preserved.
Run with --commit to apply; the default only validates the target and fixture.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main as m


def seed(commit=False):
    records = json.loads(Path(__file__).with_name('demo_collected_orders.json').read_text(encoding='utf-8'))
    with m.get_conn() as conn:
        cur = conn.cursor()
        user = cur.execute("SELECT * FROM users WHERE phone='frn' AND role='FRANCHISEE'").fetchone()
        if not user:
            raise RuntimeError('Named demo franchise account frn is required')
        franchise = cur.execute('SELECT * FROM franchisees WHERE id=?', (user['franchisee_id'],)).fetchone()
        products = {}
        for record in records:
            for item in record['items']:
                product = cur.execute('SELECT id FROM products WHERE sku_code=?', (item['sku_code'],)).fetchone()
                if not product:
                    raise RuntimeError('Missing demo product SKU: ' + item['sku_code'])
                products[item['sku_code']] = product['id']
        if not commit:
            print('Validated five demo orders; use --commit to apply.')
            return

        def insert(table, values):
            values = {k: v for k, v in values.items() if k != 'id'}
            cur.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
            return cur.lastrowid

        bill_ids = []
        for record in records:
            order = dict(record['order'])
            existing = cur.execute('SELECT id,franchisee_id FROM orders WHERE order_number=?', (order['order_number'],)).fetchone()
            if existing:
                if existing['franchisee_id'] != franchise['id']:
                    raise RuntimeError('Demo order belongs to a different franchise')
                oid = existing['id']
            else:
                order.update(franchisee_id=franchise['id'], outlet_id=franchise['outlet_id'],
                             customer_address=franchise['address'], commission_total=0,
                             commission_available_at=None, commission_released_at=m.now_str(),
                             wallet_debit_amount=0, request_key=None)
                oid = insert('orders', order)
                for source in record['items']:
                    item = dict(source)
                    item.update(order_id=oid, product_id=products[item.pop('sku_code')],
                                line_commission=0, commission_per_unit=0, slab_eligible=0)
                    insert('order_items', item)
                bill = dict(record['bill'])
                bill.update(order_id=oid, franchisee_id=franchise['id'], outlet_id=franchise['outlet_id'])
                insert('bills', bill)
                for source in record['events']:
                    event = dict(source)
                    event.update(order_id=oid, created_by=user['id'])
                    insert('order_events', event)
            bill_ids.append(cur.execute('SELECT id FROM bills WHERE order_id=?', (oid,)).fetchone()['id'])
        docket = cur.execute("SELECT id FROM dockets WHERE docket_number='DEMO-DOCKET-001'").fetchone()
        if not docket:
            weight = sum(float(r['bill']['expected_weight_kg']) for r in records[:3])
            did = insert('dockets', dict(docket_number='DEMO-DOCKET-001', franchisee_id=franchise['id'],
                         outlet_id=franchise['outlet_id'], status='DELIVERED', expected_weight_kg=weight,
                         actual_weight_kg=weight, weight_variance_kg=0, weight_check_status='PASS', created_at=records[0]['order']['created_at']))
            for bid in bill_ids[:3]:
                if not cur.execute('SELECT 1 FROM docket_bills WHERE bill_id=?', (bid,)).fetchone():
                    insert('docket_bills', dict(docket_id=did, bill_id=bid))
        print('Five demo orders and docket available for frn; existing handovers preserved.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', action='store_true')
    seed(parser.parse_args().commit)
