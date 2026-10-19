"""Package the published runtime with exact paired overlays; no board writes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.0.0'
RUNTIME = ('board_inventory.py','board_workflows.py','frame_ownership.py','hdmi_workflows.py',
           'reference_filters.py','video_driver.py','web_stream_notebook.py','web_stream_server.py')

INSTALL_CODE = '''from pathlib import Path, PurePosixPath
import hashlib
import json
import shutil
import tempfile
import zipfile

ARCHIVE = Path('__ARCHIVE__')
EXPECTED_SHA256 = '__SHA__'
ROOT = Path.cwd().resolve()
DESTINATION = ROOT / 'video_demo'
if DESTINATION.exists():
    raise FileExistsError('video_demo already exists. Stop the old application and preserve/rename it before installing; no files were overwritten.')
if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != EXPECTED_SHA256:
    raise ValueError('ZIP SHA256 mismatch. Download the ZIP and INSTALL notebook from the same Release.')
with zipfile.ZipFile(ARCHIVE) as archive:
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate archive members')
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if (path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'video_demo'
            or '\\\\' in entry.filename or ':' in entry.filename or (entry.external_attr >> 16) & 0o170000 == 0o120000):
            raise ValueError('Unsafe archive path: ' + entry.filename)
    manifest_name = 'video_demo/deployment_manifest.json'
    manifest = json.loads(archive.read(manifest_name))
    expected_files = manifest['files_sha256']
    if set(names) != {manifest_name} | {'video_demo/' + name for name in expected_files}:
        raise ValueError('Archive members differ from deployment manifest')
    for name, expected in expected_files.items():
        if hashlib.sha256(archive.read('video_demo/' + name)).hexdigest() != expected:
            raise ValueError('File fingerprint mismatch: ' + name)
    with tempfile.TemporaryDirectory(prefix='.video-install-', dir=ROOT) as temporary:
        temporary = Path(temporary)
        for entry in entries:
            target = temporary.joinpath(*PurePosixPath(entry.filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
        if DESTINATION.exists():
            raise FileExistsError('Destination appeared during install; installation cancelled')
        (temporary / 'video_demo').rename(DESTINATION)
print('PACKAGE_CHECK_PASS: installed video_demo; no overlay loaded and no packages installed.')
print('Open video_demo/04_web_stream.ipynb, then Cell -> Run All on the PYNQ board.')
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--overlays', type=Path, required=True)
    parser.add_argument('--vectors', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'dist')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    evidence=json.loads((ROOT/'evidence/board_validation.json').read_text(encoding='utf-8'))
    for name, expected in evidence['runtime_files_sha256'].items():
        if sha(ROOT/'software'/name)!=expected:
            raise ValueError('Runtime changed since board validation: '+name)
    with tempfile.TemporaryDirectory(prefix='.board-package-',dir=args.output) as temporary:
        stage=Path(temporary)/'video_demo'
        stage.mkdir()
        for name in RUNTIME:
            shutil.copy2(ROOT/'software'/name,stage/name)
        for source in (ROOT/'notebooks').glob('*.ipynb'):
            shutil.copy2(source,stage/source.name)
        shutil.copytree(args.vectors,stage/'test_vectors')
        vector_manifest=json.loads((stage/'test_vectors/manifest.json').read_text())
        for name, metadata in vector_manifest['files'].items():
            if sha(stage/'test_vectors'/name)!=metadata['sha256']:
                raise ValueError('Vector mismatch: '+name)
        overlay_contracts=[]
        for name in ('overlay_manifest.json','hdmi_overlay_manifest.json'):
            original=json.loads((args.overlays/name).read_text())
            for kind in ('bit','hwh'):
                filename=original[kind+'_file']
                if Path(filename).name!=filename or sha(args.overlays/filename)!=original[kind+'_sha256']:
                    raise ValueError('Overlay fingerprint mismatch: '+filename)
                (stage/'overlays').mkdir(exist_ok=True)
                shutil.copy2(args.overlays/filename,stage/'overlays'/filename)
            original.pop('build_receipt_file',None)
            original.pop('build_receipt_sha256',None)
            write_json(stage/'overlays'/name,original)
            overlay_contracts.append(original)
        historical=json.loads((ROOT/'evidence/implementation_summary.json').read_text())['overlays']
        for current in overlay_contracts:
            tested=next(item for item in historical if item['bit_file']==current['bit_file'])
            if any(current[k]!=tested[k] for k in ('bit_sha256','hwh_sha256')):
                raise ValueError('Overlays differ from recorded implementation')
        shutil.copytree(ROOT/'licenses',stage/'licenses')
        shutil.copy2(ROOT/'LICENSE',stage/'LICENSE')
        shutil.copy2(ROOT/'docs/THIRD_PARTY_NOTICES.md',stage/'THIRD_PARTY_NOTICES.md')
        shutil.copy2(ROOT/'docs/GETTING_STARTED.md',stage/'README.md')
        write_json(stage/'web_stream_runtime_manifest.json', {
            'schema_version':1, 'files_sha256':evidence['runtime_files_sha256']})
        write_json(stage/'deployment_manifest.json', {
            'schema_version':1,'version':VERSION,'board':'PYNQ-Z2',
            'status':'PACKAGED_VERIFIED_RUNTIME',
            'runtime_hashes_match_physical_evidence':True,
            'fresh_installation_retested_on_board':False,
            'files_sha256':{p.relative_to(stage).as_posix():sha(p) for p in stage.rglob('*') if p.is_file()}})
        archive=args.output/('pynq-z2-video-demo-v'+VERSION+'.zip')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for path in sorted(stage.rglob('*')):
                if path.is_file():z.write(path,'video_demo/'+path.relative_to(stage).as_posix())
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:raise ValueError('ZIP CRC failure')
    code=INSTALL_CODE.replace('__ARCHIVE__',archive.name).replace('__SHA__',sha(archive))
    compile(code,'INSTALL.ipynb','exec')
    notebook={'nbformat':4,'nbformat_minor':5,'metadata':{'kernelspec':{'name':'python3','display_name':'Python 3','language':'python'}},
        'cells':[{'cell_type':'markdown','metadata':{},'source':[
            '# Cài gói PYNQ-Z2 / Install deployment\n',
            'Upload notebook này và ZIP cùng Release vào một thư mục trên Jupyter của board, rồi Run All.\n',
            'Kiểm tra SHA256 và giải nén; không nạp FPGA, không ghi đè video_demo đang có.\n']},
            {'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],'source':code.splitlines(True)}]}
    write_json(args.output/'INSTALL.ipynb',notebook)
    (args.output/'SHA256SUMS.txt').write_text(''.join(sha(p)+'  '+p.name+'\n' for p in (archive,args.output/'INSTALL.ipynb')),encoding='ascii')
    print(json.dumps({'archive':archive.name,'bytes':archive.stat().st_size,'sha256':sha(archive),'installer':'INSTALL.ipynb'}))


if __name__=='__main__':
    main()
