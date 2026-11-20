"""The project a command works on: its root, its xbx.toml, its XBE, its
function table and its unit files. One copy for every tool.

The root is $XBX_ROOT, else the nearest directory at or above the current one
that holds xbx.toml. Machine-local paths never go in xbx.toml (it is
committed); they come from the environment, else from defaults inside the
root that .gitignore covers:

    XBX_XBE          the retail executable        default <root>/<image.path>
    XBX_MSVC         compiler dir (bin, include, lib)     <root>/toolchain/msvc
    XBX_WINEPREFIX   Wine prefix for the compiler         <root>/toolchain/wineprefix
    XBX_OUT          work files, caches, variants         <root>/build/xbx

xbx.toml:

    [project]
    name = "jsrf-decomp"
    version = "5345000A"              # label of the progress report

    [image]
    path = "orig/default.xbe"
    sha256 = "..."                    # every tool refuses another executable
    code_sections = [".text", "D3D"]  # sections that hold functions

    [compiler]
    profile = "msvc7.0"
    banner = "13.00.9466"             # must appear in cl's banner
    cflags = ["/O2"]
    allowed_cflags = ["/O1", "/O2"]   # what a unit's /* cflags: */ line may use
    includes = ["include", "include/game", "include/sdk"]

    [layout]
    functions = "config/functions.csv"
    matched = "src/funcs"
    nonmatching = "src/nonmatching"
    unit_glob = "func_*.cpp"
    symbols = "config/symbols.csv"    # optional: name,addr rows shared by every unit

    [owners]
    headline = ["game"]               # owners the headline progress counts
    [owners.sections]                 # code section -> owner of everything in it
    D3D = "lib:d3d8"
    [[owners.ranges]]                 # explicit ranges, applied last
    start = "0x00140000"              # (made-up numbers)
    end = "0x00148000"
    owner = "lib:example"

    [names]
    folded = ["0x00011c90"]           # functions the linker folded together (made-up number):
                                      # units may also call it func_00011C90_1, _2, ... (units.py)
"""
import csv
import functools
import hashlib
import os
import re
import tomllib

from . import xbe as xbe_module

CONFIG = 'xbx.toml'
UNIT_ADDRESS = re.compile(r'func_([0-9A-Fa-f]{8})\.(?:cpp|c)$')


class ProjectError(Exception):
    pass


def find_root(start=None):
    env = os.environ.get('XBX_ROOT')
    if env:
        return os.path.abspath(env)
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(d, CONFIG)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise ProjectError(f'no {CONFIG} at or above {os.path.abspath(start or os.getcwd())} (set XBX_ROOT)')
        d = parent


class Project:
    def __init__(self, root=None):
        self.root = find_root(root) if root is None or not os.path.exists(os.path.join(root, CONFIG)) \
            else os.path.abspath(root)
        with open(os.path.join(self.root, CONFIG), 'rb') as f:
            self.config = tomllib.load(f)
        c = self.config
        self.name = c.get('project', {}).get('name', os.path.basename(self.root))
        self.version = c.get('project', {}).get('version', 'main')
        image = c.get('image', {})
        self.xbe_path = os.environ.get('XBX_XBE') or self.path(image.get('path', 'orig/default.xbe'))
        self.xbe_sha256 = image.get('sha256', '')
        self.code_sections = list(image.get('code_sections', ['.text']))
        comp = c.get('compiler', {})
        self.profile = comp.get('profile', 'msvc')
        self.banner = comp.get('banner', '')
        self.cflags = list(comp.get('cflags', ['/O2']))
        self.allowed_cflags = list(comp.get('allowed_cflags', []))
        self.includes = [self.path(p) for p in comp.get('includes', ['include'])]
        self.msvc = os.environ.get('XBX_MSVC') or self.path('toolchain/msvc')
        self.wineprefix = os.environ.get('XBX_WINEPREFIX') or self.path('toolchain/wineprefix')
        layout = c.get('layout', {})
        self.functions_csv = self.path(layout.get('functions', 'config/functions.csv'))
        self.matched_dir = self.path(layout.get('matched', 'src/funcs'))
        self.nonmatching_dir = self.path(layout.get('nonmatching', 'src/nonmatching'))
        self.unit_glob = layout.get('unit_glob', 'func_*.cpp')
        self.symbols_csv = self.path(layout['symbols']) if layout.get('symbols') else None
        owners = c.get('owners', {})
        self.headline = list(owners.get('headline', ['game']))
        self.section_owners = dict(owners.get('sections', {}))
        self.owner_ranges = [(int(r['start'], 16), int(r['end'], 16), r['owner']) for r in owners.get('ranges', [])]
        self.folded = frozenset(int(a, 16) for a in c.get('names', {}).get('folded', []))
        self.out = os.path.abspath(os.environ.get('XBX_OUT') or self.path('build/xbx'))

    def path(self, relative):
        return relative if os.path.isabs(relative) else os.path.join(self.root, relative)

    def relative(self, path):
        return os.path.relpath(os.path.abspath(path), self.root)

    @functools.cached_property
    def image(self):
        """The retail XBE, refused when it is not the one the project pins."""
        if not os.path.exists(self.xbe_path):
            raise ProjectError(f'{self.xbe_path}: missing. Put your own {os.path.basename(self.xbe_path)} there '
                               'or set XBX_XBE.')
        try:
            image = xbe_module.Xbe(self.xbe_path)
        except (OSError, ValueError) as e:
            raise ProjectError(str(e)) from e
        digest = hashlib.sha256(image.data).hexdigest()
        if self.xbe_sha256 and digest != self.xbe_sha256:
            raise ProjectError(f'{self.xbe_path} is not the executable this project matches '
                               f'(sha256 {digest}, expected {self.xbe_sha256})')
        return image

    @functools.cached_property
    def rows(self):
        """{address: row} of the function table. Rows keep every column as text;
        'addr' and 'size' are also parsed into row['_addr'], row['_size']."""
        if not os.path.exists(self.functions_csv):
            raise ProjectError(f'{self.functions_csv}: missing (run xbx inventory)')
        out = {}
        with open(self.functions_csv, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                r['_addr'] = int(r['addr'], 16)
                r['_size'] = int(r['size'])
                out[r['_addr']] = r
        return out

    @functools.cached_property
    def starts(self):
        return frozenset(self.rows)

    def retail(self, addr, size=None):
        return self.image.read(addr, self.rows[addr]['_size'] if size is None else size)

    def unit_path(self, addr, tier='matched'):
        directory = self.matched_dir if tier == 'matched' else self.nonmatching_dir
        return os.path.join(directory, self.unit_glob.replace('*', f'{addr:08X}'))

    def units(self, tier='matched'):
        """Sorted (address, path) of the unit files of a tier."""
        import glob
        directory = self.matched_dir if tier == 'matched' else self.nonmatching_dir
        found = []
        for p in glob.glob(os.path.join(directory, self.unit_glob)):
            m = UNIT_ADDRESS.search(os.path.basename(p))
            if m:
                found.append((int(m.group(1), 16), p))
        return sorted(found)

    @functools.cached_property
    def shared_symbols(self):
        """{name: address} from the optional shared symbol table."""
        out = {}
        if self.symbols_csv and os.path.exists(self.symbols_csv):
            with open(self.symbols_csv, newline='', encoding='utf-8') as f:
                for r in csv.DictReader(f):
                    out[r['name']] = int(r['addr'], 16)
        return out

    def workdir(self, addr):
        d = os.path.join(self.out, f'{addr:08x}')
        os.makedirs(d, exist_ok=True)
        return d


@functools.lru_cache(None)
def current():
    return Project()


def unit_address(path):
    """The address a unit file is named for, or None."""
    m = UNIT_ADDRESS.search(os.path.basename(path))
    return int(m.group(1), 16) if m else None
