"""Create fran1..fran5 / 12345 and 25 labelled demo orders at one outlet.

Two orders await billing, one awaits packing, one is in an open docket, and one
is packed for adding later. Synthetic stock receipts support these test orders.
No existing users, passwords, orders, wallet balances or dockets are reset.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main as m


def seed(commit=False):
    source = json.loads(Path(__file__).with_name('demo_collected_orders.json').read_text(encoding='utf-8'))[0]
    with m.get_conn() as conn:
        cur = conn.cursor()
        original = cur.execute("SELECT f.* FROM franchisees f JOIN users u ON u.franchisee_id=f.id WHERE u.phone='frn'").fetchone()
        if not original:
            raise RuntimeError('The existing frn outlet mapping is required')
        product = cur.execute('SELECT * FROM products WHERE sku_code=?', (source['items'][0]['sku_code'],)).fetchone()
        pos = cur.execute("SELECT * FROM users WHERE role='POS' AND outlet_id=? ORDER BY id LIMIT 1", (original['outlet_id'],)).fetchone()
        if not product or not pos:
            raise RuntimeError('Mapped POS user and demo product are required')
        for i in range(1, 6):
            user = cur.execute('SELECT * FROM users WHERE phone=?', (f'fran{i}',)).fetchone()
            if user and (user['role'] != 'FRANCHISEE' or not cur.execute("SELECT 1 FROM franchisees WHERE id=? AND shop_name=? AND outlet_id=?", (user['franchisee_id'], f'Demo Franchise {i}', original['outlet_id'])).fetchone()):
                raise RuntimeError(f'fran{i} already exists outside this demo; refusing to change it')
        if not commit:
            print('Validated target outlet, five account names and demo product.')
            return

        def insert(table, values):
            values = {k: v for k, v in values.items() if k != 'id'}
            cur.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
            return cur.lastrowid

        now = datetime.strptime(m.now_str(), '%Y-%m-%d %H:%M:%S')
        for i in range(1, 6):
            login = f'fran{i}'
            user = cur.execute('SELECT * FROM users WHERE phone=?', (login,)).fetchone()
            if user:
                fid, uid = user['franchisee_id'], user['id']
            else:
                address = f'Demo Shop {i}, Kotra Sultanabad, Bhopal 462003'
                fid = insert('franchisees', dict(name=f'Demo Partner {i}', mobile=f'000000020{i}',
                    shop_name=f'Demo Franchise {i}', address=address, delivery_address=address,
                    area='Demo testing', outlet_id=original['outlet_id'], status='ACTIVE',
                    wallet_balance=0, created_at=m.now_str(), delivery_mode='BOTH'))
                uid = insert('users', dict(name=f'Demo Partner {i}', phone=login,
                    pin=m.hash_password('12345'), role='FRANCHISEE', outlet_id=original['outlet_id'], franchisee_id=fid))
                cur.execute('INSERT INTO earnings_wallets(franchisee_id,balance,updated_at) VALUES(?,0,?)', (fid, m.now_str()))
            for j in range(1, 6):
                number = f'DEMO-{login.upper()}-{j:03}'
                if cur.execute('SELECT 1 FROM orders WHERE order_number=?', (number,)).fetchone():
                    continue
                stamp = (now - timedelta(hours=6-j, minutes=i)).strftime('%Y-%m-%d %H:%M:%S')
                order = dict(source['order'])
                order.update(order_number=number, franchisee_id=fid, outlet_id=original['outlet_id'],
                    customer_name=f'Demo Customer {i}-{j}', customer_phone=f'00000{i:02}{j:03}',
                    buyer_address=f'Demo House {j}, Block {i}, Bhopal 462003',
                    customer_address=f'Demo Shop {i}, Bhopal 462003', status='EXECUTED',
                    item_subtotal=float(product['rsp']), total=float(product['rsp']),
                    commission_total=0, delivery_charge=0, wallet_debit_amount=0,
                    payable_to_outlet=0, wallet_required=0, created_at=stamp, updated_at=stamp,
                    executed_at=stamp, delivered_at=None, commission_available_at=None,
                    commission_released_at=None, request_key=None)
                oid = insert('orders', order)
                item = dict(source['items'][0]);item.pop('sku_code')
                weight = m.to_kg(float(product['unit_weight']), product['weight_unit']) * int(product['pack_multiplier'])
                item.update(order_id=oid, product_id=product['id'], qty=1, unit_rsp=float(product['rsp']),
                    line_total=float(product['rsp']), line_weight_kg=weight, selected_weight_kg=weight,
                    line_commission=0, commission_per_unit=0, slab_eligible=0,
                    product_name_snapshot=product['product_name'], taxable_value=float(product['rsp']))
                insert('order_items', item)
                # Add exactly the synthetic stock this demo order needs; record it in the ledger.
                cur.execute('''INSERT INTO inventory_balances(outlet_id,product_id,on_hand,reserved,updated_at)
                    VALUES(?,?,1,0,?) ON CONFLICT(outlet_id,product_id)
                    DO UPDATE SET on_hand=inventory_balances.on_hand+1,updated_at=excluded.updated_at''',
                    (original['outlet_id'], product['id'], m.now_str()))
                insert('inventory_movements', dict(outlet_id=original['outlet_id'], product_id=product['id'],
                    kind='RECEIPT', quantity_delta=1, reserved_delta=0, reference=number,
                    reason='Synthetic stock for demo franchise order', created_by=pos['id'], created_at=stamp))
                m.operations.reserve_order_inventory(cur, oid, original['outlet_id'], uid)
                m.add_order_event(cur, oid, 'EXECUTED', 'Demo order for outlet workflow testing', uid)
                if j >= 3:
                    m.operations.consume_order_inventory(cur, oid, original['outlet_id'], pos['id'])
                    bid = insert('bills', dict(bill_number=f'DEMO-BILL-{login.upper()}-{j:03}',
                        order_id=oid, franchisee_id=fid, outlet_id=original['outlet_id'],
                        status='BILLED' if j==3 else 'PACKED_INDIVIDUALLY',
                        expected_weight_kg=weight, created_at=stamp, updated_at=stamp,
                        pack_status='UNPACKED' if j==3 else 'PACKED_INDIVIDUALLY'))
                    m.add_order_event(cur, oid, 'BILLED', 'Demo bill', pos['id'])
                    if j >= 4:
                        m.add_order_event(cur, oid, 'PACKED_INDIVIDUALLY', 'Demo packed bill', pos['id'])
                    if j == 4:
                        rows = cur.execute('SELECT b.*,o.fulfillment_mode FROM bills b JOIN orders o ON o.id=b.order_id WHERE b.id=?', (bid,)).fetchall()
                        m.dispatch_rules.make_docket(cur, rows, pos)
            m.audit(cur, pos, 'SEED_DEMO', 'FRANCHISE', fid, 'Demo franchise workflow setup; existing records preserved')
        print('Five demo franchises and 25 orders ready; five open dockets and five later packed bills.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', action='store_true')
    seed(parser.parse_args().commit)
