"""Writes the project's progress report: objdiff's report.json (version 2),
the file bytematch.dev and other progress trackers read.

    xbx report [-o build/report/<version>/report.json] [--owners all]

It needs no game file and no compiler, so it runs anywhere, in CI too: the
function table gives every function's address and size, and the unit files
say which are done. A function with a unit in the matched tier counts as
100%: the match gate proved it before the unit could be merged. A function
with a unit in the non-matching tier counts by the score on its
/* fuzzy: */ line, which the same gate checked, and never as matched. A
compiler-made exception-handling stub counts with the function it belongs to.

By default only the owners that xbx.toml lists as the project's subject
([owners] headline) are reported; --owners all adds the libraries the game
was linked with, each as its own category.

Derived from tools/objdiff_report.py of the Decompathon Steward.
"""
import argparse
import json
import os
import sys

from . import units

REPORT_VERSION = 2
GROUP_BYTES = 0x10000


def pct(part, total):
    return 100.0 * part / total if total else 0.0


def statuses(project):
    """{address: similarity 0..100} for every row that has any progress."""
    done = {}
    for addr, path in project.units('nonmatching'):
        score = units.load(path).fuzzy
        done[addr] = min(score if score is not None else 0.0, 99.9)
    for addr, _ in project.units('matched'):
        done[addr] = 100.0
    matched = {addr for addr, _ in project.units('matched')}
    for addr, unit in project.covered.items():     # carried by a matched unit; the match gate checks it
        if addr in project.rows and project.unit_address_of(unit) in matched:
            done[addr] = 100.0
    for row in project.rows.values():
        if row.get('kind') == 'eh':
            best = max((done.get(int(p, 16), 0.0) for p in row.get('parent', '').split()), default=0.0)
            if best == 100.0:
                done[row['_addr']] = 100.0
    return done


def measures(rows, done, unit_count=1):
    total = sum(r['_size'] for r in rows)
    matched = sum(r['_size'] for r in rows if done.get(r['_addr']) == 100.0)
    fuzzy = sum(r['_size'] * done.get(r['_addr'], 0.0) / 100.0 for r in rows)
    count = sum(1 for r in rows if done.get(r['_addr']) == 100.0)
    return {
        'fuzzy_match_percent': pct(fuzzy, total),
        'total_code': str(total),
        'matched_code': str(matched),
        'matched_code_percent': pct(matched, total),
        'total_data': '0',
        'matched_data': '0',
        'matched_data_percent': 0.0,
        'total_functions': len(rows),
        'matched_functions': count,
        'matched_functions_percent': pct(count, len(rows)),
        'complete_code': '0',
        'complete_code_percent': 0.0,
        'complete_data': '0',
        'complete_data_percent': 0.0,
        'total_units': unit_count,
        'complete_units': 0,
    }


def category_id(owner):
    return owner.replace(':', '_')


def build(project, owners=None):
    done = statuses(project)
    rows = sorted(project.rows.values(), key=lambda r: r['_addr'])
    owners = owners or project.headline
    if owners == ['all']:
        owners = sorted({r['owner'] for r in rows}, key=lambda o: (o not in project.headline, o))
    report_units, categories, everything = [], [], []
    for owner in owners:
        mine = [r for r in rows if r['owner'] == owner]
        if not mine:
            continue
        chunks, current, start = [], [], None
        for r in mine:
            if current and (r['_addr'] - start >= GROUP_BYTES or r['section'] != current[-1]['section']):
                chunks.append(current)
                current = []
            if not current:
                start = r['_addr']
            current.append(r)
        if current:
            chunks.append(current)
        for chunk in chunks:
            base = chunk[0]['_addr']
            m = measures(chunk, done)
            report_units.append({
                'name': f"{category_id(owner)}/{base:08X}-{chunk[-1]['_addr'] + chunk[-1]['_size']:08X}",
                'measures': m,
                'sections': [{
                    'name': chunk[0]['section'],
                    'size': m['total_code'],
                    'fuzzy_match_percent': m['fuzzy_match_percent'],
                    'metadata': {'virtual_address': str(base)},
                }],
                'functions': [{
                    'name': f"func_{r['_addr']:08X}",
                    'size': str(r['_size']),
                    'fuzzy_match_percent': done.get(r['_addr'], 0.0),
                    'metadata': {'virtual_address': str(r['_addr'])},
                    'address': str(r['_addr'] - base),
                } for r in chunk],
                'metadata': {'complete': False, 'progress_categories': [category_id(owner)],
                             'source_path': os.path.relpath(project.matched_dir, project.root)},
            })
        categories.append({'id': category_id(owner), 'name': owner, 'measures': measures(mine, done, len(chunks))})
        everything += mine
    return {
        'measures': measures(everything, done, len(report_units)),
        'units': report_units,
        'version': REPORT_VERSION,
        'categories': categories,
    }


def main(argv=None):
    from . import project as project_module
    ap = argparse.ArgumentParser(prog='xbx report', description=__doc__.splitlines()[0])
    ap.add_argument('-o', '--output')
    ap.add_argument('--owners', nargs='*', help="owners to report (default: the headline owners; 'all' for every owner)")
    ap.add_argument('--compact', action='store_true')
    args = ap.parse_args(argv)
    try:
        project = project_module.current()
    except project_module.ProjectError as e:
        sys.exit(f'error: {e}')
    report = build(project, args.owners)
    out = args.output or os.path.join(project.root, 'build', 'report', project.version, 'report.json')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(report, f, indent=None if args.compact else 2)
        f.write('\n')
    m = report['measures']
    print(f"{out}: {m['matched_functions']}/{m['total_functions']} functions, "
          f"{m['matched_code']}/{m['total_code']} bytes ({m['matched_code_percent']:.3f}% matched, "
          f"{m['fuzzy_match_percent']:.3f}% fuzzy), {len(report['units'])} unit(s)")


if __name__ == '__main__':
    main()
