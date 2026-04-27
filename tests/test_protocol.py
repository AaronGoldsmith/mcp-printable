"""Tests for the TCP protocol framing used between MCP server and Blender addon.

These tests verify the length-prefixed JSON protocol works correctly
without requiring a running Blender instance.
"""

import json
import struct
import socket
import threading
import pytest

# Import the protocol functions from the addon
# We can't import the addon directly (it needs bpy), so we test the
# equivalent protocol implementation from server.py
from server import BlenderConnection


def _frame_message(obj: dict) -> bytes:
    """Frame a JSON object with 4-byte big-endian length prefix."""
    data = json.dumps(obj).encode('utf-8')
    return struct.pack('>I', len(data)) + data


def _parse_framed(raw: bytes) -> dict:
    """Parse a length-prefixed JSON message from raw bytes."""
    length = struct.unpack('>I', raw[:4])[0]
    return json.loads(raw[4:4 + length].decode('utf-8'))


class TestProtocolFraming:
    """Test message framing (4-byte length prefix + JSON)."""

    def test_frame_simple_message(self):
        msg = {"id": "abc", "command": "get_scene_info", "params": {}}
        framed = _frame_message(msg)

        assert len(framed) > 4
        length = struct.unpack('>I', framed[:4])[0]
        payload = json.loads(framed[4:].decode('utf-8'))
        assert payload == msg
        assert length == len(framed) - 4

    def test_frame_empty_params(self):
        msg = {"id": "1", "command": "clear_scene", "params": {}}
        framed = _frame_message(msg)
        parsed = _parse_framed(framed)
        assert parsed == msg

    def test_frame_large_payload(self):
        """Ensure large payloads (e.g., base64 images) frame correctly."""
        large_data = "x" * 500_000  # ~500KB
        msg = {"id": "2", "status": "success", "result": {"image": large_data}}
        framed = _frame_message(msg)

        length = struct.unpack('>I', framed[:4])[0]
        assert length == len(framed) - 4
        parsed = _parse_framed(framed)
        assert parsed['result']['image'] == large_data

    def test_frame_unicode(self):
        msg = {"id": "3", "command": "rename_object", "params": {"new_name": "hélice_鋼"}}
        framed = _frame_message(msg)
        parsed = _parse_framed(framed)
        assert parsed['params']['new_name'] == "hélice_鋼"

    def test_roundtrip_all_commands(self):
        """Verify every command name can be framed and parsed."""
        commands = [
            "get_scene_info", "get_object_info", "clear_scene", "rename_object",
            "execute_code", "get_screenshot", "render_tiled", "cross_section",
            "cross_section_gallery", "render_printability_heatmap",
            "render_turntable", "render_with_dimensions", "render_before_after",
            "boolean", "mesh_health", "check_intersection",
            "check_overhangs", "check_thin_walls", "check_clearance",
            "check_clearance_sweep", "full_printability_check",
            "export_stl", "import_stl", "save_blend",
        ]
        for cmd in commands:
            msg = {"id": "test", "command": cmd, "params": {}}
            framed = _frame_message(msg)
            parsed = _parse_framed(framed)
            assert parsed['command'] == cmd


class TestBlenderConnectionProtocol:
    """Test BlenderConnection behavior without a real Blender."""

    def test_connection_refused(self):
        """Verify clear error when Blender is not running."""
        conn = BlenderConnection(port=19999)  # Port nothing listens on
        with pytest.raises(ConnectionError, match="Cannot connect to Blender"):
            conn.send("get_scene_info")

    def test_send_and_receive(self):
        """Test full send/receive cycle with a mock server."""
        # Start a mock "Blender addon" server
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', 0))
        port = server.getsockname()[1]
        server.listen(1)

        def mock_blender():
            conn, _ = server.accept()
            # Read the command
            header = conn.recv(4)
            length = struct.unpack('>I', header)[0]
            data = conn.recv(length)
            request = json.loads(data.decode('utf-8'))

            # Send response
            response = {
                'id': request['id'],
                'status': 'success',
                'result': {'object_count': 3, 'objects': []},
            }
            resp_data = json.dumps(response).encode('utf-8')
            conn.sendall(struct.pack('>I', len(resp_data)) + resp_data)
            conn.close()

        t = threading.Thread(target=mock_blender, daemon=True)
        t.start()

        try:
            conn = BlenderConnection(port=port)
            result = conn.send("get_scene_info")
            assert result['object_count'] == 3
        finally:
            server.close()
            t.join(timeout=2)

    def test_error_response(self):
        """Test that Blender error responses raise RuntimeError."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', 0))
        port = server.getsockname()[1]
        server.listen(1)

        def mock_blender_error():
            conn, _ = server.accept()
            header = conn.recv(4)
            length = struct.unpack('>I', header)[0]
            conn.recv(length)

            response = {
                'id': 'test',
                'status': 'error',
                'error': "Object 'Foo' not found",
            }
            resp_data = json.dumps(response).encode('utf-8')
            conn.sendall(struct.pack('>I', len(resp_data)) + resp_data)
            conn.close()

        t = threading.Thread(target=mock_blender_error, daemon=True)
        t.start()

        try:
            conn = BlenderConnection(port=port)
            with pytest.raises(RuntimeError, match="Object 'Foo' not found"):
                conn.send("get_object_info", {"name": "Foo"})
        finally:
            server.close()
            t.join(timeout=2)

    def test_connection_drop_reconnect(self):
        """Test that a dropped connection is detected."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', 0))
        port = server.getsockname()[1]
        server.listen(1)

        def mock_blender_drop():
            conn, _ = server.accept()
            conn.close()  # Drop immediately

        t = threading.Thread(target=mock_blender_drop, daemon=True)
        t.start()

        try:
            conn = BlenderConnection(port=port)
            with pytest.raises(ConnectionError):
                conn.send("get_scene_info")
            # After failure, sock should be cleared for reconnect
            assert conn.sock is None
        finally:
            server.close()
            t.join(timeout=2)

    def test_socket_released_after_successful_send(self):
        """Verify the socket is closed after each command so other clients can connect.

        Without this, whichever MCP client connects first holds the bridge for
        the lifetime of its process, starving every other client.
        """
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', 0))
        port = server.getsockname()[1]
        server.listen(1)

        def mock_blender():
            conn, _ = server.accept()
            header = conn.recv(4)
            length = struct.unpack('>I', header)[0]
            data = conn.recv(length)
            request = json.loads(data.decode('utf-8'))
            response = {'id': request['id'], 'status': 'success', 'result': {}}
            resp_data = json.dumps(response).encode('utf-8')
            conn.sendall(struct.pack('>I', len(resp_data)) + resp_data)
            conn.close()

        t = threading.Thread(target=mock_blender, daemon=True)
        t.start()

        try:
            conn = BlenderConnection(port=port)
            conn.send("get_scene_info")
            assert conn.sock is None, "BlenderConnection should release its socket after send"
        finally:
            server.close()
            t.join(timeout=2)

    def test_two_clients_interleave(self):
        """Two BlenderConnection instances against the same server can both succeed.

        Models the real-world case of Claude Desktop + Claude Code (or any two
        MCP clients) both wanting to drive Blender — the addon's accept loop
        picks them up in order because the MCP server side closes its socket
        after each command.
        """
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', 0))
        port = server.getsockname()[1]
        server.listen(8)  # Mirror the new addon backlog

        def mock_blender_serve_n(n: int):
            """Accept n connections in sequence, respond to each, close."""
            for _ in range(n):
                conn, _ = server.accept()
                header = conn.recv(4)
                length = struct.unpack('>I', header)[0]
                data = conn.recv(length)
                request = json.loads(data.decode('utf-8'))
                response = {'id': request['id'], 'status': 'success',
                            'result': {'echo': request['command']}}
                resp_data = json.dumps(response).encode('utf-8')
                conn.sendall(struct.pack('>I', len(resp_data)) + resp_data)
                conn.close()

        # We'll fire 4 commands across 2 clients — server must serve all 4.
        t = threading.Thread(target=mock_blender_serve_n, args=(4,), daemon=True)
        t.start()

        try:
            client_a = BlenderConnection(port=port)
            client_b = BlenderConnection(port=port)

            # Interleaved: A, B, A, B
            r1 = client_a.send("cmd_a1")
            r2 = client_b.send("cmd_b1")
            r3 = client_a.send("cmd_a2")
            r4 = client_b.send("cmd_b2")

            assert r1 == {'echo': 'cmd_a1'}
            assert r2 == {'echo': 'cmd_b1'}
            assert r3 == {'echo': 'cmd_a2'}
            assert r4 == {'echo': 'cmd_b2'}
        finally:
            server.close()
            t.join(timeout=5)
