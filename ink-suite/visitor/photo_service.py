"""Session-owned, bounded still-photo jobs. No camera or remote renderer is used.

The HTTP handler and native panel share this service. One renderer subprocess
runs at a time; finishing a visitor cannot publish into the next visitor's data.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import threading
import time
import uuid
import warnings

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .store import IDENT, now

MAX_UPLOAD = 16 * 1024 * 1024
MAX_PIXELS = 24_000_000
LONG_SIDE = 1920
STYLES = ('color', 'ink')
TERMINAL = ('completed', 'failed')
RESAMPLE = getattr(Image, 'Resampling', Image)
STAGES = {'preparing_image': '准备照片', 'loading_model': '加载水墨模型',
          'painting': '水墨风格转换', 'finishing': '细化笔墨与设色',
          'finished': '正在保存作品', 'failed': '处理未完成'}


class PhotoError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _png(rgb):
    stream = io.BytesIO()
    Image.fromarray(rgb, 'RGB').save(stream, format='PNG', compress_level=3)
    return stream.getvalue()


def _atomic(path, content):
    temporary = path.with_name(path.name + '.tmp')
    try:
        with temporary.open('wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def normalize_photo(data):
    """Verify before decoding, apply phone orientation, then cap renderer memory."""
    if not isinstance(data, bytes) or not data:
        raise PhotoError('请选择一张照片。')
    if len(data) > MAX_UPLOAD:
        raise PhotoError('照片不能超过 16 MB。', 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                if probe.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise PhotoError('请使用 JPG、PNG 或 WebP 静态照片。', 415)
                if getattr(probe, 'is_animated', False) or getattr(probe, 'n_frames', 1) != 1:
                    raise PhotoError('暂不支持动图，请选择静态照片。', 415)
                if min(probe.size) < 32:
                    raise PhotoError('照片太小，请选择宽高至少 32 像素的图片。')
                if probe.width * probe.height > MAX_PIXELS:
                    raise PhotoError('照片不能超过 2400 万像素。', 413)
                probe.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()  # Reject truncated images rather than silently filling them.
                image = ImageOps.exif_transpose(image)
                if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
                    rgba = image.convert('RGBA')
                    base = Image.new('RGBA', rgba.size, (248, 244, 231, 255))
                    base.alpha_composite(rgba)
                    image = base.convert('RGB')
                else:
                    image = image.convert('RGB')
                image.thumbnail((LONG_SIDE, LONG_SIDE), RESAMPLE.LANCZOS)
                if min(image.size) < 32 or max(image.size) / min(image.size) > 32:
                    raise PhotoError('照片过于狭长，请裁剪后再上传。')
                return np.asarray(image, dtype=np.uint8).copy()
    except PhotoError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PhotoError('无法读取这张照片，请重新导出完整的 JPG 或 PNG 后再试。', 415) from exc


class PhotoService:
    def __init__(self, store, renderer=None, max_jobs=20, max_storage=512 * 1024 * 1024):
        self.store = store
        self.root = store.root / 'photos'
        self.root.mkdir(parents=True, exist_ok=True)
        # A second host must not "recover" another live host's processing row.
        self._owner = (self.root / 'service.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self._owner.seek(0)
                self._owner.write(b'0');self._owner.flush();self._owner.seek(0)
                msvcrt.locking(self._owner.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._owner.close()
            raise PhotoError('照片处理服务已在另一个窗口运行。', 503) from exc
        self.max_jobs = int(max_jobs)
        self.max_storage = int(max_storage)
        self._lock = threading.RLock()
        self._busy = False
        self._closed = False
        self._cancel = threading.Event()
        self._thread = None
        self._active_id = None
        self._started_monotonic = None
        self._renderer = renderer or self._render
        with self.store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS photo_jobs(
              id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),
              status TEXT NOT NULL,created TEXT NOT NULL,started TEXT,finished TEXT,
              style TEXT NOT NULL,stage TEXT NOT NULL,progress REAL NOT NULL DEFAULT 0,
              elapsed_s REAL NOT NULL DEFAULT 0,error TEXT,art_id TEXT,width INTEGER,height INTEGER,
              details TEXT NOT NULL DEFAULT '{}');
            CREATE INDEX IF NOT EXISTS photos_session ON photo_jobs(session_id,created,id);
            ''')
            # A restarted process cannot inherit an old renderer. Preserve source
            # and failure evidence, never show a permanently spinning job.
            db.execute("UPDATE photo_jobs SET status='failed',stage='处理已中断',error=?,finished=? "
                       "WHERE status IN ('queued','processing')",
                       ('上次处理因程序退出而中断，请重新选择照片。', now()))

    def _render(self, rgb, settings, cancelled, progress):
        from environment.photo_job import PhotoJobs
        suite = Path(__file__).resolve().parents[1]
        environment = suite / 'environment'
        runtime = environment / 'runtime'
        runtime = runtime if platform.machine().lower() in ('aarch64', 'arm64') and runtime.is_dir() else None
        jobs = PhotoJobs(worker=environment / 'landscape_worker.py', runtime=runtime,
                         scratch=str(self.root))
        return jobs.render(rgb, settings, cancelled, progress, timeout=180)

    def submit_path(self, token, path, style='color'):
        path = Path(path)
        try:
            with path.open('rb') as stream:
                data = stream.read(MAX_UPLOAD + 1)
        except OSError as exc:
            raise PhotoError('无法读取所选照片，请检查文件或 U 盘是否仍然可用。') from exc
        return self.submit_bytes(token, data, style)

    def submit_bytes(self, token, data, style='color'):
        if style not in STYLES:
            raise PhotoError('请选择淡彩水墨或黑白水墨。')
        # Reserve the only slot before image decoding. Multiple upload requests
        # cannot simultaneously inflate large photos in this A53 process.
        with self._lock:
            if self._closed:
                raise PhotoError('照片处理服务正在关闭，请稍后重试。', 503)
            if self._busy:
                raise PhotoError('已有一张照片正在处理，请等完成后再提交。', 409)
            self._busy = True
        folder = None
        accepted = False
        try:
            session = self.store.by_token(token)
            if session['status'] != 'active':
                raise PhotoError('本次游玩已结束，请扫描主菜单的新二维码上传。', 409)
            with self.store.connect() as db:
                count = db.execute('SELECT COUNT(*) FROM photo_jobs WHERE session_id=?', (session['id'],)).fetchone()[0]
            if count >= self.max_jobs:
                raise PhotoError('本次照片数量已达上限，请结束游玩后重新开始。', 409)
            used = sum(path.stat().st_size for path in self.root.rglob('*') if path.is_file())
            if used >= self.max_storage or shutil.disk_usage(self.root).free < 96 * 1024 * 1024:
                raise PhotoError('板卡存储空间不足，请先清理或下载已有作品。', 503)
            rgb = normalize_photo(data)
            source = _png(rgb)
            if used + len(source) * 3 > self.max_storage:
                raise PhotoError('照片存储空间不足，请先下载已有作品。', 503)
            ident = uuid.uuid4().hex
            folder = self.root / session['id'] / ident
            folder.mkdir(parents=True, exist_ok=False)
            _atomic(folder / 'source.png', source)
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT status FROM sessions WHERE id=?', (session['id'],)).fetchone()
                if not current or current['status'] != 'active':
                    raise PhotoError('本次游玩已结束，请扫描新的二维码。', 409)
                db.execute('''INSERT INTO photo_jobs(id,session_id,status,created,style,stage,width,height)
                  VALUES(?,?,?,?,?,?,?,?)''',
                           (ident, session['id'], 'queued', now(), style, '照片已导入', rgb.shape[1], rgb.shape[0]))
            with self._lock:
                if self._closed:
                    self._fail(ident, '程序已退出，请重新选择照片。')
                    raise PhotoError('照片处理服务正在关闭。', 503)
                self._cancel = threading.Event()
                self._active_id = ident
                self._started_monotonic = time.monotonic()
                self._thread = threading.Thread(target=self._run, args=(ident, session['id'], rgb, style, self._cancel),
                                                name='visitor-photo', daemon=True)
                accepted = True
                self._thread.start()
            return self._job(token, ident)
        finally:
            if not accepted:
                if folder and folder.exists():
                    shutil.rmtree(folder)
                with self._lock:
                    self._busy = False

    def _fail(self, ident, message):
        elapsed = self._elapsed(ident)
        with self.store.connect() as db:
            db.execute("UPDATE photo_jobs SET status='failed',stage='处理未完成',error=?,finished=?,elapsed_s=? "
                       "WHERE id=? AND status NOT IN ('completed','failed')", (message, now(), elapsed, ident))

    def _elapsed(self, ident):
        with self._lock:
            if self._active_id == ident and self._started_monotonic is not None:
                return round(time.monotonic() - self._started_monotonic, 2)
        return 0.

    def _run(self, ident, session_id, rgb, style, cancelled):
        monitor_done = threading.Event()
        def monitor():
            while not monitor_done.wait(.5):
                try:
                    with self.store.connect() as db:
                        state = db.execute('SELECT status FROM sessions WHERE id=?', (session_id,)).fetchone()
                    if not state or state['status'] != 'active':
                        cancelled.set()
                        return
                except (sqlite3.Error, OSError):
                    # A momentarily busy DB cannot invent a session transition.
                    # Publication still requires an authoritative transaction.
                    continue
        watcher = threading.Thread(target=monitor, name='visitor-photo-session', daemon=True)
        watcher.start()
        try:
            with self.store.connect() as db:
                db.execute("UPDATE photo_jobs SET status='processing',started=?,stage='准备画面' WHERE id=?", (now(), ident))
            last = [0.]
            def progress(value):
                if cancelled.is_set():
                    return
                stamp = time.monotonic()
                # Read status often enough to cancel on session end even if the
                # artist repeats the same stage; throttle SQLite writes.
                if stamp - last[0] < .2:
                    return
                last[0] = stamp
                with self.store.connect() as db:
                    state = db.execute('SELECT status FROM sessions WHERE id=?', (session_id,)).fetchone()
                    if not state or state['status'] != 'active':
                        cancelled.set()
                        return
                    raw_stage = str(value.get('stage', '正在绘制'))[:80]
                    stage = STAGES.get(raw_stage, raw_stage)
                    amount = max(0., min(1., float(value.get('progress', 0.))))
                    db.execute('UPDATE photo_jobs SET stage=?,progress=?,elapsed_s=? WHERE id=?',
                               (stage, amount, self._elapsed(ident), ident))
            output, metadata = self._renderer(rgb, {'style': style, 'long_side': LONG_SIDE}, cancelled, progress)
            if cancelled.is_set():
                raise PhotoError('处理已取消，请重新选择照片。')
            output = np.asarray(output)
            if (output.dtype != np.uint8 or output.ndim != 3 or output.shape[2] != 3 or
                    min(output.shape[:2]) < 32 or max(output.shape[:2]) > LONG_SIDE):
                raise PhotoError('处理结果格式异常，请重试。')
            png = _png(output)
            folder = self.root / session_id / ident
            _atomic(folder / 'result.png', png)
            digest = hashlib.sha256(png).hexdigest()
            # Same lock as begin_finish: either this complete artwork belongs to
            # the old visitor before archiving, or no artwork is published.
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                state = db.execute('SELECT status FROM sessions WHERE id=?', (session_id,)).fetchone()
                if not state or state['status'] != 'active' or cancelled.is_set():
                    raise PhotoError('本次游玩已结束，处理结果未加入新游客的作品。')
                old = db.execute('SELECT id FROM artworks WHERE session_id=? AND sha=?', (session_id, digest)).fetchone()
                art_id = old['id'] if old else uuid.uuid4().hex
                if not old:
                    _atomic(self.store.art_root / (art_id + '.png'), png)
                    db.execute('INSERT INTO artworks VALUES(?,?,?,?,?,?)', (art_id, session_id, digest, '照片水墨', now(), 0))
                details = json.dumps(metadata if isinstance(metadata, dict) else {}, ensure_ascii=False, allow_nan=False)
                db.execute("UPDATE photo_jobs SET status='completed',stage='作品已完成',progress=1,error=NULL,"
                           "finished=?,elapsed_s=?,art_id=?,details=? WHERE id=?",
                           (now(), self._elapsed(ident), art_id, details[:12000], ident))
        except Exception as exc:
            try:
                detail = json.dumps({'type': type(exc).__name__, 'detail': str(exc)[:4000], 'at': now()}, ensure_ascii=False)
                _atomic(self.root / session_id / ident / 'failure.json', detail.encode('utf-8'))
            except OSError:
                pass
            message = str(exc) if isinstance(exc, PhotoError) else '照片处理未完成，请重新选择照片后重试。'
            self._fail(ident, message)
        finally:
            monitor_done.set()
            watcher.join(1.)
            with self._lock:
                self._busy = False
                self._active_id = None
                self._started_monotonic = None
                if self._closed:
                    self._owner.close()

    def _public(self, row, token):
        value = dict(row)
        value.pop('session_id', None)
        value.pop('details', None)
        value['elapsed_s'] = self._elapsed(value['id']) if value['status'] not in TERMINAL else value['elapsed_s']
        prefix = '/v/' + token + '/photos/' + value['id']
        value['source_url'] = prefix + '/source.png'
        value['result_url'] = prefix + '/result.png' if value['status'] == 'completed' else None
        return value

    def _job(self, token, ident):
        session = self.store.by_token(token)
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM photo_jobs WHERE id=? AND session_id=?', (ident, session['id'])).fetchone()
        if not row:
            raise KeyError(ident)
        return self._public(row, token)

    def list_jobs(self, token):
        session = self.store.by_token(token)
        with self.store.connect() as db:
            rows = db.execute('SELECT * FROM photo_jobs WHERE session_id=? ORDER BY created DESC,rowid DESC', (session['id'],)).fetchall()
        return [self._public(row, token) for row in rows]

    def read_image(self, token, ident, kind):
        if not isinstance(ident, str) or not IDENT.fullmatch(ident) or kind not in ('source', 'result'):
            raise KeyError(ident)
        job = self._job(token, ident)
        if kind == 'result' and job['status'] != 'completed':
            raise KeyError(ident)
        session = self.store.by_token(token)
        path = self.root / session['id'] / ident / (kind + '.png')
        if path.is_symlink() or not path.is_file():
            raise KeyError(ident)
        return path.read_bytes()

    def close(self):
        with self._lock:
            self._closed = True
            self._cancel.set()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(5.)
        # A cooperative render exits before this point. If an injected renderer
        # ignores cancellation, retain ownership until that thread is gone.
        if not thread or not thread.is_alive():
            self._owner.close()

