"""Build a frozen Git runtime whitelist and sign using publisher's minisign CLI."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import types
from package import runtime_path, valid_version

NATIVE_MEMBERS=frozenset('studio/deploy/native/'+name for name in ('robotd','manifest.json','padd','controller-manifest.json'))

def frozen_voice_contract(archive):
    """Read an explicit consumer declaration without importing the audio daemon."""
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        members=list(source)
        voices=[item for item in members if item.name=='software/duck-echo/duck_echo.py']
        if not voices:return {}
        if len(voices)!=1 or not voices[0].isfile() or not 0<voices[0].size<=2*1024*1024:
            raise ValueError('invalid frozen voice consumer source')
        try:tree=ast.parse(source.extractfile(voices[0]).read())
        except (SyntaxError,ValueError):raise ValueError('invalid frozen voice consumer source') from None
        values=[]
        for node in tree.body:
            targets=node.targets if isinstance(node,ast.Assign) else [node.target] if isinstance(node,ast.AnnAssign) else []
            if any(isinstance(target,ast.Name) and target.id=='PADD_MAPPING_CONTRACT' for target in targets):
                try:values.append(ast.literal_eval(node.value))
                except (ValueError,TypeError):raise ValueError('voice mapping contract must be a literal integer') from None
        if not values:return {}
        if len(values)!=1 or type(values[0]) is not int or values[0]!=1:
            raise ValueError('unsupported voice mapping contract')
        readers=[item for item in members if item.name=='software/duck-echo/pad_mapping.py']
        if len(readers)!=1 or not readers[0].isfile() or not 0<readers[0].size<=2*1024*1024:
            raise ValueError('voice mapping contract requires the frozen reader')
        return {'padd_mapping':1}

def frozen_manifest_module(archive, filename):
    """Publisher metadata must come from the same frozen tree as the payload."""
    name='software/studio/deploy/'+filename
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        matches=[item for item in source if item.name==name]
        if len(matches)!=1 or not matches[0].isfile() or not 0<matches[0].size<=512*1024:
            raise ValueError('frozen source lacks a bounded manifest module: '+filename)
        data=source.extractfile(matches[0]).read()
    module=types.ModuleType('publisher_'+filename.replace('.','_'))
    module.__file__=name
    exec(compile(data,name,'exec'),module.__dict__)
    return module

def build(repo, ref, version, output, signing_key, public_key, minisign='minisign', changelog='', zstd='zstd', native_robotd=None, native_padd=None):
    if not valid_version(version): raise ValueError('version must be SemVer')
    if native_padd is not None and native_robotd is None:
        raise ValueError('controller bundle requires an explicit matching robotd input')
    revision=subprocess.check_output(['git','-C',str(repo),'rev-parse','--verify',ref+'^{commit}'],text=True).strip()
    frozen=subprocess.check_output(['git','-C',str(repo),'archive','--format=tar',revision,'software/studio','software/duck-echo'])
    contracts=frozen_voice_contract(frozen)
    if native_padd is not None and contracts!={'padd_mapping':1}:
        raise ValueError('controller bundle requires the declared voice mapping contract')
    native_files={}
    if native_robotd is not None:
        path=Path(native_robotd)
        if path.is_symlink() or not path.is_file() or path.stat().st_size>32*1024*1024:
            raise ValueError('native input must be a bounded regular file')
        native=frozen_manifest_module(frozen,'native_update.py')
        data=path.read_bytes(); native_manifest=native.native_manifest(version,data)
        native.validate_payload(data,native_manifest,version)
        native_files={'studio/deploy/native/robotd':data,
                      'studio/deploy/native/manifest.json':(json.dumps(native_manifest,sort_keys=True,indent=2)+'\n').encode()}
        if native_padd is not None:
            path=Path(native_padd)
            if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=32*1024*1024:
                raise ValueError('padd input must be a bounded regular file')
            padd=path.read_bytes()
            bundle=frozen_manifest_module(frozen,'controller_bundle.py')
            manifest=bundle.bundle_manifest(version,data,padd)
            bundle.validate_bundle(data,padd,manifest,version)
            native_files.update({'studio/deploy/native/padd':padd,
                'studio/deploy/native/controller-manifest.json':(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()})
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    artifact=output/f'{version}.tar.zst'; files={}; tar_buffer=io.BytesIO()
    with tarfile.open(fileobj=tar_buffer,mode='w') as dest:
        emitted=set()
        def add(info,payload=None):
            # Emit parent directory entries (0755) before each file: the engine
            # otherwise creates missing dirs under UMask 0077 -> 0700, breaking
            # the radxa-owned studio CHDIR after apply.
            parts=info.name.split('/')
            for i in range(1,len(parts)):
                directory='/'.join(parts[:i])
                if directory not in emitted:
                    emitted.add(directory)
                    dinfo=tarfile.TarInfo(directory);dinfo.type=tarfile.DIRTYPE;dinfo.mode=0o755;dinfo.size=0
                    dest.addfile(dinfo)
            dest.addfile(info,payload)
        with tarfile.open(fileobj=io.BytesIO(frozen)) as source:
            for item in source:
                name=runtime_path(item.name)
                if not name: continue
                if not item.isfile():
                    if item.isdir(): continue
                    raise ValueError('runtime whitelist contains a non-regular file')
                if name in files:raise ValueError('duplicate runtime target in frozen source: '+name)
                data=source.extractfile(item).read(); files[name]=hashlib.sha256(data).hexdigest()
                if name in NATIVE_MEMBERS: raise ValueError('native payload must only come from the explicit publisher input')
                info=tarfile.TarInfo(name); info.size=len(data); info.mode=0o755 if name.endswith('microduck-helper') else 0o644
                add(info,io.BytesIO(data))
        if native_files:
            if any(name not in files for name in ('studio/deploy/microduck-helper','studio/deploy/native_update.py')):
                raise ValueError('frozen source does not contain the native updater')
            for name,data in native_files.items():
                files[name]=hashlib.sha256(data).hexdigest()
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o755 if name.rsplit('/',1)[-1] in ('robotd','padd') else 0o644
                add(info,io.BytesIO(data))
        metadata={'version':version,'source_revision':revision,'files':files}
        if contracts:metadata['contracts']=contracts
        provenance=json.dumps(metadata,sort_keys=True,indent=2).encode()
        info=tarfile.TarInfo('suite-version.json'); info.size=len(provenance); info.mode=0o644
        add(info,io.BytesIO(provenance))
    with artifact.open('wb') as stream:
        if shutil.which(zstd):
            subprocess.run([zstd,'-q','-T1','-10','-c'],input=tar_buffer.getvalue(),stdout=stream,check=True)
        else:
            import ctypes
            from ctypes.util import find_library
            library=ctypes.CDLL(find_library('zstd') or 'libzstd.so.1')
            library.ZSTD_compressBound.argtypes=[ctypes.c_size_t]; library.ZSTD_compressBound.restype=ctypes.c_size_t
            library.ZSTD_compress.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int]
            library.ZSTD_compress.restype=ctypes.c_size_t
            library.ZSTD_isError.argtypes=[ctypes.c_size_t]; library.ZSTD_isError.restype=ctypes.c_uint
            data=tar_buffer.getvalue(); capacity=library.ZSTD_compressBound(len(data)); buffer=ctypes.create_string_buffer(capacity)
            size=library.ZSTD_compress(buffer,capacity,data,len(data),10)
            if library.ZSTD_isError(size): raise RuntimeError('zstd compression failed')
            stream.write(buffer.raw[:size])
    manifest=output/f'{version}.manifest.json'
    manifest.write_text(json.dumps({'channel':'software','version':version,'url':artifact.name,
        'sig_url':artifact.name+'.minisig','sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),
        'size':artifact.stat().st_size,'min_hw_rev':1,'schema_version':1,'source_revision':revision,
        'changelog':changelog,'compatibility':{'architecture':'aarch64','python':'3.13','robotd':'HD1910 motion/control'}},
        sort_keys=True,indent=2)+'\n',encoding='utf-8')
    # Keys are explicit paths and are never included in archives or command output.
    for path in (manifest,artifact):
        subprocess.run([minisign,'-S','-s',str(signing_key),'-m',str(path),'-x',str(path)+'.minisig'],check=True,capture_output=True)
        subprocess.run([minisign,'-V','-p',str(public_key),'-m',str(path),'-x',str(path)+'.minisig'],check=True,capture_output=True)
    outer=output/f'{version}.software-update.tar'
    with tarfile.open(outer,'w',format=tarfile.USTAR_FORMAT) as archive:
        for path in (manifest,Path(str(manifest)+'.minisig'),artifact,Path(str(artifact)+'.minisig')):
            info=tarfile.TarInfo(path.name);info.size=path.stat().st_size;info.mode=0o644
            with path.open('rb') as stream:archive.addfile(info,stream)
    return outer

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('repo','ref','version','output','signing-key','public-key'): parser.add_argument('--'+name,required=True)
    parser.add_argument('--minisign',default='minisign'); parser.add_argument('--zstd',default='zstd'); parser.add_argument('--changelog',default='')
    parser.add_argument('--native-robotd',help='Optional reviewed Qinmo AArch64 ELF; all identities are fixed and verified')
    parser.add_argument('--native-padd',help='Reviewed AArch64 padd companion; requires the matching explicit robotd input')
    args=vars(parser.parse_args())
    print(build(**{key.replace('-','_'):value for key,value in args.items()}))
