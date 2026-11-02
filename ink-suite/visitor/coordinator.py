"""Native-only finish transaction; archive must succeed before clearing ink."""
import hashlib
import json
import os
from pathlib import Path
import threading
import urllib.request
import uuid
import zipfile
from .store import now

class FinishCoordinator:
    def __init__(self,store,base,transport=None):
        self.store=store;self.base=base.rstrip('/');self.lock=threading.Lock()
        self.transport=transport or self.http
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(self,path,data=None):
        request=urllib.request.Request(self.base+path,data=json.dumps(data).encode('utf-8') if data is not None else None,
                                       headers={'Content-Type':'application/json'})
        with self.opener.open(request,timeout=8) as response:return response.read()

    def state(self):return json.loads(self.transport('/api/state'))

    def snapshot(self):
        """Called while native menu has paused drawing; never capture a camera."""
        with self.lock:
            session=self.store.current()
            if session['status']!='active':return session
            before=self.state();png=self.transport('/art.png');after=self.state()
            if before.get('art_revision')!=after.get('art_revision'):raise RuntimeError('画卷仍在更新，请稍后重试')
            if after.get('event_count',0)>0:self.store.add_art(session['id'],png,after['event_count'])
            return session

    def finish(self,session_id,finish_key=None):
        """Only a native confirmed action calls this, never an HTTP route.

        A persisted ending intent authorizes recovery. Clear is retried only
        when the current PNG still equals the archived final artwork; a changed
        canvas is kept for human review. An empty canvas confirms lost clear ACK.
        """
        with self.lock:
            self.store.replay_pending()
            current=self.store.current()
            if current['id']!=session_id:
                with self.store.connect() as db:old=db.execute('SELECT status FROM sessions WHERE id=?',(session_id,)).fetchone()
                if old and old['status']=='archived':return dict(accepted=True,repeated=True,session=current)
                raise RuntimeError('游客会话已变化，请重新打开菜单')
            state=self.state();png=self.transport('/art.png');digest=hashlib.sha256(png).hexdigest()
            if current['status']=='active':
                if state.get('event_count',0)>0:self.store.add_art(session_id,png,state['event_count'])
                # Build and verify the entire downloadable package BEFORE ink clear.
                payload=self.store.report(current['token'],'zip',final=True)
                with zipfile.ZipFile(__import__('io').BytesIO(payload)) as archive:
                    if archive.testzip() is not None:raise RuntimeError('作品报告归档校验失败')
                target=self.store.root/('archive-'+session_id+'.zip');temp=target.with_suffix('.tmp')
                with temp.open('wb') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
                temp.replace(target)
                current=self.store.begin_finish(session_id,finish_key or uuid.uuid4().hex,digest)
            archive_path=self.store.root/('archive-'+session_id+'.zip')
            if not archive_path.is_file():raise RuntimeError('归档文件缺失，已保留画卷，请重试')
            with zipfile.ZipFile(archive_path) as archive:
                if archive.testzip() is not None:raise RuntimeError('归档文件损坏，已保留画卷')
            if state.get('event_count',0)>0:
                if digest!=current['finish_art_hash']:raise RuntimeError('结束操作后画卷发生变化，已保留当前画卷，请人工检查')
                result=json.loads(self.transport('/api/command',{'command':'clear'}))
                if result.get('accepted') is not True:raise RuntimeError('画卷尚未清理，已保留游客归档，请重试')
                if self.state().get('event_count')!=0:raise RuntimeError('画卷清理未确认，请重试')
            new=self.store.complete_finish(session_id)
            return dict(accepted=True,session=new,archived_token=current['token'],completed_at=now())

    def recover(self):
        session=self.store.current()
        return self.finish(session['id']) if session['status']=='ending' else None
