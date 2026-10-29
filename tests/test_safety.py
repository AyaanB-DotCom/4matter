import dataclasses

import pytest
from conftest import parse_ext, lsblk, usb_node

from fourmatter.core.models import Identity, SafetyError
from fourmatter.core.safety import assess, check_identity, confirmation_ok, confirmation_token, revalidate


def test_plain_usb_is_eligible(usb):
    s = assess(usb)
    assert s.eligible and not s.to_unmount


def test_system_disk_blocked_even_though_unmounted_parts(system_tree):
    nvme = system_tree[0]
    s = assess(nvme)
    assert not s.eligible
    text = s.reason
    assert "Not USB" in text and "/boot/efi" in text and "swap" in text and "stacked" in text


def test_usb_with_root_on_it_is_blocked():
    n = usb_node()
    n["children"][0]["mountpoints"] = ["/"]
    assert not assess(parse_ext(lsblk(n))[0]).eligible


def test_usb_hosting_lvm_root_blocked_via_stack():
    n = usb_node()
    n["children"][0]["children"] = [{"name": "vg-root", "type": "lvm", "mountpoints": ["/"]}]
    s = assess(parse_ext(lsblk(n))[0])
    assert not s.eligible and "stacked" in s.reason


def test_root_devno_blocks(usb):
    assert not assess(usb, root_dev="8:17").eligible
    assert assess(usb, root_dev="8:99").eligible


def test_removable_mounts_need_unmount_not_blocked():
    n = usb_node()
    n["children"][0]["mountpoints"] = ["/run/media/me/TRAVEL"]
    s = assess(parse_ext(lsblk(n))[0])
    assert s.eligible and [m.mountpoint for m in s.to_unmount] == ["/run/media/me/TRAVEL"]


def test_odd_mountpoint_fails_closed():
    n = usb_node()
    n["children"][0]["mountpoints"] = ["/home/me/stick"]
    assert not assess(parse_ext(lsblk(n))[0]).eligible


@pytest.mark.parametrize("kw,frag", [
    ({"ro": True}, "read-only"), ({"tran": "sata"}, "Not USB"), ({"tran": None}, "Not USB"),
    ({"size": 0}, "No media"), ({"maj:min": None}, "identity"), ({"type": "loop"}, "Unsupported"),
    ({"name": "zram0", "path": "/dev/zram0"}, "Virtual"),
])
def test_blockers(kw, frag):
    d = parse_ext(lsblk(usb_node(**kw)))[0]
    s = assess(d)
    assert not s.eligible and frag in s.reason


def test_loop_and_optical_excluded(system_tree):
    assert not assess(system_tree[1]).eligible and not assess(system_tree[2]).eligible


def test_identity_roundtrip_and_mismatch(usb):
    ident = Identity.of(usb)
    assert check_identity(ident, usb) == []
    other = dataclasses.replace(usb, serial="OTHER", size=usb.size + 1)
    assert len(check_identity(ident, other)) == 2


def test_revalidate_ok_missing_changed_unsafe(usb):
    ident = Identity.of(usb)
    assert revalidate(ident, lambda: [usb])[0] == usb
    with pytest.raises(SafetyError, match="no longer present"):
        revalidate(ident, lambda: [])
    with pytest.raises(SafetyError, match="identity changed"):
        revalidate(ident, lambda: [dataclasses.replace(usb, serial="X")])
    with pytest.raises(SafetyError, match="not eligible"):
        revalidate(ident, lambda: [dataclasses.replace(usb, read_only=True)])

    def boom():
        raise OSError("lsblk gone")
    with pytest.raises(SafetyError, match="re-scan"):
        revalidate(ident, boom)


def test_same_path_new_device_detected(usb):
    """/dev/sdb now points at a different stick: path alone must not be trusted."""
    swapped = dataclasses.replace(usb, serial="NEWSTICK", model="Cruzer")
    with pytest.raises(SafetyError):
        revalidate(Identity.of(usb), lambda: [swapped])


def test_confirmation_token(usb):
    i = Identity.of(usb)
    assert confirmation_token(i) == "sdb"
    assert confirmation_ok(i, " sdb ") and not confirmation_ok(i, "sdc") and not confirmation_ok(i, "")
