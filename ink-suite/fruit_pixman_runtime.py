"""Select a private, hash-pinned pixman for the fruit child without loading it.

Manifest: ROOT/runtime/pixman-a53/manifest.json
{"schema":1,"kind":"fruit-pixman-runtime","architecture":"aarch64",
 "library":{"path":"lib/libpixman-1.so.0","sha256":"<lowercase SHA256>"}}
The SONAME may link to a versioned file in the same lib directory. Failures
return a copy of the original child environment; no parent environment changes.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re


def _prepend_library_path(directory, existing):
    """Prepend one directory and retain the first occurrence of inherited paths."""
    entries=[str(directory), *(existing.split(os.pathsep) if existing else [])]
    result=[];seen=set()
    for entry in entries:
        # Keep an intentional empty component in its inherited position.
        key=os.path.normcase(os.path.abspath(os.path.normpath(entry))) if entry else None
        if key not in seen:
            seen.add(key);result.append(entry)
    return os.pathsep.join(result)


def _manifest(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate manifest key')
            result[key]=value
        return result
    value=json.loads(text,object_pairs_hook=pairs)
    if not isinstance(value,dict) or set(value)!={'schema','kind','architecture','library'}:
        raise ValueError('unexpected manifest fields')
    if type(value['schema']) is not int or value['schema']!=1 or value['kind']!='fruit-pixman-runtime' or value['architecture']!='aarch64':
        raise ValueError('unsupported manifest identity')
    library=value['library']
    if not isinstance(library,dict) or set(library)!={'path','sha256'} or library['path']!='lib/libpixman-1.so.0':
        raise ValueError('unexpected library path')
    if not isinstance(library['sha256'],str) or re.fullmatch('[0-9a-f]{64}',library['sha256']) is None:
        raise ValueError('invalid library digest')
    return library['sha256']


def build_child_environment(root, mode, parent_env, *, machine=None):
    """Return (new_env, diagnostic). Only vetted fruit/aarch64 changes LD_LIBRARY_PATH."""
    child=dict(parent_env)
    def fallback(reason, **details):return child,dict(enabled=False,reason=reason,**details)
    if mode!='fruit':return fallback('mode-not-fruit')
    if str(child.get('INK_FRUIT_PIXMAN','')).strip().lower()=='system':return fallback('system-override')
    if (machine if machine is not None else platform.machine()).lower()!='aarch64':
        return fallback('unsupported-architecture')
    try:
        root=Path(root).resolve(strict=True)
        runtime=root/'runtime/pixman-a53';manifest=runtime/'manifest.json'
        if not manifest.is_file():return fallback('manifest-missing')
        if manifest.resolve(strict=True)!=manifest:return fallback('unsafe-manifest-path')
        if manifest.stat().st_size>16384:return fallback('manifest-invalid',detail='manifest too large')
        before=manifest.stat();payload=manifest.read_text(encoding='utf-8');after=manifest.stat()
        if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
            return fallback('manifest-changed')
        try:wanted=_manifest(payload)
        except (ValueError,TypeError):return fallback('manifest-invalid')
        library=runtime/'lib/libpixman-1.so.0';libdir=runtime/'lib'
        if not library.is_file():return fallback('library-missing')
        if libdir.resolve(strict=True)!=libdir or library.resolve(strict=True).parent!=libdir:
            return fallback('unsafe-library-path')
        before=library.stat();hasher=hashlib.sha256()
        with library.open('rb') as stream:
            for data in iter(lambda:stream.read(1024*1024),b''):hasher.update(data)
            opened=os.fstat(stream.fileno())
        after=library.stat()
        signature=lambda item:(item.st_dev,item.st_ino,item.st_size,item.st_mtime_ns)
        if signature(before)!=signature(opened) or signature(before)!=signature(after):
            return fallback('library-changed')
        actual=hasher.hexdigest()
        if actual!=wanted:return fallback('hash-mismatch')
        # Recheck the manifest before selecting the directory for the child.
        if manifest.read_text(encoding='utf-8')!=payload:return fallback('manifest-changed')
        inherited=child.get('LD_LIBRARY_PATH','')
        if not isinstance(inherited,str):return fallback('environment-invalid')
        child['LD_LIBRARY_PATH']=_prepend_library_path(libdir,inherited)
        return child,dict(enabled=True,reason='verified-private-runtime',library=str(library),sha256=actual)
    except (OSError,ValueError,UnicodeError) as exc:
        return fallback('runtime-unavailable',detail=type(exc).__name__)
