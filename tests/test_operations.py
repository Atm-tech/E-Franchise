"""Isolated integration tests: no live payment, SMS, R2, or business database writes."""
import hashlib
from contextlib import closing
import hmac
import json
import os
import re
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as m
from app import operations as ops


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.old_db,self.old_data=m.DB_PATH,m.DATA_DIR
        m.DATA_DIR=Path(self.directory.name)
        m.DB_PATH=m.DATA_DIR/'operations_test.db'
        self.env=patch.dict(os.environ,{'APP_ENV':'test','SEED_DEMO':'1'})
        self.env.start()
        self.client=TestClient(m.app)
        self.client.__enter__()
        self.product=self.row("SELECT * FROM products WHERE status='ACTIVE' AND unit_weight>0 ORDER BY rsp LIMIT 1")
        self.fid=self.row("SELECT franchisee_id FROM users WHERE phone='frn'")[0]

    def tearDown(self):
        self.client.__exit__(None,None,None)
        m.DB_PATH,m.DATA_DIR=self.old_db,self.old_data
        self.env.stop();self.directory.cleanup()

    def row(self,sql,args=()):
        with m.get_conn() as conn:return conn.execute(sql,args).fetchone()

    def sql(self,sql,args=()):
        with m.get_conn() as conn:conn.execute(sql,args)

    def post(self,path,data=None,client=None,**kwargs):
        client=client or self.client
        page=client.get('/login')
        token=re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
        headers=kwargs.pop('headers',{}) | {'x-csrf-token':token}
        data=dict(data or {});data.setdefault('request_key',str(time.time_ns()))
        return client.post(path,data=data,headers=headers,follow_redirects=False,**kwargs)

    def login(self,role,client=None):
        response=self.post('/login',{'phone':role,'pin':role+'@123'},client=client)
        self.assertEqual(response.status_code,303,response.text)
        self.assertNotIn('error=',response.headers['location'])

    def test_temporary_quick_login_roles_expiry_and_revocation(self):
        self.assertEqual(self.post('/testing/quick-login', {'role': 'ADMIN'}).status_code, 404)
        self.sql("INSERT INTO settings(key,value) VALUES('testing_quick_login_until',?)", (str(time.time()+3600),))
        page = self.client.get('/login')
        self.assertIn('action="/testing/quick-login"', page.text)
        for role, path in [('FRANCHISEE','/franchise/home'), ('POS','/pos/home'),
                           ('GUARD','/guard/home'), ('DELIVERY','/delivery/home'), ('ADMIN','/admin/dashboard')]:
            response = self.post('/testing/quick-login', {'role':role})
            self.assertEqual(response.status_code,303)
            self.assertEqual(response.headers['location'],path)
        self.assertEqual(self.post('/testing/quick-login', {'role':'UNKNOWN'}).status_code,404)
        self.assertEqual(self.client.post('/testing/quick-login',data={'role':'ADMIN'}).status_code,403)
        self.sql("UPDATE settings SET value='0' WHERE key='testing_quick_login_until'")
        self.assertEqual(self.client.get('/admin/dashboard',follow_redirects=False).headers['location'],'/login')
        self.assertNotIn('action="/testing/quick-login"', self.client.get('/login').text)
        self.assertEqual(self.post('/testing/quick-login', {'role':'ADMIN'}).status_code,404)
        for deadline in [time.time()-1, time.time()+72*3600]:
            self.sql("UPDATE settings SET value=? WHERE key='testing_quick_login_until'", (str(deadline),))
            self.assertEqual(self.post('/testing/quick-login', {'role':'ADMIN'}).status_code,404)

    def order(self,mode='DELIVERY_MAN',status='EXECUTED',key=None):
        self.login('frn')
        body={'cart_json':json.dumps([{'id':self.product['id'],'qty':1}]),'customer_name':'Test Buyer','customer_phone':'9999999999','fulfillment_mode':mode,'delivery_type':'DAY','status_mode':status,'distance_km':2,'request_key':key or str(time.time_ns())}
        result=self.post('/franchise/orders',body)
        self.assertEqual(result.status_code,303,result.text)
        self.assertIn('created=',result.headers['location'])
        return self.row('SELECT * FROM orders ORDER BY id DESC LIMIT 1')

    def bill(self,order):
        self.login('pos')
        self.assertEqual(self.post(f'/pos/orders/{order["id"]}/claim').status_code,303)
        self.assertEqual(self.post(f'/pos/orders/{order["id"]}/scan',{'sku':self.product['sku_code'],'qty':1}).status_code,303)
        result=self.post('/pos/bills',{'order_id':order['id']})
        self.assertEqual(result.status_code,303,result.text)
        bill=self.row('SELECT * FROM bills WHERE order_id=?',(order['id'],))
        self.assertEqual(self.post(f'/pos/bills/{bill["id"]}/pack').status_code,303)
        return bill

    def at_guard(self,kind,row):
        self.login('pos')
        self.assertEqual(self.post('/pos/handover',{'entity_type':kind,'entity_id':row['id']}).status_code,303)
        self.login('grd')
        number=row['bill_number' if kind=='BILL' else 'docket_number']
        result=self.post('/guard/receive',{'dispatch_number':number})
        self.assertEqual(result.status_code,303,result.text)
        result=self.post('/guard/weight-check',{'entity_type':kind,'entity_id':row['id'],'actual_weight_kg':row['expected_weight_kg']})
        self.assertEqual(result.status_code,303,result.text)
        result=self.post('/guard/seal-check',{'entity_type':kind,'entity_id':row['id'],'seal_status':'INTACT'})
        self.assertEqual(result.status_code,303,result.text)

    def code(self,purpose):
        self.login('frn')
        response=self.post('/franchise/dispatch-code',{'purpose':purpose})
        self.assertEqual(response.status_code,200,response.text)
        return re.search(r'data-handover-code>(\d{6})',response.text).group(1)

    def delivery_task(self):
        order=self.order();bill=self.bill(order);self.at_guard('BILL',bill)
        response=self.post('/guard/handover',{'entity_type':'BILL','entity_id':bill['id'],'target_role':'DELIVERY'})
        self.assertEqual(response.status_code,303,response.text)
        task=self.row('SELECT * FROM delivery_tasks ORDER BY id DESC LIMIT 1')
        self.login('dlv')
        for state in ('ACCEPTED','PICKED_UP','OUT_FOR_DELIVERY'):
            self.assertEqual(self.post('/delivery/tasks',{'task_id':task['id'],'status':state}).status_code,303)
        return order,bill,task

    def test_role_pages_security_and_restart(self):
        self.assertEqual(self.client.post('/login',data={'phone':'adm','pin':'adm@123'}).status_code,403)
        for role,path in [('adm','/admin/dashboard'),('frn','/franchise/home'),('pos','/pos/home'),('grd','/guard/home'),('dlv','/delivery/home')]:
            self.login(role)
            for route in (path,'/workspace','/account'):
                response=self.client.get(route)
                self.assertEqual(response.status_code,200,response.text[:300])
                self.assertIn('csrf_token',response.text)
            self.assertEqual(self.client.get('/api/events').status_code,200)
        self.assertEqual(self.post('/admin/settlements',{'outlet_id':1}).status_code,403)
        self.login('adm')
        self.assertEqual(self.post('/account/password',{'current_password':'adm@123','new_password':'LongerSecret123'}).status_code,303)
        self.sql("UPDATE settings SET value='456' WHERE key='delivery_per_km'")
        m.init_db()
        self.assertEqual(self.row("SELECT value FROM settings WHERE key='delivery_per_km'")[0],'456')
        from app.security import verify_password
        self.assertTrue(verify_password('LongerSecret123',self.row("SELECT pin FROM users WHERE phone='adm'")[0]))
        self.assertGreater(self.row('SELECT COUNT(*) FROM config_versions')[0],0)

    def test_complete_delivery_otp_rewards_and_settlement(self):
        self.sql('UPDATE products SET slab_eligible=1 WHERE id=?',(self.product['id'],))
        self.sql('UPDATE target_slabs SET threshold_amount=1,reward_percent=5 WHERE id=(SELECT MIN(id) FROM target_slabs)')
        order,bill,task=self.delivery_task()
        self.assertEqual(self.row('SELECT balance FROM earnings_wallets WHERE franchisee_id=?',(self.fid,))[0],0)
        invalid=self.post('/delivery/tasks',{'task_id':task['id'],'status':'DELIVERED','pod_reference':'1234'})
        self.assertEqual(invalid.status_code,400)
        code=self.code(f'DELIVERY:{task["id"]}')
        self.login('dlv')
        invalid=self.post('/delivery/tasks',{'task_id':task['id'],'status':'DELIVERED','pod_reference':'not-code'})
        self.assertEqual(invalid.status_code,400)
        self.assertEqual(self.row('SELECT attempts FROM otp_challenges ORDER BY id DESC LIMIT 1')[0],1)
        result=self.post('/delivery/tasks',{'task_id':task['id'],'status':'DELIVERED','pod_reference':code})
        self.assertEqual(result.status_code,303,result.text)
        self.assertEqual(self.row('SELECT status FROM orders WHERE id=?',(order['id'],))[0],'DELIVERED')
        self.assertGreater(self.row('SELECT balance FROM earnings_wallets WHERE franchisee_id=?',(self.fid,))[0],0)
        self.assertEqual(self.row('SELECT pod_reference FROM delivery_tasks WHERE id=?',(task['id'],))[0],'VERIFIED_RECEIVER')
        self.login('adm')
        self.assertEqual(self.post('/admin/settlements',{'outlet_id':1}).status_code,303)
        self.assertEqual(self.post('/admin/settlements',{'outlet_id':1}).status_code,303)
        self.assertEqual(self.row('SELECT COUNT(*) FROM settlements')[0],1)
        settlement=self.row('SELECT * FROM settlements')
        self.assertEqual(self.post(f'/admin/settlements/{settlement["id"]}/paid',{'reference':'BANK-123','amount':settlement['total']+1}).status_code,409)
        self.assertEqual(self.post(f'/admin/settlements/{settlement["id"]}/paid',{'reference':'BANK-123','amount':settlement['total']}).status_code,303)
        self.assertEqual(self.client.get(f'/admin/settlements/{settlement["id"]}.csv').status_code,200)

    def test_multi_bill_pickup_and_repeated_release(self):
        bills=[self.bill(self.order(mode='FRANCHISE_PICKUP')) for _ in range(2)]
        result=self.post('/pos/dockets',{'bill_ids':','.join(str(b['id']) for b in bills)})
        self.assertEqual(result.status_code,303,result.text)
        docket=self.row('SELECT * FROM dockets ORDER BY id DESC LIMIT 1')
        label=self.client.get(f'/dispatch/DOCKET/{docket["id"]}/label.pdf')
        self.assertEqual(label.status_code,200);self.assertTrue(label.content.startswith(b'%PDF'))
        self.at_guard('DOCKET',docket)
        code=self.code(f'PICKUP:DOCKET:{docket["id"]}')
        self.login('grd')
        body={'entity_type':'DOCKET','entity_id':docket['id'],'target_role':'FRANCHISEE','receiver_code':code}
        self.assertEqual(self.post('/guard/handover',body).status_code,303)
        self.assertEqual(self.post('/guard/handover',body).status_code,409)
        self.assertEqual(self.row("SELECT COUNT(*) FROM orders WHERE status='DELIVERED'")[0],2)

    def test_order_idempotency_hold_refund_and_concurrency(self):
        key='replay-order-0000001'
        order=self.order(key=key)
        self.order(key=key)
        self.assertEqual(self.row('SELECT COUNT(*) FROM orders')[0],1)
        self.assertEqual(self.row("SELECT COUNT(*) FROM wallet_ledger WHERE entry_type='ORDER_DEBIT'")[0],1)
        held=self.order(status='ON_HOLD')
        self.login('adm')
        cookies=dict(self.client.cookies)
        def execute():
            with TestClient(m.app) as client:
                client.cookies.update(cookies)
                return self.post(f'/admin/orders/{held["id"]}/status',{'status':'EXECUTED'},client=client).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:execute(),range(2)))
        self.assertEqual(results,[303,303])
        self.assertEqual(self.row("SELECT COUNT(*) FROM wallet_ledger WHERE reference_id=? AND entry_type='ORDER_DEBIT'",(held['id'],))[0],1)
        for _ in range(2):self.post(f'/admin/orders/{held["id"]}/status',{'status':'CANCELLED','reason':'Duplicate cancellation test'})
        self.assertEqual(self.row("SELECT COUNT(*) FROM wallet_ledger WHERE reference_id=? AND entry_type='ORDER_REFUND'",(held['id'],))[0],1)

    def test_instant_demo_topup_is_scoped_and_replay_safe(self):
        self.login('frn')
        before = self.row('SELECT wallet_balance FROM franchisees WHERE id=?', (self.fid,))[0]
        body = {'amount': 500, 'request_key': 'instant-demo-topup-123456'}
        for _ in range(2):
            response = self.post('/franchise/demo-topups', body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['wallet_balance'], before + 500)
        self.assertEqual(self.row("SELECT COUNT(*) FROM wallet_ledger WHERE entry_type='TEST_CREDIT'")[0], 1)
        for amount in (0, -1, 100001, 'nan'):
            self.assertEqual(self.post('/franchise/demo-topups', {'amount': amount}).status_code, 400)
        with patch.dict(os.environ, {'APP_ENV': 'production'}):
            self.assertEqual(self.post('/franchise/demo-topups', body).status_code, 403)
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://remote'}):
            self.assertEqual(self.post('/franchise/demo-topups', body).status_code, 403)
        self.sql("UPDATE settings SET value='0' WHERE key='test_wallet_topup_enabled'")
        self.assertEqual(self.post('/franchise/demo-topups', body).status_code, 403)
        self.login('pos')
        self.assertIn(self.post('/franchise/demo-topups', body).status_code, (303, 401, 403))

    def test_background_profit_credit_and_restart_replay(self):
        from app.profit_worker import ProfitWorker
        import threading
        outlet = self.row('SELECT outlet_id FROM franchisees WHERE id=?',(self.fid,))[0]
        self.sql("INSERT INTO inventory_balances(outlet_id,product_id,on_hand,reserved,updated_at) VALUES(?,?,20,0,?) ON CONFLICT(outlet_id,product_id) DO UPDATE SET on_hand=20,reserved=0",(outlet,self.product['id'],m.now_str()))
        due=self.order(mode='FRANCHISE_PICKUP',status='ON_HOLD')
        future=self.order(mode='FRANCHISE_PICKUP',status='ON_HOLD')
        self.sql("UPDATE orders SET status='DELIVERED',commission_total=100,commission_available_at='2000-01-01 00:00:00' WHERE id=?",(due['id'],))
        self.sql("UPDATE orders SET status='DELIVERED',commission_total=200,commission_available_at='2099-01-01 00:00:00' WHERE id=?",(future['id'],))
        finished=threading.Event()
        def release():
            count=m.credit_due_profits()
            finished.set()
            return count
        worker=ProfitWorker(release,.05)
        worker.start()
        try:self.assertTrue(finished.wait(3))
        finally:worker.stop()
        self.assertFalse(worker.thread.is_alive())
        self.assertEqual(m.credit_due_profits(),0)
        self.assertEqual(self.row("SELECT COUNT(*),SUM(amount) FROM earnings_wallet_ledger WHERE entry_type='ORDER_COMMISSION'" )[:],(1,100))
        self.assertIsNone(self.row('SELECT commission_released_at FROM orders WHERE id=?',(future['id'],))[0])
        self.sql("INSERT INTO settings(key,value) VALUES('profit_credit_mode','immediate') ON CONFLICT(key) DO UPDATE SET value='immediate'")
        self.assertEqual(m.credit_due_profits(),1)
        self.assertEqual(m.credit_due_profits(),0)
        self.assertEqual(self.row("SELECT SUM(amount) FROM earnings_wallet_ledger WHERE entry_type='ORDER_COMMISSION'")[0],300)
        self.sql("UPDATE settings SET value='delayed' WHERE key='profit_credit_mode'")
        self.assertEqual(m.credit_due_profits(),0)


    def test_redirect_message_preserves_query_and_fragment(self):
        from urllib.parse import urlsplit,parse_qs
        response = ops.redirect('Stock saved', '/admin/dashboard?period=month#catalog')
        parts = urlsplit(response.headers['location'])
        self.assertEqual(parts.fragment,'catalog')
        self.assertEqual(parse_qs(parts.query),{'period':['month'],'message':['Stock saved']})
        self.assertEqual(ops.redirect('Picked','/pos/home#queue').headers['location'],'/pos/home?message=Picked#queue')

    def test_billing_consumes_inventory_once(self):
        outlet = self.row('SELECT outlet_id FROM franchisees WHERE id=?', (self.fid,))[0]
        self.sql("INSERT INTO inventory_balances(outlet_id,product_id,on_hand,reserved,updated_at) VALUES(?,?,20,0,?) ON CONFLICT(outlet_id,product_id) DO UPDATE SET on_hand=20,reserved=0", (outlet,self.product['id'],m.now_str()))
        order = self.order(mode='FRANCHISE_PICKUP')
        self.login('pos')
        self.assertEqual(self.post(f'/pos/orders/{order["id"]}/claim').status_code,303)
        self.assertEqual(self.post(f'/pos/orders/{order["id"]}/scan',{'sku':self.product['sku_code'],'qty':1}).status_code,303)
        for _ in range(2):
            self.assertEqual(self.post('/pos/bills',{'order_id':order['id']}).status_code,303)
        self.assertEqual(self.row('SELECT COUNT(*) FROM bills WHERE order_id=?',(order['id'],))[0],1)
        stock = self.row('SELECT on_hand,reserved FROM inventory_balances WHERE outlet_id=? AND product_id=?',(outlet,self.product['id']))
        self.assertEqual(tuple(stock),(19,0))
        sale = self.row("SELECT COUNT(*),SUM(quantity_delta),SUM(reserved_delta) FROM inventory_movements WHERE reference=? AND kind='SALE'",(f'ORDER:{order["id"]}',))
        self.assertEqual(tuple(sale),(1,-1,-1))

    def test_payment_signature_duplicates_amount_and_reconciliation(self):
        self.login('frn')
        env={'RAZORPAY_KEY_ID':'test_key','RAZORPAY_KEY_SECRET':'test_secret','RAZORPAY_WEBHOOK_SECRET':'webhook_test_secret'}
        with patch.dict(os.environ,env),patch('app.providers.razorpay',return_value={'id':'order_test123','amount':10000,'currency':'INR'}):
            body={'amount':100,'request_key':'payment-replay-123456'}
            self.assertEqual(self.post('/franchise/topups',body).status_code,303)
            self.assertEqual(self.post('/franchise/topups',body).status_code,303)
            self.assertEqual(self.row('SELECT COUNT(*) FROM payment_transactions')[0],1)
            self.assertEqual(self.client.get('/franchise/payments/1').status_code,200)
            status = self.client.get('/franchise/payments/1', headers={'Accept': 'application/json'})
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()['order_id'], 'order_test123')
            self.assertEqual(status.json()['status'], 'PENDING')
            self.assertNotIn('test_secret', status.text)
            self.assertEqual(status.headers['cache-control'], 'no-store')
            page = self.client.get('/franchise/home')
            self.assertEqual(page.status_code, 200)
            self.assertIn('id="wallet-topup"', page.text)
            self.assertIn('id="topup-amount"', page.text)

            def send(amount,event_id='event123',signature=True):
                payload={'event':'payment.captured','payload':{'payment':{'entity':{'id':'pay_test123','order_id':'order_test123','status':'captured','amount':amount,'currency':'INR'}}}}
                raw=json.dumps(payload).encode();digest=hmac.new(env['RAZORPAY_WEBHOOK_SECRET'].encode(),raw,hashlib.sha256).hexdigest()
                return self.client.post('/integrations/razorpay/webhook',content=raw,headers={'x-razorpay-signature':digest if signature else 'bad','x-razorpay-event-id':event_id,'content-type':'application/json'})
            before=self.row('SELECT wallet_balance FROM franchisees WHERE id=?',(self.fid,))[0]
            self.assertEqual(send(10000,signature=False).status_code,401)
            self.assertEqual(send(10001).status_code,400)
            self.assertEqual(send(10000).status_code,200)
            self.assertEqual(send(10000).status_code,200)
            self.assertEqual(send(10000,'event456').status_code,200)
            self.assertEqual(self.row('SELECT wallet_balance FROM franchisees WHERE id=?',(self.fid,))[0],before+100)
            self.assertEqual(self.row("SELECT COUNT(*) FROM wallet_ledger WHERE entry_type='TOPUP'")[0],1)
            status = self.client.get('/franchise/payments/1', headers={'Accept': 'application/json'}).json()
            self.assertEqual(status['status'], 'CREDITED')
            self.assertEqual(status['wallet_balance'], before + 100)
            self.sql('UPDATE payment_transactions SET franchisee_id=? WHERE id=1', (self.fid + 999,))
            self.assertEqual(self.client.get('/franchise/payments/1', headers={'Accept': 'application/json'}).status_code, 404)


    def test_dispatch_failure_reassignment_geofence_and_proof(self):
        order,bill,task=self.delivery_task()
        self.assertEqual(self.post(f'/delivery/tasks/{task["id"]}/failed',{'reason':'Receiver not available'}).status_code,303)
        self.login('adm')
        self.assertEqual(self.post(f'/admin/delivery/tasks/{task["id"]}',{'action':'REOPEN','reason':'Receiver is now available'}).status_code,303)
        self.sql("UPDATE settings SET value='100' WHERE key='geofence_radius_m'")
        self.sql('UPDATE franchisees SET latitude=23,longitude=77 WHERE id=?',(self.fid,))
        code=self.code(f'DELIVERY:{task["id"]}')
        self.login('dlv')
        body={'task_id':task['id'],'status':'DELIVERED','pod_reference':code}
        self.assertEqual(self.post('/delivery/tasks',body).status_code,400)
        self.assertEqual(self.post(f'/delivery/tasks/{task["id"]}/location',{'latitude':23,'longitude':77}).status_code,200)
        import io
        from PIL import Image
        buffer=io.BytesIO();Image.new('RGB',(8,8),'white').save(buffer,format='PNG');png=buffer.getvalue()
        response=self.post(f'/delivery/tasks/{task["id"]}/proof',{'kind':'PHOTO'},files={'proof':('photo.png',png,'image/png')})
        self.assertEqual(response.status_code,303,response.text)
        body['pod_type']='PHOTO'
        self.assertEqual(self.post('/delivery/tasks',body).status_code,303)
        self.assertEqual(self.client.get('/proofs/1').status_code,200)

    def test_picking_locks_outlet_isolation_and_bad_input(self):
        order=self.order();self.login('pos')
        self.assertEqual(self.post('/pos/bills',{'order_id':order['id']}).status_code,409)
        self.post(f'/pos/orders/{order["id"]}/claim')
        self.assertEqual(self.post(f'/pos/orders/{order["id"]}/scan',{'sku':'NOT_ORDERED'}).status_code,400)
        self.assertEqual(self.post(f'/pos/orders/{order["id"]}/scan',{'sku':self.product['sku_code'],'qty':2}).status_code,400)
        self.sql("UPDATE users SET outlet_id=2 WHERE phone='pos'")
        self.assertEqual(self.post(f'/pos/orders/{order["id"]}/claim').status_code,404)
        self.login('frn')
        self.assertEqual(self.post('/franchise/orders',{'customer_name':'Test Buyer','customer_phone':'9999999999','cart_json':'{"bad":1}'}).status_code,400)
        self.assertEqual(self.post('/franchise/orders',{'customer_name':'Test Buyer','customer_phone':'9999999999','cart_json':'[]','distance_km':'nan'}).status_code,400)

    def test_machine_reading_signature_and_count_mismatch(self):
        bill=self.bill(self.order(mode='FRANCHISE_PICKUP'));self.at_guard('BILL',bill)
        keys={'scale1':{'secret':'a-long-device-secret','outlet_id':1}}
        with patch.dict(os.environ,{'DEVICE_KEYS_JSON':json.dumps(keys)}):
            raw=json.dumps({'request_id':'reading1','entity_type':'BILL','entity_id':bill['id'],'weight_kg':bill['expected_weight_kg'],'item_count':20}).encode()
            stamp=str(time.time());sig=hmac.new(keys['scale1']['secret'].encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
            headers={'x-device-id':'scale1','x-device-timestamp':stamp,'x-device-signature':sig,'content-type':'application/json'}
            response=self.client.post('/integrations/device/reading',content=raw,headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(self.client.post('/integrations/device/reading',content=raw,headers=headers).json()['duplicate'],True)
            self.assertEqual(self.post(f'/guard/readings/{response.json()["reading_id"]}/apply').status_code,303)
            self.assertEqual(self.row('SELECT weight_check_status FROM bills WHERE id=?',(bill['id'],))[0],'ALERT')

    def test_targets_bank_profiles_and_tax(self):
        self.login('adm')
        response=self.post('/admin/targets',{'name':'Quarter Launch','scope':'TOTAL_SALE','slabs':'100:2\n200:4','starts_on':'2026-01-01','ends_on':'2026-12-31'})
        self.assertEqual(response.status_code,303,response.text)
        self.assertEqual(self.post('/admin/targets',{'name':'Invalid','slabs':'bad'}).status_code,400)
        self.login('frn')
        response=self.post('/franchise/banks',{'holder':'Test Partner','bank_name':'Example Bank','account_number':'123456789012','ifsc':'TEST0123456'})
        self.assertEqual(response.status_code,303,response.text)
        self.assertEqual(self.row('SELECT account_last4 FROM franchise_bank_accounts')[0],'9012')
        self.sql("UPDATE franchisees SET state_code='27' WHERE id=?",(self.fid,))
        bill=self.bill(self.order(mode='FRANCHISE_PICKUP'))
        self.login('frn')
        invoice=self.client.get(f'/franchise/bills/{bill["id"]}')
        self.assertEqual(invoice.status_code,200);self.assertIn('Inter-state',invoice.text)
        self.assertEqual(self.client.get('/franchise/summary.pdf').status_code,200)
        taxes=m.invoice_tax_breakdown([{'gst_rate':18,'taxable_value':100,'gst_amount':18,'line_total':118}],interstate=True)
        self.assertEqual(taxes[0]['igst'],18);self.assertEqual(taxes[0]['cgst'],0)

    def test_onboarding_manual_money_replay_and_ledger_integrity(self):
        self.login('adm')
        body={'name':'New Owner','mobile':'9876543210','shop_name':'Test Shop','advance_amount':1000,'outlet_id':1,'initial_password':'StrongPassword1','request_key':'onboarding-1234567'}
        self.assertEqual(self.post('/admin/franchisees',body).status_code,303)
        self.assertEqual(self.post('/admin/franchisees',body).status_code,303)
        partner=self.row("SELECT * FROM franchisees WHERE mobile='9876543210'")
        self.assertEqual(partner['wallet_balance'],750)
        account=self.row("SELECT * FROM users WHERE phone='9876543210'")
        self.assertTrue(account['pin'].startswith('pbkdf2_sha256$'))
        advance={'advance_amount':200,'received_mode':'BANK','request_key':'advance-replay-123456'}
        for _ in range(2):self.assertEqual(self.post(f'/admin/franchisees/{partner["id"]}/advance',advance).status_code,303)
        self.assertEqual(self.row('SELECT wallet_balance FROM franchisees WHERE id=?',(partner['id'],))[0],900)
        with self.assertRaises(sqlite3.IntegrityError):self.sql('UPDATE wallet_ledger SET amount=0')
        self.assertEqual(self.post('/admin/operations/settings',{'wallet_credit_percent':999}).status_code,400)

    def test_payment_ambiguous_creation_is_not_retried(self):
        self.login('frn')
        env={'RAZORPAY_KEY_ID':'test_key','RAZORPAY_KEY_SECRET':'test_secret','RAZORPAY_WEBHOOK_SECRET':'webhook_test_secret'}
        with patch.dict(os.environ,env),patch('app.providers.razorpay',side_effect=ops.providers.ProviderError('timeout')) as provider:
            for _ in range(2):self.assertEqual(self.post('/franchise/topups',{'amount':100,'request_key':'ambiguous-payment-12345'}).status_code,303)
            self.assertEqual(provider.call_count,1)
            self.assertEqual(self.row('SELECT status FROM payment_transactions')[0],'REVIEW')
        self.login('adm')
        with patch.dict(os.environ,env),patch('app.providers.razorpay',side_effect=[{'receipt':'EF-1','amount':10000,'currency':'INR'},{'items':[{'id':'pay_reconciled','order_id':'order_reconciled','status':'captured','amount':10000,'currency':'INR'}]}]):
            self.assertEqual(self.post('/admin/payments/1/reconcile',{'provider_order_id':'order_reconciled'}).status_code,303)
        self.assertEqual(self.row('SELECT status FROM payment_transactions')[0],'CREDITED')

    def test_charge_ownership_and_route_snapshot(self):
        for bearer in ('CUSTOMER','FRANCHISEE','BUSINESS'):
            self.sql("UPDATE settings SET value=? WHERE key='delivery_charge_bearer'",(bearer,))
            self.sql('UPDATE franchisees SET route_distance_km=7,route_outlet_id=1 WHERE id=?',(self.fid,))
            order=self.order()
            self.assertEqual(order['distance_km'],7)
            self.assertEqual(order['delivery_charge'],order['delivery_cost'] if bearer=='CUSTOMER' else 0)
            self.assertEqual(order['wallet_required'],round(order['payable_to_outlet']+(0 if bearer=='BUSINESS' else order['delivery_cost']),2))

    def test_otp_lockout_login_rate_limit_and_backup_restore(self):
        _,_,task=self.delivery_task();code=self.code(f'DELIVERY:{task["id"]}');self.login('dlv')
        for _ in range(5):self.assertEqual(self.post('/delivery/tasks',{'task_id':task['id'],'status':'DELIVERED','pod_reference':'incorrect'}).status_code,400)
        self.assertEqual(self.post('/delivery/tasks',{'task_id':task['id'],'status':'DELIVERED','pod_reference':code}).status_code,400)
        self.assertEqual(self.row('SELECT attempts FROM otp_challenges ORDER BY id DESC LIMIT 1')[0],5)
        for _ in range(10):self.assertEqual(self.post('/login',{'phone':'unknown','pin':'wrong'}).status_code,303)
        self.assertEqual(self.post('/login',{'phone':'unknown','pin':'wrong'}).status_code,429)
        from scripts.backup_database import backup
        destination=m.DATA_DIR/'verified_backup.db'
        backup(m.DB_PATH,destination)
        with closing(sqlite3.connect(destination)) as conn:self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        with self.assertRaises(ValueError):backup(m.DB_PATH,destination)


    def test_fulfilment_modes_and_held_execution(self):
        held=self.order(mode='DELIVERY_MAN',status='ON_HOLD')
        self.login('adm')
        self.assertEqual(self.post('/admin/delivery-modes',{'kind':'OUTLET','record_id':1,'mode':'FRANCHISE_PICKUP'}).status_code,303)
        self.assertEqual(self.post('/admin/delivery-modes',{'kind':'FRANCHISE','record_id':self.fid,'mode':'DELIVERY_MAN'}).status_code,409)
        self.login('frn')
        self.assertEqual(self.post(f'/franchise/orders/{held["id"]}/execute').status_code,409)
        self.assertEqual(self.row('SELECT status FROM orders WHERE id=?',(held['id'],))[0],'ON_HOLD')
        page=self.client.get('/franchise/home')
        self.assertEqual(page.status_code,200)
        self.assertRegex(page.text,r'value="DELIVERY_MAN" disabled')

    def test_fixed_delivery_pricing_and_franchise_deals(self):
        self.sql("UPDATE settings SET value='FIXED' WHERE key='delivery_pricing_mode'")
        self.sql("UPDATE settings SET value='57' WHERE key='delivery_fixed_charge'")
        self.sql("UPDATE settings SET value='1000000' WHERE key='free_delivery_threshold'")
        self.sql("INSERT INTO promotions(name,scope,product_id,discount_type,discount_value,status,franchisee_id,created_at) VALUES('Partner deal','PRODUCT',?,'PERCENT',10,'ACTIVE',?,?)",(self.product['id'],self.fid,m.now_str()))
        order=self.order()
        self.assertAlmostEqual(order['item_subtotal'],round(self.product['rsp']*.9,2))
        self.assertAlmostEqual(order['delivery_cost'],57+float(self.row("SELECT value FROM settings WHERE key='day_delivery_charge'")[0]))
        with m.get_conn() as conn:
            price,_=m.resolve_deal(conn.cursor(),self.product,self.product['rsp'],999999)
        self.assertEqual(price,self.product['rsp'])

    def test_required_and_auto_docket_handover(self):
        first=self.bill(self.order(mode='FRANCHISE_PICKUP'))
        second=self.bill(self.order(mode='FRANCHISE_PICKUP'))
        self.login('adm')
        self.assertEqual(self.post('/admin/dispatch-policy',{'docket_policy':'REQUIRED','require_seal_check':1}).status_code,303)
        self.login('pos')
        self.assertEqual(self.post('/pos/handover',{'entity_type':'BILL','entity_id':first['id']}).status_code,409)
        self.login('adm')
        self.assertEqual(self.post('/admin/dispatch-policy',{'docket_policy':'AUTO','require_seal_check':1}).status_code,303)
        self.login('pos')
        self.assertEqual(self.post('/pos/handover',{'entity_type':'BILL','entity_id':second['id']}).status_code,303)
        docket=self.row('SELECT * FROM dockets ORDER BY id DESC LIMIT 1')
        self.assertEqual(docket['status'],'HANDED_TO_GUARD')
        self.assertEqual(self.row('SELECT COUNT(*) FROM docket_bills WHERE docket_id=?',(docket['id'],))[0],2)
        self.assertEqual(self.post('/pos/handover',{'entity_type':'BILL','entity_id':second['id']}).status_code,303)
        self.assertEqual(self.row('SELECT COUNT(*) FROM dockets')[0],1)

    def test_seal_hold_and_inspection_audit(self):
        bill=self.bill(self.order())
        self.at_guard('BILL',bill)
        self.sql("UPDATE bills SET seal_status='PENDING' WHERE id=?",(bill['id'],))
        release={'entity_type':'BILL','entity_id':bill['id'],'target_role':'DELIVERY'}
        self.assertEqual(self.post('/guard/handover',release).status_code,409)
        self.assertEqual(self.post('/guard/seal-check',{'entity_type':'BILL','entity_id':bill['id'],'seal_status':'BROKEN','notes':'Seal torn at receipt'}).status_code,303)
        self.assertEqual(self.post('/guard/handover',release).status_code,409)
        self.assertEqual(self.post('/guard/seal-check',{'entity_type':'BILL','entity_id':bill['id'],'seal_status':'INTACT','notes':'Repacked and rechecked'}).status_code,303)
        self.assertEqual(self.post('/guard/handover',release).status_code,303)
        self.assertGreaterEqual(self.row("SELECT COUNT(*) FROM audit_logs WHERE action='SEAL_CHECK'")[0],3)

    def test_mobile_token_scopes_revocation_and_shared_order(self):
        from app import mobile_api
        token=self.client.post('/api/mobile/token',json={'login':'frn','password':'frn@123'})
        self.assertEqual(token.status_code,200,token.text)
        headers={'Authorization':'Bearer '+token.json()['access_token']}
        import jwt
        from app.security import session_secret
        claims=jwt.decode(token.json()['access_token'],session_secret(m.DATA_DIR),algorithms=['HS256'],audience=mobile_api.AUDIENCE,issuer=mobile_api.ISSUER)
        claims['exp']=int(time.time())-1
        expired=jwt.encode(claims,session_secret(m.DATA_DIR),algorithm='HS256')
        self.assertEqual(self.client.get('/api/mobile/me',headers={'Authorization':'Bearer '+expired}).status_code,401)
        self.assertEqual(self.client.get('/api/mobile/me',headers={'Authorization':'Bearer broken-token'}).status_code,401)
        self.assertEqual(self.client.get('/api/mobile/me',headers=headers).json()['role'],'FRANCHISEE')
        self.assertEqual(self.client.get('/api/mobile/catalogue',headers=headers).status_code,200)
        self.assertEqual(self.client.get('/api/summary',headers=headers).status_code,403)
        result=self.client.post('/api/mobile/franchise/orders',headers=headers,data={'cart_json':json.dumps([{'id':self.product['id'],'qty':1}]),'customer_name':'Mobile buyer','customer_phone':'9999999999','fulfillment_mode':'FRANCHISE_PICKUP','request_key':'mobile-order-1'},follow_redirects=False)
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(result.json()['ok'])
        self.assertEqual(self.client.get('/api/mobile/orders',headers=headers).json()['orders'][0]['customer_name'],'Mobile buyer')
        bad=self.client.post('/api/mobile/pos/bills',headers=headers,data={'order_id':1})
        self.assertEqual(bad.status_code,403,bad.text)
        self.assertEqual(self.client.post('/api/mobile/logout',headers=headers).status_code,200)
        self.assertEqual(self.client.get('/api/mobile/me',headers=headers).status_code,401)
        token=self.client.post('/api/mobile/token',json={'login':'frn','password':'frn@123'}).json()['access_token']
        self.sql('UPDATE users SET auth_version=auth_version+1 WHERE franchisee_id=?',(self.fid,))
        self.assertEqual(self.client.get('/api/mobile/me',headers={'Authorization':'Bearer '+token}).status_code,401)

    def test_serial_bridge_sender_and_duplicate_reading(self):
        import io
        from scripts.device_bridge import send
        bill=self.bill(self.order())
        self.at_guard('BILL',bill)
        config={'bridge-test':{'secret':'isolated-test-device-secret','outlet_id':bill['outlet_id']}}
        def transport(request,timeout=15):
            result=self.client.post('/integrations/device/reading',content=request.data,headers=dict(request.header_items()))
            self.assertEqual(result.status_code,200,result.text)
            return io.BytesIO(result.content)
        body={'entity_type':'BILL','entity_id':bill['id'],'weight_kg':bill['expected_weight_kg'],'item_count':1,'request_id':'bridge-reading-1'}
        with patch.dict(os.environ,{'DEVICE_KEYS_JSON':json.dumps(config)}),patch('scripts.device_bridge.urlopen',transport):
            first=send('http://testserver','bridge-test',config['bridge-test']['secret'],body)
            second=send('http://testserver','bridge-test',config['bridge-test']['secret'],body)
        self.assertEqual(first['reading_id'],second['reading_id'])
        self.assertTrue(second['duplicate'])


if __name__=='__main__':unittest.main(verbosity=2)
