"""Fresh-process checks for production-only configuration and startup behavior."""
import os
import subprocess
import sys
import tempfile
import unittest


class ProductionTests(unittest.TestCase):
    def test_fresh_production_bootstrap_secure_cookie_and_no_demo_data(self):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,APP_ENV='production',SEED_DEMO='0',E_FRANCHISE_DATA_DIR=folder,
                     E_FRANCHISE_DB=os.path.join(folder,'production_test.db'),SESSION_SECRET='test-only-random-session-secret-0123456789',
                     BOOTSTRAP_ADMIN_LOGIN='production-admin',BOOTSTRAP_ADMIN_PASSWORD='TestOnlyBootstrapPassword123')
            program=r'''
import re
from fastapi.testclient import TestClient
from app import main
with TestClient(main.app,base_url='https://testserver') as client:
    page=client.get('/login')
    assert page.status_code==200
    assert 'Quick login' not in page.text
    assert 'secure' in page.headers['set-cookie'].lower()
    token=re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
    result=client.post('/login',data={'phone':'production-admin','pin':'TestOnlyBootstrapPassword123','csrf_token':token})
    assert result.status_code==200 and str(result.url).endswith('/admin/dashboard')
    assert client.get('/workspace').status_code==200
    assert client.get('/ready').status_code==200
    with main.get_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM franchisees').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]==0
        assert conn.execute("SELECT value FROM settings WHERE key='test_wallet_topup_enabled'").fetchone()[0]=='0'
print('PRODUCTION_BOOTSTRAP_PASS')
'''
            result=subprocess.run([sys.executable,'-c',program],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('PRODUCTION_BOOTSTRAP_PASS',result.stdout)

    def test_production_requires_real_session_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,APP_ENV='production',E_FRANCHISE_DATA_DIR=folder,SESSION_SECRET='replace-with-at-least-32-random-characters')
            result=subprocess.run([sys.executable,'-c','import app.main'],env=env,capture_output=True,text=True,timeout=15)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('SESSION_SECRET',result.stderr)


if __name__=='__main__':unittest.main(verbosity=2)
