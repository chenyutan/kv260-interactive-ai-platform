"""Capture practice ownership at start, never infer it from a late save callback."""
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import threading

IDENT=re.compile(r'^[a-f0-9]{32}$')
MAX_PENDING=64
MAX_PENDING_BYTES=2*1024*1024

class PracticeOwnership:
    def __init__(self,store,autostart=True):
        self.store=store;self.owners={};self.pending={};self.lock=threading.RLock()
        self.drain_lock=threading.Lock();self.last_error=None;self._worker=None
        self.pending_path=Path(store.root)/'health-pending.json' if getattr(store,'root',None) else None
        self._load_pending()
        if autostart and self.pending:self.kick()

    @staticmethod
    def _bounded_report(report):
        # Keep only the same bounded statistics accepted by VisitorStore.
        # Paths, names, video metadata, images and raw joints are never queued.
        from ai_assistant.health import sanitize_report
        safe=sanitize_report(report)
        return dict(session_id=safe['practice_id'],course_id=safe['course_id'],kind=report['kind'],
            mode=safe['mode'],active_seconds=safe['active_seconds'],lessons=safe['lessons'],
            timer_finished_lessons=safe['timer_finished_lessons'],report_save_state='saved')

    def _load_pending(self):
        path=self.pending_path
        if path is None or not path.exists():return
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_PENDING_BYTES:raise ValueError('Invalid pending store')
            value=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value,dict) or set(value)!={'schema','pending'} or value['schema']!=1:raise ValueError('Invalid pending schema')
            rows=value['pending']
            if not isinstance(rows,list) or len(rows)>MAX_PENDING:raise ValueError('Invalid pending count')
            pending={};owners={}
            for row in rows:
                if not isinstance(row,dict) or set(row)!={'owner','practice_id','report'}:raise ValueError('Invalid pending row')
                owner=row['owner'];practice=row['practice_id']
                if not isinstance(owner,str) or not IDENT.fullmatch(owner):raise ValueError('Invalid saved owner')
                if not isinstance(practice,str) or not IDENT.fullmatch(practice) or practice in pending:raise ValueError('Invalid saved practice')
                report=self._bounded_report(row['report'])
                if report['session_id']!=practice:raise ValueError('Pending identity mismatch')
                pending[practice]=(owner,report);owners[practice]=owner
            self.pending=pending;self.owners.update(owners)
        except (OSError,ValueError,TypeError,KeyError,RecursionError):
            self.last_error='待关联练习记录暂时无法读取，未关联到任何当前用户。'

    def _persist_locked(self):
        if self.pending_path is None:return
        path=self.pending_path
        if path.is_symlink():raise ValueError('Invalid pending path')
        payload=json.dumps(dict(schema=1,pending=[dict(owner=owner,practice_id=practice,report=report)
            for practice,(owner,report) in self.pending.items()]),ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
        if len(payload)>MAX_PENDING_BYTES:raise ValueError('Pending data too large')
        path.parent.mkdir(parents=True,exist_ok=True)
        descriptor,temporary=tempfile.mkstemp(prefix='.health-pending-',suffix='.tmp',dir=path.parent)
        try:
            with os.fdopen(descriptor,'wb') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,path)
            # Persist the directory entry on Linux before process shutdown.
            if hasattr(os,'O_DIRECTORY'):
                descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(descriptor)
                finally:os.close(descriptor)
        finally:
            try:os.unlink(temporary)
            except FileNotFoundError:pass

    def bind(self,practice_id,visitor_id):
        if not isinstance(practice_id,str) or not IDENT.fullmatch(practice_id):return False
        if not isinstance(visitor_id,str) or not IDENT.fullmatch(visitor_id):return False
        with self.lock:
            previous=self.owners.get(practice_id)
            if previous is not None and previous!=visitor_id:return False
            self.owners[practice_id]=visitor_id
        return True

    def saved(self,report):
        if not isinstance(report,dict) or not report.get('report_path') or report.get('report_save_error'):return False
        practice=report.get('session_id')
        try:report=self._bounded_report(report)
        except (ValueError,TypeError,KeyError):return False
        with self.lock:
            owner=self.owners.get(practice)
            if owner is None:return False
            if practice not in self.pending and len(self.pending)>=MAX_PENDING:
                self.last_error='待关联练习记录较多，请等待重试后再开始下一轮。'
                return False
            self.pending[practice]=(owner,copy.deepcopy(report))
            try:self._persist_locked()
            except (OSError,ValueError):self.last_error='练习已保存，待关联记录写入暂未完成，将继续重试。'
        self.kick()
        return True

    def kick(self):
        with self.lock:
            if not self.pending:return False
            if self._worker is not None and self._worker.is_alive():return True
            self._worker=threading.Thread(target=self.drain,daemon=True,name='health-record-owner')
            self._worker.start()
        return True

    def flush(self,timeout=1.5):
        """Bounded shutdown wait; the fixed-owner queue survives a timeout."""
        with self.lock:
            try:self._persist_locked()
            except (OSError,ValueError):self.last_error='待关联练习记录尚未写入，请保留应用并稍后重试。'
        self.kick()
        with self.lock:worker=self._worker
        if worker is not None and worker is not threading.current_thread():worker.join(max(0.,float(timeout)))
        with self.lock:return not self.pending

    def drain(self):
        if not self.drain_lock.acquire(False):return False
        try:
            while True:
                with self.lock:
                    if not self.pending:return True
                    practice,(owner,report)=next(iter(self.pending.items()))
                try:self.store.record_health(owner,report)
                except Exception:
                    self.last_error='练习已保存，账户关联暂未完成，将自动重试。'
                    return False
                with self.lock:
                    # A newer delivery of the same practice is not discarded.
                    if self.pending.get(practice)==(owner,report):
                        self.pending.pop(practice,None)
                        try:self._persist_locked()
                        except (OSError,ValueError):
                            self.pending[practice]=(owner,report)
                            self.last_error='练习已关联，待关联记录清理尚未完成，将安全重试。'
                            return False
                    self.last_error=None
        finally:self.drain_lock.release()
