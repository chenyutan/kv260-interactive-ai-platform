"""Local teaching media gate; watching never advances the practice clock.

The original lesson controller remains responsible for practice and observations.
This small coordinator pauses it while an independently decoded video is shown.
"""
import json
import hashlib
from pathlib import Path


class LessonVideos:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else Path(__file__).parent/'assets/video'
        self._clips = None

    def reload(self):
        self._clips = None

    def lookup(self, identifier):
        if self._clips is None:
            try:
                manifest = self.directory/'manifest.json'
                if manifest.stat().st_size > 256*1024:
                    raise ValueError('Video manifest is too large')
                data = json.loads(manifest.read_text(encoding='utf8'))
                if not isinstance(data, dict):
                    raise ValueError('Video manifest must be an object')
                rows = data.get('clips', [])
                if not isinstance(rows, list):
                    raise ValueError('Video clips must be a list')
                self._clips = {row.get('id', row.get('lesson_id')): row for row in rows if isinstance(row, dict)}
            except (OSError, ValueError, TypeError):
                self._clips = {}
        row = self._clips.get(identifier)
        if not row:
            return None
        try:
            relative = Path(row['path'])
            if relative.is_absolute() or '..' in relative.parts:
                return None
            path = self.directory/relative
            if not path.is_file() or path.is_symlink() or self.directory.resolve() not in path.resolve().parents:
                return None
            return dict(row, local_path=str(path))
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def companion_lookup(self, identifier):
        """Qualified full-body excerpt for free practice, else original video.

        Full teaching playback always keeps lookup(), including its original
        soundtrack and explanations. This excerpt is only the silent loop.
        """
        directory = self.directory.parent/'reference'
        candidate = directory/(identifier+'.json')
        try:
            if candidate.stat().st_size > 4*1024*1024:
                raise ValueError('Reference manifest too large')
            data = json.loads(candidate.read_text(encoding='utf8'))
            if (data.get('schema_version') != 1 or data.get('course_id') != 'baduanjin'
                    or data.get('lesson_id') != identifier or self.directory.name != 'video'):
                raise ValueError('Reference identity mismatch')
            relative = Path(data['clip_path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Reference path outside assets')
            path = directory/relative
            if (not path.is_file() or path.is_symlink() or
                    directory.resolve() not in path.resolve().parents or
                    path.stat().st_size > 512*1024*1024):
                raise ValueError('Invalid reference clip')
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != data['clip_sha256']:
                raise ValueError('Reference clip checksum mismatch')
            return dict(id=identifier, local_path=str(path), path=str(relative),
                        synchronized_reference=True)
        except (OSError, KeyError, TypeError, ValueError):
            return self.lookup(identifier)


class LessonVideoFlow:
    """One video before each selected lesson, with explicit skip and rewatch."""
    def __init__(self, session, player, library=None):
        self.session = session
        self.session.stop_at_lesson_boundary = True
        self.player = player
        self.library = library or LessonVideos()
        self.gated_lesson = None
        self.active = False
        self.paused = False
        self.clip = None
        self.error = None
        self.resume_after_video = True
        self.opened = []
        self.skipped = []
        self.finished = []
        self.companion_suspended = False
        self.companion_lesson = None
        self.companion_clip = None
        self.companion_error = None
        self.companion_loops = 0
        self._stopped = False

    @staticmethod
    def _remember(target, identifier):
        target.append(identifier)
        del target[:-128]

    def _gate(self, snapshot, force=False, resume_after=None):
        identifier = snapshot.get('lesson_id')
        if self._stopped or snapshot.get('status') == 'completed' or (not force and identifier == self.gated_lesson):
            return
        self._stop_companion()
        self.gated_lesson = identifier
        self.resume_after_video = (snapshot.get('status') != 'paused'
                                   if resume_after is None else bool(resume_after))
        self.session.action('pause')
        self.active = True
        self.paused = False
        self.clip = self.library.lookup(identifier)
        self.error = None
        self._remember(self.opened, identifier)
        if self.clip is None:
            self.error = '本式教学视频暂不可用；可以重试，或跳过视频进入骨架示意。'
            self.paused = True
            self.player.stop()
        else:
            self.player.play(self.clip['local_path'])

    def _end_video(self, skipped=False):
        if not self.active:
            return
        target = self.skipped if skipped else self.finished
        self._remember(target, self.gated_lesson)
        self.player.stop()
        self.active = self.paused = False
        self.error = None
        if self.resume_after_video:
            self.session.action('play')

    def _stop_companion(self):
        if self.companion_lesson is not None:
            self.player.stop()
        self.companion_lesson = None
        self.companion_clip = None
        self.companion_error = None
        self.companion_loops = 0

    def set_companion_suspended(self, suspended):
        """Hide reference without changing practice time or the full-video gate."""
        self.companion_suspended = bool(suspended)
        if self.companion_suspended:
            self._stop_companion()

    def _play_companion(self, clip):
        try:
            if self.player.play_companion(clip['local_path']) is False:
                self.companion_error = '参考视频已关闭，可继续跟练。'
        except Exception:
            # Decoder/thread setup failure must not escape a GTK tick or make
            # unavailable teaching media block the independent practice clock.
            self.companion_error = '参考视频暂时无法启动，可继续跟练或回看视频。'

    def _sync_companion(self, snapshot):
        eligible = (not self._stopped and not self.active and not self.companion_suspended
                    and snapshot.get('status') in ('running', 'paused')
                    and snapshot.get('phase') in ('demo', 'practice', 'paused'))
        if not eligible:
            self._stop_companion()
            return None
        identifier = snapshot.get('lesson_id')
        paused = snapshot.get('status') == 'paused'
        if identifier != self.companion_lesson:
            self._stop_companion()
            self.companion_lesson = identifier
            # The independent practice loop can take as long as the observed
            # full-body cycle. Do not change accepted timed-course voice cues.
            lookup = (getattr(self.library, 'companion_lookup', self.library.lookup)
                      if snapshot.get('mode') == 'free' else self.library.lookup)
            self.companion_clip = lookup(identifier)
            clip = self.companion_clip
            if clip is None:
                self.companion_error = '本式参考视频暂不可用，可继续跟练。'
            elif not callable(getattr(self.player, 'play_companion', None)):
                # Legacy injected players remain safe: never fall back to an
                # unmuted play(path) merely because the compact API is absent.
                self.companion_error = '参考视频不可用，可继续跟练。'
            else:
                self._play_companion(clip)
        clip = self.companion_clip
        state = self.player.snapshot()
        status = state.get('status', state.get('state', 'idle'))
        if not self.companion_error:
            if status == 'error':
                self.companion_error = '参考视频播放失败，可继续跟练或回看视频。'
            elif status == 'ended' and not paused:
                # Only the media restarts. Never repeat/select the lesson or
                # touch its pose, elapsed time, repetition count or report.
                self.companion_loops += 1
                self._play_companion(clip)
            elif paused and state.get('desired', status) == 'playing':
                self.player.pause()
            elif not paused and state.get('desired', status) == 'paused':
                self.player.resume()
            state = self.player.snapshot()
        return dict(state, clip=clip, muted=True, error_message=self.companion_error,
                    loop_count=self.companion_loops, independent_reference=True)

    def snapshot(self):
        snapshot = dict(self.session.snapshot())
        if self._stopped:
            snapshot['companion_video'] = None
            return snapshot
        if not self.active:
            self._gate(snapshot)
        if self.active:
            state = self.player.snapshot()
            status = state.get('status', state.get('state', 'idle'))
            if status == 'ended' and not self.error:
                self._end_video()
                snapshot = dict(self.session.snapshot())
                snapshot['companion_video'] = self._sync_companion(snapshot)
                return snapshot
            if status == 'error':
                self.error = '视频播放失败；可以重试，或跳过视频进入骨架示意。'
                self.paused = True
            snapshot.update(status='paused' if self.paused else 'running', phase='video',
                            phase_name='视频已暂停' if self.paused else '教学视频',
                            pose_points=[None]*17, display_pose_points=[None]*17, observable=False,
                            video=dict(state, clip=self.clip, error_message=self.error,
                                       muted=state.get('muted', True)))
        snapshot['companion_video'] = self._sync_companion(snapshot)
        return snapshot

    def update(self, state):
        if self._stopped:
            return self.snapshot()
        if not self.active:
            self.session.update(state)
        return self.snapshot()

    def action(self, operation):
        if self._stopped:
            return self.snapshot()
        if operation == 'video_skip':
            self._end_video(skipped=True)
        elif operation in ('video_rewatch', 'video_retry'):
            resume_after = (self.resume_after_video if self.active else
                            self.session.status == 'running')
            self.player.stop()
            if isinstance(self.library, LessonVideos):
                self.library.reload()
            self._gate(self.session.snapshot(), force=True, resume_after=resume_after)
        elif self.active and operation in ('pause', 'play', 'resume', 'toggle_pause'):
            pause = operation == 'pause' or operation == 'toggle_pause' and not self.paused
            if self.error:
                return self.snapshot()
            self.paused = pause
            self.player.pause() if pause else self.player.resume()
        else:
            was_active = self.active
            resume_after = (not self.paused if was_active else
                            self.session.status == 'running')
            if was_active:
                self.player.stop()
                self.active = self.paused = False
            result = self.session.action(operation)
            if was_active and result.get('status') == 'paused':
                result = self.session.action('play')
            if operation in ('next', 'previous', 'repeat') or operation.startswith('select_'):
                self._gate(result, force=True, resume_after=resume_after)
        return self.snapshot()

    def stop(self):
        self._stopped = True
        self._stop_companion()
        self.player.stop()
        self.active = False
        if self.session.status == 'running':
            self.session.action('pause')

    def report_metadata(self):
        return dict(opened=list(self.opened), skipped=list(self.skipped),
                    watched_to_end=list(self.finished),
                    watching_included_in_practice_seconds=False)
