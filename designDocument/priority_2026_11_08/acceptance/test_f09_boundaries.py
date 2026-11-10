"""Independent safety/fallback boundaries; no real extensions or model calls."""
import os
import shutil
import subprocess
import time
import pytest
from test_f09_independent import workspace


def test_no_rg_uses_git_with_nested_ignore_and_direct_file_filter(workspace,monkeypatch):
    original=shutil.which
    monkeypatch.setattr(shutil,'which',lambda name,*a,**k:None if name=='rg' else original(name,*a,**k))
    grep=workspace['tools']['grep']
    result=grep.execute('F09_',limit=200)
    assert '[backend: git' in result and 'F09_FORBIDDEN_' not in result
    assert 'F09_ALLOWED_ROOT_EXCEPTION' in result and 'F09_ALLOWED_NESTED_EXCEPTION' in result
    assert 'F09_FORBIDDEN_IGNORED' not in grep.execute('F09_',path='ignored/leak.py')


def test_no_backends_fails_closed_when_ignore_semantics_unavailable(workspace,monkeypatch):
    monkeypatch.setattr(shutil,'which',lambda *a,**k:None)
    for name in ('grep','glob','repo_map'):
        kwargs={'pattern':'F09_'} if name=='grep' else {'pattern':'**/*'} if name=='glob' else {}
        result=workspace['tools'][name].execute(**kwargs)
        assert 'Error:' in result and 'ignore' in result.lower() and 'F09_FORBIDDEN_' not in result


def test_real_external_junction_and_raw_parent_path_never_expose_external(workspace,tmp_path):
    root=workspace['root'];outside=tmp_path/'outside';outside.mkdir()
    (outside/'canary.py').write_text('F09_ESCAPE_CANARY\n',encoding='utf-8')
    link=root/'external-link'
    if os.name=='nt':
        subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True)
    else:link.symlink_to(outside,target_is_directory=True)
    try:
        for name in ('grep','glob','repo_map'):
            kwargs={'pattern':'F09_ESCAPE_CANARY'} if name=='grep' else {'pattern':'**/*'} if name=='glob' else {}
            for raw in ('external-link','external-link/../src',str(outside)):
                result=workspace['tools'][name].execute(path=raw,**kwargs)
                assert 'Error:' in result and 'F09_ESCAPE_CANARY' not in result
    finally:
        if os.name=='nt':os.rmdir(link)
        else:link.unlink()
    assert (outside/'canary.py').read_text(encoding='utf-8')=='F09_ESCAPE_CANARY\n'


def test_file_line_ast_and_regex_time_limits_are_visible(workspace):
    put=workspace['put'];tools=workspace['tools']
    put('large.py','F09_HIDDEN_BIG\n'+'x'*2_000_000)
    put('wide.py','F09_HIDDEN_WIDE'+'x'*20000+'\n')
    output=tools['grep'].execute('F09_HIDDEN',limit=200)
    assert 'incomplete' in output and 'oversized' in output and 'long lines' in output
    assert 'F09_HIDDEN_BIG' not in output and 'F09_HIDDEN_WIDE' not in output
    put('many.py','x=1\n'*12000)
    mapping=tools['repo_map'].execute(limit=200)
    assert 'incomplete' in mapping and 'line limit' in mapping
    put('catastrophic.txt','a'*5000+'!\n')
    start=time.monotonic();result=tools['grep'].execute('(a+)+$',path='catastrophic.txt')
    assert time.monotonic()-start<8 and 'timed out' in result and 'incomplete' in result


def test_actual_large_inventory_marks_truncated_negative_result(workspace):
    for i in range(5005):
        workspace['put'](f'many/file_{i:05d}.py',f'F09_BUDGET_{i:05d}\n')
    result=workspace['tools']['grep'].execute('F09_BUDGET_05004',path='many')
    # Either byte/file coverage or the independent regex worker deadline may
    # exhaust first on Windows; both must explicitly reject a complete negative.
    assert 'incomplete' in result and ('file scan limit' in result or 'timed out' in result)
    assert 'F09_BUDGET_05004' not in result
    page=workspace['tools']['glob'].execute('*.py',path='many',offset=4999,limit=200)
    assert len(__import__('re').findall(r'file_\d{5}\.py',page))==1 and 'incomplete' in page
