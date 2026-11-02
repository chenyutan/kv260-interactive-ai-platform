"""Optional outbound HTTPS publishing. Local play and downloads remain independent."""
import hashlib
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
import urllib.request
import urllib.error


class PublicPublisher:
    def __init__(self,store,images,config):
        if not isinstance(config,dict) or not isinstance(config.get('base_url'),str) or not isinstance(config.get('device_key'),str) or not config['device_key']:raise ValueError('Invalid public site configuration')
        self.store=store;self.images=images;self.base=config['base_url'].rstrip('/')
        u=urlsplit(self.base)
        if u.scheme!='https' and not (u.scheme=='http' and u.hostname in ('127.0.0.1','localhost')):raise ValueError('Public site requires HTTPS')
        if u.username or u.password or u.query or u.fragment or u.path not in ('','/'):raise ValueError('Invalid public site address')
        self.secret=config['device_key'];self.stop=threading.Event();self.wake=threading.Event();self.lock=threading.Lock()
        current=store.current()
        with store.connect() as db:cutoff=db.execute('SELECT rowid FROM sessions WHERE id=?',(current['id'],)).fetchone()[0]
        self.path=store.root/'public-sync.json';self.state={'installed':current['started'],'installed_session_rowid':cutoff,'base_url':self.base,'visitors':{}}
        if self.path.is_file():
            try:
                state=json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(state,dict) or not isinstance(state.get('visitors'),dict) or not isinstance(state.get('installed'),str):raise ValueError('Invalid sync state')
                if 'installed_session_rowid' in state and (type(state['installed_session_rowid']) is not int or state['installed_session_rowid']<0):raise ValueError('Invalid sync cutoff')
                from .store import IDENT,TOKEN
                for ident,item in state['visitors'].items():
                    if not IDENT.fullmatch(ident) or not isinstance(item,dict) or not TOKEN.fullmatch(str(item.get('token',''))) or not isinstance(item.get('assets'),dict):raise ValueError('Invalid sync visitor')
                self.state=state
                if state.get('base_url')!=self.base:
                    self.state['base_url']=self.base
                    for item in self.state['visitors'].values():item.update(synced=None,assets={},ready=False)
            except (ValueError,KeyError,TypeError):
                self.path.replace(self.path.with_name('public-sync-corrupt-'+str(time.time_ns())+'.json'))
        current=store.current();self.enroll(current)
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.error=None;self.last_success=None;self.thread=None

    def save(self):
        temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps(self.state,ensure_ascii=False),encoding='utf-8');temporary.replace(self.path)

    def enroll(self,row):
        with self.lock:
            self.state['visitors'].setdefault(row['id'],dict(token=row['token'],synced=None,assets={},ready=False))
            self.save()
        self.wake.set()

    def request(self,path,data=None,method='GET',mime='application/json'):
        headers={'Content-Type':mime}
        if data is not None:headers['Authorization']='Bearer '+self.secret
        req=urllib.request.Request(self.base+path,data=data,headers=headers,method=method)
        for attempt in range(3):
            try:
                with self.opener.open(req,timeout=15) as response:
                    return json.load(response) if response.headers.get('Content-Type','').startswith('application/json') else response.read()
            except urllib.error.HTTPError as exc:
                if exc.code not in (502,503,504) or attempt==2:raise
                exc.close()
                if self.stop.wait(.5*(attempt+1)):raise OSError('Publisher stopped')

    def discover(self):
        with self.store.connect() as db:
            if 'installed_session_rowid' in self.state:
                rows=[dict(r) for r in db.execute('SELECT id,token FROM sessions WHERE rowid>?',(self.state['installed_session_rowid'],))]
            else:rows=[dict(r) for r in db.execute('SELECT id,token FROM sessions WHERE started>=?',(self.state['installed'],))]
        for row in rows:
            if row['id'] not in self.state['visitors']:self.enroll(row)

    @staticmethod
    def stable_view(report):
        value=json.loads(json.dumps(report,ensure_ascii=False))
        for row in value['rounds']:
            if row['status']=='active':row.update(data={},score=0,updated=row['started'])
        return value

    def sync_one(self,ident,item):
        token=item['token'];report=self.stable_view(self.store.view(token));changed=False
        if item.get('ready'):
            try:remote=self.request('/api/visitor/'+token)
            except urllib.error.HTTPError as exc:
                if exc.code!=404:raise
                item.update(ready=False,synced=None,assets={});remote={}
            # Phone names become authoritative once the record is published.
            if remote.get('name') and remote['name']!=report['name']:
                self.store.rename(token,remote['name']);report['name']=remote['name'];changed=True
        signature=hashlib.sha256(json.dumps(report,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if signature==item.get('synced') and not changed:return
        info=self.images.manifest(token,report)
        path='/api/device/sessions/'+token
        revision=signature
        payload={'view':report,'report_pages':info['pages'],'image_name':report['name'],'revision':revision,'commit':False}
        response=self.request(path,json.dumps(payload,ensure_ascii=False).encode(),'POST')
        if response.get('name') and response['name']!=report['name']:
            self.store.rename(token,response['name']);report['name']=response['name'];payload.update(view=report,image_name=report['name'])
            revision=hashlib.sha256(json.dumps(report,sort_keys=True,ensure_ascii=False).encode()).hexdigest();payload['revision']=revision
            self.request(path,json.dumps(payload,ensure_ascii=False).encode(),'POST')
        # Process each image sequentially. PNG work runs in a lower priority process.
        files=[('report-page-'+str(i)+'.png',lambda i=i:self.images.png(token,i,report),'image/png') for i in range(1,info['pages']+1)]
        if info['pages']<=12:files.append(('report-images.zip',lambda:self.images.archive(token,report),'application/zip'))
        for art in report['artworks']:files.append(('art/'+art['id']+'.png',lambda ident=art['id']:self.store.artwork(token,ident)[1],'image/png'))
        for kind in ('html','csv',*(['zip'] if len(report['artworks'])<12 else [])):
            files.append(('report.'+kind,lambda kind=kind:self.store.report(token,kind,view=report),{'html':'text/html; charset=utf-8','csv':'text/csv; charset=utf-8','zip':'application/zip'}[kind]))
        for name,reader,mime in files:
            if self.stop.is_set():return
            data=reader();digest=hashlib.sha256(data).hexdigest()
            cache_key=revision+'/'+name
            if item['assets'].get(cache_key)==digest:continue
            if len(data)>32_000_000 and name.endswith('.zip'):continue
            self.request(path+'/assets/'+name+'?revision='+revision,data,'PUT',mime)
            with self.lock:item['assets'][cache_key]=digest;self.save()
        # Publish the completed snapshot once all assets are durable.
        payload['commit']=True
        response=self.request(path,json.dumps(payload,ensure_ascii=False).encode(),'POST')
        with self.lock:item['synced']=hashlib.sha256(json.dumps(report,sort_keys=True,ensure_ascii=False).encode()).hexdigest();item['ready']=True;self.save()
        self.last_success=time.time();self.error=None

    def share(self,ident):
        with self.lock:item=dict(self.state['visitors'].get(ident,{ }))
        return dict(base_url=self.base if item.get('ready') else None,scope='public',ready=bool(item.get('ready')),
            message=('公网暂不可达，最新记录已保存本地，恢复联网后自动补传。' if self.error else '公网作品与战绩已同步，可离开现场后查看。' if item.get('ready') else '正在同步公网作品与战绩；可先使用同一Wi-Fi下载。'),
            error='公网暂不可达，已保存本地，恢复联网后自动重试。' if self.error else None)

    def run(self):
        while not self.stop.is_set():
            try:
                self.discover()
                failed=False
                for ident,item in reversed(list(self.state['visitors'].items())):
                    if self.stop.is_set():break
                    try:self.sync_one(ident,item)
                    except Exception as exc:
                        self.error=type(exc).__name__;failed=True
                if not failed:self.error=None
            except Exception as exc:
                self.error=type(exc).__name__
            self.wake.wait(10. if self.error else 5.);self.wake.clear()

    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True,name='visitor-public-sync');self.thread.start();return self

    def close(self):
        self.stop.set();self.wake.set()
        if self.thread:self.thread.join(2.)


def configured_publisher(store,images,path=None):
    path=Path(path) if path else store.root/'public-site.json'
    if not path.is_file():return None
    try:return PublicPublisher(store,images,json.loads(path.read_text(encoding='utf-8')))
    except (ValueError,TypeError,KeyError,OSError):return None
