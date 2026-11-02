"""Local KV260 gallery. Reads saved artwork/results; never acquires a camera."""
import argparse
import hashlib
import io
import ipaddress
import json
import math
import mimetypes
import re
import socket
import subprocess
import sys
import threading
import time
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'vendor'))
from PIL import Image
import qrcode

ID = re.compile(r'^[a-f0-9]{24}$')
THEMES = {'landscape': '山水', 'bamboo': '墨竹', 'plum': '梅韵', 'mixed': '山水竹梅'}
ACTIONS = {'A01': '山', 'A02': '竹', 'A03': '梅', 'A04': '云雾', 'A05': '流水', 'A06': '点染'}


def read_json(path, limit=2_000_000):
    if path.is_symlink() or path.stat().st_size > limit:
        raise ValueError('Invalid data file')
    def reject_nonfinite(value):
        raise ValueError('Non-finite JSON value')
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=reject_nonfinite)


class Gallery:
    def __init__(self, works, fruit, fight, cache, port=8090, public_url=None):
        self.works, self.fruit, self.fight = map(lambda p: Path(p).resolve(), (works, fruit, fight))
        self.cache = Path(cache).resolve()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.port, self.public_url = port, public_url
        self.lock = threading.RLock()
        self.catalog, self.scanned_at = {}, -100.
        self.network_cache, self.network_at = {}, -100.

    def artworks(self, force=False):
        with self.lock:
            if not force and time.monotonic() - self.scanned_at < 3:
                return list(self.catalog.values())
            records = []
            for path in self.works.glob('ink-*.png'):
                try:
                    if path.is_symlink() or not path.is_file() or path.stat().st_size > 30_000_000:
                        continue
                    stat = path.stat()
                    ident = hashlib.sha256(path.name.encode()).hexdigest()[:24]
                    with Image.open(path) as im:
                        width, height = im.size
                    if width * height > 36_000_000:
                        continue
                    events = []
                    art_kind = 'gesture'
                    metadata = path.with_suffix('.json')
                    if metadata.exists():
                        try:
                            data = read_json(metadata)
                            events = data if isinstance(data, list) else data.get('events', [])
                            if isinstance(data, dict) and data.get('theme') == 'environment':
                                art_kind = 'environment'
                        except (OSError, ValueError, TypeError, AttributeError):
                            pass
                    events = events if isinstance(events, list) else []
                    codes = []
                    for event in events:
                        if isinstance(event, dict):
                            code = event.get('action') or event.get('action_id') or event.get('label')
                            if isinstance(code, str) and code in ACTIONS and code not in codes:
                                codes.append(code)
                    title = ' · '.join(ACTIONS[c] for c in codes) if codes else '水墨作品'
                    if art_kind == 'environment':
                        title = '眼前风景 · 水墨写生'
                    records.append(dict(id=ident, name=path.name, title=title,
                        created_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                        width=width, height=height, bytes=stat.st_size, marks=len(events), kind=art_kind,
                        image_url=f'/artwork/{ident}.png', thumbnail_url=f'/thumb/{ident}.jpg',
                        download_url=f'/artwork/{ident}.png?download=1', page_url=f'/art/{ident}'))
                except (OSError, ValueError, Image.DecompressionBombError):
                    continue
            records.sort(key=lambda r: (r['created_at'], r['name']), reverse=True)
            self.catalog = {item['id']: item for item in records}
            self.scanned_at = time.monotonic()
            return records

    def artwork(self, ident):
        if not ID.fullmatch(ident):
            raise KeyError(ident)
        self.artworks()
        item = self.catalog.get(ident)
        if item is None:
            self.artworks(force=True)
            item = self.catalog.get(ident)
        if item is None:
            raise KeyError(ident)
        path = self.works / item['name']
        if path.is_symlink() or path.resolve().parent != self.works or not path.is_file():
            raise KeyError(ident)
        return item, path

    def thumbnail(self, ident):
        item, source = self.artwork(ident)
        target = self.cache / (ident + '.jpg')
        with self.lock:
            if target.is_symlink():
                raise KeyError(ident)
            if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
                with Image.open(source) as im:
                    im.thumbnail((960, 720))
                    if im.mode == 'RGBA':
                        canvas = Image.new('RGB', im.size, '#f5f0e5')
                        canvas.paste(im, mask=im.getchannel('A'))
                    else:
                        canvas = im.convert('RGB')
                    with tempfile.NamedTemporaryFile(dir=self.cache, prefix='.'+ident+'-', suffix='.tmp', delete=False) as output:
                        temp = Path(output.name)
                        try:
                            canvas.save(output, format='JPEG', quality=86)
                        except Exception:
                            output.close(); temp.unlink(missing_ok=True); raise
                    try:
                        temp.replace(target)
                    finally:
                        temp.unlink(missing_ok=True)
        return target

    def network(self):
        with self.lock:
            if time.monotonic() - self.network_at < 5:
                return self.network_cache
            interfaces = []
            try:
                data = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr', 'show', 'up'], timeout=2))
                for interface in data:
                    name = interface['ifname']
                    wifi = (Path('/sys/class/net') / name / 'wireless').exists() or name.startswith(('wl', 'wlan'))
                    for addr in interface.get('addr_info', []):
                        ip = ipaddress.ip_address(addr['local'])
                        if addr.get('scope') == 'global' and not ip.is_loopback and not ip.is_link_local:
                            interfaces.append(dict(name=name, ip=str(ip), wireless=wifi))
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                pass
            interfaces.sort(key=lambda item: (not item['wireless'], item['name']))
            selected = interfaces[0] if interfaces else None
            base = self.public_url or (f"http://{selected['ip']}:{self.port}" if selected else None)
            self.network_cache = dict(base_url=base, wireless_connected=bool(selected and selected['wireless']),
                address=selected['ip'] if selected else None, interfaces=interfaces, scope='same-network')
            self.network_at = time.monotonic()
            return self.network_cache

    def share(self, name=None, ident=None):
        net = self.network()
        if not net['base_url']:
            raise ConnectionError('板卡还没有可用的局域网地址')
        if name is not None:
            if Path(name).name != name or '/' in name or '\\' in name:
                raise KeyError(name)
            self.artworks(force=True)
            ident = next((x['id'] for x in self.catalog.values() if x['name'] == name), None)
            if not ident:
                raise KeyError(name)
        if ident:
            item, _ = self.artwork(ident)
            return dict(url=net['base_url'] + item['page_url'],
                qr_url=f'/api/artworks/{ident}/qr.png',
                download_url=net['base_url'] + item['download_url'],
                wireless_connected=net['wireless_connected'])
        return dict(url=net['base_url']+'/', qr_url='/api/qr.png', download_url=None,
                    wireless_connected=net['wireless_connected'])

    def qr(self, ident=None):
        link = self.share(ident=ident)['url']
        code = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=7, border=4)
        code.add_data(link); code.make(fit=True)
        output = io.BytesIO()
        code.make_image(fill_color='black', back_color='white').save(output, format='PNG')
        return output.getvalue()

    def scores(self, game):
        directory = {'fruit': self.fruit, 'fight': self.fight}.get(game)
        if directory is None:
            raise KeyError(game)
        records = []
        # Only completed application round files, never capture diagnostics.
        fields = ('round_id', 'mode', 'score', 'best_combo', 'fruits_cut', 'bombs_hit', 'missed',
                  'elapsed', 'result', 'hp', 'enemy_hp', 'hits', 'blocks', 'perfect_blocks', 'move_counts')
        for path in directory.glob('*.json'):
            try:
                value = read_json(path, 100_000)
                if not isinstance(value, dict) or value.get('completed') is not True:
                    continue
                numeric = ('score', 'best_combo', 'fruits_cut', 'bombs_hit', 'missed',
                           'elapsed', 'hp', 'enemy_hp', 'hits', 'blocks', 'perfect_blocks')
                if any(key in value and (isinstance(value[key], bool) or not isinstance(value[key], (int, float))
                    or not math.isfinite(value[key])) for key in numeric):
                    continue
                if game == 'fruit' and 'score' not in value:
                    continue
                if game == 'fight' and value.get('result') not in ('胜', '负', '平'):
                    continue
                row = {key: value[key] for key in fields if key in value}
                completed = value.get('completed_at')
                try:
                    stamp = datetime.fromisoformat(completed.replace('Z', '+00:00'))
                    if stamp.tzinfo is None:
                        raise ValueError('Timestamp needs timezone')
                except (AttributeError, ValueError, TypeError):
                    stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                row['completed_at'] = stamp.astimezone(timezone.utc).isoformat()
                records.append(row)
            except (OSError, ValueError, TypeError):
                continue
        records.sort(key=lambda r: r['completed_at'], reverse=True)
        return records[:100]


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, gallery):
        self.gallery = gallery
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)
    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close(); return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release(); raise
    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'InkGallery/1.0'
    def setup(self):
        super().setup(); self.connection.settimeout(10)
    def log_message(self, fmt, *args):
        if len(args) > 1 and str(args[1]).startswith(('4', '5')):
            super().log_message(fmt, *args)
    def respond(self, data, content_type='application/json; charset=utf-8', status=200, headers=None):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        headers = dict(headers or {})
        self.send_header('Cache-Control', headers.pop('Cache-Control',
            'no-store' if content_type.startswith('application/json') else 'private, max-age=60'))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)
    def do_HEAD(self):
        self.do_GET()
    def do_GET(self):
        try:
            self.route()
        except KeyError:
            self.respond(dict(error='没有找到这份作品或页面'), status=404)
        except ConnectionError as exc:
            self.respond(dict(error=str(exc), message=str(exc)), status=503)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except (OSError, ValueError, TypeError):
            self.respond(dict(error='暂时无法读取，请稍后重试'), status=503)
    def route(self):
        request = urlsplit(self.path)
        path, query, gallery = request.path, parse_qs(request.query), self.server.gallery
        if path in ('/', '/scores') or re.fullmatch(r'/art/[a-f0-9]{24}', path):
            return self.respond((ROOT/'static/index.html').read_bytes(), 'text/html; charset=utf-8')
        if path in ('/static/app.js', '/static/style.css', '/static/favicon.svg'):
            asset = ROOT/'static'/path.rsplit('/', 1)[1]
            return self.respond(asset.read_bytes(), mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
        if path == '/health':
            return self.respond(dict(ok=True, version='1.0', camera_used=False))
        if path == '/api/network':
            return self.respond(gallery.network())
        if path == '/api/artworks':
            return self.respond(dict(items=gallery.artworks()))
        if path == '/api/share':
            return self.respond(gallery.share(name=query.get('name', [None])[0]))
        if path == '/api/scores':
            return self.respond(dict(items=gallery.scores(query.get('game', ['fruit'])[0])))
        if path == '/api/qr.png':
            return self.respond(gallery.qr(), 'image/png', headers={'Cache-Control':'no-store'})
        match = re.fullmatch(r'/api/artworks/([a-f0-9]{24})(/qr.png)?', path)
        if match:
            if match[2]:
                return self.respond(gallery.qr(match[1]), 'image/png', headers={'Cache-Control':'no-store'})
            item, _ = gallery.artwork(match[1])
            return self.respond(item)
        match = re.fullmatch(r'/(artwork|thumb)/([a-f0-9]{24})\.(png|jpg)', path)
        if match:
            if (match[1], match[3]) == ('thumb', 'jpg'):
                return self.respond(gallery.thumbnail(match[2]).read_bytes(), 'image/jpeg')
            if (match[1], match[3]) == ('artwork', 'png'):
                item, source = gallery.artwork(match[2])
                headers = {}
                if query.get('download') == ['1']:
                    headers['Content-Disposition'] = 'attachment; filename="'+item['name']+'"'
                return self.respond(source.read_bytes(), 'image/png', headers=headers)
        raise KeyError(path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--host', default='0.0.0.0'); p.add_argument('--port', type=int, default=8090)
    p.add_argument('--works', default='/home/ubuntu/bodyink-live/works')
    p.add_argument('--fruit-rounds', default='/home/ubuntu/fruit-slice/native/rounds')
    p.add_argument('--fight-rounds', default='/home/ubuntu/ink-fight/native/rounds')
    p.add_argument('--cache', default=str(ROOT/'data/thumbs'))
    p.add_argument('--public-url', help='Explicit LAN origin, useful for local development')
    args = p.parse_args()
    if args.public_url:
        parsed = urlsplit(args.public_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path not in ('', '/'):
            p.error('--public-url must be an HTTP origin')
        args.public_url = args.public_url.rstrip('/')
    gallery = Gallery(args.works, args.fruit_rounds, args.fight_rounds, args.cache, args.port, args.public_url)
    server = Server((args.host, args.port), gallery)
    print(f'InkGallery ready on {args.host}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
