"""CPU time of PDF.redact on a page of form fields and notes.

Run: python benchmarks/redaction.py [--against OTHER/source-code/OpenPDF] [--rounds 3] [--runs 9]

Prints, per case, the median CPU time of each round as a range. With --against
(another checkout of OpenPDF, such as master) the two trees are timed in turn
within each round, and the ratio of this tree to the other is printed per round.
The load average is printed because the figures move with it.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parents[1]
SMALL = (10, 8, 320, 22)  # over the first field and the first note
CASES = [  # (fields, notes, area, label); the page is 600 pt wide, one row per 12 pt
    (60, 0, SMALL, 'one field'),
    (120, 0, SMALL, 'one field'),
    (50, 50, SMALL, 'one of each'),
    (100, 100, SMALL, 'one of each'),
    (200, 200, SMALL, 'one of each'),
    (200, 200, None, 'whole page'),
]


def build(folder):
    """Write each page once (adding fields is the slow part), for every tree to read."""
    sys.path.insert(0, str(HERE))
    import fitz
    from core import PDF
    for fields, notes in sorted({case[:2] for case in CASES}):
        pdf = PDF()
        pdf.doc.new_page(width=600, height=20 + 12 * max(fields, notes))
        for n in range(fields):
            pdf.add_field(0, fitz.Rect(20, 10 + n * 12, 200, 20 + n * 12), f'f{n}')
            pdf.fill_field(0, f'f{n}', f'value {n}')
        for n in range(notes):
            pdf.note(0, fitz.Point(300, 10 + n * 12), f'note {n}')
        (folder / f'{fields}-{notes}.pdf').write_bytes(pdf.bytes())


def measure(tree, folder, runs):
    """Median CPU milliseconds per case, using the OpenPDF source in tree."""
    sys.path.insert(0, str(tree))
    import fitz
    from core import PDF
    result = []
    for fields, notes, area, _label in CASES:
        data = (folder / f'{fields}-{notes}.pdf').read_bytes()
        pdf = PDF()
        pdf.restore(data)
        rect = fitz.Rect(area) if area else pdf.doc[0].rect
        times = []
        for _ in range(runs):
            pdf.restore(data)
            start = time.process_time()
            pdf.redact(0, rect)
            times.append(time.process_time() - start)
        result.append(statistics.median(times) * 1000)
    return result


def child(tree, folder, runs):
    out = subprocess.run([sys.executable, __file__, '--tree', str(tree), '--pages', str(folder), '--runs', str(runs)],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def span(values, unit=''):
    low, high = min(values), max(values)
    return f'{low:.0f}{unit}' if round(low) == round(high) else f'{low:.0f} to {high:.0f}{unit}'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--against', type=Path, help='another OpenPDF source folder to compare with')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--runs', type=int, default=9, help='redactions per case in a round; the median counts')
    parser.add_argument('--tree', type=Path, help=argparse.SUPPRESS)  # one tree, JSON out
    parser.add_argument('--pages', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.tree:
        print(json.dumps(measure(args.tree.resolve(), args.pages, args.runs)))
        sys.exit()
    here, other = [], []
    with tempfile.TemporaryDirectory() as folder:
        build(Path(folder))
        load = [os.getloadavg()[0]]
        for _ in range(args.rounds):
            if args.against:
                other.append(child(args.against.resolve(), folder, args.runs))
            here.append(child(HERE, folder, args.runs))
            load.append(os.getloadavg()[0])
    print(f'CPU ms of PDF.redact, median of {args.runs}, {args.rounds} rounds; '
          f'1-minute load average {span(load)} on {os.cpu_count()} cores')
    head = '| On the page | Area | this tree |' + (' other | this / other |' if other else '')
    print(head)
    print('|' + ' --- |' * (head.count('|') - 1))
    for n, (fields, notes, _area, label) in enumerate(CASES):
        row = f'| {fields} fields + {notes} notes | {label} | {span([r[n] for r in here])} |'
        if other:
            ratios = [h[n] / o[n] for h, o in zip(here, other)]
            row += f' {span([r[n] for r in other])} | {min(ratios):.1f} to {max(ratios):.1f} |'
        print(row)
