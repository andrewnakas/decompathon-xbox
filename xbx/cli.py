"""The `xbx` command: one entry point, one subcommand per tool.

    xbx <command> [args...]        xbx help
"""
import importlib
import sys

# command -> (module, one-line help). Modules are imported on use.
COMMANDS = {
    'init':       ('xbx.scaffold', 'start a new game project from your own XBE'),
    'xbe':        ('xbx.xbe', 'summarise an XBE: sections, libraries, kernel imports'),
    'xiso':       ('xbx.xiso', 'list or extract the files of an Xbox disc image'),
    'functions':  ('xbx.functions', 'count the functions function discovery finds in an XBE'),
    'ltcg-probe': ('xbx.ltcg_probe', 'measure link-time code generation in an XBE'),
    'inventory':  ('xbx.inventory', "write the project's function table from its XBE"),
    'cc':         ('xbx.toolchain', 'compile one source with the pinned compiler'),
    'match':      ('xbx.match', 'compile units and compare them with the retail code'),
    'asm':        ('xbx.asmview', "print a function's retail disassembly"),
    'names':      ('xbx.names', 'the declarations the tree has settled on, by address'),
    'report':     ('xbx.report', 'write the objdiff-format progress report'),
    'gate':       ('xbx.gates.run', 'run a merge gate: toolcheck, scan, split, match, build'),
    'score':      ('xbx.workbench.cli', 'workbench: score source variants of a function'),
    'diff':       ('xbx.workbench.cli', 'workbench: diagnose and show retail next to ours'),
    'triage':     ('xbx.workbench.cli', 'workbench: classify open functions'),
    'try':        ('xbx.workbench.cli', 'workbench: score a file of variants and record them'),
    'history':    ('xbx.workbench.cli', "workbench: a function's recorded variants"),
    'exemplars':  ('xbx.workbench.cli', 'workbench: matched functions that look like this one'),
    'idiom':      ('xbx.workbench.cli', 'workbench: where matched code uses a retail-only instruction'),
    'search':     ('xbx.workbench.cli', 'workbench: beam search over source mutators'),
}
WORKBENCH = {name for name, (module, _) in COMMANDS.items() if module == 'xbx.workbench.cli'}


def usage():
    width = max(map(len, COMMANDS))
    lines = [__doc__.strip(), '', 'commands:']
    lines += [f'  {name:<{width}}  {text}' for name, (_, text) in COMMANDS.items()]
    return '\n'.join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('help', '-h', '--help'):
        print(usage())
        return 0
    name, rest = argv[0], argv[1:]
    if name not in COMMANDS:
        print(f'xbx: unknown command {name!r}\n\n{usage()}', file=sys.stderr)
        return 2
    try:
        module = importlib.import_module(COMMANDS[name][0])
    except ModuleNotFoundError as e:
        if e.name and e.name.startswith('xbx'):
            print(f'xbx: {name} is not built yet ({e.name})', file=sys.stderr)
            return 2
        raise
    result = module.main([name] + rest) if name in WORKBENCH else module.main(rest)
    return result or 0


if __name__ == '__main__':
    sys.exit(main())
