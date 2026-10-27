"""Check shared errors on a mobile viewport without production data."""
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width":390,"height":844})
    page.route('https://errors.test/**', lambda route: route.fulfill(status=200, content_type='text/html', body='<main><form><label>Name<input name="name" required></label><button>Save</button></form><div id="result"></div></main>'))
    page.goto('https://errors.test/')
    page.evaluate("window.fetch = async () => new Response(JSON.stringify({detail:'Wallet balance too low'}), {status:400,headers:{'content-type':'application/json'}})")
    page.add_script_tag(path=str(root/'app/static/errors.js'))
    popup = page.locator('#ui-error-popup')
    def check(text):
        page.get_by_role('dialog').wait_for()
        assert text in popup.inner_text()
        assert popup.evaluate('(d)=>d.matches(":modal")')
        box = popup.bounding_box()
        assert box['y'] >= 0 and box['y'] + box['height'] <= 844
        page.get_by_role('button',name='OK',exact=True).click()
    page.evaluate("alert('Cannot scan barcode')")
    check('Cannot scan barcode')
    page.evaluate("fetch('/wallet', {method:'POST'})")
    check('Wallet balance too low')
    page.evaluate("document.querySelector('#result').innerHTML='<div class=order-error>Order is already closed</div>'")
    check('Order is already closed')
    page.get_by_role('button',name='Save',exact=True).click()
    check('Name:')
    page.evaluate("fetch('/api/ui-state')")
    assert not popup.is_visible()
    page.evaluate("window.dispatchEvent(new ErrorEvent('error', {error:new Error('internal details')}))")
    check('Something went wrong')
    assert 'internal details' not in popup.inner_text()
    browser.close()
print('PASS: native alerts, API errors, inline errors, validation, runtime errors and quiet background retries')
