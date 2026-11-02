"""Bounded, idempotent child actions. No arbitrary method names or payloads."""
from collections import OrderedDict
import re

COMMON = frozenset(('pause', 'continue'))
GAME = COMMON | {'start', 'restart'}
AUDIO = frozenset(('audio_low','audio_normal','audio_mute','audio_unmute',
    'audio_music_on','audio_music_off','audio_effects_on','audio_effects_off','audio_retry'))
SUPPORTED_ACTIONS = {
    'ink': COMMON | AUDIO | {'save', 'new_scroll', 'undo', 'theme_mixed',
                     'theme_landscape', 'theme_bamboo', 'theme_plum'},
    'fight': GAME | AUDIO,
    'fruit': GAME | AUDIO | {'rules_arcade', 'rules_classic', 'mirror_on', 'mirror_off'},
    'sword': GAME | AUDIO,
    'platformer': GAME | AUDIO,
    'runner': GAME | AUDIO,
    'pal': GAME,
    'kart': GAME | AUDIO,
    'taiko': GAME | AUDIO | {'retry_audio', 'silent', 'difficulty_easy', 'difficulty_normal', 'difficulty_hard',
                           'practice_full', 'practice_intro', 'practice_alternate', 'practice_finale',
                           'timing_earlier', 'timing_later', 'timing_reset',
                           'timing_preset_m100', 'timing_preset_m150', 'judgment_relaxed', 'judgment_strict'},
}


class ActionDispatcher:
    """Call a fixed executor only while the host owns the paused experience.

    The executor receives (action, done). Synchronous and asynchronous actions
    acknowledge through done(ok, reason='', **result). Duplicate command IDs
    replay the cached acknowledgement, never an already started action.
    """
    def __init__(self, mode, is_paused, execute, send):
        self.mode = mode
        self.is_paused = is_paused
        self.execute = execute
        self.send = send
        self.completed = OrderedDict()
        self.pending = None

    def dispatch(self, request):
        if not isinstance(request, dict):
            return False
        command_id, action = request.get('command_id'), request.get('action')
        safe_id = isinstance(command_id, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,96}', command_id)
        if not safe_id:
            self.send('action_result', mode=self.mode, command_id=None, action=None,
                      ok=False, reason='invalid_command_id')
            return False
        if command_id in self.completed:
            cached = self.completed[command_id]
            if cached['action'] == action:
                self.send('action_result', **cached)
            else:
                self.send('action_result', mode=self.mode, command_id=command_id,
                          action=action if isinstance(action, str) else None,
                          ok=False, reason='command_id_conflict')
            return False
        if self.pending and self.pending[0] == command_id:
            if self.pending[1] != action:
                self.send('action_result', mode=self.mode, command_id=command_id,
                          action=action if isinstance(action, str) else None,
                          ok=False, reason='command_id_conflict')
            return False
        settled = False

        def done(ok, reason='', **result):
            nonlocal settled
            if settled:
                return False
            settled = True
            packet = dict(mode=self.mode, command_id=command_id,
                          action=action if isinstance(action, str) else None,
                          ok=bool(ok), reason=str(reason)[:500])
            if result:
                packet['result'] = result
            self.completed[command_id] = packet
            while len(self.completed) > 128:
                self.completed.popitem(last=False)
            if self.pending and self.pending[0] == command_id:
                self.pending = None
            self.send('action_result', **packet)
            return False

        if (set(request) != {'op', 'command_id', 'action'} or request.get('op') != 'action'
                or not isinstance(action, str) or action not in SUPPORTED_ACTIONS.get(self.mode, ())):
            return done(False, 'unsupported_action')
        if self.pending:
            return done(False, 'action_busy')
        if not self.is_paused():
            return done(False, 'experience_not_paused')
        self.pending = (command_id, action)
        try:
            self.execute(action, done)
        except Exception as error:
            done(False, 'action_failed: '+str(error))
        return False
