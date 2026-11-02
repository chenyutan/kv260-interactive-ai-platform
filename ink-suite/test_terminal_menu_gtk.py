"""Isolated Xvfb, real child processes, terminal -> gesture menu -> replay.

No live API, images, visitor store or speaker is permitted. The test-only child
wrapper advances actual engines to a terminal result without waiting a minute;
then the unchanged production host sends the confirmed restart and resume IPC.
Run from a staged suite copy, never the production application directory.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from types import SimpleNamespace as NS
from urllib.parse import urlsplit


def child(runner, argv):
    assert '--offline-preview' in argv, 'Child must be offline'
    assert not os.environ.get('INKSUITE_VISITOR_DATA'), 'No visitor records in test'
    import gi
    gi.require_version('Gtk','3.0')
    from gi.repository import GLib
    if runner == 'embedded':
        import embedded_runner as ui
        name = 'Controller'
    elif runner == 'sword':
        from sword import main as ui
        name = 'SwordPanel'
    else:
        from taiko import main as ui
        name = 'TaikoPanel'
    restart_receipt = None
    original_emit = ui.emit
    def observe_emit(event, **fields):
        nonlocal restart_receipt
        if event == 'action_result' and fields.get('action') == 'restart' and fields.get('ok') is True:
            restart_receipt = dict(fields)
        return original_emit(event, **fields)
    ui.emit = observe_emit
    cls = getattr(ui,name); original = cls.__init__
    def init(self, args):
        original(self,args)
        mode = args.mode if runner == 'embedded' else runner
        prepared = False; released = False
        def fixture_emit(event, **fields):
            # Taiko's production emitter already owns mode='taiko'. Passing
            # it twice raises before the fixture packet ever reaches stdout.
            if runner != 'taiko': fields['mode'] = mode
            ui.emit(event, **fields)
        def advance_fixture():
            nonlocal prepared,released
            if (self.closed if runner=='embedded' else self.stop.is_set()): return False
            active = not self.paused if runner=='embedded' else self.active
            if not active: return True
            game = self.app.game if runner=='embedded' else self.session.game if mode=='taiko' else self.game
            if not prepared:
                if mode in ('fruit','fight'):
                    if mode == 'fight':
                        # Offline benchmark automatically starts a new fight
                        # at 'over'. Keep this terminal result on screen until
                        # the real confirmed restart; export remains disabled.
                        self.app.args.benchmark = False
                    game.start(); game.advance(3.1); game.advance(120.)
                    phase = game.state
                    assert phase == 'over', (mode,phase)
                elif mode == 'sword':
                    game.start(countdown=0); game.pause(False)
                    for _ in range(400):
                        game.update(.25,dict(valid=True,x=.5,y=.8))
                        if game.phase in ('won','lost'): break
                    phase = game.phase
                    assert phase in ('won','lost'), phase
                else:
                    # The explicit SilentClock is advanced; Session performs
                    # its normal note settling and finished/pause transition.
                    assert self.session.resume()
                    self.audio.seek(game.duration+1.)
                    self.session.tick(time.monotonic(),[])
                    phase = self.phase()
                    assert phase == 'finished', phase
                prepared = True
                fixture_emit('fixture_terminal',game_state=phase)
                return True
            phase = game.state if mode in ('fruit','fight') else self.phase() if mode=='taiko' else game.phase
            clock = getattr(game,'elapsed',getattr(game,'time',0.))
            running = (phase=='playing' and (self.session.running if mode=='taiko' else True))
            if restart_receipt is not None and running and clock > .06 and not released:
                released = True
                if runner=='embedded':
                    # Simulate the same delayed native signal connected by
                    # the inherited constructor, after the new round plays.
                    self.app.focus_out(None,None)
                    assert game.state=='playing', 'Late focus paused new round'
                fixture_emit('fixture_restarted_progress',game_state=phase,game_clock=clock,
                             command_id=restart_receipt['command_id'])
            return True
        def fixture():
            try: return advance_fixture()
            except Exception:
                fixture_emit('fixture_error',traceback=traceback.format_exc())
                return False
        GLib.timeout_add(100,fixture,priority=GLib.PRIORITY_DEFAULT)
    cls.__init__=init
    if runner=='taiko':
        from taiko.audio_clock import SilentClock
        cls.make_audio=lambda self,sink:SilentClock()
    sys.argv=[str(Path(ui.__file__).resolve()),*argv]
    return ui.main() if runner=='embedded' else ui.main(argv)


def run(args):
    root=Path(args.suite_root).resolve()
    if root==Path('/home/ubuntu/ink-suite'):
        raise ValueError('Run on a staged suite copy, never production')
    sys.path.insert(0,str(root))
    os.environ.pop('INKSUITE_VISITOR_DATA',None)
    os.environ.pop('INKSUITE_VISITOR_SESSION',None)
    import gi
    gi.require_version('Gtk','3.0')
    from gi.repository import Gtk,GLib
    import main as host
    from test_gtk_action_suite import FakeBackend, synthetic_pose
    from gesture_menu import ZONES
    fake=FakeBackend()
    assert urlsplit(fake.base).hostname=='127.0.0.1'
    assert urlsplit(fake.base).port not in (8088,8091)
    evidence=dict(passed=False,camera_used=False,production_used=False,
        audio_device_used=False,visitor_store_used=False,base=fake.base,
        records=[],events=[],commands=[],navigation=[],errors=[])
    original_popen=host.subprocess.Popen
    wrapper=Path(__file__).resolve()
    def popen(argv,*p_args,**p_kwargs):
        if isinstance(argv,list) and len(argv)>1 and Path(argv[1]).name in ('embedded_runner.py','main.py'):
            path=Path(argv[1]); runner='embedded' if path.name=='embedded_runner.py' else path.parent.name
            if runner not in ('embedded','sword','taiko'): raise AssertionError(argv)
            assert '--offline-preview' in argv,argv
            argv=[argv[0],str(wrapper),'--child',runner,'--suite-root',str(root),'--',*argv[2:]]
        return original_popen(argv,*p_args,**p_kwargs)
    host.subprocess.Popen=popen
    class LoggedInput:
        def __init__(self,stream,process): self.stream=stream;self.process=process
        def write(self,value):
            for line in value.splitlines():
                evidence['commands'].append(dict(pid=self.process.pid,at=time.monotonic(),packet=json.loads(line)))
            return self.stream.write(value)
        def __getattr__(self,name): return getattr(self.stream,name)
    class Suite(host.Suite):
        def spawn_mode(self,mode,generation):
            result=super().spawn_mode(mode,generation)
            if self.child:self.child.stdin=LoggedInput(self.child.stdin,self.child)
            return result
        def child_event(self,process,mode,generation,event):
            evidence['events'].append(dict(pid=process.pid,mode=mode,at=time.monotonic(),packet=dict(event)))
            return super().child_event(process,mode,generation,event)
        def choose_action(self,action):
            evidence['navigation'].append(dict(mode=self.mode,action=action,at=time.monotonic(),via_dwell=self.selector.done))
            return super().choose_action(action)
    app=Suite(NS(base=fake.base,fullscreen=False,offline_preview=True,entry_delay=0.,
        ink_source='/not-used',fruit_source=args.fruit_source,fight_source=args.fight_source))
    app.resize(960,540)
    deadline=time.monotonic()+args.timeout; context='starting'
    def packets(kind):
        pid=app.child.pid if app.child else None
        return [x for x in evidence['events'] if x['pid']==pid and x['packet'].get('event')==kind]
    def wait(predicate,label,seconds=15.):
        nonlocal context
        context=label;end=time.monotonic()+seconds
        while not predicate():
            if packets('fixture_error'):raise AssertionError(packets('fixture_error')[-1]['packet'])
            if time.monotonic()>end:raise TimeoutError(label)
            yield
    def delay(seconds,label):
        end=time.monotonic()+seconds
        yield from wait(lambda:time.monotonic()>=end,label,seconds+2)
    def dwell(action,predicate):
        index=app.selector.choices.index(action);a,b,c,d=ZONES[index]
        fake.points(synthetic_pose((.5,.10)))
        yield from delay(.55,'neutral before '+action)
        fake.points(synthetic_pose(((a+c)/2,(b+d)/2)))
        yield from wait(predicate,'real fresh-pose dwell '+action,8.)
        fake.points(None)
    def scenario():
        yield from wait(app.interaction_ready,'host pipeline readiness')
        for mode in ('fruit','fight','sword','taiko'):
            app.select(mode)
            yield from wait(lambda:app.mode==mode and not app.switching and not app.menu_open and
                            bool(packets('fixture_terminal')),'terminal '+mode,24.)
            pid=app.child.pid; terminal=packets('fixture_terminal')[-1]['packet']
            circle_at=time.monotonic();serial=fake.circle()
            yield from wait(lambda:app.menu_open and app.menu_level=='actions' and app.serial==serial and
                            any(x['at']>=circle_at for x in packets('paused')),'circle menu '+mode)
            paused=next(x['packet'] for x in reversed(packets('paused')) if x['at']>=circle_at)
            assert paused['game_state']==terminal['game_state'],(terminal,paused)
            assert app.child_paused and app.stack.get_visible_child_name()=='actions'
            before=len([x for x in evidence['commands'] if x['packet'].get('op')=='action'])
            yield from dwell('restart',lambda:app.action_page=='confirm:restart')
            assert len([x for x in evidence['commands'] if x['packet'].get('op')=='action'])==before
            yield from dwell('confirm:restart',lambda:bool(packets('action_result')))
            receipt=packets('action_result')[-1]['packet']
            assert receipt['action']=='restart' and receipt['ok'] is True,receipt
            receipt_at=packets('action_result')[-1]['at']
            yield from wait(lambda:any(x['at']>=receipt_at for x in packets('resumed')) and not app.menu_open,'release '+mode)
            yield from wait(lambda:bool(packets('fixture_restarted_progress')),'new round advances '+mode,15.)
            actions=[x['packet'] for x in evidence['commands'] if x['pid']==pid and x['packet'].get('op')=='action']
            assert len(actions)==1 and actions[0]['action']=='restart',actions
            progress=packets('fixture_restarted_progress')[-1]
            assert progress['at']>=receipt_at and progress['packet']['command_id']==receipt['command_id']
            assert app.child.pid==pid and app.child.poll() is None
            record=dict(mode=mode,pid=pid,terminal=terminal,receipt=receipt,
                terminal_pause=paused,progressed=progress['packet'],
                confirmation_sent_once=True,resumed=True,same_process=True,
                gesture_confirmation=True,late_focus_kept_playing=mode in ('fruit','fight'))
            evidence['records'].append(record);print(json.dumps(record),flush=True)
            fake.circle();yield from wait(lambda:app.menu_open,'menu after successful '+mode)
            app.open_menu(main=True)
            yield from wait(app.interaction_ready,'main before next child')
        assert not fake.errors,fake.errors
        assert fake.paths=={'/api/state'},fake.paths
        assert all(p['data'].get('command')=='suite' for p in fake.posts)
        evidence['passed']=True
    procedure=scenario()
    def tick():
        try:
            if time.monotonic()>deadline:raise TimeoutError('Absolute deadline: '+context)
            next(procedure);return True
        except StopIteration:app.destroy();return False
        except Exception:
            evidence['errors'].append(traceback.format_exc())
            evidence['context']=context
            evidence['failure_category']=('selection_not_completed' if 'dwell' in context else
                'paused_after_resume' if 'new round advances' in context else
                'ownership_release_failed' if 'release ' in context else 'terminal_or_menu_failed')
            evidence['failure_state']=dict(menu_open=app.menu_open,page=app.action_page,
                child_paused=app.child_paused,backend_ready=app.backend_ready,
                pending_action=bool(app.pending_action))
            app.destroy();return False
    GLib.timeout_add(40,tick,priority=GLib.PRIORITY_DEFAULT)
    try:Gtk.main()
    finally:
        app.close();host.subprocess.Popen=original_popen
        fake.server.shutdown();fake.server.server_close()
        path=Path(args.output).resolve();path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    if not evidence['passed']:raise AssertionError(evidence.get('context'),evidence['errors'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--suite-root',default=str(Path(__file__).resolve().parent))
    p.add_argument('--output',default='test-evidence/terminal-restart.json')
    p.add_argument('--timeout',type=float,default=165.)
    p.add_argument('--fruit-source',default='/home/ubuntu/fruit-slice/native/main.py')
    p.add_argument('--fight-source',default='/home/ubuntu/ink-fight/native/main_v2.py')
    p.add_argument('--child',choices=('embedded','sword','taiko'))
    p.add_argument('child_args',nargs=argparse.REMAINDER)
    args=p.parse_args()
    if args.child:
        sys.path.insert(0,str(Path(args.suite_root).resolve()))
        return child(args.child,args.child_args[1:] if args.child_args[:1]==['--'] else args.child_args)
    run(args)


if __name__=='__main__':main()
