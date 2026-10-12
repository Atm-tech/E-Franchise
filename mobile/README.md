# Aapoorti mobile preview

Native Android and iOS Capacitor projects wrap the existing role-based app. They are development/internal preview shells; they are not a completed app-store release. The online backend must remain reachable. Capacitor documents `server.url` as intended for live reload, not production: [configuration](https://capacitorjs.com/docs/config). A store release needs a bundled mobile frontend, release signing and device acceptance testing.

## Configure and sync

```powershell
cd mobile
npm.cmd ci
$env:APP_SERVER_URL='https://aapoorti-e-franchise.vercel.app'
npm.cmd run sync
```

For an Android emulator and local Uvicorn, set `APP_SERVER_URL=http://10.0.2.2:8000` and `MOBILE_DEV=1`. HTTP is allowed only for explicitly enabled local testing. Physical devices need an accessible LAN/HTTPS backend; device localhost refers to the device itself.

## Android

Java 21, Android SDK platform 36 and Android Build Tools are required. From the repository root, set `ANDROID_HOME` and `ANDROID_SDK_ROOT` to the SDK directory, then run `mobile/android/gradlew.bat -p mobile/android assembleDebug`. The debug APK appears at `mobile/android/app/build/outputs/apk/debug/app-debug.apk`; the current workspace copy is `output/apk/Aapoorti-E-Franchise-debug.apk`. Camera and location permissions are declared; install it on a physical phone to verify permissions, background sharing, notification, and tracking stop behavior. A debug APK is signed with a development key, not a store release key.

## iOS

Open `ios/App/App.xcodeproj` on a Mac with Xcode and configure your signing team. Camera/location usage descriptions are included. Build and device verification cannot be performed on this Windows PC. [Capacitor environment requirements](https://capacitorjs.com/docs/getting-started/environment-setup).

## Mobile API

- `POST /api/mobile/token` takes JSON `{ "login": "frn", "password": "your-password" }` and returns a one-hour bearer JWT.
- Send `Authorization: Bearer <token>` to `/api/mobile/me`, `/api/mobile/orders`, `/api/mobile/catalogue`, or shared operation routes.
- Mutation routes retain existing form-data fields and validation. Examples: `/api/mobile/franchise/orders`, `/api/mobile/pos/orders/{id}/claim`, `/api/mobile/pos/orders/{id}/scan`, `/api/mobile/guard/seal-check`, `/api/mobile/delivery/tasks`.
- Financial mutations still require request keys. Shared operation results return `{ok,next}`; validation failures return HTTP errors. Never store the access token in a URL.
- `/api/mobile/logout` revokes the current token. Password changes and staff deactivation invalidate outstanding tokens through the user's auth version. Tokens have pinned HS256 signing, issuer/audience validation and expiry, following [PyJWT validation guidance](https://pyjwt.readthedocs.io/en/latest/usage.html).
- The current preview shell uses the existing web session; JWT APIs support a future bundled/native client.

## Live delivery tracking

The native app includes the Capgo Capacitor 8 background-geolocation plugin and local notifications. The driver must start sharing from an active trip and grant location and notification permissions. Android shows a persistent notification; iOS uses its location background mode. Native HTTP is enabled so GPS posts do not depend on a throttled WebView fetch in the background.
`npm run sync` bundles the plugin bridge and synchronizes native projects. The in-app franchise map uses OpenStreetMap tiles, and road route/ETA requires an operator-configured `OSRM_ROUTING_URL`. Browser-only tracking works while the delivery page stays open. Background behavior must be validated on physical Android/iOS devices; iOS build/signing requires a Mac with Xcode.
