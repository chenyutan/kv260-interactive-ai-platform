"""Latest-only offline lesson narration on a verified Cube output.

Public controls do not read assets, decode WAVs, import GI, probe Pulse or touch
an audio pipeline. A single worker owns all of that work. This is speech, not a
second music clock: old instructions are flushed rather than played late.
Real lesson snapshots select one complete overview per lesson. They never use
the animation's repeating instruction index to claim the user's current pose.
"""
from collections import OrderedDict
import hashlib,io,json,re,threading,time,wave
from pathlib import Path
from audio.menu_music import _select_cube,_identity,_physical_matches
from audio.runtime import GstBackend

RATE=48000
CHANNELS=2
SAMPLE_WIDTH=2
BLOCK_FRAMES=1440
BLOCK_BYTES=BLOCK_FRAMES*CHANNELS*SAMPLE_WIDTH
RETRY_SECONDS=3.
MAX_CLIP_FRAMES=RATE*30
MAX_CLIP_BYTES=MAX_CLIP_FRAMES*4+65536
MAX_CACHE_BYTES=8*1024*1024
MAX_CACHE_CLIPS=4
COURSES=('baduanjin','taichi')


class _Cancelled(Exception):pass


def _cue(snapshot):
    if not isinstance(snapshot,dict):return None
    course=snapshot.get('course_id','baduanjin')
    if course not in COURSES:return None
    status=snapshot.get('status')
    if status=='completed':
        return ('complete',('completed',snapshot.get('session_id')) if course=='baduanjin'
                else ('completed',course,snapshot.get('session_id')))
    if status!='running':return None
    identifier=snapshot.get('lesson_id');instruction=snapshot.get('instruction')
    if not isinstance(identifier,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,48}',identifier):return None
    mode=snapshot.get('mode')
    if course=='taichi':
        # Each Tai Chi lesson has its own complete overview in a separate
        # asset root. Never interpret animation steps as observed movement.
        return (identifier+':0',('taichi-overview-v1',snapshot.get('session_id'),identifier,mode))
    if mode in ('learn','follow','free'):
        # The lesson timer/animation is not an observation of the user. A
        # complete overview can finish naturally while the reference advances;
        # repeated cycle positions cannot cut it off or replay three commands.
        # Free-mode preparation additionally explains the Next action.
        index=1 if identifier=='prepare' and mode=='free' else 0
        return (identifier+':'+str(index),
                ('overview-v2',snapshot.get('session_id'),identifier,mode))
    # Compatibility for older callers without a course mode. New production
    # LessonSession snapshots always include their explicit mode above.
    index=snapshot.get('instruction_index')
    if not isinstance(index,int) or isinstance(index,bool):
        instructions=snapshot.get('instructions')
        if not isinstance(instructions,(list,tuple)) or instruction not in instructions:return None
        index=instructions.index(instruction)
    if index not in range(3):return None
    return (identifier+':'+str(index),(snapshot.get('session_id'),identifier,snapshot.get('phase'),index))


class HealthVoice:
    """Nonblocking controls with one pending instruction and one PCM worker."""
    def __init__(self,enabled=True,asset_dir=None,backend_factory=None,
                 physical_checker=None,clock=time.monotonic,course_asset_dirs=None):
        self.enabled=bool(enabled)
        self.asset_dir=Path(asset_dir) if asset_dir is not None else Path(__file__).parent/'assets/voice'
        self._asset_dirs={'baduanjin':self.asset_dir,'taichi':Path(__file__).parent/'assets/taichi_voice'}
        if course_asset_dirs is not None:
            if not isinstance(course_asset_dirs,dict) or any(key not in COURSES for key in course_asset_dirs):
                raise ValueError('Expected explicit supported course asset roots')
            self._asset_dirs.update({key:Path(value) for key,value in course_asset_dirs.items()})
        self._backend_factory=backend_factory or GstBackend
        self._physical_checker=physical_checker or _physical_matches
        self._clock=clock;self._lock=threading.RLock();self._wake=threading.Event();self._thread=None
        self._active=False;self._closed=False;self._route=None;self._generation=0
        self._course_id='baduanjin'
        self._session_status='idle';self._last_key=None;self._latest=None;self._pending=None
        self._replay_identifier=None
        self._stats=dict(state='idle' if self.enabled else 'disabled',error=None,output_active=False,
            clip_id=None,position_frames=0,accepted=0,replaced=0,attempts=0,retries=0,
            cache_bytes=0,cache_clips=0,blocks=0,acknowledged_flush=0,next_retry=0.)

    def _ensure_worker(self):
        if self.enabled and not self._closed and (self._thread is None or not self._thread.is_alive()):
            self._thread=threading.Thread(target=self._run,name='health-voice',daemon=True);self._thread.start()

    def _cancel(self,retain=False):
        self._generation+=1
        self._pending=(self._latest,self._generation) if retain and self._latest else None

    def update_inventory(self,snapshot):
        route=_select_cube(snapshot)
        with self._lock:
            if self._closed:return False
            changed=(_identity(route),bool(route and route.get('discovery_stale'))) != (_identity(self._route),bool(self._route and self._route.get('discovery_stale')))
            self._route=route
            if changed:
                self._cancel(retain=self._active and self._session_status in ('running','completed'))
                self._ensure_worker();self._wake.set()
            return bool(route and not route.get('discovery_stale'))

    def set_active(self,value):
        with self._lock:
            if self._closed:return False
            value=bool(value)
            if value!=self._active:
                self._active=value;self._cancel()
                if not value:self._latest=self._last_key=self._replay_identifier=None;self._session_status='idle'
                self._ensure_worker();self._wake.set()
            return self.enabled

    def sync(self,snapshot):
        selected=_cue(snapshot)
        status=snapshot.get('status','idle') if isinstance(snapshot,dict) else 'idle'
        course=snapshot.get('course_id','baduanjin') if isinstance(snapshot,dict) else None
        course=course if course in COURSES else None
        with self._lock:
            if self._closed or not self.enabled or not self._active:return False
            if course!=self._course_id:
                # Only immutable identifiers cross this control boundary.
                # Manifest reads, cache eviction and output flush stay on the
                # worker; an in-flight old load cannot publish a new cue.
                self._course_id=course
                self._latest=self._last_key=self._replay_identifier=None
                self._session_status='idle';self._cancel()
                self._ensure_worker();self._wake.set()
            was_running=self._session_status in ('running','completed')
            self._session_status=status
            if selected is None:
                self._latest=self._replay_identifier=None
                if was_running or self._pending is not None:self._cancel();self._wake.set()
                # Retain the last key across pause: resume cannot repeat an old
                # sentence. A new instruction or an explicit page exit rearms.
                return False
            identifier,key=selected
            self._replay_identifier=identifier
            if key==self._last_key:return False
            self._last_key=key;self._latest=identifier
            if self._pending is not None:self._stats['replaced']+=1
            self._cancel(retain=True);self._stats['accepted']+=1
            self._ensure_worker();self._wake.set();return True

    def replay(self):
        """Replay the current overview explicitly; never resume paused speech.

        The host should sync its current lesson snapshot first. This method
        does no file/device work and retains the single pending request limit.
        """
        with self._lock:
            if (self._closed or not self.enabled or not self._active
                    or self._session_status not in ('running','completed')
                    or self._replay_identifier is None):return False
            self._latest=self._replay_identifier
            if self._pending is not None:self._stats['replaced']+=1
            self._cancel(retain=True);self._stats['accepted']+=1
            self._ensure_worker();self._wake.set();return True

    def _publish(self,**values):
        with self._lock:self._stats.update(values)

    def status(self):
        with self._lock:
            result=dict(self._stats)
            state=result['state'];route=self._route
            message=('讲解已关闭' if not self.enabled else '讲解已停止' if self._closed else
                '正在播放讲解' if state=='playing' else '讲解已暂停' if state=='paused' else
                '正在准备讲解' if state=='loading' else '讲解暂不可用，稍后自动重试' if state=='unavailable' else
                '音响未连接，仍可看示范跟练' if state=='disconnected' or not route or route.get('discovery_stale') else '讲解已就绪')
            result.update(message=message,voice_status=message,enabled=self.enabled,active=self._active,
                course_id=self._course_id,
                worker_alive=bool(self._thread and self._thread.is_alive()),queued=int(self._pending is not None),
                queue_limit=1,block_frames=BLOCK_FRAMES,connected=bool(route and not route.get('discovery_stale')),
                target_sink=route['sink'] if route else None,flush_generation=self._generation,
                silenced=not result['output_active'] and (self._thread is None or result['acknowledged_flush']==self._generation))
            return result

    def close(self):
        with self._lock:
            if self._closed:return
            self._closed=True;self._active=False;self._latest=self._last_key=self._replay_identifier=None;self._cancel()
            if self._thread is None:self._stats.update(state='closed',output_active=False,acknowledged_flush=self._generation)
            self._wake.set()

    def wait_closed(self,timeout=2.):
        """Tests and process shutdown only; never call from a GTK callback."""
        thread=self._thread
        if thread:thread.join(timeout)
        return not thread or not thread.is_alive()

    def _guard(self,route):
        with self._lock:
            if (self._closed or not self._active or self._session_status not in ('running','completed') or
                    _identity(route)!=_identity(self._route) or not self._route or self._route.get('discovery_stale')):return False
        return self._physical_checker(route)

    def _load(self,identifier,cache,manifest,asset_dir=None):
        if identifier in cache:
            cache.move_to_end(identifier);return cache[identifier],manifest
        root=(asset_dir if asset_dir is not None else self.asset_dir).resolve()
        if manifest is None:
            path=root/'manifest.json'
            if path.stat().st_size>128*1024:raise ValueError('Voice manifest exceeds 128 KiB')
            data=json.loads(path.read_text(encoding='utf-8'))
            entries=data.get('clips')
            if data.get('schema')!=1 or not isinstance(entries,list) or not 1<=len(entries)<=64:raise ValueError('Expected voice manifest schema 1 with 1..64 clips')
            manifest={}
            for item in entries:
                if not isinstance(item,dict):raise ValueError('Invalid voice clip entry')
                clip_id=item.get('id')
                if not isinstance(clip_id,str) or not re.fullmatch(r'(?:[a-z][a-z0-9_-]{0,48}:[0-2]|complete)',clip_id) or clip_id in manifest:raise ValueError('Invalid or duplicate voice clip id')
                manifest[clip_id]=dict(item)
        if identifier not in manifest:raise ValueError('Missing voice clip: '+identifier)
        item=manifest[identifier];name=item.get('path');frames=item.get('frames')
        if (not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.wav',name) or
                isinstance(frames,bool) or not isinstance(frames,int) or not 0<frames<=MAX_CLIP_FRAMES or
                (item.get('rate'),item.get('channels'),item.get('sample_width'))!=(RATE,CHANNELS,SAMPLE_WIDTH)):
            raise ValueError('Expected bounded PCM16 stereo 48000 Hz voice metadata')
        path=(root/name).resolve()
        if path.parent!=root or path.stat().st_size>MAX_CLIP_BYTES:raise ValueError('Voice path escapes root or clip is too large')
        blob=path.read_bytes();expected=item.get('sha256','')
        if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',expected) or hashlib.sha256(blob).hexdigest()!=expected.lower():raise ValueError('Voice checksum mismatch: '+identifier)
        with wave.open(io.BytesIO(blob),'rb') as source:
            if (source.getframerate(),source.getnchannels(),source.getsampwidth(),source.getcomptype(),source.getnframes())!=(RATE,CHANNELS,SAMPLE_WIDTH,'NONE',frames):raise ValueError('Voice WAV format/frame mismatch: '+identifier)
            pcm=source.readframes(frames)
        if len(pcm)!=frames*4:raise ValueError('Truncated voice PCM: '+identifier)
        cache[identifier]=pcm;cache.move_to_end(identifier)
        while len(cache)>MAX_CACHE_CLIPS or sum(map(len,cache.values()))>MAX_CACHE_BYTES:cache.popitem(last=False)
        self._publish(cache_bytes=sum(map(len,cache.values())),cache_clips=len(cache))
        return pcm,manifest

    def _run(self):
        backend=None;backend_route=None;owner=-1;pcm=None;offset=0;next_block=0.;drain_until=None
        cache=OrderedDict();manifest=None;full_since=None;next_retry=0.
        cache_course=None
        def teardown():
            nonlocal backend,backend_route
            if backend is not None:
                try:backend.pause()
                except Exception:pass
                try:backend.close()
                except Exception:pass
            backend=backend_route=None
        try:
            while True:
                self._wake.clear()
                with self._lock:
                    closed=self._closed;generation=self._generation;active=self._active
                    session_status=self._session_status;pending=self._pending
                    course=self._course_id
                    route=dict(self._route) if self._route else None
                if closed:break
                allowed=active and session_status in ('running','completed')
                try:
                    if course!=cache_course:
                        teardown();cache.clear();manifest=None;cache_course=course;next_retry=0.
                        self._publish(cache_bytes=0,cache_clips=0)
                    if backend is not None and _identity(route)!=backend_route:teardown()
                    if owner!=generation:
                        if backend is not None:backend.pause()
                        owner=generation;pcm=None;offset=0;drain_until=None;full_since=None
                        self._publish(output_active=False,clip_id=None,position_frames=0,acknowledged_flush=generation)
                    if not allowed:
                        self._publish(state='paused' if active else 'idle',output_active=False)
                        self._wake.wait();continue
                    if route is None or route.get('discovery_stale'):
                        teardown();self._publish(state='disconnected',output_active=False)
                        self._wake.wait();continue
                    if pcm is None and pending is None:
                        self._publish(state='idle',output_active=False)
                        self._wake.wait();continue
                    if pcm is None:
                        if self._clock()<next_retry:
                            self._wake.wait(min(.1,max(.001,next_retry-self._clock())));continue
                        identifier,pending_generation=pending
                        self._publish(state='loading')
                        data,manifest=self._load(identifier,cache,manifest,self._asset_dirs[course])
                        with self._lock:
                            if (self._closed or not self._active or self._generation!=pending_generation or
                                    self._session_status not in ('running','completed')):continue
                        if backend is None:
                            if not self._guard(route):raise RuntimeError('Verified Cube is unavailable')
                            from connectivity.audio_device import _pulse_environment
                            server=_pulse_environment().get('PULSE_SERVER')
                            if not isinstance(server,str) or not server:raise RuntimeError('No explicit desktop Pulse server available')
                            with self._lock:self._stats['attempts']+=1
                            backend=self._backend_factory('pulsesink',device=route['sink'],server=server,
                                route_guard=lambda chosen=dict(route):self._guard(chosen),
                                stream_name='InkSuite health guidance',live_timestamps=True,block_frames=BLOCK_FRAMES)
                            backend_route=_identity(route)
                        with self._lock:
                            if self._generation!=pending_generation or self._closed or not self._active:continue
                            self._pending=None;self._stats.update(output_active=True,state='playing',clip_id=identifier,error=None)
                        backend.resume();pcm=data;offset=0;next_block=time.monotonic();drain_until=None
                    backend.poll();now=time.monotonic()
                    if drain_until is not None:
                        if now<drain_until:self._wake.wait(min(.01,drain_until-now));continue
                        backend.pause();pcm=None;drain_until=None
                        self._publish(state='idle',output_active=False);continue
                    if now<next_block:self._wake.wait(min(.01,next_block-now));continue
                    if not backend.writable():
                        full_since=now if full_since is None else full_since
                        if now-full_since>1.:raise RuntimeError('Voice output stopped consuming PCM')
                        self._wake.wait(.005);continue
                    full_since=None
                    with self._lock:
                        if self._closed or self._generation!=owner or not self._active:continue
                    block=pcm[offset:offset+BLOCK_BYTES];offset+=len(block)
                    backend.push(block+b'\0'*(BLOCK_BYTES-len(block)))
                    self._publish(position_frames=offset//4)
                    with self._lock:self._stats['blocks']+=1
                    next_block=max(next_block+.03,now)
                    if offset>=len(pcm):drain_until=now+.12
                except Exception as exc:
                    teardown();pcm=None;drain_until=None;manifest=None
                    next_retry=self._clock()+RETRY_SECONDS
                    with self._lock:
                        self._pending=(self._latest,self._generation) if self._active and self._session_status in ('running','completed') and self._latest else None
                        self._stats.update(state='unavailable',error=str(exc)[:400],output_active=False,
                            next_retry=next_retry,retries=self._stats['retries']+1,acknowledged_flush=self._generation)
                    self._wake.wait(.05)
        finally:
            teardown();cache.clear()
            self._publish(state='closed',output_active=False,clip_id=None,cache_bytes=0,cache_clips=0,acknowledged_flush=self._generation)
