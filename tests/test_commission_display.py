"""Catalogue commission labels must follow the checkout resolver's rule priority."""
import html
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main, core


class CommissionDisplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {'APP_ENV':'test','SEED_DEMO':'1','DATABASE_URL':''})
        self.environment.start()
        self.database = patch.object(core, 'DB_PATH', Path(self.directory.name)/'test.db')
        self.data_dir = patch.object(core, 'DATA_DIR', Path(self.directory.name))
        self.database.start(); self.data_dir.start()
        self.client = TestClient(main.app); self.client.__enter__()
        with core.get_conn() as conn:
            self.id = conn.execute("SELECT id FROM products WHERE department='Food & Grocery' LIMIT 1").fetchone()['id']
            conn.execute("UPDATE products SET sku_code='DISPLAY-TEST',mrp=100,rsp=90,commission_value=0 WHERE id=?", (self.id,))
            conn.execute('DELETE FROM commission_rules WHERE product_id=?', (self.id,))
        page = self.client.get('/login')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"',page.text)[1]
        self.client.post('/login',data={'phone':'adm','pin':'adm@123','csrf_token':csrf})

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.data_dir.stop(); self.database.stop(); self.environment.stop()
        self.directory.cleanup()

    def catalogue_row(self):
        page = self.client.get('/admin/dashboard')
        self.assertEqual(page.status_code, 200)
        row = next(row for row in re.findall(r'<tr>(.*?)</tr>',page.text,re.S) if 'table-product' in row and 'DISPLAY-TEST' in row)
        return html.unescape(re.sub(r'<[^>]+>', ' ', row))

    def add_rule(self, kind='PERCENT', value=11.5, status='ACTIVE', starts=None, ends=None):
        with core.get_conn() as conn:
            conn.execute('''INSERT INTO commission_rules(scope,department,product_id,commission_type,commission_value,status,created_at,starts_on,ends_on)
                VALUES('PRODUCT','',?,?,?,?,?,?,?)''', (self.id,kind,value,status,core.now_str(),starts,ends))

    def test_department_rule_overrides_zero_product_fallback(self):
        row = self.catalogue_row()
        self.assertIn('8.00%', row)
        self.assertIn('Department rule', row)
        with core.get_conn() as conn:
            product = conn.execute('SELECT * FROM products WHERE id=?',(self.id,)).fetchone()
            self.assertEqual(core.resolve_commission(conn.cursor(),product,100),8)

    def test_unmatched_department_shows_fallback_without_inventing_rate(self):
        with core.get_conn() as conn:
            conn.execute("UPDATE products SET department='FMCG' WHERE id=?",(self.id,))
        row = self.catalogue_row()
        self.assertIn('0.00%', row)
        self.assertIn('No matching rule', row)
        with core.get_conn() as conn:
            conn.execute('UPDATE products SET commission_value=3 WHERE id=?',(self.id,))
        self.assertIn('3.00%', self.catalogue_row())

    def test_product_rule_takes_precedence_and_fixed_rule_is_capped(self):
        self.add_rule()
        row = self.catalogue_row()
        self.assertIn('11.50%', row)
        self.assertIn('Product rule', row)
        self.add_rule(kind='FIXED',value=150)
        row = self.catalogue_row()
        self.assertIn('₹90.00', row)
        self.assertIn('per base pack', row)
        with core.get_conn() as conn:
            product = conn.execute('SELECT * FROM products WHERE id=?',(self.id,)).fetchone()
            self.assertEqual(core.resolve_commission(conn.cursor(),product,90),90)

    def test_inactive_expired_and_future_rules_do_not_replace_current_rule(self):
        self.add_rule(status='INACTIVE')
        self.add_rule(ends='2000-01-01')
        self.add_rule(starts='2999-01-01')
        self.assertIn('8.00%', self.catalogue_row())


if __name__ == '__main__':
    unittest.main()
