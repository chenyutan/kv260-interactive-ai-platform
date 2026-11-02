"""Absolute body-position lanes and bounded jump/crouch edges.

Coordinates are in the displayed (possibly mirrored) aspect-correct space.
The neutral anchor is captured automatically from a short steady torso. It is
not a screen target and never follows a held side-step or a crouch.
"""
from collections import deque


class BodyMotion:
    WARMUP = .24
    LANE_TRIGGER = .34
    LANE_PAIR_MINIMUM = .22
    LANE_NEUTRAL = .22
    LANE_CENTER_LIMIT = .30
    LANE_PAIR_LIMIT = .45
    LANE_SIDE_SECONDS = .08
    LANE_CENTRE_SECONDS = .06
    LANE_MIN_OBSERVATIONS = 2
    LANE_RETURN_CLEAR_TRIGGER = .46
    LANE_RETURN_CLEAR_PAIR = .30
    LANE_RETURN_SETTLE_SECONDS = .10
    LANE_RETURN_SETTLE_RANGE = .10
    CROUCH_SHOULDER_DEPTH = .38
    CROUCH_HIP_DEPTH = .26
    CROUCH_CONFIRM_SECONDS = .18
    CROUCH_MIN_OBSERVATIONS = 3
    CROUCH_RETURN_SHOULDER = .18
    CROUCH_RETURN_HIP = .16

    def __init__(self):
        self.reset()

    def reset(self):
        self.samples = deque(maxlen=24)
        self.history = deque(maxlen=16)
        self.scale_samples = deque(maxlen=16)
        self.anchor = None
        self.calibration = 'calibrating'
        self.progress = 0.
        self.lane_target = 1
        self._clear_lane_candidate()
        self.return_from_lane = None
        self.return_centre = deque(maxlen=24)
        self.jump_armed = False
        self.upright_since = None
        self.crouched = False
        self.slide_armed = True
        self.crouch_since = None
        self.crouch_observations = 0
        self.slide_return_since = None
        self.slide_relaxed_rearm = False
        self.slide_previous = None
        self.scale_crouch_anchor = None
        self.airborne = False
        self.jump_at = -1e9

    def _clear_lane_candidate(self):
        self.lane_candidate = None
        self.lane_candidate_since = None
        self.lane_candidate_observations = 0

    def _select_lane(self, dsx, dhx, dx, stamp):
        """Confirm light side steps, protecting only a continuous return path.

        A settled centre or re-entry to the same side uses the light threshold.
        On an unsteady side-to-centre return, a shallow opposite continuation
        can be rebound: require a clearer cross or a naturally settled centre.
        Time alone never releases a held shallow rebound. A clear cross does
        not require stopping in the centre. Only fresh observations reach here.
        """
        centre = (abs(dx) <= self.LANE_NEUTRAL
                  and max(abs(dsx), abs(dhx)) <= self.LANE_CENTER_LIMIT)
        if self.return_from_lane is not None:
            if centre:
                self.return_centre.append((stamp, dsx, dhx))
                while (len(self.return_centre) > 1
                       and stamp-self.return_centre[1][0] >= self.LANE_RETURN_SETTLE_SECONDS):
                    self.return_centre.popleft()
                if (len(self.return_centre) >= 2
                        and stamp-self.return_centre[0][0]+1e-6 >= self.LANE_RETURN_SETTLE_SECONDS
                        and all(max(row[k] for row in self.return_centre)
                                - min(row[k] for row in self.return_centre) <= self.LANE_RETURN_SETTLE_RANGE
                                for k in (1, 2))):
                    self.return_from_lane = None
                    self.return_centre.clear()
            else:
                self.return_centre.clear()
        target = self.lane_target
        side = (dsx*dhx > 0 and min(abs(dsx), abs(dhx)) >= self.LANE_PAIR_MINIMUM
                and abs(dx) >= self.LANE_TRIGGER
                and abs(dsx-dhx) <= self.LANE_PAIR_LIMIT)
        if side:
            desired = 2 if dx > 0 else 0
            protected = (self.lane_target == 1 and self.return_from_lane is not None
                         and desired != self.return_from_lane)
            clear_cross = (abs(dx) >= self.LANE_RETURN_CLEAR_TRIGGER
                           and min(abs(dsx), abs(dhx)) >= self.LANE_RETURN_CLEAR_PAIR)
            if not protected or clear_cross:
                target = desired
        elif centre:
            target = 1
        if target == self.lane_target:
            self._clear_lane_candidate()
            return
        if target != self.lane_candidate:
            self.lane_candidate = target
            self.lane_candidate_since = stamp
            self.lane_candidate_observations = 0
        self.lane_candidate_observations += 1
        seconds = self.LANE_CENTRE_SECONDS if target == 1 else self.LANE_SIDE_SECONDS
        if (self.lane_candidate_observations >= self.LANE_MIN_OBSERVATIONS
                and stamp-self.lane_candidate_since+1e-6 >= seconds):
            previous = self.lane_target
            self.lane_target = target
            self._clear_lane_candidate()
            if target == 1 and previous in (0, 2):
                self.return_from_lane = previous
                self.return_centre.clear()
            elif target in (0, 2):
                self.return_from_lane = None
                self.return_centre.clear()

    def result(self, **events):
        out = dict(move=0, lane_target=None, jump=False, slide=False,
                   calibration=self.calibration,
                   calibration_progress=self.progress,
                   jump_ready=self.jump_armed)
        out.update(events)
        return out

    def suspend_observation(self):
        """Keep the neutral stance and edge latches across a short blind spot.

        Velocities and dwell times cannot span a gap. An already emitted lane
        or jump stays disarmed, so regaining a held pose cannot replay it.
        """
        self.history.clear()
        self.scale_samples.clear()
        self._clear_lane_candidate()
        self.return_centre.clear()
        self.upright_since = None
        self.crouch_since = None
        self.crouch_observations = 0
        self.slide_return_since = None
        self.slide_previous = None
        if self.anchor is None:
            self.samples.clear()
            self.progress = 0.

    def resume_observation(self, shoulder, hip, width, stamp):
        """Prime a fresh velocity sample without emitting an action edge."""
        self.suspend_observation()
        self.history.append((stamp, shoulder[1], hip[1]))

    def update(self, shoulder, hip, width, stamp):
        sx, sy = shoulder
        hx, hy = hip
        if self.anchor is None:
            self.samples.append((stamp, sx, sy, hx, hy, width))
            # Four observations at 5 FPS take .6 seconds. The former .42s
            # window could never calibrate that stream, however still it was.
            while self.samples and stamp-self.samples[0][0] > .9:
                self.samples.popleft()
            elapsed = stamp-self.samples[0][0]
            self.progress = min(1., max(0., elapsed/self.WARMUP))
            # Require a steady body, not specific arm or screen coordinates.
            stable = all(max(row[k] for row in self.samples)-min(row[k] for row in self.samples) <= width*.09
                         for k in (1, 2, 3, 4))
            if not stable:
                self.samples.clear()
                self.samples.append((stamp, sx, sy, hx, hy, width))
                self.progress = 0.
            elif elapsed + 1e-6 >= self.WARMUP and len(self.samples) >= 4:
                self.anchor = tuple(sum(row[k] for row in self.samples)/len(self.samples) for k in range(1, 6))
                self.calibration = 'ready'
                self.progress = 1.
                self.jump_armed = True
                self.history.append((stamp, sy, hy))
            return self.result()

        bsx, bsy, bhx, bhy, bw = self.anchor
        sd, hd = (sy-bsy)/bw, (hy-bhy)/bw
        previous = self.slide_previous
        self.slide_previous = (stamp, sy, hy, width)
        # Return from a clear crouch need not match the original upright
        # sample exactly. This only releases the slide edge; jump rearming
        # and all horizontal references retain their original stricter gate.
        ref = self.scale_crouch_anchor
        rsd, rhd = (((sy-ref[0])/ref[2], (hy-ref[1])/ref[2])
                    if ref is not None else (sd, hd))
        returned = (abs(rsd) <= self.CROUCH_RETURN_SHOULDER
                    and abs(rhd) <= self.CROUCH_RETURN_HIP)
        if returned and stamp-self.jump_at >= .55:
            if self.slide_return_since is None:
                self.slide_return_since = stamp
            if stamp-self.slide_return_since+1e-6 >= .12:
                if not self.slide_armed:
                    self.slide_relaxed_rearm = True
                self.slide_armed = True
                self.scale_crouch_anchor = None
        else:
            self.slide_return_since = None
        if not .82 <= width/bw <= 1.22:
            # Reconfirm a stable scale without learning a side-step endpoint
            # as a new horizontal neutral. A single ROI estimate cannot move
            # the anchor or create an airborne edge.
            self.history.clear()
            self._clear_lane_candidate()
            self.return_centre.clear()
            # Shoulder width changes when facing/turning into a squat. Keep
            # the original crouch test alive while lane/jump scale recovers;
            # retain a temporary slide reference across that reconfirmation.
            # Uniform width AND torso-length scaling is instead consistent
            # with walking towards/away from the camera and cannot slide.
            span_ratio = (hy-sy)/(bhy-bsy)
            coherent_scale = abs(span_ratio-width/bw) <= .10
            width_step = (previous is not None
                          and not .82 <= width/previous[3] <= 1.22)
            lowered = sd > .10 and hd > .075
            if coherent_scale:
                self.scale_crouch_anchor = None
            elif (lowered and not width_step and .70 <= width/bw <= 1.45
                  and self.scale_crouch_anchor is None):
                self.scale_crouch_anchor = (bsy, bhy, bw)
            slide = self._crouch_edge(sd, hd, stamp,
                allow=(not coherent_scale and .70 <= width/bw <= 1.45 and not width_step),
                previous=previous, sy=sy, hy=hy, bw=bw)
            self.scale_samples.append((stamp,sx,sy,hx,hy,width))
            while self.scale_samples and stamp-self.scale_samples[0][0] > .9:
                self.scale_samples.popleft()
            elapsed=stamp-self.scale_samples[0][0]
            stable=(all(max(row[k] for row in self.scale_samples)-min(row[k] for row in self.scale_samples) <= width*.09
                        for k in (1,2,3,4)) and
                    max(row[5] for row in self.scale_samples)/min(row[5] for row in self.scale_samples) <= 1.08)
            self.calibration='calibrating';self.progress=min(1.,elapsed/self.WARMUP)
            if not stable:
                self.scale_samples.clear();self.scale_samples.append((stamp,sx,sy,hx,hy,width));self.progress=0.
            elif elapsed+1e-6 >= self.WARMUP and len(self.scale_samples)>=4:
                mean=lambda k:sum(row[k] for row in self.scale_samples)/len(self.scale_samples)
                self.anchor=(bsx,mean(2),bhx,mean(4),mean(5))
                self.scale_samples.clear();self.calibration='ready';self.progress=1.
                self.history.append((stamp,sy,hy))
            return self.result(slide=slide)
        self.scale_samples.clear();self.calibration='ready';self.progress=1.
        dsx, dhx = (sx-bsx)/bw, (hx-bhx)/bw
        dx = (dsx+dhx)/2
        sd, hd = (sy-bsy)/bw, (hy-bhy)/bw
        self.history.append((stamp, sy, hy))
        while self.history and stamp-self.history[0][0] > .30:
            self.history.popleft()
        reference=next((row for row in reversed(tuple(self.history)[:-1])
                        if stamp-row[0]>=.025),self.history[0])
        dt = stamp-reference[0]
        # Use the most recent meaningful interval. A long averaging window
        # could compare a second take-off with the preceding airborne pose
        # and report zero upward speed even after a valid landing.
        vs = (reference[1]-sy)/(bw*dt) if dt >= .025 else 0.
        vh = (reference[2]-hy)/(bw*dt) if dt >= .025 else 0.

        upright = abs(sd) <= .065 and abs(hd) <= .065
        if upright:
            if self.upright_since is None:
                self.upright_since = stamp
            if stamp-self.upright_since >= .12:
                self.jump_armed = True
                if self.scale_crouch_anchor is None:
                    self.slide_armed = True
                self.slide_relaxed_rearm = False
                self.crouched = False
                self.airborne = False
        else:
            self.upright_since = None

        # Standing position owns a lane, not a relative left/right command.
        # A held side position therefore cannot walk through two lanes, while
        # returning to the captured centre briefly confirms the middle.
        # Separate entry/exit thresholds keep boundary jitter in its current
        # lane. Both torso centres must move together; an arm swing or a
        # shoulder-only lean cannot select a side or return from one.
        previous_lane = self.lane_target
        self._select_lane(dsx, dhx, dx, stamp)
        move = (1 if self.lane_target > previous_lane else -1
                if self.lane_target < previous_lane else 0)

        jump = slide = False
        landing_guard = self.airborne or stamp-self.jump_at < .55
        if not landing_guard and sd > .10 and hd > .075:
            self.crouched = True
            self.jump_armed = False
        slide = self._crouch_edge(sd, hd, stamp, allow=True,
                                 previous=previous, sy=sy, hy=hy, bw=bw)
        # Real upward translation of BOTH torso centres. Merely lifting a
        # hand, knee or rising back from an observed crouch is not a jump.
        if ((self.jump_armed or self.crouched) and not self.airborne
                and sd < -.11 and hd < -.11 and min(vs, vh) > .45):
            jump = True
            self.jump_armed = False
            self.airborne = True
            self.jump_at = stamp
        # Slowly accommodate ordinary stance drift only in the upright band.
        # Horizontal anchors stay fixed until an explicit reset/reacquisition.
        if upright and not self.airborne and not self.crouched:
            self.anchor = (bsx, bsy*.995+sy*.005, bhx, bhy*.995+hy*.005, bw*.995+width*.005)
        return self.result(move=move, lane_target=self.lane_target, jump=jump, slide=slide,
                           body_offset=round(dx, 3), body_vertical=round((sd+hd)/2, 3))

    def _crouch_edge(self, sd, hd, stamp, *, allow, previous, sy, hy, bw):
        # The existing jump latch may stay airborne until precise standing;
        # release only the slide protection after measured downward return.
        guarded = self.airborne or stamp-self.jump_at < .55
        if self.airborne and stamp-self.jump_at >= .55 and sd > .10 and hd > .075:
            guarded = False
        extra = False
        ref = self.scale_crouch_anchor
        if ref is not None:
            extra = ((sy-ref[0])/ref[2] >= self.CROUCH_SHOULDER_DEPTH
                     and (hy-ref[1])/ref[2] >= self.CROUCH_HIP_DEPTH)
        primary = sd >= self.CROUCH_SHOULDER_DEPTH and hd >= self.CROUCH_HIP_DEPTH
        rising = ((self.slide_relaxed_rearm or extra and not primary) and previous is not None
                  and stamp-previous[0] >= .025
                  and min(previous[1]-sy, previous[2]-hy)
                      / (bw*(stamp-previous[0])) > .45)
        deep = (allow and not guarded and self.slide_armed and not rising
                and (primary or extra))
        if deep:
            if self.crouch_since is None:
                self.crouch_since = stamp
            self.crouch_observations += 1
            if (self.crouch_observations >= self.CROUCH_MIN_OBSERVATIONS
                    and stamp-self.crouch_since+1e-6 >= self.CROUCH_CONFIRM_SECONDS):
                self.slide_armed = False
                self.crouch_since = None
                self.crouch_observations = 0
                return True
        else:
            self.crouch_since = None
            self.crouch_observations = 0
        return False
