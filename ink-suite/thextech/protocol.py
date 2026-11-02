"""Private TheXTech v1 socket protocol. No process, GTK, or device ownership.

Call tick() from the owning event loop every 20 ms. Start in PAUSE, pass
child_env/pass_fds to Popen, then close_child() in the parent immediately.
All methods belong to that same event-loop thread.
"""
from dataclasses import dataclass
import secrets
import socket
import struct
import time

MAGIC = 0x584B4E49
VERSION = 1
REQUEST, STATE, TELEMETRY, EVENT = 1, 2, 3, 4
RUN, PAUSE, QUIT, RESTART = 0, 1, 2, 3
PACKET = struct.Struct('<16I')
MASK = 0xFFF
UINT32 = 0xFFFFFFFF
HEARTBEAT = 0.020
STALE_AFTER = 2.0
ERROR_NAMES = {0: 'okay', 1: 'malformed', 2: 'stale',
               3: 'wrong_phase', 4: 'reload_failed'}


def newer(a, b):
    return 0 < ((a - b) & UINT32) < 0x80000000


def signed(value):
    return value if value < 0x80000000 else value - 0x100000000


def _uint(value, name, maximum=UINT32):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError('invalid ' + name)
    return value


@dataclass
class RequestTicket:
    op: int
    generation: int
    created_at: float
    deadline: float
    seq: object = None  # None until successfully published to the socket.
    ack_seq: object = None
    status: str = 'pending'
    error: object = None
    applied: bool = False
    completed: bool = False
    baseline_restarts: object = None

    @property
    def done(self):
        return self.status in ('applied', 'completed', 'error', 'timeout')

    @property
    def success(self):
        return self.status in ('applied', 'completed')


class BridgePeer:
    def __init__(self, session=None, clock=time.monotonic, *, startup_timeout=30.0, _sockets=None):
        if isinstance(startup_timeout, bool) or not isinstance(startup_timeout, (int, float)) or not 0 < startup_timeout <= 120:
            raise ValueError('invalid startup timeout')
        self.startup_timeout = float(startup_timeout)
        self.session = _uint(session if session is not None else secrets.randbelow(UINT32) + 1,
                             'session')
        if not self.session:
            raise ValueError('session must be nonzero')
        if _sockets is None:
            if not hasattr(socket, 'AF_UNIX') or not hasattr(socket, 'SOCK_SEQPACKET'):
                raise RuntimeError('TheXTech private bridge requires AF_UNIX SOCK_SEQPACKET')
            _sockets = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self._socket, self._child = _sockets
        self._socket.setblocking(False)
        self._child.setblocking(False)
        self._socket.set_inheritable(False)
        self._child.set_inheritable(False)
        self._clock = clock
        self._started = self._last_rx = clock()
        self._startup_deadline = self._started + self.startup_timeout
        self._ready_once = False
        self._last_send = float('-inf')
        self._seq = 0
        self._generation = 0
        self._sent = {}
        self._tickets = []
        self._op = PAUSE
        self._mask = 0
        self._size = 0
        self._mute = False
        self._release = None
        self._restart_return_op = PAUSE
        self._last_state_seq = self._last_telemetry_seq = None
        self._last_player_seq = None
        self._stale_latched = False
        self.state = None
        self.telemetry = None
        self.player = None
        self.pending_restart = None
        self.closed = False
        self.error = None
        self.invalid_packets = 0

    @property
    def child_fd(self):
        if self._child is None:
            raise RuntimeError('child descriptor already closed in parent')
        return self._child.fileno()

    @property
    def pass_fds(self):
        return (self.child_fd,)

    @property
    def child_env(self):
        return {'INK_XTECH_FD': str(self.child_fd)}

    @property
    def healthy(self):
        return (not self.closed and not self._stale_latched and self.state is not None
                and self._clock() - self._last_rx <= STALE_AFTER
                and not self.state['stale'] and not self.state['peer_closed']
                and not self.state['error'])

    def close_child(self):
        if self._child is not None:
            self._child.close()
            self._child = None

    def _ensure_open(self):
        if self.closed:
            raise RuntimeError('bridge is closed')

    def _begin_release(self):
        self._release = {'generation': self._generation, 'sent_at': None,
                         'baseline': self.state['input_updates'] if self.state else None,
                         'observed': False}

    def _command(self, op, timeout):
        self._ensure_open()
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
            raise ValueError('invalid timeout')
        now = self._clock()
        self._generation += 1
        for old in self._tickets:
            if not old.done and old is not self.pending_restart:
                old.status, old.error = 'error', 'superseded'
        self._op = op
        ticket = RequestTicket(op, self._generation, now, now + timeout)
        self._tickets = [t for t in self._tickets if not t.done][-32:] + [ticket]
        if op == RUN:
            self._begin_release()
        else:
            self._mask = 0
        if op == QUIT and self.pending_restart and not self.pending_restart.done:
            self.pending_restart.status, self.pending_restart.error = 'error', 'cancelled_by_quit'
        self._send(now, force=True)
        return ticket

    def run(self, mask=0, timeout=2.0):
        _uint(mask, 'mask', MASK)
        if self.pending_restart and not self.pending_restart.done:
            raise RuntimeError('restart still pending')
        if self._stale_latched:
            if self._clock() - self._last_rx > STALE_AFTER:
                raise RuntimeError('fresh native state required before resume')
            self._stale_latched, self.error = False, None
        self._mask = mask if self._ready_once else 0
        return self._command(RUN, timeout)

    def pause(self, timeout=2.0):
        return self._command(PAUSE, timeout)

    def quit(self, timeout=2.0):
        return self._command(QUIT, timeout)

    def restart(self, timeout=5.0):
        if self.pending_restart and not self.pending_restart.done:
            raise RuntimeError('restart still pending')
        if not self.healthy:
            raise RuntimeError('fresh native state required before restart')
        if self.state['op'] == RESTART or self.state['restart_pending']:
            raise RuntimeError('wait for a non-restart receipt before another restart')
        self._restart_return_op = PAUSE if self._op == PAUSE else RUN
        ticket = self._command(RESTART, timeout)
        ticket.baseline_restarts = self.state['restarts_completed']
        self.pending_restart = ticket
        return ticket

    def set_input(self, mask):
        self._ensure_open()
        _uint(mask, 'mask', MASK)
        self._mask = mask if self._op == RUN and self._ready_once else 0

    def set_size(self, width, height):
        self._ensure_open()
        _uint(width, 'width', 3840)
        _uint(height, 'height', 2160)
        if width < 320 or height < 200:
            raise ValueError('invalid video size')
        self._size = width | (height << 16)

    def set_mute(self, muted):
        self._ensure_open()
        if not isinstance(muted, bool):
            raise ValueError('mute must be bool')
        self._mute = muted

    def _send(self, now, force=False):
        if self.closed or (not force and now - self._last_send < HEARTBEAT - 1e-9):
            return False
        seq = (self._seq + 1) & UINT32
        mask = self._mask if self._ready_once and self._op == RUN and self._release is None else 0
        packet = PACKET.pack(MAGIC, VERSION, REQUEST, self.session, seq,
                             self._op, mask, self._size, int(self._mute), *([0] * 7))
        try:
            count = self._socket.send(packet)
        except (BlockingIOError, InterruptedError):
            return False
        except OSError as exc:
            self._disconnect('send_failed:' + str(exc))
            return False
        if count != PACKET.size:
            self._disconnect('short_seqpacket_send')
            return False
        self._seq, self._last_send = seq, now
        self._sent[seq] = (self._generation, self._op, mask, self._size, self._mute)
        if len(self._sent) > 256:
            del self._sent[next(iter(self._sent))]
        for ticket in self._tickets:
            if ticket.generation == self._generation and ticket.seq is None:
                ticket.seq = seq
        if self._release and self._release['generation'] == self._generation:
            if self._release['sent_at'] is None:
                self._release['sent_at'] = now
        return True

    def _disconnect(self, reason):
        self.error = reason
        self._mask = 0
        for ticket in self._tickets:
            if not ticket.done:
                ticket.status, ticket.error = 'error', reason
        self.close()

    def _parse(self, data, now):
        if len(data) != PACKET.size:
            self.invalid_packets += 1
            return None
        p = PACKET.unpack(data)
        if p[:2] != (MAGIC, VERSION) or p[2] not in (STATE, TELEMETRY, EVENT) or p[3] != self.session:
            self.invalid_packets += 1
            return None
        if p[4] not in self._sent:
            self.invalid_packets += 1
            return None
        if p[2] == STATE:
            if p[5] > RESTART or p[6] & ~127 or p[12] > 4 or p[13] > 4 or p[9] & ~MASK:
                self.invalid_packets += 1
                return None
            if self._last_state_seq is not None and p[4] != self._last_state_seq and not newer(p[4], self._last_state_seq):
                return None
            sent = self._sent[p[4]]
            if p[5] != sent[1]:
                self.invalid_packets += 1
                return None
            if not self._ready_once and now >= self._startup_deadline:
                # A late ready bit must not revive an expired bootstrap or be
                # exposed as a usable first frame to the host UI.
                self._fail_stale('heartbeat_stale')
                return None
            self._last_state_seq = p[4]
            self.state = dict(kind='state', seq=p[4], op=p[5], flags=p[6],
                              ready=bool(p[6] & 1), paused=bool(p[6] & 2),
                              stale=bool(p[6] & 4), audio_paused=bool(p[6] & 8),
                              restart_pending=bool(p[6] & 16), peer_closed=bool(p[6] & 32),
                              native_ui=bool(p[6] & 64),
                              size=(p[7] & 65535, p[7] >> 16), size_word=p[7],
                              xid=p[8], held_mask=p[9], input_updates=p[10],
                              presented_frames=p[11], phase=p[12], error=p[13],
                              error_name=ERROR_NAMES[p[13]], native_ms=p[14],
                              restarts_completed=p[15], received_at=now)
            self._last_rx = now
            if (not self._ready_once and self.state['ready'] and not self._stale_latched
                    and not self.state['stale'] and not self.state['peer_closed'] and not self.state['error']
                    and now < self._startup_deadline):
                self._ready_once = True
                # Loading receipts can advance input_updates. They do not
                # replace a fresh zero-input observation once gameplay is ready.
                self._mask = 0
                if self._op == RUN:
                    self._begin_release()
            self._apply_state(sent, now)
            return self.state.copy()
        if p[2] == EVENT:
            # v1 subtype 1: diagnostic P1 snapshot, never a lifecycle receipt.
            # Bounds come from v1.7.4 global_constants.h; zero is permitted
            # before player setup, and maxSections=200 covers non-SMBX64 builds.
            if p[5] != 1 or p[10] > 5 or p[11] > 7 or p[12] > 1 or p[13] > 200 or p[15] != 0:
                self.invalid_packets += 1
                return None
            if self._last_player_seq is not None and p[4] != self._last_player_seq and not newer(p[4], self._last_player_seq):
                return None
            self._last_player_seq = p[4]
            self.player = dict(kind='player', seq=p[4], x=signed(p[6]) / 100.0,
                               y=signed(p[7]) / 100.0, speed_x=signed(p[8]) / 100.0,
                               speed_y=signed(p[9]) / 100.0, character=p[10],
                               state=p[11], dead=bool(p[12]), section=p[13],
                               level_hash=p[14], received_at=now)
            return self.player.copy()
        if p[14] > 4:
            self.invalid_packets += 1
            return None
        if self._last_telemetry_seq is not None and p[4] != self._last_telemetry_seq and not newer(p[4], self._last_telemetry_seq):
            return None
        self._last_telemetry_seq = p[4]
        self.telemetry = dict(kind='telemetry', seq=p[4], logic_ticks=p[5],
                              presented_frames=p[6], score=signed(p[7]), coins=signed(p[8]),
                              lives=signed(p[9]) / 100.0, lives100=signed(p[9]),
                              stars=p[10], level_hash=p[11], episode_hash=p[12],
                              active_ms=p[13], phase=p[14], beat_code=signed(p[15]),
                              received_at=now)
        # State receipts, not telemetry alone, prove lifecycle responsiveness.
        return self.telemetry.copy()

    def _apply_state(self, sent, now):
        state = self.state
        generation, op, mask, size, mute = sent
        applied = (not state['stale'] and not state['peer_closed'] and not state['error']
                   and (not size or size == state['size_word']))
        if op == PAUSE:
            applied = applied and state['paused'] and state['audio_paused'] and not state['held_mask']
        elif op in (RUN, RESTART):
            audio_applied = state['audio_paused'] if mute else (not state['audio_paused'] or state['native_ui'])
            applied = applied and not state['paused'] and audio_applied
        for ticket in self._tickets:
            if ticket.done or ticket.generation != generation or ticket.seq is None:
                continue
            if state['seq'] != ticket.seq and not newer(state['seq'], ticket.seq):
                continue
            if state['error']:
                ticket.status, ticket.error = 'error', state['error_name']
            elif applied:
                ticket.applied, ticket.ack_seq = True, state['seq']
                if ticket.op != RESTART:
                    ticket.status = 'applied'
        restart = self.pending_restart
        if restart and not restart.done and restart.seq is not None:
            # Completion may arrive on a later PAUSE generation; admission still
            # requires an applied receipt for this restart's own generation.
            if generation == restart.generation and state['error']:
                restart.status, restart.error = 'error', state['error_name']
            elif (restart.applied and not state['restart_pending'] and not state['error']
                  and newer(state['restarts_completed'], restart.baseline_restarts)):
                restart.status, restart.completed = 'completed', True
                self._mask = 0
                if self._op == RESTART:
                    self._generation += 1
                    self._op = self._restart_return_op
                    if self._op == RUN:
                        self._begin_release()
        if restart and restart.done and not restart.success and self._op == RESTART:
            self._mask = 0
            self._generation += 1
            self._op = PAUSE
        release = self._release
        if release and generation == release['generation'] and op == RUN and applied and mask == 0 and state['held_mask'] == 0:
            if release['baseline'] is None:
                release['baseline'] = state['input_updates']
            elif newer(state['input_updates'], release['baseline']):
                release['observed'] = True
        if state['peer_closed']:
            self._disconnect('native_peer_closed')
        elif state['stale']:
            self._fail_stale('native_stale')

    def _fail_stale(self, reason):
        if self._stale_latched or self._op == QUIT:
            return
        self._stale_latched, self.error = True, reason
        self._mask = 0
        self._generation += 1
        self._op = PAUSE
        for ticket in self._tickets:
            if not ticket.done:
                ticket.status, ticket.error = 'error', reason

    def drain(self):
        messages = []
        if self.closed:
            return messages
        for _ in range(64):
            try:
                data = self._socket.recv(PACKET.size + 1)
            except (BlockingIOError, InterruptedError):
                break
            except OSError as exc:
                self._disconnect('recv_failed:' + str(exc))
                break
            if not data:
                self._disconnect('peer_closed')
                break
            message = self._parse(data, self._clock())
            if message:
                messages.append(message)
            if self.closed:
                break
        return messages

    def tick(self):
        messages = self.drain()
        if self.closed:
            return messages
        now = self._clock()
        for ticket in self._tickets:
            if not ticket.done and now >= ticket.deadline:
                ticket.status, ticket.error = 'timeout', 'ack_timeout' if ticket.op != RESTART else 'restart_timeout'
                if ticket.op != QUIT:
                    self._mask = 0
                    self._generation += 1
                    self._op = PAUSE
        # Startup readiness, not the first window/loading State, ends grace.
        # Its absolute deadline never moves when loading receipts arrive.
        if ((not self._ready_once and now >= self._startup_deadline)
                or (self._ready_once and now - self._last_rx > STALE_AFTER)):
            self._fail_stale('heartbeat_stale')
        release = self._release
        if (release and release['observed'] and release['sent_at'] is not None
                and now - release['sent_at'] >= HEARTBEAT):
            self._release = None
        self._send(now)
        return messages

    def close(self):
        if not self.closed:
            self.closed = True
            self._mask = 0
            self._socket.close()
            self.close_child()
            for ticket in self._tickets:
                if not ticket.done:
                    ticket.status, ticket.error = 'error', self.error or 'closed'
