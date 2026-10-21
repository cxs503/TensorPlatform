import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('development_sandbox',Path(__file__).resolve().parents[2]/'deploy/development-sandbox.py')
sandbox=importlib.util.module_from_spec(spec);spec.loader.exec_module(sandbox)

@pytest.mark.parametrize('name',['../escape.py','/tmp/escape.py','x.sh','a/b.py'])
def test_package_cannot_supply_paths_or_commands(name):
    with pytest.raises(ValueError):sandbox.validate({'files':{name:'pass','test_a.py':'pass'}})

def test_package_requires_code_and_tests():
    with pytest.raises(ValueError):sandbox.validate({'files':{'x.py':'pass','y.py':'pass'}})
    assert sandbox.validate({'files':{'x.py':'x=1','test_x.py':'import unittest'}})
    with pytest.raises(ValueError):sandbox.validate({'files':{'x.py':'pass','test_x.py':'pass'},'docker_args':['--privileged']})
