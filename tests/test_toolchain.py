"""The idle process that keeps a Wine prefix warm: when one is started and
when the running one is left alone. No Wine is needed: process creation is
replaced."""
import os
import subprocess

from xbx import toolchain


class Recorder:
    """Stands in for subprocess.Popen and subprocess.run."""

    def __init__(self, pid):
        self.pid, self.started, self.ran = pid, [], []

    def popen(self, command, **options):
        self.started.append(command)
        return self

    def run(self, command, **options):
        self.ran.append(command)


def compiler(tmp_path, monkeypatch, pid=None, keep=900):
    prefix = tmp_path / 'prefix'
    prefix.mkdir(exist_ok=True)
    recorder = Recorder(os.getpid() if pid is None else pid)
    monkeypatch.setattr(toolchain, 'WINDOWS', False)
    monkeypatch.setattr(toolchain, 'KEEP_WARM', keep)
    monkeypatch.setattr(toolchain.subprocess, 'Popen', recorder.popen)
    monkeypatch.setattr(toolchain.subprocess, 'run', recorder.run)
    return toolchain.Compiler(str(tmp_path / 'msvc'), str(prefix)), recorder, prefix / toolchain.WARM_STAMP


def test_the_first_run_starts_one_and_pays_the_cold_start_once(tmp_path, monkeypatch):
    c, r, stamp = compiler(tmp_path, monkeypatch)
    assert c.keep_warm(now=1000.0) is True
    assert r.started == [['sh', '-c', 'sleep 900 | wine cmd.exe']] and r.ran == [['wine', 'cmd.exe', '/c', 'exit']]
    assert stamp.read_text().split() == ['1900', str(os.getpid())]


def test_a_running_one_is_left_alone_until_half_its_time_is_gone(tmp_path, monkeypatch):
    c, r, stamp = compiler(tmp_path, monkeypatch)
    c.keep_warm(now=1000.0)
    assert c.keep_warm(now=1400.0) is False and len(r.started) == 1
    assert c.keep_warm(now=1500.0) is True and len(r.started) == 2      # 400 s left of 900
    assert len(r.ran) == 1                                               # the prefix was warm: no second warm-up
    assert stamp.read_text().split()[0] == '2400'


def test_one_that_died_is_replaced(tmp_path, monkeypatch):
    gone = subprocess.Popen(['true'])
    gone.wait()
    c, r, stamp = compiler(tmp_path, monkeypatch, pid=gone.pid)
    c.keep_warm(now=1000.0)
    assert c.keep_warm(now=1001.0) is True and len(r.started) == 2 and len(r.ran) == 2


def test_it_can_be_turned_off_and_needs_a_prefix(tmp_path, monkeypatch):
    c, r, stamp = compiler(tmp_path, monkeypatch, keep=0)
    assert c.keep_warm(now=1000.0) is False and not r.started and not stamp.exists()
    c, r, stamp = compiler(tmp_path, monkeypatch)
    c.wineprefix = str(tmp_path / 'no-such-prefix')
    assert c.keep_warm(now=1000.0) is False and not r.started
