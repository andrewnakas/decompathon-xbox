import os

from xbx import project, report

TABLE = '''addr,size,owner,kind,section,conv,parent,calls
00011000,100,game,func,.text,,,
00011070,50,game,func,.text,,,
000110b0,30,game,func,.text,,,
00011100,8,game,eh,.text,,00011000,
00011108,8,game,eh,.text,,00011070 000110b0,
0018cb40,400,lib:d3d8,func,D3D,,,
'''
CONFIG = '''[project]
name = "t"
version = "V1"
[image]
code_sections = [".text", "D3D"]
[owners]
headline = ["game"]
[owners.sections]
D3D = "lib:d3d8"
'''


def new_project(tmp_path):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config' / 'functions.csv').write_text(TABLE)
    (tmp_path / 'xbx.toml').write_text(CONFIG)
    for folder in ('src/funcs', 'src/nonmatching'):
        os.makedirs(tmp_path / folder)
    (tmp_path / 'src/funcs/func_00011000.cpp').write_text('void func_00011000() {}\n')
    (tmp_path / 'src/nonmatching/func_00011070.cpp').write_text('/* fuzzy: 50.0 */\nvoid func_00011070() {}\n')
    return project.Project(str(tmp_path))


def test_report_counts(tmp_path):
    r = report.build(new_project(tmp_path))
    m = r['measures']
    assert r['version'] == 2 and m['total_functions'] == 5 and m['total_code'] == '196'
    assert m['matched_functions'] == 2 and m['matched_code'] == '108'      # the function and its own eh stub
    assert abs(m['fuzzy_match_percent'] - 100.0 * (108 + 25) / 196) < 1e-9
    assert [c['id'] for c in r['categories']] == ['game']
    names = {f['name']: f['fuzzy_match_percent'] for u in r['units'] for f in u['functions']}
    assert names['func_00011070'] == 50.0 and names['func_00011108'] == 0.0


def test_report_all_owners(tmp_path):
    r = report.build(new_project(tmp_path), ['all'])
    assert [c['id'] for c in r['categories']] == ['game', 'lib_d3d8']
    assert r['measures']['total_code'] == '596'
