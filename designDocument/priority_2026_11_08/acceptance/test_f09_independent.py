"""Prepared product behavior assertions; execute only after F09 freeze."""
import re
import pytest
from corecoder.state import SessionState
from corecoder.tools import create_tools
from retrieval_fixture import build_repository


@pytest.fixture
def workspace(tmp_path):
    fixture=build_repository(tmp_path/'repository')
    state=SessionState(workspace_root=fixture['root'],cwd=fixture['root'])
    fixture['tools']={tool.name:tool for tool in create_tools(state)}
    return fixture


def test_stable_search_pages_cover_fixed_targets_without_overlap(workspace):
    grep=workspace['tools']['grep']
    pages=[grep.execute(r'F09_ENTRY_\d+',path='.',offset=offset,limit=6) for offset in (0,6,12,18)]
    values=[re.findall(r'F09_ENTRY_\d+',page) for page in pages]
    assert all(len(page)==6 for page in values)
    assert set(sum(values,[]))==workspace['visible_entries']
    assert len(sum(values,[]))==len(set(sum(values,[])))
    assert grep.execute(r'F09_ENTRY_\d+',path='.',offset=6,limit=6)==pages[1]


def test_ignore_exceptions_sensitive_paths_and_binary_are_not_leaked(workspace):
    grep=workspace['tools']['grep']
    text=grep.execute('F09_',path='.',limit=200)
    assert 'F09_FORBIDDEN_' not in text
    assert 'F09_ALLOWED_ROOT_EXCEPTION' in text and 'F09_ALLOWED_NESTED_EXCEPTION' in text
    assert 'F09_FORBIDDEN_CREDENTIAL' not in grep.execute('F09_',path='.env')
    assert 'F09_FORBIDDEN_BINARY' not in grep.execute('F09_',path='binary.bin')


def test_glob_pages_use_a_stable_tie_breaker(workspace):
    glob=workspace['tools']['glob']
    pages=[glob.execute('*.py',path='pages',offset=offset,limit=6) for offset in (0,6,12,18)]
    names=[re.findall(r'entry_\d{4}\.py',page) for page in pages]
    assert all(len(page)==6 for page in names)
    assert len(set(sum(names,[])))==24
    assert glob.execute('*.py',path='pages',offset=6,limit=6)==pages[1]


def test_map_locates_cross_file_symbols_and_rescans_after_mutation(workspace):
    mapping=workspace['tools']['repo_map']
    original=mapping.execute(path='.',query='',limit=200)
    for symbol in ('Ledger','calculate','invoice','consumer.py','calculation.py'):
        assert symbol in original
    workspace['put']('src/calculation.py','def replacement_symbol():\n    return 99\n')
    (workspace['root']/'src/consumer.py').unlink()
    fresh=mapping.execute(path='.',query='',limit=200)
    assert 'replacement_symbol' in fresh and 'Ledger' not in fresh and 'invoice' not in fresh


@pytest.mark.parametrize('kwargs',[{'offset':-1},{'offset':True},{'limit':0},{'limit':True},{'limit':1000000}])
def test_invalid_pagination_is_explicitly_rejected(workspace,kwargs):
    text=workspace['tools']['grep'].execute('F09_',path='.',**kwargs)
    assert re.search(r'error|invalid|错误|无效',text,re.I),text
