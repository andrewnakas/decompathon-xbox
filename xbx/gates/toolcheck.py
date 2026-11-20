"""toolcheck: the judge is the machine it claims to be.

  - the game's executable has the sha256 that xbx.toml names
  - every compiler file has the sha256 recorded in toolchain/checksums.sha256
  - the compiler's banner is the version that xbx.toml names
  - a fixed source file (tests/fixtures/canary.cpp) compiles to the object
    recorded in tests/fixtures/canary.sha256: the code, the constants, the
    relocations and the compiler's own build stamp, so a different compiler,
    different flags or a different emulation layer result shows here first
  - the installed tools are the commit that requirements.txt pins

    xbx gate toolcheck --record     write the two recorded files (a maintainer's
                                    act: it defines what the judge must be)
"""
import hashlib
import json
import os
import re
import shutil
import subprocess

from .. import coff, project as project_module, toolchain
from ..toolchain import ToolError
from .common import Report

CHECKSUMS = 'toolchain/checksums.sha256'
CANARY = 'tests/fixtures/canary.cpp'
CANARY_SUM = 'tests/fixtures/canary.sha256'
PIN = re.compile(r'^decompathon-xbox\s*@\s*git\+\S+?@([0-9a-f]{7,40})\s*$', re.M)


def object_digest(obj):
    """One hash of everything in an object that reaches the image."""
    names = {s.index: s.name for s in obj.symbols}
    h = hashlib.sha256()
    for sym in obj.symbols:
        if sym.name == '@comp.id':
            h.update(f'comp.id {sym.value:#x}\n'.encode())
    for sec in obj.sections:
        if sec.is_info:
            continue
        h.update(f'section {sec.name} {sec.flags:#x} {sec.comdat} {sec.size}\n'.encode())
        h.update(sec.data)
        for r in sec.relocations:
            h.update(f'reloc {r.offset:#x} {r.type} {names.get(r.symbol, "?")}\n'.encode())
    for sym in obj.symbols:
        if sym.section > 0 and not obj.section(sym.section).is_info:
            h.update(f'symbol {sym.name} {obj.section(sym.section).name} {sym.value:#x} {sym.storage}\n'.encode())
    return h.hexdigest()


def canary_digest(project, compiler):
    out = os.path.join(project.out, 'toolcheck')
    os.makedirs(out, exist_ok=True)
    obj = os.path.join(out, f'canary.{os.getpid()}.obj')
    try:
        compiler.compile(project.path(CANARY), obj, project.cflags, project.includes)
        return object_digest(coff.load(obj))
    finally:
        if os.path.exists(obj):
            os.remove(obj)


def installed_commit():
    """(commit or None, True when the tools are an editable checkout)."""
    from importlib import metadata
    try:
        raw = metadata.distribution('decompathon-xbox').read_text('direct_url.json')
    except metadata.PackageNotFoundError:
        return None, False
    info = json.loads(raw) if raw else {}
    return info.get('vcs_info', {}).get('commit_id'), bool(info.get('dir_info', {}).get('editable'))


def run(project, record=False):
    report = Report('toolcheck', project)
    try:
        image = project.image
        print(f'image     {project.relative(project.xbe_path)}  sha256 {image.sha256()[:16]}...  ok')
    except project_module.ProjectError as e:
        report.problem(None, str(e))
    compiler = toolchain.for_project(project)
    sums = project.path(CHECKSUMS)
    try:
        digests = compiler.digests()
    except (ToolError, OSError) as e:
        report.problem(None, f'compiler files: {e}')
        return report.finish()
    if record:
        os.makedirs(os.path.dirname(sums), exist_ok=True)
        with open(sums, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(f'{digest}  {name}\n' for name, digest in sorted(digests.items()))
    recorded = {}
    if os.path.exists(sums):
        with open(sums, encoding='utf-8') as f:
            recorded = {line.split()[1]: line.split()[0] for line in f if line.strip()}
    else:
        report.problem(sums, 'missing: a maintainer records it with `xbx gate toolcheck --record`')
    for name in sorted(set(recorded) | set(digests)):
        if name not in digests:
            report.problem(sums, f'compiler file {name} is missing from {project.relative(compiler.msvc)}/bin')
        elif name not in recorded:
            report.problem(sums, f'compiler file {name} is not in the recorded list')
        elif recorded[name] != digests[name]:
            report.problem(sums, f'compiler file {name} is not the recorded one')
    print(f'compiler  {len(digests)} files checked')
    try:
        banner = compiler.banner()
        print(f'banner    {banner}')
        if project.banner and banner != project.banner:
            report.problem(None, f'the compiler is version {banner}, xbx.toml names {project.banner}')
        digest = canary_digest(project, compiler)
        canary = project.path(CANARY_SUM)
        if record:
            with open(canary, 'w', encoding='utf-8', newline='\n') as f:
                f.write(f'{digest}  canary.cpp\n')
        want = open(canary, encoding='utf-8').read().split()[0] if os.path.exists(canary) else None
        if want is None:
            report.problem(canary, 'missing: a maintainer records it with `xbx gate toolcheck --record`')
        elif want != digest:
            report.problem(canary, 'the canary compiles to a different object than the recorded one '
                                   '(another compiler, other flags, or a changed header)')
        else:
            print('canary    ok')
    except ToolError as e:
        report.problem(None, f'the compiler does not run: {str(e).splitlines()[0] if str(e) else e}')
    wine = shutil.which('wine')
    if wine:
        version = subprocess.run([wine, '--version'], capture_output=True, text=True).stdout.strip()
        print(f'wine      {version}')
    requirements = project.path('requirements.txt')
    pin = PIN.search(open(requirements, encoding='utf-8').read()) if os.path.exists(requirements) else None
    commit, editable = installed_commit()
    if pin and commit:
        if not commit.startswith(pin.group(1)) and not pin.group(1).startswith(commit):
            report.problem(requirements, f'pins the tools at {pin.group(1)[:12]}, installed is {commit[:12]}: '
                                         'run tools/gate.sh, which installs the pinned commit')
        else:
            print(f'tools     decompathon-xbox {commit[:12]} (pinned)')
    elif pin and os.environ.get('XBX_REQUIRE_PIN'):
        report.problem(requirements, 'the tools are not installed from the pinned commit')
    else:
        print('tools     ' + ('an editable checkout: the pin is not checked' if editable else 'no pin to check'))
    return report.finish()
