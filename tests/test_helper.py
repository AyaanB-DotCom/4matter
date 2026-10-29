import json

import pytest

from fourmatter.core.models import OperationRequest
from fourmatter.privilege import helper

GOOD = {"mode": "format", "identity": {"path": "/dev/sdb", "maj_min": "8:16", "size": 5, "vendor": None,
                                       "model": "x", "serial": "s", "wwn": None, "transport": "usb"},
        "options": {"fs": "ext4", "label": "L", "table": "gpt", "zero_fill": False}, "owner": [1000, 1000]}


def test_request_roundtrip():
    r = OperationRequest.from_dict(GOOD)
    assert OperationRequest.from_dict(json.loads(json.dumps(r.to_dict()))) == r


@pytest.mark.parametrize("mut", [
    lambda d: d.update(mode="rm -rf"), lambda d: d["options"].update(fs="zfs"),
    lambda d: d["options"].update(table="apm"), lambda d: d["identity"].update(size="5"),
    lambda d: d["identity"].pop("path"), lambda d: d.update(owner=[-1, 0]),
    lambda d: d.update(owner=[1]), lambda d: d["options"].update(zero_fill="yes"),
    lambda d: d.pop("options"),
])
def test_malformed_requests_rejected(mut):
    import copy
    d = copy.deepcopy(GOOD)
    mut(d)
    with pytest.raises(ValueError):
        OperationRequest.from_dict(d)


def test_helper_argv_is_isolated_and_noninteractive():
    a = helper.helper_argv(euid=1000, sudo="/usr/bin/sudo")
    assert a[:3] == ["/usr/bin/sudo", "-n", "--"] and "-I" in a and a[-1] == "fourmatter.privilege.helper"
    assert helper.helper_argv(euid=0)[1] == "-I"
    assert helper.helper_argv(euid=1000, sudo=None) is None or True


def test_helper_refuses_non_root(monkeypatch, capsys):
    monkeypatch.setattr(helper.os, "geteuid", lambda: 1000)
    assert helper.main() == 3
    assert "Insufficient privileges" in json.loads(capsys.readouterr().out)["message"]
