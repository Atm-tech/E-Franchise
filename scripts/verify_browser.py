"""Verify role screens and live refresh using an isolated temporary database."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='aapoorti-browser-') as directory:
    log_path = Path(directory)/'server.log'
    with log_path.open('w') as log:
        env = dict(os.environ, APP_ENV='test', SEED_DEMO='1', E_FRANCHISE_DATA_DIR=directory, E_FRANCHISE_DB=str(Path(directory)/'test.db'))
        server = subprocess.Popen([os.sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8012'],cwd=root,env=env,stdout=log,stderr=log)
        try:
            base='http://127.0.0.1:8012'
            for _ in range(50):
                try:
                    with urlopen(base+'/ready',timeout=1) as response:
                        if response.status == 200: break
                except Exception: time.sleep(.2)
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                context=browser.new_context(viewport={'width':390,'height':844})
                page=context.new_page()
                errors=[]
                page.on('pageerror', lambda exc: errors.append(str(exc)))
                shots=root/'data'/'pending-review'
                shots.mkdir(parents=True,exist_ok=True)
                for role,path in [('adm','/admin/dashboard'),('frn','/franchise/home'),('pos','/pos/home'),('grd','/guard/home'),('dlv','/delivery/home')]:
                    page.goto(base+'/login')
                    page.locator('input[name=phone]').fill(role)
                    page.locator('input[name=pin]').fill(role+'@123')
                    page.locator('button[type=submit]').click()
                    page.wait_for_url('**'+path)
                    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), role+' horizontal overflow'
                    page.goto(base+'/workspace')
                    page.locator('#app-loader').wait_for(state='hidden',timeout=10000)
                    if role=='adm':
                        page.get_by_text('Fulfilment and dispatch policies',exact=True).click()
                        page.locator('select[name=docket_policy]').scroll_into_view_if_needed()
                        assert page.locator('select[name=docket_policy]').is_visible()
                    page.screenshot(path=str(shots/(role+'-workspace.png')),full_page=True)
                    assert page.locator('main').inner_text().strip()
                page.goto(base+'/login')
                page.locator('input[name=phone]').fill('frn')
                page.locator('input[name=pin]').fill('frn@123')
                page.locator('button[type=submit]').click()
                page.wait_for_url('**/franchise/home')
                # Observe the streaming connection's initial event before creating a new order.
                page.evaluate("""window.liveInitialized = false;
                    const Original = window.EventSource;
                    const stream = new Original('/api/events/stream');
                    stream.onmessage = () => {window.liveInitialized = true; stream.close();};""")
                page.wait_for_function('window.liveInitialized',timeout=10000)
                token=context.request.post(base+'/api/mobile/token',data={'login':'frn','password':'frn@123'}).json()['access_token']
                headers={'Authorization':'Bearer '+token}
                product=context.request.get(base+'/api/mobile/catalogue',headers=headers).json()['products'][0]
                body={'cart_json':json.dumps([{'id':product['id'],'qty':1}]),'customer_name':'Browser live check','customer_phone':'9999999999','fulfillment_mode':'FRANCHISE_PICKUP','status_mode':'ON_HOLD','request_key':'browser-live-check'}
                page.evaluate("showView('orders-view')")
                with page.expect_navigation(wait_until='domcontentloaded',timeout=15000):
                    result=context.request.post(base+'/api/mobile/franchise/orders',headers=headers,form=body)
                    assert result.ok, result.text()
                page.wait_for_function("document.querySelector('#orders-view').classList.contains('active')")
                assert page.locator('#orders-view').inner_text().find('Browser live check')>=0
                page.locator('#customer-name').fill('Unsaved buyer',force=True)
                body['request_key']='browser-live-draft'
                context.request.post(base+'/api/mobile/franchise/orders',headers=headers,form=body)
                page.locator('#event-notice').wait_for(timeout=15000)
                assert page.locator('#customer-name').input_value()=='Unsaved buyer'
                page.screenshot(path=str(shots/'live-draft-preserved.png'),full_page=True)
                assert not errors, errors
                browser.close()
                print('Browser checks passed: five role workspaces, mobile width, live refresh, preserved view and unsaved input; no JavaScript errors.')
        finally:
            server.terminate()
            server.wait(timeout=10)
