"""Local real AF_UNIX SOCK_SEQPACKET peer tests; no engine or hardware needed."""
import socket
import unittest

from thextech.protocol import (BridgePeer, PACKET, MAGIC, VERSION, STATE, TELEMETRY, EVENT,
                              RUN, PAUSE, QUIT, RESTART, UINT32, newer)


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now

    def advance(self, value=.020):
        self.now += value


@unittest.skipUnless(hasattr(socket, 'AF_UNIX') and hasattr(socket, 'SOCK_SEQPACKET'),
                     'requires Unix seqpacket; run with WSL Python on Windows')
class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.peer = BridgePeer(session=123, clock=self.clock)
        self.native = self.peer._child.dup()
        self.native.setblocking(False)
        self.peer.close_child()

    def tearDown(self):
        self.peer.close()
        self.native.close()

    def request(self):
        data = self.native.recv(65)
        self.assertEqual(len(data), 64)
        return PACKET.unpack(data)

    def receipt(self, request, *, flags=None, error=0, updates=1, completed=0,
                held=0, phase=3, seq=None, session=None, size=None):
        if flags is None:
            flags = 1 | (10 if request[5] == PAUSE else (8 if request[8] else 0))
        self.native.send(PACKET.pack(MAGIC, VERSION, STATE,
            request[3] if session is None else session,
            request[4] if seq is None else seq, request[5], flags,
            (960 | 540 << 16) if size is None else size, 456, held, updates, 9,
            phase, error, 10000, completed))

    def establish(self):
        ticket = self.peer.pause()
        request = self.request()
        self.receipt(request)
        self.peer.drain()
        self.assertTrue(ticket.success)
        return request

    def player_event(self, request, **changes):
        words = [MAGIC, VERSION, EVENT, 123, request[4], 1,
                 (-12345) & UINT32, 98765, (-250) & UINT32, 400, 1, 2, 0, 3, UINT32, 0]
        for index, value in changes.items():
            words[int(index[1:])] = value
        self.native.send(PACKET.pack(*words))

    def test_layout_fd_and_initial_pause(self):
        self.assertEqual(PACKET.size, 64)
        with self.assertRaises(RuntimeError):
            _ = self.peer.child_fd
        ticket = self.peer.pause()
        request = self.request()
        self.assertEqual(request[:6], (MAGIC, VERSION, 1, 123, 1, PAUSE))
        self.assertEqual(request[6:], (0,) * 10)
        self.assertEqual(ticket.seq, 1)
        self.receipt(request)
        messages = self.peer.tick()
        self.assertEqual(len(messages), 1)
        self.assertTrue(ticket.applied)
        self.assertTrue(self.peer.healthy)
        self.assertEqual(self.peer.state['xid'], 456)
        self.assertEqual(self.peer.state['size'], (960, 540))

    def test_heartbeat_20ms_and_no_busy_send(self):
        self.establish()
        self.peer.tick()
        with self.assertRaises(BlockingIOError):
            self.request()
        self.clock.advance()
        self.peer.tick()
        self.assertEqual(self.request()[4], 2)

    def test_backpressure_does_not_publish_or_consume_sequence(self):
        self.peer._socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        while True:
            try:
                self.peer._socket.send(b'fill')
            except BlockingIOError:
                break
        ticket = self.peer.pause()
        self.assertIsNone(ticket.seq)
        self.assertEqual(self.peer._seq, 0)
        self.assertFalse(ticket.applied)
        while True:
            try:
                self.native.recv(128)
            except BlockingIOError:
                break
        self.peer.tick()
        request = self.request()
        self.assertEqual(request[4], 1)
        self.assertEqual(ticket.seq, 1)
        self.receipt(request)
        self.peer.drain()
        self.assertTrue(ticket.success)

    def test_resume_waits_zero_native_observation_and_time(self):
        self.establish()
        run = self.peer.run(mask=16)
        zero = self.request()
        self.assertEqual(zero[6], 0)
        self.receipt(zero, updates=1)
        self.peer.drain()
        self.assertTrue(run.applied)
        self.clock.advance()
        self.peer.tick()
        zero2 = self.request()
        self.assertEqual(zero2[6], 0)  # No native input observation yet.
        self.receipt(zero2, updates=2)
        self.peer.tick()
        self.clock.advance()
        self.peer.tick()
        self.assertEqual(self.request()[6], 16)

    def test_pause_requires_applied_audio_and_zero_input(self):
        ticket = self.peer.pause()
        request = self.request()
        self.receipt(request, flags=3)
        self.peer.drain()
        self.assertFalse(ticket.applied)
        self.receipt(request, flags=11, held=4)
        self.peer.drain()
        self.assertFalse(ticket.applied)
        self.receipt(request, flags=11)
        self.peer.drain()
        self.assertTrue(ticket.applied)

    def test_native_ui_flag_is_exposed_and_unknown_flags_rejected(self):
        ticket = self.peer.pause()
        request = self.request()
        self.receipt(request, flags=11 | 64)
        self.peer.drain()
        self.assertTrue(ticket.applied)
        self.assertTrue(self.peer.state['native_ui'])
        self.receipt(request, flags=11)
        self.peer.drain()
        self.assertFalse(self.peer.state['native_ui'])
        self.receipt(request, flags=11 | 128)
        self.peer.drain()
        self.assertEqual(self.peer.invalid_packets, 1)
        self.assertFalse(self.peer.state['native_ui'])

    def test_native_ui_can_retain_native_audio_pause_after_run(self):
        self.establish()
        ticket = self.peer.run()
        request = self.request()
        self.receipt(request, flags=1 | 8)
        self.peer.drain()
        self.assertFalse(ticket.applied)  # No native reason signalled.
        self.receipt(request, flags=1 | 8 | 64)
        self.peer.drain()
        self.assertTrue(ticket.applied)

    def test_native_ui_does_not_relax_explicit_mute(self):
        self.establish()
        self.peer.set_mute(True)
        ticket = self.peer.run()
        request = self.request()
        self.receipt(request, flags=1 | 64)
        self.peer.drain()
        self.assertFalse(ticket.applied)
        self.receipt(request, flags=1 | 8 | 64)
        self.peer.drain()
        self.assertTrue(ticket.applied)

    def test_startup_has_30_seconds_but_first_ready_enables_two(self):
        ticket = self.peer.pause(timeout=30)
        self.request()
        self.clock.advance(3.)
        self.peer.tick()
        request = self.request()
        self.assertIsNone(self.peer.error)
        self.assertFalse(ticket.done)
        self.receipt(request)
        self.peer.drain()
        self.assertTrue(ticket.applied)
        self.clock.advance(2.01)
        self.peer.tick()
        self.assertEqual(self.peer.error, 'heartbeat_stale')

    def test_no_first_state_startup_timeout_fails_closed(self):
        self.peer.pause(timeout=30)
        self.request()
        self.clock.advance(30.01)
        self.peer.tick()
        self.assertEqual(self.peer.error, 'heartbeat_stale')
        self.assertFalse(self.peer.healthy)
        self.assertEqual(self.request()[5:7], (PAUSE, 0))

    def test_loading_state_gap_279_seconds_does_not_pause_bootstrap(self):
        self.peer.pause(timeout=30)
        request = self.request()
        self.receipt(request, flags=10, phase=3, updates=0)
        self.peer.drain()
        self.peer.set_mute(True)
        ticket = self.peer.run(mask=16, timeout=30)
        request = self.request()
        self.receipt(request, flags=8, phase=0, updates=1)
        self.peer.drain()
        self.assertTrue(ticket.applied)
        self.peer.set_input(32)
        self.clock.advance(2.79)
        self.peer.tick()
        request = self.request()
        self.assertEqual(request[5:7], (RUN, 0))
        self.assertIsNone(self.peer.error)
        self.receipt(request, flags=8, phase=0, updates=2)
        self.peer.drain()
        self.clock.advance(2.79)
        self.peer.tick()
        self.assertEqual(self.request()[5:7], (RUN, 0))
        self.assertFalse(self.peer._ready_once)

    def test_loading_receipts_cannot_extend_absolute_startup_deadline(self):
        self.peer.run(timeout=120)
        request = self.request()
        for step in range(6):
            self.clock.advance(5.)
            self.receipt(request, flags=0, phase=0, updates=step)
            self.peer.tick()
            request = self.request()
            self.assertEqual(request[6], 0)
            if step < 5:
                self.assertEqual(request[5], RUN)
                self.assertIsNone(self.peer.error)
        self.assertEqual(request[5], PAUSE)
        self.assertEqual(self.peer.error, 'heartbeat_stale')

    def test_late_first_ready_cannot_bypass_startup_deadline(self):
        self.peer.run(timeout=120)
        request = self.request()
        self.clock.advance(30.01)
        self.receipt(request, flags=1)
        self.peer.tick()
        self.assertFalse(self.peer._ready_once)
        self.assertIsNone(self.peer.state)
        self.assertEqual(self.request()[5:7], (PAUSE, 0))
        self.assertEqual(self.peer.error, 'heartbeat_stale')

    def test_first_ready_discards_bootstrap_input_and_requires_new_zero_observation(self):
        self.peer.run(mask=16, timeout=30)
        request = self.request()
        self.receipt(request, flags=0, phase=0, updates=1)
        self.peer.drain()
        self.clock.advance()
        self.peer.tick()
        request = self.request()
        self.receipt(request, flags=0, phase=0, updates=2)
        self.peer.drain()
        self.peer.set_input(32)
        self.clock.advance()
        self.peer.tick()
        request = self.request()
        self.assertEqual(request[6], 0)
        self.receipt(request, flags=1, phase=3, updates=3)
        self.peer.drain()
        self.assertTrue(self.peer._ready_once)
        self.assertEqual(self.peer._mask, 0)
        self.peer.set_input(64)
        self.clock.advance()
        self.peer.tick()
        request = self.request()
        self.assertEqual(request[6], 0)
        self.receipt(request, flags=1, updates=4)
        self.peer.drain()
        self.clock.advance(.021)
        self.peer.tick()
        self.assertEqual(self.request()[6], 64)

    def test_later_loading_phase_does_not_reopen_startup_grace(self):
        self.establish()
        self.peer.run()
        request = self.request()
        self.receipt(request, flags=0, phase=0)
        self.peer.drain()
        self.clock.advance(2.01)
        self.peer.tick()
        self.assertEqual(self.peer.error, 'heartbeat_stale')
        self.assertEqual(self.request()[5:7], (PAUSE, 0))

    def test_ready_peer_does_not_expire_at_old_startup_deadline(self):
        request = self.establish()
        for step in range(31):
            self.clock.advance(1.)
            self.receipt(request, updates=step + 2)
            self.peer.tick()
            request = self.request()
            self.assertIsNone(self.peer.error)
        self.assertTrue(self.peer._ready_once)
        self.assertTrue(self.peer.healthy)

    def test_native_stale_during_bootstrap_still_fails_closed(self):
        self.peer.pause(timeout=30)
        request = self.request()
        self.receipt(request, flags=14, phase=0)
        self.peer.drain()
        self.assertEqual(self.peer.error, 'native_stale')
        self.assertFalse(self.peer._ready_once)

    def test_wrong_session_future_seq_and_oversize_rejected(self):
        ticket = self.peer.pause()
        request = self.request()
        self.receipt(request, session=999)
        self.receipt(request, seq=17)
        self.native.send(b'x' * 80)
        self.peer.drain()
        self.assertEqual(self.peer.invalid_packets, 3)
        self.assertIsNone(self.peer.state)
        self.assertFalse(ticket.done)

    def test_wrap_and_old_ack_cannot_apply_new_command(self):
        self.peer._seq = UINT32 - 1
        old = self.peer.pause()
        pause = self.request()
        new = self.peer.run()
        run = self.request()
        self.assertEqual(pause[4], UINT32)
        self.assertEqual(run[4], 0)
        self.receipt(pause)
        self.peer.drain()
        self.assertFalse(new.applied)
        self.assertEqual(old.error, 'superseded')
        self.receipt(run, updates=2)
        self.peer.drain()
        self.assertTrue(new.applied)
        self.receipt(pause)
        self.peer.drain()
        self.assertEqual(self.peer.state['seq'], 0)

    def test_later_heartbeat_can_ack_same_command_generation(self):
        ticket = self.peer.pause()
        self.request()
        self.clock.advance()
        self.peer.tick()
        later = self.request()
        self.receipt(later)
        self.peer.drain()
        self.assertTrue(ticket.applied)
        self.assertEqual(ticket.ack_seq, later[4])

    def test_resize_mute_ack_and_validation(self):
        self.peer.set_size(960, 540)
        self.peer.set_mute(True)
        ticket = self.peer.run()
        request = self.request()
        self.assertEqual(request[7:9], (960 | 540 << 16, 1))
        self.receipt(request, flags=1, size=800 | 600 << 16)
        self.peer.drain()
        self.assertFalse(ticket.applied)
        self.receipt(request, flags=9)
        self.peer.drain()
        self.assertTrue(ticket.applied)
        for width, height in ((319, 540), (960, 2161), (True, 540)):
            with self.assertRaises(ValueError):
                self.peer.set_size(width, height)
        with self.assertRaises(ValueError):
            self.peer.run(mask=4096)
        with self.assertRaises(ValueError):
            self.peer.set_mute(1)

    def test_restart_ack_is_not_completion(self):
        self.establish()
        ticket = self.peer.restart()
        request = self.request()
        self.receipt(request, flags=17, completed=0)
        self.peer.drain()
        self.assertTrue(ticket.applied)
        self.assertFalse(ticket.done)
        self.assertFalse(ticket.success)
        self.clock.advance()
        self.peer.tick()
        heartbeat = self.request()
        self.assertEqual(heartbeat[5], RESTART)
        self.receipt(heartbeat, flags=1, completed=1)
        self.peer.drain()
        self.assertTrue(ticket.completed)
        self.assertEqual(ticket.status, 'completed')
        self.clock.advance()
        self.peer.tick()
        self.assertEqual(self.request()[5], PAUSE)  # Restore pre-restart pause.

    def test_restart_completion_count_wrap(self):
        request = self.establish()
        self.receipt(request, completed=UINT32)
        self.peer.drain()
        ticket = self.peer.restart()
        request = self.request()
        self.receipt(request, completed=0)
        self.peer.drain()
        self.assertTrue(ticket.completed)

    def test_restart_error_does_not_claim_completed(self):
        self.establish()
        ticket = self.peer.restart()
        request = self.request()
        self.receipt(request, error=3)
        self.peer.drain()
        self.assertEqual(ticket.status, 'error')
        self.assertEqual(ticket.error, 'wrong_phase')
        self.assertFalse(ticket.completed)
        self.clock.advance()
        self.peer.tick()
        self.assertEqual(self.request()[5], PAUSE)

    def test_restart_timeout_even_with_heartbeat_ack(self):
        self.establish()
        ticket = self.peer.restart(timeout=.1)
        request = self.request()
        self.receipt(request, flags=17)
        self.peer.drain()
        self.clock.advance(.11)
        self.peer.tick()
        self.assertEqual(ticket.status, 'timeout')
        self.assertFalse(ticket.success)
        self.assertEqual(self.request()[5], PAUSE)

    def test_two_second_stale_fails_closed_and_latches(self):
        self.establish()
        ticket = self.peer.run(mask=4)
        self.request()
        self.clock.advance(2.01)
        self.peer.tick()
        paused = self.request()
        self.assertEqual(paused[5:7], (PAUSE, 0))
        self.assertFalse(self.peer.healthy)
        self.assertFalse(ticket.success)
        with self.assertRaises(RuntimeError):
            self.peer.run()
        self.receipt(paused)
        self.peer.drain()
        self.assertFalse(self.peer.healthy)  # Explicit resume required.
        self.peer.run()
        self.assertEqual(self.request()[6], 0)

    def test_native_stale_fails_closed(self):
        self.establish()
        ticket = self.peer.run(mask=4)
        request = self.request()
        self.receipt(request, flags=15, error=2)
        self.peer.drain()
        self.assertEqual(self.peer.error, 'native_stale')
        self.assertFalse(ticket.success)
        self.clock.advance()
        self.peer.tick()
        self.assertEqual(self.request()[5:7], (PAUSE, 0))

    def test_telemetry_signed_fields_and_no_lifecycle_ack(self):
        ticket = self.peer.pause()
        request = self.request()
        self.native.send(PACKET.pack(MAGIC, VERSION, TELEMETRY, 123, request[4],
            8, 9, UINT32, 5, UINT32 - 99, 3, 11, 12, 13, 3, UINT32 - 1))
        self.peer.drain()
        self.assertEqual(self.peer.telemetry['score'], -1)
        self.assertEqual(self.peer.telemetry['lives'], -1.0)
        self.assertEqual(self.peer.telemetry['beat_code'], -2)
        self.assertFalse(ticket.done)
        self.assertFalse(self.peer.healthy)

    def test_player_snapshot_signed_values_types_and_no_ack(self):
        ticket = self.peer.pause()
        request = self.request()
        self.player_event(request)
        messages = self.peer.tick()
        self.assertEqual([m['kind'] for m in messages], ['player'])
        self.assertEqual(self.peer.player['x'], -123.45)
        self.assertEqual(self.peer.player['y'], 987.65)
        self.assertEqual(self.peer.player['speed_x'], -2.5)
        self.assertEqual(self.peer.player['speed_y'], 4.0)
        self.assertIs(self.peer.player['dead'], False)
        self.assertIsInstance(self.peer.player['character'], int)
        self.assertEqual(self.peer.player['level_hash'], UINT32)
        self.assertEqual(self.peer.player['received_at'], self.clock())
        self.assertIsNone(self.peer.state)
        self.assertFalse(ticket.done)
        self.assertFalse(self.peer.healthy)

    def test_player_event_malformed_ranges_session_sequence(self):
        self.peer.pause()
        request = self.request()
        cases = ({'w5': 2}, {'w10': 6}, {'w11': 8}, {'w12': 2}, {'w13': 201},
                 {'w15': 1}, {'w3': 124}, {'w4': 100}, {'w2': 99})
        for changes in cases:
            self.player_event(request, **changes)
        self.peer.drain()
        self.assertEqual(self.peer.invalid_packets, len(cases))
        self.assertIsNone(self.peer.player)
        self.player_event(request, w6=0x80000000, w7=0x7fffffff, w10=0, w11=0, w12=1, w13=200)
        self.peer.drain()
        self.assertEqual(self.peer.player['x'], -21474836.48)
        self.assertEqual(self.peer.player['y'], 21474836.47)
        self.assertIs(self.peer.player['dead'], True)

    def test_player_sequence_wrap_and_old_snapshot_is_ignored(self):
        self.peer._seq = UINT32 - 1
        self.peer.pause()
        old = self.request()
        self.player_event(old)
        self.peer.drain()
        self.clock.advance()
        self.peer.tick()
        new = self.request()
        self.player_event(new, w6=500)
        self.peer.drain()
        self.assertEqual(self.peer.player['seq'], 0)
        self.assertEqual(self.peer.player['x'], 5.0)
        self.player_event(old)
        self.peer.drain()
        self.assertEqual(self.peer.player['x'], 5.0)

    def test_player_event_cannot_keep_lifecycle_alive(self):
        self.establish()
        self.clock.advance(2.01)
        self.peer.tick()
        request = self.request()
        self.player_event(request)
        self.peer.tick()
        self.assertEqual(self.peer.error, 'heartbeat_stale')
        self.assertFalse(self.peer.healthy)

    def test_eof_marks_pending_error_and_closes(self):
        ticket = self.peer.pause()
        self.request()
        self.native.close()
        self.peer.tick()
        self.assertTrue(self.peer.closed)
        self.assertEqual(ticket.status, 'error')
        self.assertFalse(ticket.success)

    def test_quit_cancels_pending_restart(self):
        self.establish()
        restart = self.peer.restart()
        self.request()
        quit_ticket = self.peer.quit()
        request = self.request()
        self.assertEqual(restart.error, 'cancelled_by_quit')
        self.receipt(request)
        self.peer.drain()
        self.assertTrue(quit_ticket.success)


class ArithmeticTests(unittest.TestCase):
    def test_serial_order_half_range(self):
        self.assertTrue(newer(0, UINT32))
        self.assertFalse(newer(UINT32, 0))
        self.assertFalse(newer(7, 7))
        self.assertFalse(newer(0x80000000, 0))


if __name__ == '__main__':
    unittest.main()
