"""Versioned placement for NEW strokes; existing event recipes never move.

This module does no painting or recognition. Absolute authoritative event themes
remain unchanged during replay. New mixed additions use the most recent woody
parent, and new canes choose a gap rather than cycling four fixed roots.
"""
import math

COMPOSITION_VERSION = 1
STEM_OFFSETS = {'A01': (-.047, 0., .045), 'A02': (0.,),
                'A03': (-.025, .036), 'A04': (0.,)}


def composition_version(event):
    value = event.get('composition_version', 0)
    if type(value) is not int or value not in (0, COMPOSITION_VERSION):
        raise ValueError('Unsupported ink composition_version: ' + repr(value))
    return value


def latest_woody_parent(parents):
    options = [(name, parent) for name, parent in parents.items() if name in ('bamboo', 'plum')]
    return max(options, key=lambda item: item[1]['event'])[0] if options else 'landscape'


def bamboo_root(event, width, height, history, legacy):
    """Return a root in pixels; only v1 events store a new normalized recipe."""
    if composition_version(event) == 0:
        return list(legacy)
    recipe = event.get('bamboo_layout')
    if recipe is not None:
        if (not isinstance(recipe, dict) or recipe.get('version') != 1
                or not isinstance(recipe.get('root_unit'), list) or len(recipe['root_unit']) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                       for v in recipe['root_unit'])):
            raise ValueError('Invalid versioned bamboo layout')
        x, y = recipe['root_unit']
        return [width*x, height*y]

    # Preserve measured bend and amplitude. Reserve enough paper for the
    # existing recipe's tips even when the user's arm movement leans outward.
    bend = .05*event['direction'] + .06*event['lean']
    if event['action'] == 'A04':
        bend += .09*(1 if event['direction'] >= 0 else -1)
    offsets = STEM_OFFSETS[event['action']]
    low_shift, high_shift = min(0., bend+min(offsets)), max(0., bend+max(offsets))
    lower, upper = ((.16, .82) if event.get('requested_theme') == 'bamboo' else (.075, .40))
    lower, upper = max(lower, .035-low_shift), min(upper, .965-high_shift)
    if lower >= upper:
        raise ValueError('Bamboo recipe has no visible placement interval')
    occupied = []
    for previous in history:
        if previous.get('theme') == 'bamboo' and previous.get('action') in STEM_OFFSETS:
            anchor = previous.get('anchor')
            if not isinstance(anchor, list) or len(anchor) != 2 or not math.isfinite(anchor[0]):
                raise ValueError('Bamboo parent has no replayed anchor')
            occupied.append(anchor[0]/width)
    preferred = min(upper, max(lower, legacy[0]/width))
    if not occupied:
        x = preferred
    else:
        knots = sorted(set([lower, upper] + [v for v in occupied if lower <= v <= upper]))
        choices = [lower, upper] + [(a+b)/2 for a, b in zip(knots, knots[1:])]
        x = max(choices, key=lambda v: (min(abs(v-p) for p in occupied), -abs(v-preferred), -v))
    event['bamboo_layout'] = dict(version=1, root_unit=[x, .91], policy='largest-visible-gap')
    return [width*x, height*.91]
