"""Owned external TheXTech process; complete game data remains independent."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path(__file__).with_name('runtime.json')


def read_config(path=DEFAULT_CONFIG):
    path = Path(path)
    if path.stat().st_size > 16384:
        raise ValueError('TheXTech configuration is too large')
    data = json.loads(path.read_text(encoding='utf8'))
    if not isinstance(data, dict) or data.get('schema') != 1:
        raise ValueError('Unsupported TheXTech configuration')
    for key in ('engine', 'content', 'user_root'):
        value = data.get(key)
        if not isinstance(value, str) or '\0' in value or not Path(value).is_absolute():
            raise ValueError('Invalid TheXTech path: ' + key)
    graphics = data.get('graphics_library')
    if graphics is not None and (not isinstance(graphics, str) or '\0' in graphics
                                 or not Path(graphics).is_absolute() or not Path(graphics).is_file()):
        raise ValueError('Invalid verified graphics library')
    engine, content = Path(data['engine']), Path(data['content'])
    world = content / 'worlds/the first adventure/world.wldx'
    if not engine.is_file() or not world.is_file() or not (content / 'License.txt').is_file():
        raise ValueError('TheXTech engine or complete adventure is missing')
    digest = data.get('engine_sha256')
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Missing verified TheXTech engine hash')
    if hashlib.sha256(engine.read_bytes()).hexdigest() != digest:
        raise ValueError('TheXTech engine verification failed')
    return dict(data, world=str(world))


def launch_spec(config, peer, *, no_audio=False, test_level=None, user_root=None):
    """No shell, SDL_WINDOWID, global input injection or system environment edits."""
    content = Path(config['content'])
    user = Path(user_root or config['user_root'])
    target = str(test_level or config['world'])
    command = [config['engine'], '--asset-pack', str(content), '--user-directory', str(user),
               '--num-players', '1', '--player1', 'c1;s1', '--save-slot', '1',
               '--lang', 'zh-cn', '--render', 'opengles', '--never-pause', target]
    env = dict(os.environ)
    # This build owns physical input exclusively in the suite, through one
    # private descriptor. Native SDL joystick/controller/haptic init is skipped.
    env.update(peer.child_env)
    env.update(SDL_VIDEODRIVER='x11', SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR='0',
               SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS='0', SDL_VIDEO_X11_DGAMOUSE='0',
               SDL_AUDIODRIVER='dummy' if no_audio else 'pulseaudio',
               SDL_AUDIO_DEVICE_APP_NAME='InkSuite TheXTech',
               SDL_AUDIO_DEVICE_STREAM_NAME='TheXTech adventure')
    env.pop('SDL_WINDOWID', None)
    env.pop('INK_PAL_CONTROL', None)
    env.pop('LD_PRELOAD', None)
    if config.get('graphics_library'):
        # SDL's GLX auto-detection can create a Mesa context while this engine
        # calls Mali symbols. Keep EGL and GLES on the same verified driver,
        # scoped to this child; never replace system graphics libraries.
        env.update(SDL_OPENGL_ES_DRIVER='1', SDL_VIDEO_X11_FORCE_EGL='1',
                   SDL_VIDEO_GL_DRIVER=config['graphics_library'],
                   SDL_VIDEO_EGL_DRIVER=config['graphics_library'])
    libs = Path(config['engine']).parent.parent / 'lib'
    env['LD_LIBRARY_PATH'] = str(libs) + (':' + os.environ['LD_LIBRARY_PATH'] if os.environ.get('LD_LIBRARY_PATH') else '')
    return command, env, user


class NativeChild:
    def __init__(self, config, *, no_audio=False, user_root=None, test_level=None, log_path=None):
        from thextech.protocol import BridgePeer
        self.config = config
        self.peer = BridgePeer()
        self.no_audio = no_audio
        self.user_root, self.test_level = user_root, test_level
        self.log_path = Path(log_path or ROOT / 'logs/thextech-child.log')
        self.process = self.log = None
        self.started = None

    def start(self, audio_sink=None):
        if self.process is not None:
            raise RuntimeError('TheXTech already started')
        command, env, user = launch_spec(self.config, self.peer, no_audio=self.no_audio,
                                        user_root=self.user_root, test_level=self.test_level)
        if audio_sink:
            if not isinstance(audio_sink, str) or '\0' in audio_sink or len(audio_sink) > 1024:
                raise ValueError('Invalid audio sink')
            from connectivity.audio_device import _pulse_environment
            pulse = _pulse_environment()
            for key in ('PULSE_SERVER', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'):
                if pulse.get(key): env[key] = pulse[key]
            env['PULSE_SINK'] = audio_sink
        user.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = self.log_path.open('wb')
        try:
            self.process = subprocess.Popen(command, env=env, cwd=self.config['content'],
                stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True, pass_fds=self.peer.pass_fds)
            self.peer.close_child()
            self.started = time.monotonic()
            self.peer.set_mute(True)  # bootstrap cannot overlap the menu music
            self.peer.pause(timeout=30.)
        except Exception:
            self.close()
            raise

    def close(self):
        try:
            if self.process and self.process.poll() is None:
                try:
                    if not self.peer.closed:
                        self.peer.quit()
                        deadline = time.monotonic() + 2.5
                        while self.process.poll() is None and time.monotonic() < deadline:
                            self.peer.tick()
                            time.sleep(.01)
                except (OSError, RuntimeError, ValueError):
                    pass  # EOF still requires reaping our own child below.
                if self.process.poll() is None:
                    try: os.killpg(self.process.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                    try: self.process.wait(timeout=1.)
                    except subprocess.TimeoutExpired:
                        try: os.killpg(self.process.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                        self.process.wait(timeout=1.)
        finally:
            self.peer.close()
            if self.log: self.log.close(); self.log = None


class ReportGame:
    """Scalar engine counters, separated from the old platformer's rules."""
    def __init__(self):
        self.state = 'ready'
        self.data = dict(mode='thextech-aod', score=0, coins=0, lives=0, time=0.)
        self.time_base = 0

    def reset_round(self, telemetry):
        self.time_base = (telemetry or {}).get('active_ms', 0)

    def update(self, telemetry):
        if not telemetry: return
        self.data.update(score=telemetry.get('score', 0), coins=telemetry.get('coins', 0),
                         lives=telemetry.get('lives100', 0) / 100.,
                         time=max(0, telemetry.get('active_ms', 0) - self.time_base) / 1000.)
        phase = telemetry.get('phase', 0)
        self.state = 'won' if phase == 4 else 'playing' if phase in (2, 3) else 'ready'

    def report(self):
        return dict(self.data)
