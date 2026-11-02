"""Deterministic lifecycle for an explicitly requested, single-photo capture.

The caller supplies monotonic times and carries the generation token through
asynchronous work. No clocks, threads, or UI dependencies belong in this module.
"""

import math


class PhotoSession:
    def __init__(self):
        self.phase = 'viewfinder'
        self.active = False
        self.generation = 0
        self.deadline = None
        self.error = None
        self.started_at = None
        self.stage = None

    def resume(self):
        """Activate the panel without requesting a photo."""
        if self.phase == 'closed':
            return
        self.active = True

    def pause(self):
        """Cancel pending work, keeping a finished result available."""
        self.active = False
        self.generation += 1
        self.deadline = None
        self.error = None
        self.started_at = None
        self.stage = None
        if self.phase != 'result':
            self.phase = 'viewfinder'

    def shutdown(self):
        self.pause()
        self.phase = 'closed'

    def retake(self):
        """Return to the viewfinder; a new click must start the next photo."""
        if not self.active:
            return False
        self.generation += 1
        self.phase = 'viewfinder'
        self.deadline = None
        self.error = None
        self.started_at = None
        self.stage = None
        return True

    def start_countdown(self, now):
        if not self.active or self.phase not in ('viewfinder', 'error'):
            return False
        self.generation += 1
        self.phase = 'countdown'
        self.deadline = now + 3
        self.error = None
        self.started_at = None
        self.stage = None
        return True

    def remaining(self, now):
        if self.deadline is None:
            return 0
        return math.ceil(max(0, min(3, self.deadline - now)))

    def tick(self, now):
        """Claim the due capture once and return its generation token."""
        if (not self.active or self.phase != 'countdown'
                or self.deadline is None or now < self.deadline):
            return None
        self.phase = 'capturing'
        self.started_at = now
        self.stage = 'capturing'
        self.deadline = None
        return self.generation

    def processing(self, token):
        if not self._accepts(token, ('capturing',)):
            return False
        self.phase = 'processing'
        self.stage = 'starting_worker'
        return True

    def progress(self, token, stage):
        if not self._accepts(token, ('processing',)):
            return False
        if stage not in ('loading_model', 'preparing_image', 'painting', 'finishing', 'finished'):
            return False
        self.stage = stage
        return True

    def elapsed(self, now):
        return max(0, int(now - self.started_at)) if self.started_at is not None else 0

    def complete(self, token):
        if not self._accepts(token, ('capturing', 'processing')):
            return False
        self.phase = 'result'
        self.error = None
        return True

    def fail(self, token, message):
        if not self._accepts(token, ('capturing', 'processing')):
            return False
        self.phase = 'error'
        self.error = message
        return True

    def _accepts(self, token, phases):
        return self.active and token == self.generation and self.phase in phases
