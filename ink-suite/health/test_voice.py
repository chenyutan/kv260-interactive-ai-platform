"""Silent worker/route/asset regression; never opens a real audio device."""
import hashlib,json,os,struct,tempfile,threading,time,unittest,wave
from pathlib import Path
from unittest.mock import patch
from audio.runtime import MemoryBackend
from .voice import HealthVoice,BLOCK_FRAMES,MAX_CACHE_BYTES,MAX_CACHE_CLIPS


def inventory(sink='alsa_output.cube',connected=True):
    return {'audio':{'inventory':{'devices':[dict(is_cube=True,connected=connected,playable=True,vendor_id='2dc8',
        sink=sink,usb_path='1-1.2',usb_instance='1-1.2:1:4:900',card_path='/fake/card4',pulse_index=2)]}}}


def snapshot(identifier='raise',index=0,status='running',phase='practice',**values):
    return dict(status=status,phase=phase,lesson_id=identifier,instruction_index=index,
        instruction='动态追加的提示',instructions=['准备','上举','放下'],**values)


class Backend(MemoryBackend):
    def __init__(self,sink,**kwargs):
        super().__init__('memory');self.options=kwargs;self.guard=kwargs['route_guard'];self.moved=False
    def resume(self):
        if not self.guard():raise RuntimeError('route disconnected')
        super().resume()
    def poll(self):
        if self.moved:raise RuntimeError('Pulse moved audio to a fallback')
        if not self.guard():raise RuntimeError('route disconnected')


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
        self.entries=[];self.factories=[];self.threads=[];self.clock=[0.]
        self.pulse=patch('connectivity.audio_device._pulse_environment',return_value={'PULSE_SERVER':'unix:/fake-server'});self.pulse.start()
        self.addCleanup(self.pulse.stop);self.addCleanup(self.temporary.cleanup)
        for identifier in ('raise:0','raise:1','raise:2','bow:0','single:0','sway:0','complete','prepare:0','prepare:1','finish:0'):self.asset(identifier)
        self.write_manifest()
        self.taichi_root=self.root/'taichi';self.taichi_root.mkdir();taichi_entries=[]
        for identifier in ('prepare:0','finish:0','cloud:0','complete'):
            self.asset(identifier,root=self.taichi_root,entries=taichi_entries,value=1600)
        (self.taichi_root/'manifest.json').write_text(json.dumps({'schema':1,'clips':taichi_entries}),encoding='utf8')
        self.voice=HealthVoice(asset_dir=self.root,course_asset_dirs={'taichi':self.taichi_root},
            backend_factory=self.factory,physical_checker=self.physical,clock=lambda:self.clock[0])
        self.addCleanup(self.finish)
    def finish(self):
        self.voice.close();self.assertTrue(self.voice.wait_closed(2.))
    def physical(self,route):self.threads.append(threading.current_thread().name);return True
    def factory(self,sink,**kwargs):
        self.threads.append(threading.current_thread().name)
        backend=Backend(sink,**kwargs);self.factories.append(backend);return backend
    def asset(self,identifier,frames=20000,root=None,entries=None,value=800):
        name=identifier.replace(':','-')+'.wav';path=(self.root if root is None else root)/name
        with wave.open(str(path),'wb') as output:
            output.setnchannels(2);output.setsampwidth(2);output.setframerate(48000)
            output.writeframes(struct.pack('<hh',value,-value)*frames)
        (self.entries if entries is None else entries).append(dict(id=identifier,lesson_id=identifier.split(':')[0],instruction_index=int(identifier.split(':')[1]) if ':' in identifier else 0,
            text='测试口令',path=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),frames=frames,rate=48000,channels=2,sample_width=2))
    def write_manifest(self):
        (self.root/'manifest.json').write_text(json.dumps({'schema':1,'clips':self.entries}),encoding='utf8')
    def wait(self,predicate,timeout=2.):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.005)
        self.fail('Condition timed out: '+str(self.voice.status()))
    def start(self,**kwargs):
        self.voice.update_inventory(inventory());self.voice.set_active(True);self.assertTrue(self.voice.sync(snapshot(**kwargs)))
        self.wait(lambda:self.voice.status()['blocks']>0)
    def test_first_instruction_routes_explicitly_and_runs_only_on_worker(self):
        self.start();options=self.factories[0].options
        self.assertEqual(options['device'],'alsa_output.cube');self.assertEqual(options['server'],'unix:/fake-server')
        self.assertTrue(options['live_timestamps']);self.assertEqual(options['block_frames'],1440)
        self.assertTrue(all(name=='health-voice' for name in self.threads))
        self.assertTrue(all(len(block)==BLOCK_FRAMES*4 for block in self.factories[0].blocks))
    def test_duplicate_snapshots_do_not_queue_or_replay(self):
        self.start()
        for _ in range(100):self.assertFalse(self.voice.sync(snapshot()))
        self.assertEqual(self.voice.status()['accepted'],1)
        self.assertLessEqual(self.voice.status()['queued'],1)
        self.wait(lambda:not self.voice.status()['output_active'])
        blocks=self.voice.status()['blocks'];time.sleep(.06)
        self.assertEqual(self.voice.status()['blocks'],blocks)
    def test_pause_flushes_prompt_and_same_instruction_does_not_resume(self):
        self.start();self.voice.sync(snapshot(status='paused'))
        self.wait(lambda:self.voice.status()['silenced'])
        backend=self.factories[0];self.assertFalse(backend.playing);self.assertGreater(backend.flushes,0)
        blocks=self.voice.status()['blocks'];self.assertFalse(self.voice.sync(snapshot()));time.sleep(.08)
        self.assertEqual(self.voice.status()['blocks'],blocks)
        self.assertTrue(self.voice.sync(snapshot(index=1)))
        self.wait(lambda:self.voice.status()['clip_id']=='raise:1' and self.voice.status()['output_active'])
    def test_reconnect_after_pause_does_not_resurrect_pre_pause_cue(self):
        self.start();self.voice.sync(snapshot(status='paused'))
        self.wait(lambda:self.voice.status()['silenced'])
        self.voice.sync(snapshot());blocks=self.voice.status()['blocks']
        self.voice.update_inventory(inventory(connected=False));self.voice.update_inventory(inventory())
        time.sleep(.1)
        self.assertEqual(self.voice.status()['blocks'],blocks)
        self.assertEqual(self.voice.status()['queued'],0)
    def test_latest_pending_instruction_replaces_old_before_connection(self):
        self.voice.set_active(True)
        self.voice.sync(snapshot());self.voice.sync(snapshot(index=1));self.voice.sync(snapshot(identifier='bow'))
        self.assertEqual(self.voice.status()['queued'],1)
        self.voice.update_inventory(inventory())
        self.wait(lambda:self.voice.status()['blocks']>0)
        self.assertEqual(self.voice.status()['clip_id'],'bow:0')
        self.assertEqual(len(self.factories),1)
    def test_exit_closes_output_and_does_not_play_late_pending_instruction(self):
        self.start();self.voice.sync(snapshot(index=1));self.voice.set_active(False)
        self.wait(lambda:self.voice.status()['silenced'])
        blocks=self.voice.status()['blocks'];time.sleep(.08)
        self.assertEqual(self.voice.status()['blocks'],blocks);self.assertEqual(self.voice.status()['queued'],0)
    def test_missing_asset_is_reported_and_retry_is_not_a_hot_loop(self):
        (self.root/'raise-0.wav').unlink()
        self.voice.update_inventory(inventory());self.voice.set_active(True);self.voice.sync(snapshot())
        self.wait(lambda:self.voice.status()['state']=='unavailable')
        retries=self.voice.status()['retries'];time.sleep(.12)
        self.assertEqual(self.voice.status()['retries'],retries)
        self.assertGreaterEqual(self.voice.status()['next_retry'],3.)
        self.assertEqual(self.factories,[])
    def test_hash_or_metadata_mismatch_never_reaches_output(self):
        self.entries[0]['sha256']='0'*64;self.write_manifest()
        self.voice.update_inventory(inventory());self.voice.set_active(True);self.voice.sync(snapshot())
        self.wait(lambda:self.voice.status()['state']=='unavailable')
        self.assertIn('checksum',self.voice.status()['error']);self.assertEqual(self.factories,[])
    def test_bad_wav_frame_count_never_reaches_output(self):
        self.entries[0]['frames']+=1;self.write_manifest()
        self.voice.update_inventory(inventory());self.voice.set_active(True);self.voice.sync(snapshot())
        self.wait(lambda:self.voice.status()['state']=='unavailable')
        self.assertIn('frame',self.voice.status()['error']);self.assertEqual(self.factories,[])
    def test_manifest_path_escape_is_rejected_before_backend_creation(self):
        self.entries[0]['path']='../outside.wav';self.write_manifest()
        self.voice.update_inventory(inventory());self.voice.set_active(True);self.voice.sync(snapshot())
        self.wait(lambda:self.voice.status()['state']=='unavailable')
        self.assertEqual(self.factories,[])
    def test_loading_clip_can_be_cancelled_without_blocking_public_controls(self):
        entered=threading.Event();release=threading.Event();load=self.voice._load
        def delayed(*args):
            entered.set();release.wait(2.);return load(*args)
        self.voice._load=delayed
        try:
            self.voice.update_inventory(inventory());self.voice.set_active(True);self.voice.sync(snapshot())
            self.assertTrue(entered.wait(1.))
            started=time.monotonic();self.voice.sync(snapshot(index=1));self.voice.sync(snapshot(status='paused'))
            self.assertLess(time.monotonic()-started,.1)
        finally:release.set()
        self.wait(lambda:self.voice.status()['silenced'])
        self.assertEqual(self.factories,[])
    def test_disconnect_and_inventory_recovery_select_latest_cue(self):
        self.start();old=self.factories[0]
        self.voice.update_inventory(inventory(connected=False))
        self.wait(lambda:self.voice.status()['state']=='disconnected' and old.closed)
        self.voice.sync(snapshot(identifier='bow'))
        self.voice.update_inventory(inventory(sink='alsa_output.cube.new'))
        self.wait(lambda:len(self.factories)==2 and self.voice.status()['clip_id']=='bow:0')
        self.assertEqual(self.factories[1].options['device'],'alsa_output.cube.new')
    def test_failed_pipeline_retries_after_three_seconds_and_never_uses_fallback(self):
        self.start();old=self.factories[0];old.moved=True
        self.wait(lambda:self.voice.status()['state']=='unavailable' and old.closed)
        self.clock[0]=2.99;self.voice.update_inventory(inventory());time.sleep(.12)
        self.assertEqual(len(self.factories),1)
        self.voice.sync(snapshot(identifier='bow'));self.clock[0]=3.01;self.voice.update_inventory(inventory())
        self.wait(lambda:len(self.factories)==2 and self.voice.status()['clip_id']=='bow:0')
        self.assertEqual(self.factories[1].options['device'],'alsa_output.cube')
    def test_completion_is_single_shot(self):
        self.voice.update_inventory(inventory());self.voice.set_active(True)
        self.assertTrue(self.voice.sync(snapshot(status='completed')))
        self.wait(lambda:self.voice.status()['clip_id']=='complete')
        self.assertFalse(self.voice.sync(snapshot(status='completed')))
    def test_cache_remains_bounded(self):
        self.start()
        for identifier,index in (('raise',1),('raise',2),('bow',0),('single',0),('sway',0)):
            self.voice.sync(snapshot(identifier,index))
            self.wait(lambda:self.voice.status()['clip_id']==identifier+':'+str(index))
        state=self.voice.status();self.assertLessEqual(state['cache_clips'],MAX_CACHE_CLIPS)
        self.assertLessEqual(state['cache_bytes'],MAX_CACHE_BYTES)
    def test_disabled_never_loads_or_starts_a_worker(self):
        self.voice.close();self.assertTrue(self.voice.wait_closed())
        self.voice=HealthVoice(enabled=False,asset_dir=self.root,backend_factory=self.factory,physical_checker=self.physical)
        self.voice.update_inventory(inventory());self.voice.set_active(True)
        self.assertFalse(self.voice.sync(snapshot()));self.assertFalse(self.voice.status()['worker_alive'])
        self.assertEqual(self.voice.status()['state'],'disabled');self.assertEqual(self.factories,[])
    def test_close_tears_down_owned_backend(self):
        self.start();backend=self.factories[0];self.voice.close()
        self.assertTrue(self.voice.wait_closed());self.assertTrue(backend.closed)
        self.assertEqual(self.voice.status()['state'],'closed');self.assertFalse(self.voice.status()['output_active'])

    def test_same_named_courses_load_different_pcm_and_clear_old_cache(self):
        self.start(identifier='prepare',mode='free',session_id='shared-test')
        self.assertEqual(self.voice.status()['clip_id'],'prepare:1')
        old=self.factories[0]
        self.assertEqual(old.blocks[-1][:4],struct.pack('<hh',800,-800))
        self.voice.sync(snapshot(identifier='prepare',course_id='taichi',mode='free',session_id='shared-test'))
        self.wait(lambda:len(self.factories)==2 and bool(self.factories[-1].blocks))
        self.assertTrue(old.closed);self.assertEqual(self.voice.status()['course_id'],'taichi')
        self.assertEqual(self.voice.status()['clip_id'],'prepare:0')
        self.assertEqual(self.factories[-1].blocks[-1][:4],struct.pack('<hh',1600,-1600))
        self.assertEqual(self.voice.status()['cache_clips'],1)
        self.voice.sync(snapshot(identifier='finish',course_id='taichi',mode='free',session_id='shared-test'))
        self.wait(lambda:self.voice.status()['clip_id']=='finish:0' and bool(self.factories[-1].blocks))
        self.voice.sync(snapshot(identifier='prepare',mode='free',session_id='shared-test'))
        self.wait(lambda:len(self.factories)==3 and bool(self.factories[-1].blocks))
        self.assertEqual(self.factories[-1].blocks[-1][:4],struct.pack('<hh',800,-800))
        self.assertEqual(self.voice.status()['cache_clips'],1)

    def test_course_change_during_old_asset_load_cannot_publish_old_pcm(self):
        entered=threading.Event();release=threading.Event();load=self.voice._load
        def delayed(identifier,cache,manifest,root=None):
            if root==self.root:entered.set();release.wait(2.)
            return load(identifier,cache,manifest,root)
        self.voice._load=delayed
        try:
            self.voice.update_inventory(inventory());self.voice.set_active(True)
            self.voice.sync(snapshot(identifier='prepare',mode='free'))
            self.assertTrue(entered.wait(1.))
            start=time.monotonic()
            self.voice.sync(snapshot(identifier='prepare',mode='free',course_id='taichi'))
            self.assertLess(time.monotonic()-start,.1)
        finally:release.set()
        self.wait(lambda:self.factories and bool(self.factories[-1].blocks))
        self.assertEqual(len(self.factories),1)
        self.assertEqual(self.factories[-1].blocks[-1][:4],struct.pack('<hh',1600,-1600))
        self.assertEqual(self.voice.status()['cache_clips'],1)

    def test_taichi_pause_resume_does_not_repeat_overview_but_replay_is_explicit(self):
        self.start(identifier='cloud',course_id='taichi',mode='free',session_id='taichi-test')
        self.voice.sync(snapshot(identifier='cloud',course_id='taichi',mode='free',status='paused'))
        self.wait(lambda:self.voice.status()['silenced'])
        blocks=self.voice.status()['blocks']
        self.assertFalse(self.voice.sync(snapshot(identifier='cloud',course_id='taichi',mode='free',session_id='taichi-test')))
        time.sleep(.08);self.assertEqual(self.voice.status()['blocks'],blocks)
        self.assertTrue(self.voice.replay())
        self.wait(lambda:self.voice.status()['blocks']>blocks)

    def test_missing_taichi_asset_never_falls_back_to_baduanjin(self):
        (self.taichi_root/'prepare-0.wav').unlink()
        self.voice.update_inventory(inventory());self.voice.set_active(True)
        self.voice.sync(snapshot(identifier='prepare',course_id='taichi',mode='free'))
        self.wait(lambda:self.voice.status()['state']=='unavailable')
        self.assertEqual(self.factories,[])

    def test_unknown_course_cancels_current_cue_without_fallback(self):
        self.start(identifier='prepare',mode='free')
        self.assertFalse(self.voice.sync(snapshot(identifier='prepare',course_id='unknown',mode='free')))
        self.wait(lambda:self.voice.status()['silenced'] and self.voice.status()['cache_clips']==0)
        blocks=self.voice.status()['blocks'];time.sleep(.06)
        self.assertEqual(self.voice.status()['blocks'],blocks);self.assertEqual(len(self.factories),1)

    def test_taichi_completion_uses_own_course_asset(self):
        self.start(identifier='cloud',course_id='taichi',mode='free')
        self.voice.sync(snapshot(course_id='taichi',status='completed'))
        self.wait(lambda:self.voice.status()['clip_id']=='complete' and bool(self.factories[-1].blocks))
        self.assertEqual(self.factories[-1].blocks[-1][:4],struct.pack('<hh',1600,-1600))

    def test_real_gstreamer_fakesink_accepts_30ms_live_pcm_and_flushes(self):
        try:
            import gi
            gi.require_version('Gst','1.0')
            from gi.repository import Gst
        except (ImportError,ValueError):self.skipTest('Local GStreamer introspection unavailable')
        from audio.runtime import GstBackend
        self.voice.close();self.assertTrue(self.voice.wait_closed())
        outputs=[]
        class SilentOutput:
            name='gstreamer-fakesink-health-test'
            def __init__(output,sink,**options):
                output.guard=options['route_guard'];output.options=options;output.pushed=0;output.closed=False
                output.backend=GstBackend('fakesink',live_timestamps=True,block_frames=1440)
                outputs.append(output)
            def resume(output):
                if not output.guard():raise RuntimeError('No verified test route')
                output.backend.resume()
            def poll(output):
                if not output.guard():raise RuntimeError('Test route changed')
                output.backend.poll()
            def writable(output):return output.backend.writable()
            def push(output,pcm):output.backend.push(pcm);output.pushed+=1
            def pause(output):return output.backend.pause()
            def close(output):output.backend.close();output.closed=True
        for course,identifier in (('baduanjin','raise'),('taichi','cloud')):
            with self.subTest(course=course):
                self.voice=HealthVoice(asset_dir=self.root,course_asset_dirs={'taichi':self.taichi_root},
                    backend_factory=SilentOutput,physical_checker=lambda route:True)
                self.start(identifier=identifier,course_id=course,mode='free')
                output=outputs[-1];self.wait(lambda:output.pushed>=3)
                self.assertEqual(self.voice.status()['course_id'],course)
                self.voice.sync(snapshot(identifier=identifier,course_id=course,mode='free',status='paused'))
                self.wait(lambda:self.voice.status()['silenced'])
                self.voice.close();self.assertTrue(self.voice.wait_closed());self.assertTrue(output.closed)
                self.assertEqual(output.options['device'],'alsa_output.cube')
                self.assertEqual(output.backend.block_frames,1440)


if __name__=='__main__':unittest.main()
