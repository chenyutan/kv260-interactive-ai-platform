"""Bounded directional menu navigation, independent of pose or display APIs."""
import math
from collections import OrderedDict


class Navigation:
    def __init__(self):
        self.page = None
        self.index = 0
        self.direction = None
        self.next_repeat = 0.
        self.memory = OrderedDict()
        self.memory_key = None
        self.previous_choices = ()
        self.previous_rects = ()
        self.missing_choice = None

    def update(self, snapshot, choices, rects, page, now, memory_key=None, preserve_identity=False, default_choice=None):
        if not choices:
            return None
        buttons = set(snapshot.get('buttons', ())) | set(snapshot.get('pressed', ()))
        axes = snapshot.get('axes', {})
        x = -1 if 'LEFT' in buttons else 1 if 'RIGHT' in buttons else axes.get('lx', 0)
        y = -1 if 'UP' in buttons else 1 if 'DOWN' in buttons else axes.get('ly', 0)
        direction = ('right' if x > 0 else 'left') if abs(x) > .5 and abs(x) >= abs(y) else ('down' if y > 0 else 'up') if abs(y) > .5 else None
        explicit=direction is not None or 'A' in snapshot.get('pressed',snapshot.get('buttons',()))
        if explicit:self.missing_choice=None
        if self.page != page:
            if self.memory_key is not None and self.previous_choices and self.index < len(self.previous_choices):
                self.memory[self.memory_key] = self.previous_choices[self.index]
                self.memory.move_to_end(self.memory_key)
                while len(self.memory) > 24:
                    self.memory.popitem(last=False)
            self.page, self.index, self.direction = page, 0, None
            self.missing_choice = None
            remembered = self.memory.get(memory_key) if memory_key is not None else None
            if remembered in choices:
                self.index = choices.index(remembered)
            elif default_choice in choices:
                self.index = choices.index(default_choice)
            self.memory_key = memory_key
        elif preserve_identity and tuple(choices) != self.previous_choices:
            # AI polling/submission changes enabled actions, not the page the
            # user is navigating. Retain the action id rather than an ordinal.
            old_index=min(self.index,max(0,len(self.previous_choices)-1))
            previous=self.previous_choices[old_index] if self.previous_choices else None
            if self.missing_choice in choices:
                self.index=choices.index(self.missing_choice);self.missing_choice=None
            elif previous in choices:
                self.index=choices.index(previous)
            else:
                if self.missing_choice is None:self.missing_choice=previous
                if default_choice in choices:self.index=choices.index(default_choice)
                elif old_index<len(self.previous_rects) and rects:
                    a,b,c,d=self.previous_rects[old_index];x,y=(a+c)/2,(b+d)/2
                    self.index=min(range(len(choices)),key=lambda i:
                        math.hypot((rects[i][0]+rects[i][2])/2-x,(rects[i][1]+rects[i][3])/2-y))
                else:self.index=0
        self.previous_choices = tuple(choices)
        self.previous_rects = tuple(rects)
        self.index = min(self.index, len(choices)-1)
        # Explicit navigation/confirmation owns the new target. A later poll
        # must not pull it back to an action that was temporarily disabled.
        if explicit:self.missing_choice=None
        if direction is None:
            self.direction = None
            return self.index
        changed = direction != self.direction
        if not changed and now < self.next_repeat:
            return self.index
        self.direction = direction
        self.next_repeat = now + (.32 if changed else .13)
        centers = [((r[0]+r[2])/2, (r[1]+r[3])/2) for r in rects[:len(choices)]]
        a, b = centers[self.index]
        dx, dy = {'right': (1,0), 'left': (-1,0), 'down': (0,1), 'up': (0,-1)}[direction]
        candidates = []
        for i,(x,y) in enumerate(centers):
            along = (x-a)*dx+(y-b)*dy
            if along > .005:
                cross = abs((x-a)*dy-(y-b)*dx)
                candidates.append((math.hypot(x-a,y-b)+cross*1.8, i))
        if candidates:
            self.index = min(candidates)[1]
        return self.index
