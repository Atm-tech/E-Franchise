"""Install Android command-line tools in an ignored local build directory."""
import hashlib
from pathlib import Path
from urllib.request import urlopen
import xml.etree.ElementTree as ET
import zipfile

root = Path(__file__).resolve().parents[1]
sdk = root / 'tmp' / 'android-sdk'
target = sdk / 'cmdline-tools' / 'latest'
if not (target / 'bin' / 'sdkmanager.bat').exists():
    repository = 'https://dl.google.com/android/repository/'
    tree = ET.fromstring(urlopen(repository+'repository2-3.xml', timeout=30).read())
    package = next(p for p in tree if p.tag.endswith('remotePackage') and p.get('path') == 'cmdline-tools;latest')
    archive = next(a for a in package.find('archives') if a.findtext('host-os') == 'windows')
    complete = archive.find('complete')
    sdk.mkdir(parents=True, exist_ok=True)
    archive_path = sdk / complete.findtext('url')
    if not archive_path.exists():
        print('Downloading Android command-line tools...', flush=True)
        with urlopen(repository+complete.findtext('url'), timeout=60) as response, archive_path.open('wb') as output:
            while chunk := response.read(1024*1024):
                output.write(chunk)
    if hashlib.sha1(archive_path.read_bytes()).hexdigest() != complete.findtext('checksum'):
        raise RuntimeError('SDK download checksum mismatch')
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for item in archive.infolist():
            relative = Path(item.filename).relative_to('cmdline-tools')
            destination = (target / relative).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise RuntimeError('Unsafe archive path')
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(item))
print(sdk)
