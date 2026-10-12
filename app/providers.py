"""Optional providers. No external service is contacted until its action is requested."""
import base64
import json
import os
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ProviderError(Exception):
    pass


def request_json(url, username, password, payload=None, form=False):
    auth = base64.b64encode(f'{username}:{password}'.encode()).decode()
    headers = {'Authorization': f'Basic {auth}', 'Accept': 'application/json'}
    body = None
    if payload is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        body = (urlencode(payload) if form else json.dumps(payload)).encode()
    try:
        with urlopen(Request(url, data=body, headers=headers), timeout=15) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        raise ProviderError('Provider request failed. Reconcile status before retrying.') from error


def payment_ready():
    return all(os.getenv(key) for key in ('RAZORPAY_KEY_ID', 'RAZORPAY_KEY_SECRET', 'RAZORPAY_WEBHOOK_SECRET'))


def razorpay(path, payload=None):
    if not payment_ready():
        raise ProviderError('Razorpay is not configured')
    return request_json('https://api.razorpay.com/v1/' + path, os.environ['RAZORPAY_KEY_ID'], os.environ['RAZORPAY_KEY_SECRET'], payload)


def send_otp(phone, code):
    keys = ('TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_FROM_NUMBER')
    if not all(os.getenv(key) for key in keys):
        raise ProviderError('SMS is not configured. Generate the code in the franchise account.')
    sid = os.environ['TWILIO_ACCOUNT_SID']
    return request_json(f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json', sid,
                        os.environ['TWILIO_AUTH_TOKEN'], {'To': phone, 'From': os.environ['TWILIO_FROM_NUMBER'],
                        'Body': f'Aapoorti handover code: {code}. Valid for 5 minutes. Share only when receiving your goods.'}, form=True)


def route_distance(outlet, franchise):
    """OSRM driving distance; only an explicitly configured routing service is used."""
    base=os.getenv('OSRM_ROUTING_URL','').rstrip('/')
    if not base:
        raise ProviderError('Routing service is not configured')
    coords=(outlet['longitude'],outlet['latitude'],franchise['longitude'],franchise['latitude'])
    if any(value is None for value in coords):
        raise ProviderError('Set both outlet and franchise coordinates first')
    url=f'{base}/route/v1/driving/{coords[0]},{coords[1]};{coords[2]},{coords[3]}?overview=false'
    try:
        with urlopen(Request(url,headers={'Accept':'application/json'}),timeout=10) as response:
            result=json.load(response)
        distance=float(result['routes'][0]['distance'])/1000
        if result.get('code')!='Ok' or not math.isfinite(distance) or not 0<=distance<=1000: raise ValueError()
        return round(distance,2)
    except (HTTPError,URLError,TimeoutError,ValueError,KeyError,IndexError) as error:
        raise ProviderError('Unable to calculate a driving route; check provider and coordinates') from error


def route_details(start_latitude, start_longitude, end_latitude, end_longitude):
    """Return an OSRM route for live tracking only when an operator configured the provider."""
    base=os.getenv('OSRM_ROUTING_URL','').rstrip('/')
    if not base:
        raise ProviderError('Live route and ETA require a configured OSRM_ROUTING_URL')
    values=(start_latitude,start_longitude,end_latitude,end_longitude)
    if any(value is None or not math.isfinite(float(value)) for value in values):
        raise ProviderError('Driver and franchise coordinates are required')
    url=f'{base}/route/v1/driving/{start_longitude},{start_latitude};{end_longitude},{end_latitude}?overview=full&geometries=geojson&steps=false'
    try:
        with urlopen(Request(url,headers={'Accept':'application/json'}),timeout=10) as response:
            result=json.load(response)
        route=result['routes'][0]
        distance=float(route['distance'])
        duration=float(route['duration'])
        coordinates=route['geometry']['coordinates']
        if result.get('code')!='Ok' or not math.isfinite(distance) or not math.isfinite(duration) or distance<0 or duration<0 or len(coordinates)<2:
            raise ValueError()
        normalized=[]
        for point in coordinates:
            longitude,latitude=float(point[0]),float(point[1])
            if not (-180<=longitude<=180 and -90<=latitude<=90): raise ValueError()
            normalized.append([latitude,longitude])
        return {'distance_km':round(distance/1000,1),'duration_seconds':int(duration),'coordinates':normalized}
    except (HTTPError,URLError,TimeoutError,ValueError,KeyError,IndexError,TypeError) as error:
        raise ProviderError('Unable to fetch the live driving route; check OSRM provider and coordinates') from error
