"""Tests for the server/addon version-mismatch warning (issue #19).

Covers the pure comparison logic and the once-per-process warning state on
BlenderConnection. No Blender or network required — responses are fed to the
connection object directly, the same way `send()` does after receiving them.
"""

from pathlib import Path

import pytest

from server import BlenderConnection, format_version_mismatch, _server_version

ADDON_INIT = Path(__file__).resolve().parent.parent / "addon" / "__init__.py"


class TestFormatVersionMismatch:
    """Pure comparison logic — never crashes, warns on mismatch or unknown addon."""

    def test_match_no_warning(self):
        assert format_version_mismatch("0.2.2", "0.2.2") is None

    def test_mismatch_warns_with_both_versions(self):
        warning = format_version_mismatch("0.2.0", "0.2.2")
        assert warning is not None
        assert "0.2.0" in warning
        assert "0.2.2" in warning
        assert "install.py" in warning
        assert "restart Blender" in warning

    def test_missing_addon_version_warns(self):
        """Old addons don't report a version — treat as unknown/old, still warn."""
        warning = format_version_mismatch(None, "0.2.2")
        assert warning is not None
        assert "0.2.2" in warning
        assert "install.py" in warning

    def test_unknown_server_version_no_warning(self):
        """No package metadata → nothing to compare against → no warning."""
        assert format_version_mismatch("0.2.0", None) is None
        assert format_version_mismatch(None, None) is None


class TestServerVersion:
    def test_returns_string_or_none(self):
        version = _server_version()
        assert version is None or isinstance(version, str)


class TestConnectionVersionCheck:
    """Warning fires once per connection object (i.e. once per server process)."""

    def test_mismatch_warning_pops_once(self):
        conn = BlenderConnection()
        conn._check_addon_version({"status": "success", "addon_version": "0.0.1"})
        warning = conn.pop_version_warning()
        assert warning is not None
        assert "0.0.1" in warning
        # Consumed — subsequent responses/pops don't re-warn.
        conn._check_addon_version({"status": "success", "addon_version": "0.0.1"})
        assert conn.pop_version_warning() is None

    def test_missing_version_field_warns_without_crash(self):
        """Pre-0.2.3 addon envelopes have no addon_version key at all."""
        conn = BlenderConnection()
        conn._check_addon_version({"status": "success", "result": {}})
        assert conn.addon_version is None
        if _server_version() is not None:
            warning = conn.pop_version_warning()
            assert warning is not None
            assert "install.py" in warning

    def test_matching_version_no_warning(self):
        server_version = _server_version()
        if server_version is None:
            return  # can't construct a matching envelope without metadata
        conn = BlenderConnection()
        conn._check_addon_version({"status": "success", "addon_version": server_version})
        assert conn.pop_version_warning() is None
        assert conn.addon_version == server_version

    def test_addon_version_recorded_on_every_response(self):
        conn = BlenderConnection()
        conn._check_addon_version({"addon_version": "0.0.1"})
        conn.pop_version_warning()
        conn._check_addon_version({"addon_version": "0.0.2"})
        assert conn.addon_version == "0.0.2"


class _FakeSock:
    """Just enough socket surface for BlenderConnection.send()."""

    def settimeout(self, timeout):
        pass

    def close(self):
        pass


def _stubbed_connection(response: dict) -> BlenderConnection:
    """A BlenderConnection whose network layer returns a canned response."""
    conn = BlenderConnection()
    conn._connect = lambda: setattr(conn, "sock", _FakeSock())
    conn._send_msg = lambda msg: None
    conn._recv_msg = lambda: response
    return conn


class TestErrorPathWarning:
    """A stale addon's most likely symptom is an error on the FIRST command
    (old addon rejects a new command/param). The mismatch warning must ride
    along on that error, not wait for a later successful call."""

    def test_first_error_response_carries_mismatch_warning(self):
        conn = _stubbed_connection({
            "status": "error",
            "error": "Unknown command: shiny_new_tool",
            "addon_version": "0.0.1",
        })
        if _server_version() is None:
            return  # no metadata → no mismatch to surface
        with pytest.raises(RuntimeError) as exc_info:
            conn.send("shiny_new_tool")
        message = str(exc_info.value)
        assert "Unknown command: shiny_new_tool" in message
        assert "0.0.1" in message
        assert "install.py" in message
        # Consumed — the warning must not resurface on a later call.
        assert conn.pop_version_warning() is None

    def test_error_with_matching_version_raises_plain_error(self):
        server_version = _server_version()
        if server_version is None:
            return  # can't construct a matching envelope without metadata
        conn = _stubbed_connection({
            "status": "error",
            "error": "Object 'Cube' not found",
            "addon_version": server_version,
        })
        with pytest.raises(RuntimeError) as exc_info:
            conn.send("get_object_info")
        message = str(exc_info.value)
        assert "Object 'Cube' not found" in message
        assert "install.py" not in message


class TestAddonEnvelopeSource:
    """The addon (unimportable here — needs bpy) must stamp every envelope."""

    def test_all_envelopes_carry_addon_version(self):
        source = ADDON_INIT.read_text(encoding="utf-8")
        assert source.count("'addon_version': _ADDON_VERSION") == 3, (
            "Expected addon_version in all three response envelopes "
            "(success, error, timeout) in addon/__init__.py"
        )
