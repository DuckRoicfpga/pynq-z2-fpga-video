"""Run host regressions and publication checks; never execute board notebooks."""
import ast
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'software'), pattern='test_*.py')
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    print(output.getvalue())
    if not result.wasSuccessful():
        return 1
    cells = 0
    for path in (ROOT / 'notebooks').glob('*.ipynb'):
        notebook = json.loads(path.read_text(encoding='utf-8'))
        for cell in notebook['cells']:
            if cell['cell_type'] == 'code':
                assert cell['outputs'] == [] and cell['execution_count'] is None, path
                compile(''.join(cell['source']), str(path), 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
                cells += 1
    evidence = json.loads((ROOT / 'evidence/board_validation.json').read_text(encoding='utf-8'))
    for name, expected in evidence['runtime_files_sha256'].items():
        assert digest(ROOT / 'software' / name) == expected, name
    notebook = json.loads((ROOT / 'notebooks/04_web_stream.ipynb').read_text(encoding='utf-8'))
    code = '\n\n'.join(''.join(c['source']) for c in notebook['cells'] if c['cell_type']=='code')
    assert hashlib.sha256(code.encode('utf-8')).hexdigest() == evidence['notebook_code_sha256']
    pixels = json.loads((ROOT / 'evidence/pixel_checks_40frames.json').read_text())['samples']
    assert len(pixels) == 40 and {s['mode'] for s in pixels} == {0,1,2,3}
    assert all(s['mismatched_bytes']==0 for s in pixels)
    unsafe = []
    text_extensions = {'.py','.md','.json','.ipynb','.tcl','.xdc','.v','.sv','.ps1','.txt','.yml','.xml'}
    for path in ROOT.rglob('*'):
        if not path.is_file() or any(p in ('.git','.venv','venv','output','dist','__pycache__') for p in path.relative_to(ROOT).parts):
            continue
        if path.suffix.lower() in text_extensions:
            text = path.read_text(encoding='utf-8-sig')
            # No development-machine identity, private paths or live credentials.
            if re.search(r'(?i)(?:[CD]:[\\/]+Users[\\/]+LENOVO|D:[\\/]+TRAIN AI|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)', text):
                unsafe.append(path.relative_to(ROOT).as_posix())
    assert not unsafe, 'Publication scan failed: ' + ', '.join(unsafe)
    report = {
        'status':'PASS', 'recorded_at_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'Host tests and static publication checks; no board access or physical retest',
        'tests_run':result.testsRun, 'failures':0, 'errors':0,
        'notebook_code_cells_compiled':cells, 'notebook_outputs_cleared':True,
        'verified_runtime_hashes_match_board_evidence':True,
        'basic_private_path_and_secret_pattern_scan':'PASS',
        'source_sha256':{p.relative_to(ROOT).as_posix():digest(p) for p in (ROOT/'software').glob('*.py')},
    }
    (ROOT/'evidence/publication_host_tests.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('PUBLICATION_CHECK_PASS: %d host tests, %d notebook cells, verified runtime fingerprints' % (result.testsRun,cells))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
