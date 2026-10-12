"""Send one signed scale/counter reading from a serial device or JSON file."""
import argparse
import hashlib
import hmac
import json
import math
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def serial_reading(port, baud, pattern, timeout):
    import serial
    regex = re.compile(pattern)
    until = time.monotonic() + timeout
    with serial.Serial(port, baudrate=baud, timeout=1) as device:
        while time.monotonic() < until:
            line = device.readline().decode('ascii', errors='replace').strip()
            match = regex.fullmatch(line)
            if match:
                groups = match.groupdict()
                return {name: float(groups[name]) if name == 'weight_kg' else int(groups[name])
                        for name in ('weight_kg', 'item_count') if groups.get(name) is not None}
    raise ValueError('No matching stable device reading before timeout')


def send(url, device_id, secret, payload):
    raw = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
    for attempt in range(3):
        timestamp = str(time.time())
        signature = hmac.new(secret.encode(), timestamp.encode()+b'.'+raw, hashlib.sha256).hexdigest()
        request = Request(url.rstrip('/')+'/integrations/device/reading', data=raw,
                          headers={'Content-Type':'application/json','X-Device-Id':device_id,'X-Device-Timestamp':timestamp,'X-Device-Signature':signature})
        try:
            with urlopen(request, timeout=15) as result:
                return json.load(result)
        except HTTPError:
            raise  # Do not retry authentication, validation, or dispatch conflicts.
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--device-id', required=True)
    parser.add_argument('--kind', choices=['BILL','DOCKET'], required=True)
    parser.add_argument('--entity-id', type=int, required=True)
    parser.add_argument('--request-id', default=None)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--port')
    source.add_argument('--reading-file', type=Path)
    parser.add_argument('--baud', type=int, default=9600)
    parser.add_argument('--pattern', default=r'ST\s+(?P<weight_kg>[0-9]+(?:\.[0-9]+)?)\s+kg')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    reading = serial_reading(args.port,args.baud,args.pattern,args.timeout) if args.port else json.loads(args.reading_file.read_text())
    values = {k: reading[k] for k in ('weight_kg','item_count') if reading.get(k) is not None}
    if not values or any(not math.isfinite(float(v)) or float(v)<0 for v in values.values()):
        raise ValueError('Expected non-negative weight_kg and/or item_count')
    if 'item_count' in values and int(values['item_count']) != float(values['item_count']):
        raise ValueError('Item count must be an integer')
    payload = dict(values,entity_type=args.kind,entity_id=args.entity_id,request_id=args.request_id or uuid4().hex)
    if args.dry_run:
        print(json.dumps(payload))
    else:
        secret = os.environ.get('DEVICE_BRIDGE_SECRET')
        if not secret:
            raise ValueError('Set DEVICE_BRIDGE_SECRET to the registered device secret')
        print(json.dumps(send(args.url,args.device_id,secret,payload)))
