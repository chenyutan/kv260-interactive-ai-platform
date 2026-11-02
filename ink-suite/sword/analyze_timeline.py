"""Attribute callback/GC overlap in Sword stalls without guessing missing work.

Usage: python3 -m sword.analyze_timeline /tmp/sword.timeline.jsonl
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path


PAIRS = {'tick_begin': 'tick_end', 'draw_begin': 'draw_end',
         'advance_begin': 'advance_end', 'input_begin': 'input_end',
         'game_begin': 'game_end', 'metrics_begin': 'metrics_end',
         'poll_begin': 'poll_end', 'gc_start': 'gc_stop'}
LABELS = {'tick_begin': 'tick', 'draw_begin': 'draw', 'advance_begin': 'advance',
          'input_begin': 'input', 'game_begin': 'game', 'metrics_begin': 'metrics',
          'poll_begin': 'poll', 'gc_start': 'gc'}


def union_seconds(intervals):
    total = 0.
    end = float('-inf')
    for left, right in sorted(intervals):
        if right > max(left, end):
            total += right-max(left, end)
        end = max(end, right)
    return total


def analyze(rows, threshold=.080):
    header = next((row for row in rows if row.get('kind') == 'header'), {})
    footer = next((row for row in reversed(rows) if row.get('kind') == 'footer'), {})
    main = header.get('main_thread')
    events = sorted((row for row in rows if 'at' in row), key=lambda row: (row['at'], row.get('seq', 0)))
    pending = defaultdict(list)
    spans = []
    endings = {end: begin for begin, end in PAIRS.items()}
    unmatched_ends = 0
    for row in events:
        kind, thread = row['kind'], row['thread']
        if kind in PAIRS:
            pending[(thread, kind)].append(row)
        elif kind in endings:
            stack = pending[(thread, endings[kind])]
            if not stack:
                unmatched_ends += 1
                continue
            start = stack.pop()
            spans.append(dict(kind=LABELS[start['kind']], start=start['at'], end=row['at'],
                              thread=thread, cpu=max(0., row['cpu']-start['cpu'])))
    stalls = []
    previous_tick = None
    for row in events:
        if row['kind'] != 'tick_begin' or row['thread'] != main:
            continue
        if row['previous_interval'] <= threshold:
            previous_tick = row
            continue
        end, begin = row['at'], row['at']-row['previous_interval']
        overlapping = [span for span in spans if span['start'] < end and span['end'] > begin]
        by_kind = defaultdict(list)
        for span in overlapping:
            label = span['kind']+('_main' if span['thread'] == main else '_worker')
            by_kind[label].append((max(begin, span['start']), min(end, span['end'])))
        callbacks = by_kind['tick_main']+by_kind['draw_main']
        covered = union_seconds(callbacks)
        same_window = previous_tick is not None and abs(previous_tick['at']-begin) < 1e-5
        stalls.append(dict(at=end, interval_ms=(end-begin)*1000,
            phase=row.get('phase'), game_time=row.get('game_time'),
            due_lateness_ms=(row['due_lateness']*1000 if row.get('due_lateness') is not None else None),
            main_thread_cpu_ms=((row['cpu']-previous_tick['cpu'])*1000 if same_window else None),
            callback_union_ms=covered*1000,
            uninstrumented_wall_ms=max(0., end-begin-covered)*1000,
            inclusive_overlap_ms={kind: union_seconds(intervals)*1000
                                  for kind, intervals in by_kind.items() if intervals},
            draw_callbacks_overlapping=sum(span['kind'] == 'draw' and span['thread'] == main for span in overlapping)))
        previous_tick = row
    return dict(schema='sword-timeline-analysis-v1', header=header, footer=footer,
        complete=bool(footer and not footer.get('error') and not footer.get('overwritten')
                      and not footer.get('contended') and not footer.get('pending')),
        unmatched_begin=sum(len(items) for items in pending.values()), unmatched_end=unmatched_ends,
        long_tick_count=len(stalls), long_ticks=stalls,
        interpretation='Stage/GC overlaps are inclusive, not additive. Uninstrumented wall time can include '
                       'GTK/X11 work outside draw, scheduling, GIL waiting or untraced callbacks. '
                       'CPU delta is emitted only when consecutive tick markers match the interval. '
                       'Callback timing is not physical scanout or camera-to-photon latency.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.trace.stat().st_size > 256*1024*1024:
        parser.error('Trace exceeds 256 MiB analysis bound')
    report = analyze([json.loads(line) for line in args.trace.read_text(encoding='utf-8').splitlines()])
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    else:
        print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
