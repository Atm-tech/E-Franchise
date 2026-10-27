import re
import unittest
from tests import test_commission_display as fixtures
from app import core


class PromotionTests(unittest.TestCase):
    setUp = fixtures.CommissionDisplayTests.setUp
    tearDown = fixtures.CommissionDisplayTests.tearDown

    def test_department_commission_accepts_empty_product_select(self):
        page = self.client.get('/admin/dashboard')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
        response = self.client.post('/admin/commissions', data={
            'csrf_token': csrf, 'scope': 'DEPARTMENT', 'department': 'FMCG',
            'product_id': '', 'commission_type': 'PERCENT', 'commission_value': 5,
            'starts_on': '2026-10-27', 'ends_on': '',
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        with core.get_conn() as conn:
            rule = conn.execute("SELECT * FROM commission_rules WHERE department='FMCG'").fetchone()
            self.assertEqual(rule['commission_value'], 5)
            self.assertIsNone(rule['product_id'])

    def test_no_expiry_and_removal(self):
        page = self.client.get('/admin/dashboard')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
        response = self.client.post('/admin/promotions', data={
            'csrf_token': csrf, 'name': 'Forever test', 'scope': 'PRODUCT', 'franchisee_id': '',
            'product_id': self.id, 'discount_type': 'PERCENT',
            'discount_value': 10, 'starts_on': '', 'ends_on': '',
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        with core.get_conn() as conn:
            promo = conn.execute("SELECT * FROM promotions WHERE name='Forever test'").fetchone()
            self.assertIsNone(promo['ends_on'])
            product = conn.execute('SELECT * FROM products WHERE id=?', (self.id,)).fetchone()
            self.assertEqual(core.resolve_deal(conn.cursor(), product, 100), (90, 'Forever test'))
        url = f"/admin/promotions/{promo['id']}/remove"
        page = self.client.get('/admin/dashboard')
        self.assertIn(url, page.text)
        self.assertIn('No expiry', page.text)
        self.assertEqual(self.client.post(url, follow_redirects=False).status_code, 403)
        self.assertEqual(self.client.post(url, data={'csrf_token': csrf}, follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.post(url, data={'csrf_token': csrf}, follow_redirects=False).status_code, 303)
        with core.get_conn() as conn:
            self.assertEqual(conn.execute('SELECT status FROM promotions WHERE id=?', (promo['id'],)).fetchone()['status'], 'INACTIVE')
            self.assertNotEqual(core.resolve_deal(conn.cursor(), product, 100)[1], 'Forever test')
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM audit_logs WHERE entity_type='PROMOTION' AND entity_id=? AND action='REMOVE'", (promo['id'],)).fetchone()['n'], 1)
        self.assertNotIn(url, self.client.get('/admin/dashboard').text)


if __name__ == '__main__':
    unittest.main()
