"""The covered file: functions matched units carry along, credited by the report and checked by the gate."""
import os

from xbx import covers, match, project as project_module, report


def test_compute_takes_only_func_rows_other_units_place():
    class P:
        rows = {0x11000: {'kind': 'func'}, 0x11100: {'kind': 'func'}, 0x11200: {'kind': 'eh'}}

        def relative(self, path):
            return path
    v = match.Verdict(0x11000, 'src/funcs/func_00011000.cpp', ok=True)
    v.covers = [0x11000, 0x11100, 0x11200, 0x99999]
    bad = match.Verdict(0x12000, 'src/funcs/func_00012000.cpp', ok=False)
    bad.covers = [0x11100]
    assert covers.compute(P(), None, {0x11000: v, 0x12000: bad}) == {0x11100: 'src/funcs/func_00011000.cpp'}


def test_report_credits_carried_functions(tmp_path):
    (tmp_path / 'xbx.toml').write_text('[project]\nname = "t"\n')
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config' / 'functions.csv').write_text(
        'addr,size,owner,kind,section,conv,parent,calls\n'
        '00011000,16,game,func,.text,,,\n00011100,30,game,func,.text,,,\n00011200,8,game,func,.text,,,\n')
    (tmp_path / 'config' / 'covered.csv').write_text(
        'addr,unit\n00011100,src/funcs/func_00011000.cpp\n00011200,src/funcs/func_00012000.cpp\n')
    os.makedirs(tmp_path / 'src' / 'funcs')
    (tmp_path / 'src' / 'funcs' / 'func_00011000.cpp').write_text('int x;\n')
    p = project_module.Project(str(tmp_path))
    done = report.statuses(p)
    assert done.get(0x11000) == 100.0 and done.get(0x11100) == 100.0
    assert 0x11200 not in done                      # its unit is not in the matched tier
