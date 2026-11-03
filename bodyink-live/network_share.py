"""Open the account-protected visitor page from a native board window."""
from contextlib import closing
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler


class ShareUnavailable(Exception):
    pass


def archive_saved(path, title='身随墨动 · 水墨画', marks=0):
    """Archive just-created output for the launching session, even without Wi-Fi."""
    session = os.environ.get('INKSUITE_VISITOR_SESSION')
    database = os.environ.get('INK_VISITOR_DATABASE')
    if not session or not database:
        return None  # Standalone artwork stays on the board.
    database = Path(database).resolve()
    if database.name != 'visitors.sqlite3' or not database.is_file():
        raise ShareUnavailable('游客作品库不可用')
    # In a deployed native window bodyink-live and ink-suite are siblings.
    suite = Path(__file__).resolve().parents[1] / 'ink-suite'
    if str(suite) not in sys.path:
        sys.path.insert(0, str(suite))
    from visitor.store import VisitorStore
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 8 <= path.stat().st_size <= 30_000_000:
        raise ShareUnavailable('作品文件无效')
    return VisitorStore(database.parent).add_art(session, path.read_bytes(), marks, title, source_name=path.name)


def fetch_share(name=None, base=None, opener=None, *, database=None, session_id=None):
    """Return the current visit's QR. The session token stays off the HTTP control path."""
    if name is not None and not re.fullmatch(r'ink-\d{8}-\d{6}-\d{6}\.png', name):
        raise ShareUnavailable('作品文件名无效')
    session_id = session_id or os.environ.get('INKSUITE_VISITOR_SESSION')
    if not session_id:
        raise ShareUnavailable('独立模式仅保存到板卡。请由主菜单进入体验以关联作品。')
    if base is None:
        base = 'http://127.0.0.1:' + os.environ.get('INK_VISITOR_PORT', '8091')
    if database is None:
        database = os.environ.get('INK_VISITOR_DATABASE')
    if not database:
        raise ShareUnavailable('本次游玩未连接游客作品库，请从主菜单进入体验。')
    if opener is None:
        opener = build_opener(ProxyHandler({})).open
    try:
        import qrcode
        with opener(base + '/api/network', timeout=3) as response:
            network = json.loads(response.read(4096))
        origin = urlsplit(network.get('base_url') or '')
        address = ipaddress.ip_address(origin.hostname or '')
        if (origin.scheme != 'http' or origin.username or origin.password or origin.path not in ('', '/')
                or origin.query or origin.fragment or origin.port != urlsplit(base).port
                or network.get('address') != str(address) or not network.get('wireless_connected')):
            raise ValueError('visitor Wi-Fi unavailable')
        uri = Path(database).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            row = db.execute("""SELECT s.token FROM sessions s JOIN meta m ON s.id=m.value
                                WHERE m.key='active_session' AND s.status='active' AND s.id=?""", (session_id,)).fetchone()
            art = None
            if row and name is not None:
                art = db.execute('SELECT n.art_id FROM native_art_sources n JOIN artworks a ON a.id=n.art_id '
                                 'WHERE n.session_id=? AND n.name=? AND a.session_id=n.session_id',
                                 (session_id, name)).fetchone()
                if not art:
                    raise ShareUnavailable('该作品尚未归入本次游玩。请在当前体验内重新保存。')
        if not row or not re.fullmatch(r'[A-Za-z0-9_-]{43}', row[0]):
            raise ShareUnavailable('本次游玩已结束，请回到主菜单。如已关联，可在自己的账户中查看历史作品。')
        url = f'{origin.scheme}://{origin.netloc}/v/{row[0]}'
        if art:
            url += '?art=' + art[0]
        output = io.BytesIO()
        qrcode.make(url).save(output, format='PNG')
        return dict(url=url, png=output.getvalue(), download_url=None, name=name)
    except (ImportError, OSError, ValueError, TypeError, AttributeError, sqlite3.Error) as error:
        raise ShareUnavailable('请回到主菜单，连接展台 Wi-Fi 后选择“扫码下载”。') from error
