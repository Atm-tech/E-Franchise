import {writeFileSync} from 'node:fs';
const raw = process.env.APP_SERVER_URL;
if (!raw) throw new Error('Set APP_SERVER_URL to the backend URL before syncing the mobile project.');
const url = new URL(raw);
if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('Use an HTTP(S) backend URL without credentials, query or fragment.');
const local = ['localhost','127.0.0.1','10.0.2.2'].includes(url.hostname) || /^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)/.test(url.hostname);
if (url.protocol !== 'https:' && !(local && process.env.MOBILE_DEV === '1')) throw new Error('Use HTTPS, or explicitly set MOBILE_DEV=1 for local testing.');
writeFileSync('capacitor.config.json', JSON.stringify({
  appId: 'in.aapoorti.partner', appName: 'Aapoorti Partner', webDir: 'www',
  server: {url: url.origin + '/login', cleartext: url.protocol === 'http:', errorPath:'offline.html'},
  android: {allowMixedContent:false, useLegacyBridge:true},
  plugins: {CapacitorHttp: {enabled:true}},
  ios: {contentInset:'automatic'}
}, null, 2));
console.log('Configured mobile backend:', url.origin);
writeFileSync('www/runtime-config.js', 'window.AAPOORTI_SERVER_URL=' + JSON.stringify(url.origin + '/login') + ';\n');
