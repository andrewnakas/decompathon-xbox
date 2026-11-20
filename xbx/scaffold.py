"""Start a new game project.

    xbx init DIR --xbe default.xbe --repo OWNER/NAME [--title "Game title"]
                 [--name NAME] [--profile msvc7.0|msvc7.1] [--version LABEL]
                 [--tools-commit SHA]

Writes into DIR everything a matching decompilation needs except the game and
the compiler: xbx.toml (the executable pinned by sha256, its code sections and
their owners), the function table, the Steward config, the gate wrapper, the
pull request template, the workflows, the rules and the guides. DIR must not
hold a project already.

The executable is read (its header, its section table, its code) and linked as
DIR/orig/default.xbe, which .gitignore covers. No game bytes are written.

What it cannot know is left as a first guess and says so: the compiler version
and its flags. STATUS.md in the new project lists what to do before the first
pull request.
"""
import argparse
import collections
import datetime
import hashlib
import os
import re
import struct
import subprocess
import sys
from importlib import resources

from . import xbe as xbe_module

# profile -> (compiler, the product it shipped in, the version cl reports)
PROFILES = {
    'msvc7.0': ('Visual C++ 7.0', 'Visual Studio .NET 2002', '13.00.9466'),
    'msvc7.1': ('Visual C++ 7.1', 'Visual Studio .NET 2003', '13.10.3077'),
}
# Sections that the platform's libraries bring with them, by name.
SECTION_OWNERS = {
    'D3D': 'lib:d3d8', 'D3DX': 'lib:d3dx8', 'DSOUND': 'lib:dsound', 'XGRPH': 'lib:xgraphics', 'XPP': 'lib:xpp',
    'XONLINE': 'lib:xonline', 'XNET': 'lib:xnet', 'WMADEC': 'lib:wmadec', 'XACTENG': 'lib:xact', 'DMUSIC': 'lib:dmusic',
}
PLACEHOLDER = re.compile(r'@@([A-Z][A-Z0-9_]*)@@')
EXECUTABLE = {'tools/gate.sh'}
TOOLS_REPOSITORY = 'https://github.com/andrewnakas/decompathon-xbox'


class ScaffoldError(Exception):
    pass


def call_targets(image, section):
    """{address: count} for every byte sequence in the section that reads as
    `call rel32`. Real calls and accidents alike."""
    data, out = image.section_bytes(section), collections.Counter()
    i = data.find(b'\xe8')
    while i != -1 and i + 5 <= len(data):
        out[(section.va + i + 5 + struct.unpack_from('<i', data, i + 1)[0]) & 0xFFFFFFFF] += 1
        i = data.find(b'\xe8', i + 1)
    return out


def code_sections(image):
    """The names of the sections that hold functions.

    The section flags do not say: data sections carry the executable flag too.
    The code does. Starting from the section the entry point is in, a section
    is code when at least two addresses in it are each the target of two or
    more calls, counting the calls in the section itself and in the sections
    already accepted. A byte scan for the call opcode also finds targets that
    are accidents, but not the same accident twice.
    """
    sections = [s for s in image.sections if not s.name.startswith('$$') and s.rsize]
    targets = {s.name: call_targets(image, s) for s in sections}
    accepted = [s for s in sections if s.va <= image.entry < s.va + s.vsize]
    changed = True
    while changed:
        changed = False
        for s in sections:
            if s in accepted:
                continue
            counts = collections.Counter()
            for source in accepted + [s]:
                for target, n in targets[source.name].items():
                    if s.va <= target < s.va + s.vsize:
                        counts[target] += n
            if sum(1 for n in counts.values() if n >= 2) >= 2:
                accepted.append(s)
                changed = True
    return [s.name for s in sections if s in accepted]


def tools_commit():
    """The commit of these tools: the one they were installed from, else the
    HEAD of the checkout they run from. None when neither can be told."""
    from .gates import toolcheck
    commit, _ = toolcheck.installed_commit()
    if commit:
        return commit
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(here, '.git')):
        r = subprocess.run(['git', '-C', here, 'rev-parse', 'HEAD'], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    return None


def short_name(name):
    short = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return re.sub(r'-(decomp|decompilation)$', '', short) or short


def code_list(names):
    return ', '.join(f'`{n}`' for n in names)


def english_list(items):
    items = list(items)
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]


def values(image, repo, name=None, title=None, profile=None, version=None, commit=None, today=None):
    """Everything the templates ask for that the executable and the command
    line can tell. The function table's numbers are added by `table_values`."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+', repo):
        raise ScaffoldError(f'--repo {repo!r}: expected OWNER/NAME')
    owner, repo_name = repo.split('/')
    name = name or repo_name
    versions = collections.Counter(v for _, v, _ in image.libraries)
    library_version = versions.most_common(1)[0][0] if versions else 'unknown'
    guessed = profile is None
    if guessed:
        try:
            profile = 'msvc7.1' if int(library_version.split('.')[-1]) >= 5000 else 'msvc7.0'
        except ValueError:
            profile = 'msvc7.1'
    if profile not in PROFILES:
        raise ScaffoldError(f'--profile {profile}: known profiles are {", ".join(PROFILES)}')
    compiler, studio, banner = PROFILES[profile]
    code = code_sections(image)
    if not code:
        raise ScaffoldError('no code section found: the entry point is in no section')
    by_name = {s.name: s for s in image.sections}
    owners = {n: SECTION_OWNERS[n] for n in code if n in SECTION_OWNERS}
    try:
        linked = f'{datetime.datetime.fromtimestamp(image.timestamp, datetime.timezone.utc):%Y-%m-%d}'
    except (OverflowError, OSError, ValueError):
        linked = 'on an unreadable date'
    facts = 'Code sections: ' + ', '.join(f'`{n}` (from `{by_name[n].va:#x}`)' for n in code) + '.'
    data = [s for s in image.sections if s.name not in code and not s.name.startswith('$$')]
    if data:
        facts += ' Other sections: ' + ', '.join(f'`{s.name}` (from `{s.va:#x}`)' for s in data) + '.'
    if image.thunks:
        facts += f' The kernel import table ({len(image.thunks)} slots) starts at `{image.thunk_va:#x}`.'
    first = by_name[code[0]]
    lib_sentence = ''
    if owners:
        lib_sentence = (f' The libraries that have sections of their own ({code_list(owners.values())}) are a separate'
                        ' category in the progress report and not lane targets.')
    title_id = f'{image.title_id:08X}'
    return {
        'NAME': name, 'REPO': repo, 'OWNER': owner, 'SHORT': short_name(name),
        'TITLE': title or image.title or name, 'TITLE_ID': title_id, 'VERSION': version or title_id,
        'SHA256': hashlib.sha256(image.data).hexdigest(), 'LINKED': linked, 'LIBRARY_VERSION': library_version,
        'LIBRARIES': english_list(n for n, _, _ in image.libraries) if image.libraries else 'none',
        'PROFILE': profile, 'PROFILE_GUESSED': 'yes' if guessed else '', 'COMPILER': compiler, 'STUDIO': studio,
        'BANNER': banner, 'CFLAGS': '"/O2"',
        'CODE_SECTIONS': ', '.join(f'"{n}"' for n in code),
        'OWNER_SECTIONS': ''.join(['\n[owners.sections]\n'] + [f'"{n}" = "{o}"\n' for n, o in owners.items()]).rstrip('\n')
                          if owners else '',
        'LIB_SENTENCE': lib_sentence, 'SECTION_FACTS': facts,
        'IMAGE_LOW': f'{image.base:#x}', 'IMAGE_HIGH': f'{image.end:#x}',
        'EXAMPLE_FROM': f'{first.va:08X}', 'EXAMPLE_TO': f'{min(first.va + 0x80000, first.va + first.vsize):08X}',
        'TOOLS_PIN': commit or '', 'DATE': (today or datetime.date.today()).isoformat(),
        'FUNCTION_COUNT': '0', 'CONV_COUNT': '0', 'OWNER_TABLE': '',
    }


def table_values(rows, headline=('game',)):
    """The placeholders that describe the function table."""
    owners = collections.OrderedDict()
    for r in rows:
        o = owners.setdefault(r['owner'], collections.Counter())
        o['rows'] += 1
        o['bytes'] += int(r['size'])
        o[r['kind']] += 1
    lines = []
    for owner in sorted(owners, key=lambda o: (o not in headline, o)):
        o = owners[owner]
        kinds = f" ({o['func']:,} kind `func`, {o['eh']:,} kind `eh`)" if o['eh'] else ''
        counts = 'the headline' if owner in headline else 'the library category'
        lines.append(f"| `{owner}` | {o['rows']:,}{kinds} | {o['bytes']:,} | {counts} |")
    return {'FUNCTION_COUNT': f'{len(rows):,}', 'CONV_COUNT': f"{sum(1 for r in rows if r['conv']):,}",
            'OWNER_TABLE': '\n'.join(lines),
            'CFLAGS': '"/O2", "/GX"' if any(r['kind'] == 'eh' for r in rows) else '"/O2"'}


def template_files(node=None, prefix=''):
    """[(path relative to the template, text)], in a fixed order."""
    node = node or resources.files('xbx').joinpath('template')
    out = []
    for child in sorted(node.iterdir(), key=lambda c: c.name):
        if child.name == '__pycache__':
            continue
        if child.is_dir():
            out += template_files(child, prefix + child.name + '/')
        else:
            out.append((prefix + child.name, child.read_text(encoding='utf-8')))
    return out


def destination(rel, vals):
    if rel == 'dot-gitignore':
        return '.gitignore'
    if rel.startswith('dot-github/'):
        return '.github/' + rel[len('dot-github/'):]
    if rel == 'tools/linux/steward.service':
        return f"tools/linux/{vals['SHORT']}-steward.service"
    return rel


def fill(text, vals, where):
    def substitute(m):
        if m.group(1) not in vals:
            raise ScaffoldError(f'{where}: the template asks for {m.group(0)}, which has no value')
        return vals[m.group(1)]
    return PLACEHOLDER.sub(substitute, text)


def render(vals):
    """{path in the project: text} for every template file."""
    return {destination(rel, vals): fill(text, vals, rel) for rel, text in template_files()}


def write(root, files):
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        if rel in EXECUTABLE:
            os.chmod(path, 0o755)


def create(root, xbe_path, repo, **options):
    """Writes the project and returns (values, table rows, the files written)."""
    from . import inventory, project as project_module
    root = os.path.abspath(root)
    if os.path.exists(os.path.join(root, project_module.CONFIG)):
        raise ScaffoldError(f'{root} already holds a project ({project_module.CONFIG})')
    try:
        image = xbe_module.Xbe(xbe_path)
    except OSError as e:
        raise ScaffoldError(f'{xbe_path}: {e.strerror}') from e
    except ValueError as e:
        raise ScaffoldError(str(e)) from e
    vals = values(image, repo, **options)
    if not vals['TOOLS_PIN']:
        raise ScaffoldError('cannot tell which commit of the tools is running: pass --tools-commit SHA '
                            f'(a commit of {TOOLS_REPOSITORY})')
    # the function table needs a project to read, so xbx.toml goes first
    write(root, {k: v for k, v in render(vals).items() if k == project_module.CONFIG})
    os.makedirs(os.path.join(root, 'orig'), exist_ok=True)
    link = os.path.join(root, 'orig', 'default.xbe')
    if not os.path.lexists(link):
        os.symlink(os.path.abspath(xbe_path), link)
    project = project_module.Project(root)
    rows, _ = inventory.build(project)
    vals.update(table_values(rows, project.headline))
    files = render(vals)
    files['config/functions.csv'] = inventory.render(rows)
    write(root, files)
    for folder in ('src/funcs', 'src/nonmatching', 'toolchain'):
        os.makedirs(os.path.join(root, folder), exist_ok=True)
    return vals, rows, sorted(files)


def next_steps(root, vals):
    name = os.path.relpath(root)
    guess = ' (a guess from the library version: the calibration decides)' if vals['PROFILE_GUESSED'] else ''
    return f'''next:
  1. cd {name} && git init -b main
  2. ln -s /path/to/your/msvc toolchain/msvc      your own {vals['COMPILER']}: bin/ include/ lib/{guess}
     uv venv && uv pip install -r requirements.txt && .venv/bin/xbx cc --setup
  3. match about ten small functions by hand (.venv/bin/xbx asm --small 30, .venv/bin/xbx match) and pin the
     compiler flags on them in xbx.toml; STATUS.md says how
  4. .venv/bin/xbx gate toolcheck --record        records the compiler's checksums and the canary
  5. tools/gate.sh all                            every gate must end OK
  6. push to https://github.com/{vals['REPO']}, then set up merging and the progress page:
     {TOOLS_REPOSITORY}/blob/main/docs/NEW_PROJECT.md'''


def main(argv=None):
    ap = argparse.ArgumentParser(prog='xbx init', description=__doc__.splitlines()[0])
    ap.add_argument('directory')
    ap.add_argument('--xbe', required=True, help='your own default.xbe')
    ap.add_argument('--repo', required=True, metavar='OWNER/NAME', help='the GitHub repository the project will live in')
    ap.add_argument('--name', help='project name (default: the repository name)')
    ap.add_argument('--title', help="the game's title (default: the title in the executable's certificate)")
    ap.add_argument('--profile', choices=sorted(PROFILES), help='compiler (default: a guess from the library version)')
    ap.add_argument('--version', help='label of the progress report (default: the title id)')
    ap.add_argument('--tools-commit', metavar='SHA', help='the commit of these tools to pin (default: the one running)')
    args = ap.parse_args(argv)
    from . import project as project_module
    try:
        vals, rows, files = create(args.directory, args.xbe, args.repo, name=args.name, title=args.title,
                                   profile=args.profile, version=args.version,
                                   commit=args.tools_commit or tools_commit())
    except (ScaffoldError, project_module.ProjectError) as e:
        sys.exit(f'error: {e}')
    root = os.path.abspath(args.directory)
    print(f"{vals['TITLE']} ({vals['TITLE_ID']}), sha256 {vals['SHA256'][:16]}..., libraries {vals['LIBRARY_VERSION']}")
    print(f"code sections: {vals['CODE_SECTIONS']}")
    print(f"function table: {vals['FUNCTION_COUNT']} functions, {vals['CONV_COUNT']} with a custom calling convention")
    print(f"wrote {len(files)} files to {root}; tools pinned at {vals['TOOLS_PIN'][:12]}")
    print(next_steps(root, vals))


if __name__ == '__main__':
    main()
