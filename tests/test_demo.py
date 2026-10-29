import subprocess
import threading

import pytest

from fourmatter.core.models import FormatOptions, Identity, OperationRequest
from fourmatter.core.safety import assess
from fourmatter.demo.fixtures import DemoBackend, demo_drives


@pytest.fixture
def no_side_effects(monkeypatch):
    def boom(*a, **k):
        pytest.fail("demo mode touched the system")
    import os
    for obj, name in ((subprocess, "run"), (subprocess, "Popen"), (subprocess, "check_output"),
                      (os, "open"), (os, "system"), (os, "write")):
        monkeypatch.setattr(obj, name, boom)


def test_fixtures_are_labelled_and_varied():
    ds = demo_drives()
    assert all(d.simulated for d in ds) and len({d.size for d in ds}) > 3
    verdicts = {d.name: assess(d).eligible for d in ds}
    assert verdicts["sdb"] and not verdicts["sde"] and not verdicts["sda"]  # ro + system disk
    assert any(assess(d).to_unmount for d in ds)


def test_demo_format_and_zero_touch_nothing(no_side_effects):
    b = DemoBackend(sim_seconds=0.2, os_name="linux")
    d = b.scan()[0]
    events = []
    req = OperationRequest("zero_format", Identity.of(d), FormatOptions("ext4", "DEMO", "gpt", True))
    res = b.execute(req, events.append, threading.Event())
    assert res.ok and res.simulated and "SIMULATED" in res.message
    assert any(e["event"] == "progress" for e in events)
    assert next(x for x in b.scan() if x.path == d.path).partitions[0].fstype == "ext4"
    z = b.execute(OperationRequest("zero", Identity.of(d), FormatOptions(fs=None)), events.append, threading.Event())
    assert z.ok and "SIMULATED" in z.message


def test_demo_refuses_ineligible_target(no_side_effects):
    b = DemoBackend(sim_seconds=0.1, os_name="linux")
    sys_disk = next(d for d in b.scan() if d.name == "sda")
    res = b.execute(OperationRequest("format", Identity.of(sys_disk), FormatOptions("exfat")), lambda e: None,
                    threading.Event())
    assert not res.ok and "not eligible" in res.message


def test_demo_cancel(no_side_effects):
    b = DemoBackend(sim_seconds=5, os_name="linux")
    d = b.scan()[0]
    cancel = threading.Event()
    cancel.set()
    res = b.execute(OperationRequest("zero", Identity.of(d), FormatOptions(fs=None)), lambda e: None, cancel)
    assert res.cancelled


def test_demo_backend_cannot_reach_real_helper():
    assert DemoBackend.execute is not __import__("fourmatter.core.executor", fromlist=["x"]).Backend.execute
    assert DemoBackend(os_name="linux").writes is False
