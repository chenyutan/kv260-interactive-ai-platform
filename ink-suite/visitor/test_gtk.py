"""Native visitor workflow under isolated Xvfb and random loopback servers.

Run from InkSuite (never on the visible production desktop)::

    timeout 80s xvfb-run -a -s '-screen 0 1024x600x24' python3 \
      visitor/test_gtk.py --timeout 65 --output test-evidence/visitor-gtk.json

Every artwork and score is synthetic. No production port, camera, child game,
hardware command, actual Wi-Fi inspection or real artwork API is accessed.
"""
import argparse
import io
import json
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request,build_opener,ProxyHandler

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

def arguments():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout',type=float,default=65.)
    parser.add_argument('--output',type=Path,default=ROOT/'test-evidence/visitor-gtk.json')
    args=parser.parse_args()
    if not 20<=args.timeout<=90:parser.error('--timeout must be 20 .. 90 seconds')
    return args

def pose(cursor=(.5,.10)):
    """Synthetic inverse of the production shoulder-normalized cursor mapping."""
    width=.1*(16/9);points=[[.5,.5,.9] for _ in range(17)]
    points[5],points[6]=[.45,.35,.9],[.55,.35,.9]
    points[10]=[.5+(.5-cursor[0])*width*3.2/(16/9),.35+(cursor[1]-.35)*width*2.6,.9]
    return points

class FakeBackend:
    def __init__(self,png,blank):
        from suite_control import SuiteControl
        self.validator=SuiteControl;self.lock=threading.Lock();self.paths=[];self.posts=[];self.errors=[]
        self.png=png;self.blank=blank;self.clear_calls=0
        self.state=dict(pid=9988001,sequence=0,pose_count=0,phase='tracking',points17=pose(),
            event_count=23,art_revision=7,camera_link=dict(present=True),
            suite=dict(active=True,mode='ink',menu=True,control_active=False,open_serial=0,hand=None))
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def send(self,data,mime='application/json'):
                if not isinstance(data,bytes):data=json.dumps(data).encode('utf-8')
                self.send_response(200);self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(len(data)));self.end_headers()
                try:self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):pass
            def do_GET(self):
                with owner.lock:
                    owner.paths.append(self.path)
                    if self.path=='/api/state':
                        owner.state['sequence']+=1;owner.state['pose_count']+=1
                        owner.state['timestamp_seconds']=time.monotonic()
                        result=json.loads(json.dumps(owner.state));mime='application/json'
                    elif self.path=='/art.png':result=owner.png;mime='image/png'
                    else:
                        owner.errors.append('Unexpected GET '+self.path);self.send_error(404);return
                self.send(result,mime)
            def do_POST(self):
                try:
                    packet=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    if self.path!='/api/command':raise ValueError('Unexpected POST '+self.path)
                    with owner.lock:
                        owner.posts.append(dict(packet));command=packet.pop('command')
                        if command=='suite':
                            owner.validator().configure(packet);owner.state['suite'].update(packet)
                        elif command=='clear':
                            assert not packet,'Clear accepts no extra arguments'
                            owner.clear_calls+=1;owner.state['event_count']=0
                            owner.state['art_revision']+=1;owner.png=owner.blank
                        else:raise ValueError('Unexpected command '+str(command))
                    self.send(dict(accepted=True))
                except Exception:
                    owner.errors.append(traceback.format_exc());self.send_error(400)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        assert self.server.server_port not in (8088,8090,8091),'Reserved production port'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def points(self,points):
        with self.lock:self.state['points17']=points
    def marks(self):
        with self.lock:return self.state['event_count']
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(2.)

def run(args):
    import gi
    gi.require_version('Gtk','3.0')
    from gi.repository import Gtk,GLib
    from PIL import Image,ImageDraw
    from main import Suite
    from visitor.store import VisitorStore,GAMES
    from visitor.portal import Portal
    from visitor.coordinator import FinishCoordinator

    evidence=dict(passed=False,camera_used=False,production_backend_used=False,
        production_artwork_modified=False,real_network_modified=False,audio_played=False,
        child_games_spawned=False,synthetic_marks=23,records=[],navigation=[],errors=[])
    context=dict(stage='initialization');started=time.monotonic();deadline=started+args.timeout
    args.output.parent.mkdir(parents=True,exist_ok=True)
    backend=portal=app=None;temp=tempfile.TemporaryDirectory(prefix='ink-visitor-gtk-')
    try:
        canvas=Image.new('RGB',(640,360),'#f5f0e5');draw=ImageDraw.Draw(canvas)
        draw.polygon([(0,330),(110,240),(230,310),(350,120),(520,310),(640,260),(640,360),(0,360)],fill='#506b62')
        draw.ellipse((450,40,500,90),fill='#b5553b')
        output=io.BytesIO();canvas.save(output,format='PNG');png=output.getvalue()
        output=io.BytesIO();Image.new('RGB',canvas.size,'#f5f0e5').save(output,format='PNG');blank=output.getvalue()
        backend=FakeBackend(png,blank);evidence['fake_backend']=backend.base
        store=VisitorStore(Path(temp.name)/'visitors');original=store.current()
        for index,game in enumerate(GAMES):
            store.record(original['id'],uuid.uuid4().hex,game,dict(score=1200+index*100,result='胜',hp=30,
                time=75,difficulty='easy',practice='full',judgment='relaxed',mode='arcade'),'completed')
        # Supply fake network metadata so the test never calls `ip` or reads Wi-Fi.
        network=SimpleNamespace(base=None)
        network.snapshot=lambda force=False:dict(base_url=network.base,address='127.0.0.1',
            wireless_connected=True,scope='synthetic-loopback',message='隔离测试二维码 · 合成游客数据')
        portal=Portal(store,host='127.0.0.1',port=0,network=network).start()
        assert portal.port not in (8088,8090,8091),'Reserved production port'
        network.base='http://127.0.0.1:'+str(portal.port)
        evidence['fake_portal']=network.base
        opener=build_opener(ProxyHandler({}))
        def fetch(path,data=None):
            request=Request(network.base+path,data=json.dumps(data).encode('utf-8') if data is not None else None,
                headers={'Content-Type':'application/json','X-Ink-Visitor':'1'})
            with opener.open(request,timeout=3.) as response:return response.read(),dict(response.headers)
        def api(path,data=None):return json.loads(fetch(path,data)[0])

        class TestedSuite(Suite):
            def visitor_choose(self,action):
                evidence['navigation'].append(dict(action=action,via_dwell=bool(self.selector.done),
                    menu_level=self.menu_level,choices=list(self.selector.choices)))
                return super().visitor_choose(action)
            def spawn_mode(self,*_):raise AssertionError('Visitor test must never spawn a game process')

        app=TestedSuite(SimpleNamespace(base=backend.base,fullscreen=False,offline_preview=True,entry_delay=0.))
        app.resize(1024,600)
        app.visitor_store=store;app.visitor_portal=portal
        app.visitor_coordinator=FinishCoordinator(store,backend.base)
        app.visitor_timer=GLib.timeout_add_seconds(5,app.visitor_refresh_tick)
        assert app.devices is None,'Device controller must be disabled'
        assert app.menu_music.status()['enabled'] is False,'Menu audio must be disabled'

        def wait(predicate,label,timeout=8.):
            context['stage']=label;until=min(deadline,time.monotonic()+timeout)
            while not predicate():
                if time.monotonic()>=until:raise TimeoutError(label)
                yield
        def delay(seconds,label):
            due=time.monotonic()+seconds
            yield from wait(lambda:time.monotonic()>=due,label,seconds+2.)
        def click(view,rect,label):
            context['stage']=label;a,b,c,d=rect
            result=view.click(view,SimpleNamespace(button=1,x=view.get_allocated_width()*(a+c)/2,
                y=view.get_allocated_height()*(b+d)/2))
            assert result is True,('Native mouse handler rejected choice',label)
        def record(label,**fields):evidence['records'].append(dict(step=label,**fields))
        def qr_ready():
            return app.menu_level=='visitor' and app.stack.get_visible_child_name()=='visitor' and app.visitor_view.qr is not None and app.visitor_view.cache_builds>0

        def workflow():
            yield from wait(app.interaction_ready,'warm up fake pose stream')
            app.open_menu(main=True)
            yield from wait(app.interaction_ready,'main menu suite ACK')
            assert app.selector.choices==app.menu.mode_choices and len(app.selector.choices)==8
            assert backend.marks()==23 and backend.clear_calls==0
            click(app.menu,app.menu.rectangles[6],'mouse opens main footer download')
            yield from wait(qr_ready,'native QR page renders')
            first_url=app.visitor_view.info['url'];first_token=original['token']
            assert first_url==network.base+'/v/'+first_token
            assert store.view(first_token)['artworks'][0]['marks']==23
            record('mouse footer → QR page',native_qr_rendered=True)

            view=api('/api/visitor/'+first_token+'/name',dict(name='隔离山河客'))
            assert view['name']=='隔离山河客' and len(view['rounds'])==5
            boards=api('/api/leaderboards')['boards'];assert len(boards)==5
            assert all(board['items'][0]['name']=='隔离山河客' for board in boards)
            art_id=view['artworks'][0]['id'];saved,headers=fetch('/v/'+first_token+'/art/'+art_id+'.png')
            assert saved==png and 'attachment' in headers['Content-Disposition']
            package,_=fetch('/v/'+first_token+'/report.zip')
            with zipfile.ZipFile(io.BytesIO(package)) as archive:
                assert archive.testzip() is None and archive.read('artworks/'+art_id+'.png')==png
                assert json.loads(archive.read('report.json'))['name']=='隔离山河客'
            record('phone HTTP naming, 5 boards, original PNG and complete ZIP')

            click(app.visitor_view,app.visitor_view.rectangles[2],'QR finish opens confirmation')
            yield from wait(lambda:app.menu_level=='visitor_confirm' and app.stack.get_visible_child_name()=='actions','finish confirmation visible')
            assert app.actions.choices==('visitor_cancel','visitor_confirm')
            assert backend.clear_calls==0 and backend.marks()==23
            click(app.actions,app.actions.rectangles[0],'cancel finish')
            yield from wait(lambda:app.menu_level=='main' and app.interaction_ready(),'cancel returns to main')
            assert store.current()['id']==original['id'] and backend.marks()==23 and backend.clear_calls==0
            record('cancel finish preserves synthetic 23 marks and original visitor')

            # Exercise the full real two-second DwellSelector against new poses.
            backend.points(pose());app.selector.open(None,time.monotonic(),pose())
            yield from delay(.65,'arm footer dwell with fresh neutral poses')
            a,b,c,d=app.menu.rectangles[6];backend.points(pose(((a+c)/2,(b+d)/2)))
            yield from wait(qr_ready,'fresh-pose dwell opens main footer download',7.)
            backend.points(pose())
            assert any(n['action']=='visitor_download' and n['via_dwell'] for n in evidence['navigation'])
            assert len(store.view(first_token)['artworks'])==1,'Repeated snapshot must deduplicate the same PNG'
            record('two-second observed-pose footer dwell → QR page',no_duplicate_artwork=True)

            click(app.visitor_view,app.visitor_view.rectangles[2],'open finish confirmation again')
            yield from wait(lambda:app.menu_level=='visitor_confirm' and app.actions.selection_enabled,'confirm finish enabled')
            click(app.actions,app.actions.rectangles[1],'confirm archive and finish')
            # A fast duplicate click must be ignored once the native action is busy.
            assert app.actions.click(app.actions,SimpleNamespace(button=1,
                x=app.actions.get_allocated_width()*.5,y=app.actions.get_allocated_height()*.35)) is False
            yield from wait(lambda:not app.visitor_busy and not app.switching and store.current()['id']!=original['id']
                and app.menu_level=='main' and app.interaction_ready(),'archive → one clear → next visitor',12.)
            second=store.current();assert second['id']!=original['id'] and second['token']!=first_token
            assert backend.clear_calls==1 and backend.marks()==0
            assert store.view(first_token)['status']=='archived'
            assert (store.root/('archive-'+original['id']+'.zip')).is_file()
            assert app.visitor_coordinator.finish(original['id'])['repeated'] is True
            assert backend.clear_calls==1,'Retrying an archived visitor must not clear the next visitor'
            record('confirm finish archives and clears exactly once',next_session_created=True)

            old=api('/api/visitor/'+first_token+'/name',dict(name='归档山河客'))
            assert old['status']=='archived' and old['name']=='归档山河客'
            old_png,_=fetch('/v/'+first_token+'/art/'+art_id+'.png');assert old_png==png
            old_report=api('/v/'+first_token+'/report.json');assert len(old_report['rounds'])==5
            assert all(board['items'][0]['name']=='归档山河客' for board in api('/api/leaderboards')['boards'])
            record('old private URL retains naming, original PNG, 5-game reports and boards')

            click(app.menu,app.menu.rectangles[6],'next visitor opens download page')
            yield from wait(lambda:qr_ready() and app.visitor_view.info['url']==network.base+'/v/'+second['token'],'new visitor QR rendered')
            new=api('/api/visitor/'+second['token'])
            assert new['name']=='游客' and new['rounds']==[] and new['artworks']==[]
            assert app.visitor_view.info['url']!=first_url
            assert backend.clear_calls==1 and backend.errors==[]
            assert set(backend.paths)<={'/api/state','/art.png'}
            assert all(packet['command'] in ('suite','clear') for packet in backend.posts)
            record('next visitor has a distinct QR and empty private records')
            evidence.update(passed=True,clear_calls=backend.clear_calls,archived_game_rounds=len(old_report['rounds']),
                archived_artworks=len(old_report['artworks']),new_visitor_rounds=len(new['rounds']),
                backend_paths=sorted(set(backend.paths)),native_qr_cache_builds=app.visitor_view.cache_builds)

        steps=workflow()
        def tick():
            try:
                if time.monotonic()>=deadline:raise TimeoutError('Bounded GTK visitor workflow deadline')
                next(steps);return True
            except StopIteration:app.destroy();return False
            except Exception:
                evidence['errors'].append(dict(stage=context['stage'],traceback=traceback.format_exc()))
                app.destroy();return False
        GLib.timeout_add(50,tick);Gtk.main()
        if backend.errors:evidence['errors'].extend(backend.errors);evidence['passed']=False
        if not evidence['passed'] and not evidence['errors']:evidence['errors'].append('GTK loop exited before workflow completed')
    except Exception:
        evidence['passed']=False;evidence['errors'].append(dict(stage=context['stage'],traceback=traceback.format_exc()))
    finally:
        if app and not app.stop.is_set():app.destroy()
        elif portal and portal.thread:portal.close()
        if backend:backend.close()
        temp.cleanup();evidence['elapsed_seconds']=round(time.monotonic()-started,3)
        args.output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(evidence,ensure_ascii=False),flush=True)
    return 0 if evidence['passed'] and not evidence['errors'] else 1

if __name__=='__main__':raise SystemExit(run(arguments()))
