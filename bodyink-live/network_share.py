"""Small local portal client; imported by the native display without GTK dependencies."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:8090'
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


class ShareUnavailable(Exception):
    pass


def fetch_share(name=None, base=BASE, opener=None):
    """Call only from a worker. The returned PNG can be decoded by the GUI thread."""
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
    if name is not None and not re.fullmatch(r'ink-\d{8}-\d{6}-\d{6}\.png', name):
        raise ShareUnavailable('作品文件名无效')
    endpoint = base + '/api/share'
    if name is not None:
        endpoint += '?' + urllib.parse.urlencode({'name': name})
    try:
        with opener(endpoint, timeout=3) as response:
            raw = response.read(32769)
        if len(raw) > 32768:
            raise ShareUnavailable('分享服务返回数据过大')
        payload = json.loads(raw)
        url = payload.get('url')
        qr_url = payload.get('qr_url')
        if not isinstance(url, str) or not isinstance(qr_url, str):
            raise ShareUnavailable('分享链接暂不可用')
        public = urllib.parse.urlsplit(url)
        if public.scheme not in ('http', 'https') or not public.hostname:
            raise ShareUnavailable('分享地址无效')
        # QR generation belongs to the local portal. Never fetch an arbitrary
        # remote URL supplied by an API response from the board UI.
        qr_target = urllib.parse.urljoin(base + '/', qr_url)
        qr_origin = urllib.parse.urlsplit(qr_target)
        local_origin = urllib.parse.urlsplit(base)
        if (qr_origin.scheme, qr_origin.netloc) != (local_origin.scheme, local_origin.netloc):
            raise ShareUnavailable('二维码地址无效')
        with opener(qr_target, timeout=3) as response:
            png = response.read(524289)
        if len(png) > 524288 or not png.startswith(PNG_SIGNATURE):
            raise ShareUnavailable('二维码图像无效')
        return dict(url=url, png=png, download_url=payload.get('download_url'), name=name)
    except ShareUnavailable:
        raise
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise ShareUnavailable('联网分享暂不可用，请确认无线网络和作品网站已启动') from error
