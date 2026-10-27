import re
import unittest
from tests import test_commission_display as fixtures
from app import core


class CommissionManagementTests(unittest.TestCase):
    setUp = fixtures.CommissionDisplayTests.setUp
    tearDown = fixtures.CommissionDisplayTests.tearDown

    def test_edit_remove_and_fallback(self):
        with core.get_conn() as conn:
            rule_id = conn.execute("SELECT id FROM commission_rules WHERE scope='DEPARTMENT' AND department='Food & Grocery' ORDER BY id DESC").fetchone()['id']
        page = self.client.get('/admin/dashboard')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
        edit = f'/admin/commissions/{rule_id}/edit'
        remove = f'/admin/commissions/{rule_id}/remove'
        self.assertIn(edit, page.text)
        self.assertIn(remove, page.text)
        data = {'csrf_token':csrf, 'commission_type':'PERCENT','commission_value':5,'starts_on':'','ends_on':''}
        self.assertEqual(self.client.post(edit,data=data,follow_redirects=False).status_code,303)
        with core.get_conn() as conn:
            product = conn.execute('SELECT * FROM products WHERE id=?',(self.id,)).fetchone()
            self.assertEqual(core.resolve_commission(conn.cursor(),product,100),5)
        self.assertEqual(self.client.post(edit,data=dict(data,commission_value=101),follow_redirects=False).status_code,400)
        self.assertEqual(self.client.post(remove,follow_redirects=False).status_code,403)
        for _ in range(2):
            self.assertEqual(self.client.post(remove,data={'csrf_token':csrf},follow_redirects=False).status_code,303)
        with core.get_conn() as conn:
            self.assertEqual(core.resolve_commission(conn.cursor(),product,100),0)
            self.assertEqual(conn.execute('SELECT status FROM commission_rules WHERE id=?',(rule_id,)).fetchone()['status'],'INACTIVE')
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM audit_logs WHERE action='REMOVE' AND entity_type='COMMISSION_RULE' AND entity_id=?",(rule_id,)).fetchone()['n'],1)
        self.assertNotIn(remove,self.client.get('/admin/dashboard').text)
        self.assertEqual(self.client.post(edit,data=data,follow_redirects=False).status_code,404)

    def test_non_admin_cannot_manage_rules(self):
        page = self.client.get('/admin/dashboard')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"',page.text)[1]
        self.client.post('/logout',data={'csrf_token':csrf})
        page = self.client.get('/login')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"',page.text)[1]
        self.client.post('/login',data={'phone':'frn','pin':'frn@123','csrf_token':csrf})
        page = self.client.get('/franchise/home')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"',page.text)[1]
        for action in ['edit','remove']:
            response = self.client.post('/admin/commissions/1/'+action,data={'csrf_token':csrf,'commission_type':'PERCENT','commission_value':5},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            self.assertEqual(response.headers['location'],'/login')
