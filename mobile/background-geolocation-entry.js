import { BackgroundGeolocation } from '@capgo/background-geolocation';
import { registerPlugin } from '@capacitor/core';

window.AapoortiBackgroundGeolocation = BackgroundGeolocation;
window.AapoortiLocalNotifications = registerPlugin('LocalNotifications');
