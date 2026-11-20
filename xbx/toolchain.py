"""Runs the pinned Microsoft compiler: under Wine on Linux and macOS, directly
on Windows.

    xbx cc <source> <object> [flags...]     compile one file with the project's flags
    xbx cc --banner                         print the compiler's version banner
    xbx cc --setup                          create the Wine prefix

The compiler directory holds bin/ (cl.exe, c1.dll, c1xx.dll, c2.dll and the
runtime DLLs they load), include/ and lib/, taken from your own licensed
Visual Studio. Nothing here downloads a compiler.

Each compile runs in a private temporary directory, so parallel compiles never
share scratch files, and as its own process group, so a timeout kills the
whole tree. Every run has a timeout and is retried.

Two things about Wine shape the rest:

- When no process of a Wine prefix is running, the next one starts the server,
  which loads the registry, and wineboot, which starts the services: seconds on
  a quiet machine, minutes on a loaded one, and once per compile when compiles
  come one at a time. So one idle process is kept in the prefix
  (Compiler.keep_warm). It ends by itself XBX_WINE_KEEP_WARM seconds (default
  900) after it was started; 0 turns it off.
- Those services inherit the standard output of whichever process started
  them and keep it open for as long as they live. A compile read through a pipe
  would therefore not end until the services did, long after cl.exe had
  finished. So a run writes its output to a file in its temporary directory,
  and only the compiler process itself is waited for.
"""
import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

TIMEOUT = int(os.environ.get('XBX_TOOL_TIMEOUT', '120'))
ATTEMPTS = int(os.environ.get('XBX_TOOL_ATTEMPTS', '3'))
KEEP_WARM = int(os.environ.get('XBX_WINE_KEEP_WARM', '900'))
WARM_STAMP = '.xbx-warm'     # in the prefix: "<when the idle process ends> <its process id>"
WINDOWS = sys.platform in ('win32', 'cygwin', 'msys')
PATH_SWITCHES = ('/Fo', '/Fe', '/Fd', '/Fa', '/FI', '/I', '/OUT:', '/MAP:')
BANNER = re.compile(r'Version\s+(\d+\.\d+\.\d+(?:\.\d+)?)')


# the compiler driver, both front ends, the back end, and the runtime they compute constants with
CODEGEN_FILES = ('cl.exe', 'c1.dll', 'c1xx.dll', 'c2.dll', 'msvcr70.dll', 'msvcr71.dll', 'mspdb70.dll', 'mspdb71.dll',
                 'msobj10.dll', 'msobj71.dll')


class ToolError(Exception):
    """A tool could not run, timed out, or exited with an error (its output is the message)."""


def wine_path(path):
    """An absolute host path as Wine's Z: drive sees it."""
    return 'Z:' + os.path.abspath(path).replace('/', '\\')


def tool_arg(arg):
    """Under Wine, cl and link read a leading '/' as a switch, so absolute host
    paths, alone or after a switch such as /Fo or /I, go over as Z: paths."""
    if WINDOWS:
        return arg
    for switch in PATH_SWITCHES:
        if arg.startswith(switch + '/') and not arg.startswith(switch + '//'):
            return switch + wine_path(arg[len(switch):])
    if arg.startswith('/') and os.path.dirname(arg) not in ('', '/') and os.path.isdir(os.path.dirname(arg)):
        return wine_path(arg)
    return arg


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True      # it exists and is someone else's
    return True


class Compiler:
    _warm_lock = threading.Lock()

    def __init__(self, msvc, wineprefix=None, timeout=TIMEOUT, attempts=ATTEMPTS):
        self.msvc = os.path.abspath(msvc)
        self.wineprefix = os.path.abspath(wineprefix) if wineprefix else None
        self.timeout, self.attempts = timeout, attempts

    def keep_warm(self, now=None):
        """Makes sure an idle process is running in the Wine prefix, so that the
        next compile does not start Wine cold. Returns True when it started one.

        The stamp file says when the current one ends. A new one is started
        when none is alive or less than half its time is left, so the prefix
        stays warm for as long as compiles keep coming and goes quiet
        KEEP_WARM seconds after the last one. After starting the first, one
        small command is run to the end, so that the cold start is paid once,
        by one process, before several compiles begin together."""
        if WINDOWS or KEEP_WARM <= 0 or not self.wineprefix or not os.path.isdir(self.wineprefix):
            return False
        stamp = os.path.join(self.wineprefix, WARM_STAMP)
        with Compiler._warm_lock:
            now = time.time() if now is None else now
            try:
                with open(stamp, encoding='utf-8') as f:
                    until, pid = f.read().split()[:2]
                until, running = float(until), alive(int(pid))
            except (OSError, ValueError):
                until, running = 0.0, False
            if running and until - now > KEEP_WARM / 2:
                return False
            env = dict(os.environ, WINEPREFIX=self.wineprefix, WINEDEBUG='-all')
            env.setdefault('WINEDLLOVERRIDES', 'mscoree=d;mshtml=d;winemenubuilder.exe=d')
            quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                # cmd.exe waits for input that never comes, until sleep ends and closes the pipe
                keeper = subprocess.Popen(['sh', '-c', f'sleep {KEEP_WARM} | wine cmd.exe'], cwd='/', env=env,
                                          start_new_session=True, **quiet)
                with open(stamp, 'w', encoding='utf-8') as f:
                    f.write(f'{now + KEEP_WARM:.0f} {keeper.pid}\n')
            except OSError:
                return False
            if not running:
                try:
                    subprocess.run(['wine', 'cmd.exe', '/c', 'exit'], cwd='/', env=env, timeout=self.timeout, **quiet)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            return True

    def tool(self, name):
        path = os.path.join(self.msvc, 'bin', name)
        if not os.path.exists(path):
            raise ToolError(f'{path}: missing. The compiler directory needs bin/ from your own copy of '
                            'Visual Studio (set XBX_MSVC, or put it at toolchain/msvc).')
        return path

    def env(self, tmp):
        env = dict(os.environ, TMP=tmp if WINDOWS else wine_path(tmp), TEMP=tmp if WINDOWS else wine_path(tmp))
        native = (lambda p: p) if WINDOWS else wine_path
        # Only what the project passes with /I is visible to a unit: the compiler's own
        # headers are not, so an object depends on nothing but the project's tree.
        env['INCLUDE'] = ''
        env['LIB'] = native(os.path.join(self.msvc, 'lib'))
        if not WINDOWS:
            env['WINEDEBUG'] = '-all'
            env.setdefault('WINEDLLOVERRIDES', 'mscoree=d;mshtml=d;winemenubuilder.exe=d')
            if self.wineprefix:
                env['WINEPREFIX'] = self.wineprefix
        return env

    def setup(self):
        """Creates the Wine prefix (nothing to do on Windows)."""
        if WINDOWS or not self.wineprefix or os.path.exists(os.path.join(self.wineprefix, 'system.reg')):
            return
        os.makedirs(os.path.dirname(self.wineprefix), exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='xbx') as tmp:
            subprocess.run(['wineboot', '-i'], env=self.env(tmp), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=600, check=False)
            subprocess.run(['wineserver', '-w'], env=self.env(tmp), timeout=600, check=False)

    def _once(self, command, cwd, env, log):
        """One run: (exit status, output). The output goes to the file `log`;
        only the process itself is waited for (see the module's notes)."""
        posix = os.name == 'posix'
        with open(log, 'wb') as out:
            p = subprocess.Popen(command, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=posix)
        try:
            p.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            if posix:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                p.kill()
            p.wait()
            raise
        with open(log, 'rb') as f:
            return p.returncode, f.read().decode('utf-8', errors='replace').replace('\r\n', '\n')

    def run(self, name, args, cwd=None, check=True):
        """Runs bin/<name> with args and returns (exit status, output)."""
        exe = self.tool(name)
        command = ([exe] if WINDOWS else ['wine', exe]) + [tool_arg(a) for a in args]
        self.keep_warm()
        tmp = tempfile.mkdtemp(prefix='xbxcc')
        try:
            for attempt in range(1, self.attempts + 1):
                try:
                    code, out = self._once(command, cwd or tmp, self.env(tmp), os.path.join(tmp, 'output.txt'))
                    break
                except FileNotFoundError as e:
                    raise ToolError(f'cannot run {command[0]}: {e.strerror}') from e
                except subprocess.TimeoutExpired:
                    if attempt == self.attempts:
                        raise ToolError(f'{name} gave no result in {self.timeout}s, {self.attempts} times '
                                        '(XBX_TOOL_TIMEOUT, XBX_TOOL_ATTEMPTS)')
                    print(f'{name}: no result after {self.timeout}s, retrying', file=sys.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if check and code:
            raise ToolError(f'{name} failed ({code}):\n{out}')
        return code, out

    def banner(self):
        """cl's version, e.g. '13.00.9466' (ToolError when it cannot be read)."""
        _, out = self.run('cl.exe', [], check=False)
        m = BANNER.search(out)
        if not m:
            raise ToolError(f'cl printed no version banner:\n{out[:400]}')
        return m.group(1)

    def compile(self, source, obj, flags=(), includes=()):
        """Compiles source to obj. Raises ToolError with the compiler's messages."""
        source, obj = os.path.abspath(source), os.path.abspath(obj)
        os.makedirs(os.path.dirname(obj), exist_ok=True)
        if os.path.exists(obj):
            os.remove(obj)
        args = ['/nologo', '/c', *flags, *('/I' + os.path.abspath(i) for i in includes), source, '/Fo' + obj]
        _, out = self.run('cl.exe', args)
        if not os.path.exists(obj) or os.path.getsize(obj) == 0:
            raise ToolError(f'cl produced no object for {source}:\n{out}')
        return out

    def digests(self):
        """{file under the compiler directory's bin/: sha256} for the files code generation depends on."""
        out = {}
        bin_dir = os.path.join(self.msvc, 'bin')
        present = {name.lower(): name for name in os.listdir(bin_dir)} if os.path.isdir(bin_dir) else {}
        for name in CODEGEN_FILES:
            if name in present:
                with open(os.path.join(bin_dir, present[name]), 'rb') as f:
                    out[name] = hashlib.sha256(f.read()).hexdigest()
        if 'cl.exe' not in out:
            raise ToolError(f'no cl.exe in {bin_dir}: point XBX_MSVC (or toolchain/msvc) at a directory holding '
                            'bin/, from your own copy of the compiler')
        return out


def for_project(project):
    return Compiler(project.msvc, project.wineprefix)


def main(argv=None):
    from . import project as project_module
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        project = project_module.current()
        compiler = for_project(project)
        if argv[:1] == ['--setup']:
            compiler.setup()
            print(compiler.wineprefix)
        elif argv[:1] == ['--banner']:
            print(compiler.banner())
        elif len(argv) >= 2:
            source, obj, flags = argv[0], argv[1], argv[2:]
            print(compiler.compile(source, obj, flags or project.cflags, project.includes), end='')
        else:
            sys.exit(__doc__.strip().split('\n\n')[1])
    except (ToolError, project_module.ProjectError) as e:
        sys.exit(f'error: {e}')


if __name__ == '__main__':
    main()
