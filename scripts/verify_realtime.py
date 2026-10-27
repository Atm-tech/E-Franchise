"""Exercise cross-session updates and AJAX writes against an isolated demo DB."""
import os
import json
from contextlib import closing
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='aapoorti-realtime-', ignore_cleanup_errors=True) as directory:
    database = Path(directory) / 'test.db'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    with (Path(directory) / 'server.log').open('w') as log:
        env = dict(os.environ, APP_ENV='test', SEED_DEMO='1', E_FRANCHISE_DATA_DIR=directory, E_FRANCHISE_DB=str(database))
        env.pop('DATABASE_URL', None)
        server = subprocess.Popen([os.sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)], cwd=root, env=env, stdout=log, stderr=log)
        try:
            for _ in range(80):
                try:
                    with urlopen(base + '/ready', timeout=1) as response:
                        if response.status == 200: break
                except Exception: time.sleep(.2)
            def write(query, values=()):
                with closing(sqlite3.connect(database, timeout=15)) as conn:
                    with conn:
                        return conn.execute(query, values).fetchall()
            product = write('SELECT id FROM products LIMIT 1')[0][0]
            write("INSERT INTO inventory_balances(outlet_id,product_id,on_hand,reserved,updated_at) SELECT o.id,p.id,100,0,'test' FROM outlets o CROSS JOIN products p WHERE 1 ON CONFLICT(outlet_id,product_id) DO UPDATE SET on_hand=100,reserved=0")
            write('UPDATE franchisees SET wallet_balance=100000')
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                errors = []
                pages = {}
                for role, path in [('adm','/admin/dashboard'), ('frn','/franchise/home'), ('pos','/pos/home'), ('grd','/guard/home'), ('dlv','/delivery/home')]:
                    context = browser.new_context(viewport={'width':1280,'height':900})
                    page = context.new_page(); page.on('pageerror', lambda exc: errors.append(str(exc)))
                    page.goto(base+'/login')
                    page.locator('[name=phone]').fill(role); page.locator('[name=pin]').fill(role+'@123')
                    page.locator('button[type=submit]').click(); page.wait_for_url('**'+path)
                    page.wait_for_function('Boolean(window.AppUI)')
                    page.evaluate("window.pageSentinel='kept';window.syncCount=0;document.addEventListener('app:updated',()=>window.syncCount++);")
                    page.wait_for_function('window.syncCount>0',timeout=15000)
                    pages[role] = page
                print('PASS: all five role pages initialize and patch without script errors', flush=True)
                admin, franchise = pages['adm'], pages['frn']
                admin.evaluate("openAdminTab('franchisees')")
                admin.context.set_geolocation({'latitude':23.219123,'longitude':77.400456,'accuracy':12})
                admin.context.grant_permissions(['geolocation'], origin=base)
                onboard=admin.locator('form[action="/admin/franchisees"]')
                for name,value in {'name':'GPS test owner','mobile':'GPS-TEST-LOGIN','shop_name':'GPS test shop','address':'Test shop address','initial_password':'LocalTestPassword123'}.items():
                    onboard.locator(f'[name={name}]').fill(value)
                onboard.locator('[data-fill-onboarding-location]').click()
                admin.wait_for_function("document.querySelector('form[action=\"/admin/franchisees\"] [name=latitude]').value==='23.219123'")
                assert onboard.locator('[name=longitude]').input_value()=='77.400456'
                assert onboard.locator('[name=name]').input_value()=='GPS test owner'
                onboard.locator('button[type=submit],button:not([type])').last.click()
                admin.wait_for_function("document.querySelector('main').textContent.includes('GPS test shop')",timeout=15000)
                saved=write("SELECT latitude,longitude FROM franchisees WHERE mobile='GPS-TEST-LOGIN'")[0]
                assert saved==(23.219123,77.400456),saved
                assert admin.evaluate('window.pageSentinel')=='kept'
                token=admin.locator('meta[name=csrf-token]').get_attribute('content')
                payload={'name':'Invalid GPS','mobile':'BAD-GPS-TEST','shop_name':'Invalid GPS shop','initial_password':'LocalTestPassword123','request_key':'invalid-gps-test-request'}
                for coordinates in [{'latitude':'91','longitude':'77'}, {'latitude':'23'}, {'latitude':'NaN','longitude':'77'}]:
                    result=admin.context.request.post(base+'/admin/franchisees',form=payload|coordinates,headers={'X-CSRF-Token':token})
                    assert result.status==400,result.text()
                assert not write("SELECT id FROM franchisees WHERE mobile='BAD-GPS-TEST'")
                result=admin.context.request.post(base+'/admin/franchisees',form=payload|{'mobile':'NO-GPS-TEST','request_key':'no-gps-test-request','latitude':'','longitude':''},headers={'X-CSRF-Token':token})
                assert result.ok,result.text()
                assert write("SELECT latitude,longitude FROM franchisees WHERE mobile='NO-GPS-TEST'")[0]==(None,None)
                admin.evaluate("() => { navigator.geolocation.getCurrentPosition=(_,fail)=>fail({code:1}); }")
                onboard.locator('[data-fill-onboarding-location]').click()
                assert 'permission denied' in onboard.locator('[data-onboarding-location-status]').inner_text()
                assert onboard.locator('[name=latitude]').is_editable()
                admin.set_viewport_size({'width':390,'height':844})
                assert not admin.evaluate('document.documentElement.scrollWidth>innerWidth')
                admin.set_viewport_size({'width':1280,'height':900})
                print('PASS: onboarding captures mocked GPS, saves coordinates without reload, rejects invalid pairs and supports denied-permission manual fallback on mobile',flush=True)
                admin.evaluate("openAdminTab('catalog')")
                form = admin.locator('form[action="/admin/products"]')
                form.locator('[name=sku_code]').fill('DRAFT-KEPT')
                franchise.evaluate("openShop();addItem(catalogueProducts[Object.keys(catalogueProducts)[0]]);openCart()")
                assert franchise.evaluate('Object.keys(cart).length')==1, 'Test basket was not populated'
                franchise.locator('#customer-name').fill('Unsaved customer')
                franchise.locator('#customer-phone').fill('9876543210')
                write('UPDATE products SET product_name=?,rsp=mrp WHERE id=?',('Live renamed product',product))
                write('UPDATE franchisees SET wallet_balance=123456')
                admin.wait_for_function("document.querySelector('main').textContent.includes('Live renamed product')",timeout=15000)
                franchise.wait_for_function('walletBalance===123456',timeout=15000)
                franchise.wait_for_function("Object.values(catalogueProducts).some(p=>p.name==='Live renamed product')",timeout=15000)
                assert form.locator('[name=sku_code]').input_value()=='DRAFT-KEPT'
                assert franchise.locator('#customer-name').input_value()=='Unsaved customer'
                assert franchise.locator('#customer-phone').input_value()=='9876543210'
                assert franchise.locator('#cart-drawer').get_attribute('aria-hidden')=='false'
                assert franchise.evaluate('Object.keys(cart).length')==1
                assert admin.locator('#admin-catalog').evaluate("e=>e.classList.contains('active')")
                print('PASS: external catalogue and wallet writes update both sessions; cart, typed forms and tabs survive',flush=True)
                # A browser form saves through the real validators without document navigation.
                form.locator('[name=product_name]').fill('AJAX test product')
                form.locator('[name=department]').fill('FMCG')
                form.locator('[name=category]').fill('Test')
                form.locator('[name=subcategory]').fill('Test')
                form.locator('[name=mrp]').fill('120')
                form.locator('[name=rsp]').fill('100')
                form.locator('[name=unit_weight]').fill('1')
                assert form.evaluate('form=>form.checkValidity()'), 'Incomplete test product form'
                form.locator('button').last.click()
                admin.wait_for_function("document.querySelector('main').textContent.includes('AJAX test product')",timeout=15000)
                franchise.wait_for_function("Object.values(catalogueProducts).some(p=>p.name==='AJAX test product')",timeout=15000)
                assert write("SELECT COUNT(*) FROM products WHERE sku_code='DRAFT-KEPT'")[0][0]==1
                assert admin.evaluate('window.pageSentinel')=='kept'
                assert form.locator('[name=sku_code]').input_value()==''
                for page in pages.values():
                    assert page.evaluate('window.pageSentinel')=='kept'
                assert not errors, errors
                print('PASS: product form saves once, renders immediately, resets submitted fields, updates other sessions without reload',flush=True)
                for name,value in {'sku_code':'INVALID-DRAFT','product_name':'Invalid test price','department':'FMCG','category':'Test','subcategory':'Test','mrp':'10','rsp':'100','unit_weight':'1'}.items():
                    form.locator(f'[name={name}]').fill(value)
                form.locator('button').last.click()
                admin.wait_for_function("document.querySelector('main').textContent.includes('Check product amounts')",timeout=15000)
                assert form.locator('[name=sku_code]').input_value()=='INVALID-DRAFT'
                assert form.locator('button').last.is_enabled()
                print('PASS: validation errors preserve draft entries and re-enable submission',flush=True)
                # The current browser cart submits through the actual franchise validators.
                franchise.evaluate("setOrderMode('EXECUTED');document.getElementById('checkout-form').requestSubmit()")
                franchise.wait_for_function('Object.keys(cart).length===0', timeout=15000)
                assert franchise.locator('#cart-drawer').get_attribute('aria-hidden')=='true'
                order_id, number = write("SELECT id,order_number FROM orders WHERE customer_name='Unsaved customer' ORDER BY id DESC LIMIT 1")[0]
                franchise.evaluate("showView('customers-view')")
                customer=franchise.locator('.customer-profile').filter(has_text='9876543210')
                customer.wait_for(timeout=15000);customer.locator('summary').first.click()
                customer.locator('.customer-edit > summary').click()
                customer.locator('input[name=name]').fill('Updated live customer')
                customer.locator('.customer-edit-form button').click()
                customer.locator('summary b').first.wait_for(timeout=15000)
                franchise.wait_for_function("document.getElementById('customer-directory').textContent.includes('Updated live customer')",timeout=15000)
                print('PASS: lazy customer editor saves and refreshes its profile without navigation',flush=True)
                pos = pages['pos']
                card = pos.locator(f'[data-outlet-card="order-{order_id}"]')
                card.wait_for(timeout=15000); card.locator('summary').click()
                claim = card.locator(f'form[action="/pos/orders/{order_id}/claim"]')
                if claim.count():
                    claim.locator('button').click()
                    scan = card.locator(f'form[action="/pos/orders/{order_id}/scan"]')
                    scan.wait_for(timeout=15000)
                    sku = write('SELECT p.sku_code FROM order_items oi JOIN products p ON p.id=oi.product_id WHERE oi.order_id=?',(order_id,))[0][0]
                    scan.locator('[name=sku]').fill(sku); scan.locator('button').click()
                bill_form = card.locator('form[action="/pos/bills"]')
                bill_form.wait_for(timeout=15000); bill_form.locator('button').click()
                pos.wait_for_function("id=>!document.querySelector('[data-outlet-card=\"order-'+id+'\"]')",arg=order_id,timeout=15000)
                bill_id = write('SELECT id FROM bills WHERE order_id=?',(order_id,))[0][0]
                pos.evaluate("openOp('packing')")
                pack = pos.locator(f'form[action="/pos/bills/{bill_id}/pack"]')
                pos.locator(f'[data-outlet-card="bill-{bill_id}"] > summary').click()
                pack.wait_for(timeout=15000); pack.locator('button').click()
                franchise.wait_for_function("id=>document.querySelector('[data-order-id=\"'+id+'\"]')?.dataset.orderStatus==='PACKED_INDIVIDUALLY'",arg=order_id,timeout=15000)
                assert pos.evaluate('window.pageSentinel')=='kept'
                assert franchise.evaluate('window.pageSentinel')=='kept'
                print('PASS: checkout clears only the submitted cart; POS receives, claims, scans, bills and packs with live franchise status updates',flush=True)
                write("UPDATE settings SET value='OPTIONAL' WHERE key='docket_policy'")
                handover=pos.locator(f'[data-outlet-card="bill-{bill_id}"] form[action="/pos/handover"]')
                handover.locator('button').click()
                guard=pages['grd']
                incoming=guard.locator('.guard-accept-dispatch').filter(has=guard.locator(f'input[name=dispatch_number][value="{write("SELECT bill_number FROM bills WHERE id=?",(bill_id,))[0][0]}"]'))
                incoming.wait_for(timeout=15000);incoming.locator('button').click()
                weigh=guard.locator(f'[data-ui-key="guard-bill:{bill_id}"] form[action="/guard/weight-check"]')
                weigh.wait_for(timeout=15000)
                weight=write('SELECT expected_weight_kg FROM bills WHERE id=?',(bill_id,))[0][0]
                weigh.locator('[name=actual_weight_kg]').fill(str(weight));weigh.locator('button').click()
                franchise.wait_for_function("id=>document.querySelector('[data-order-id=\"'+id+'\"]')?.dataset.orderStatus==='WEIGHT_CHECK_PASSED'",arg=order_id,timeout=15000)
                assert guard.evaluate('window.pageSentinel')=='kept'
                print('PASS: new guard handover, receipt and weight controls work without reload; franchise status follows',flush=True)
                # Poll-only clients recover even when streaming is unavailable.
                fallback_context=browser.new_context()
                fallback_context.add_init_script('window.EventSource=undefined')
                fallback=fallback_context.new_page();fallback.goto(base+'/login')
                fallback.locator('[name=phone]').fill('adm');fallback.locator('[name=pin]').fill('adm@123')
                fallback.locator('button[type=submit]').click();fallback.wait_for_url('**/admin/dashboard')
                fallback.wait_for_function('Boolean(window.AppUI)')
                fallback.evaluate("window.syncCount=0;document.addEventListener('app:updated',()=>window.syncCount++)")
                fallback.wait_for_function('window.syncCount>0',timeout=15000)
                write('UPDATE products SET product_name=? WHERE id=?',('Polling fallback product',product))
                fallback.wait_for_function("document.querySelector('main').textContent.includes('Polling fallback product')",timeout=22000)
                print('PASS: polling fallback updates without EventSource',flush=True)
                franchise.context.set_offline(True)
                write('UPDATE franchisees SET wallet_balance=123457')
                franchise.context.set_offline(False)
                franchise.wait_for_function('walletBalance===123457',timeout=15000)
                assert franchise.evaluate('window.pageSentinel')=='kept'
                print('PASS: reconnect recovers missed wallet changes without navigation',flush=True)
                # Membership cancellation revokes access but preserves historical records.
                gps_id=write("SELECT id FROM franchisees WHERE mobile='GPS-TEST-LOGIN'")[0][0]
                gps_context=browser.new_context();gps_page=gps_context.new_page()
                gps_page.goto(base+'/login');gps_page.locator('[name=phone]').fill('GPS-TEST-LOGIN');gps_page.locator('[name=pin]').fill('LocalTestPassword123')
                gps_page.locator('button[type=submit]').click();gps_page.wait_for_url('**/franchise/home')
                gps_csrf=gps_page.locator('meta[name=csrf-token]').get_attribute('content')
                jwt=admin.context.request.post(base+'/api/mobile/token',data={'login':'GPS-TEST-LOGIN','password':'LocalTestPassword123'}).json()['access_token']
                path=f'/admin/franchisees/{gps_id}/cancel-membership'
                forbidden=franchise.context.request.post(base+path,form={'request_key':'forbidden-membership-test'},headers={'X-CSRF-Token':franchise.locator('meta[name=csrf-token]').get_attribute('content')},max_redirects=0)
                assert forbidden.status==303
                assert admin.context.request.post(base+path,form={'request_key':'no-csrf-membership-test'},max_redirects=0).status==403
                admin.evaluate("openAdminTab('franchisees')")
                member=admin.locator(f'[data-franchise-id="{gps_id}"]')
                button=member.locator('[data-membership-cancel] button')
                admin.once('dialog',lambda dialog:dialog.dismiss());button.click()
                assert write('SELECT status FROM franchisees WHERE id=?',(gps_id,))[0][0]=='ACTIVE'
                write('UPDATE franchisees SET wallet_balance=100 WHERE id=?',(gps_id,))
                admin.once('dialog',lambda dialog:dialog.accept())
                with admin.expect_response(lambda response:response.url.endswith(path) and response.request.method=='POST') as attempt:
                    button.click()
                assert attempt.value.status==409
                assert write('SELECT status FROM franchisees WHERE id=?',(gps_id,))[0][0]=='ACTIVE'
                write('UPDATE franchisees SET wallet_balance=0 WHERE id=?',(gps_id,))
                held=gps_context.request.post(base+'/api/mobile/franchise/orders',headers={'Authorization':'Bearer '+jwt},form={'product_id':str(product),'qty':'1','status_mode':'ON_HOLD','customer_name':'Historical customer','customer_phone':'9888888888','request_key':'membership-history-order'})
                assert held.ok and held.json()['ok'],held.text()
                admin.once('dialog',lambda dialog:dialog.accept())
                with admin.expect_response(lambda response:response.url.endswith(path) and response.request.method=='POST') as attempt:
                    button.click()
                assert attempt.value.status==409
                held_id=write('SELECT id FROM orders WHERE franchisee_id=?',(gps_id,))[0][0]
                closed=gps_context.request.post(base+f'/franchise/orders/{held_id}/cancel',form={'request_key':'membership-close-hold'},headers={'X-CSRF-Token':gps_csrf})
                assert closed.ok
                admin.once('dialog',lambda dialog:dialog.accept());button.click()
                member.wait_for(state='detached',timeout=15000)
                assert admin.evaluate('window.pageSentinel')=='kept'
                assert write('SELECT status FROM franchisees WHERE id=?',(gps_id,))[0][0]=='CANCELLED'
                assert write('SELECT status FROM users WHERE franchisee_id=?',(gps_id,))[0][0]=='INACTIVE'
                assert write('SELECT COUNT(*) FROM orders WHERE id=?',(held_id,))[0][0]==1
                assert gps_context.request.get(base+'/api/mobile/me',headers={'Authorization':'Bearer '+jwt}).status==401
                gps_page.wait_for_url('**/login',timeout=15000)
                token=admin.locator('meta[name=csrf-token]').get_attribute('content')
                assert admin.context.request.post(base+f'/admin/franchisees/{gps_id}/status',form={'status':'ACTIVE'},headers={'X-CSRF-Token':token},max_redirects=0).status==409
                replay=admin.context.request.post(base+path,form={'request_key':'membership-repeat'},headers={'X-CSRF-Token':token},max_redirects=0)
                assert replay.status==303
                assert write("SELECT COUNT(*) FROM audit_logs WHERE action='CANCEL_MEMBERSHIP' AND entity_id=?",(gps_id,))[0][0]==1
                print('PASS: admin-only, CSRF-protected membership cancellation confirms intent, blocks dues/open orders, removes the card without reload, revokes sessions/tokens, retains history and is replay-safe',flush=True)
                assert not errors, errors
                browser.close()
        except Exception:
            if 'errors' in locals(): print('Browser errors:', errors)
            print((Path(directory)/'server.log').read_text()[-7000:])
            raise
        finally:
            server.terminate(); server.wait(timeout=15)
