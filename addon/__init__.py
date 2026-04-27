"""Printable Blender Addon — TCP bridge between the Printable MCP server and Blender."""

bl_info = {
    "name": "Printable Blender Bridge",
    "author": "Printable",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "Preferences > Add-ons",
    "description": "TCP server that lets any MCP-capable AI agent control Blender for 3D-print modeling",
    "category": "Development",
}

import bpy
import json
import socket
import struct
import threading
import queue
import traceback

# Late import — handlers/utils depend on bpy being available
from . import handlers


# ---------------------------------------------------------------------------
# TCP protocol: 4-byte big-endian length prefix + JSON payload
# ---------------------------------------------------------------------------

def recv_exact(sock, n):
    """Read exactly n bytes from a socket."""
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed")
        buf += chunk
    return buf


def recv_message(sock):
    """Read a length-prefixed JSON message."""
    header = recv_exact(sock, 4)
    length = struct.unpack('>I', header)[0]
    if length > 50 * 1024 * 1024:  # 50MB safety limit
        raise ValueError(f"Message too large: {length} bytes")
    data = recv_exact(sock, length)
    return json.loads(data.decode('utf-8'))


def send_message(sock, obj):
    """Send a length-prefixed JSON message."""
    data = json.dumps(obj).encode('utf-8')
    header = struct.pack('>I', len(data))
    sock.sendall(header + data)


# ---------------------------------------------------------------------------
# Command queue and main-thread execution
# ---------------------------------------------------------------------------

class CommandRequest:
    """A command waiting to be executed on the main thread."""
    __slots__ = ('id', 'command', 'params', 'event', 'result')

    def __init__(self, msg):
        self.id = msg.get('id', '')
        self.command = msg['command']
        self.params = msg.get('params', {})
        self.event = threading.Event()
        self.result = None


# Global state
_pending = queue.Queue()
_server_socket = None
_server_thread = None
_running = False


def _process_commands():
    """Timer callback — runs on Blender's main thread. Drains the command queue."""
    while not _pending.empty():
        try:
            req = _pending.get_nowait()
        except queue.Empty:
            break

        try:
            result = handlers.dispatch(req.command, req.params)
            req.result = {
                'id': req.id,
                'status': 'success',
                'result': result,
            }
        except Exception as e:
            req.result = {
                'id': req.id,
                'status': 'error',
                'error': str(e),
                'traceback': traceback.format_exc(),
            }
        req.event.set()

    return 0.05  # Check every 50ms


# ---------------------------------------------------------------------------
# TCP socket server thread
# ---------------------------------------------------------------------------

def _handle_client(conn, addr):
    """Handle a single client connection."""
    print(f"[Printable Bridge] Client connected from {addr}")
    try:
        while _running:
            try:
                msg = recv_message(conn)
            except (ConnectionError, OSError):
                break

            req = CommandRequest(msg)
            _pending.put(req)

            # Wait for main thread to process (120s timeout)
            if not req.event.wait(timeout=120):
                req.result = {
                    'id': req.id,
                    'status': 'error',
                    'error': 'Command timed out after 120 seconds',
                }

            try:
                send_message(conn, req.result)
            except (ConnectionError, OSError):
                break
    finally:
        conn.close()
        print(f"[Printable Bridge] Client disconnected: {addr}")


def _server_loop(host, port):
    """Accept client connections in a loop."""
    global _server_socket
    _server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    _server_socket.settimeout(1.0)  # So we can check _running periodically

    try:
        _server_socket.bind((host, port))
        # Backlog of 8 lets multiple MCP clients (Claude Desktop + Claude Code +
        # Goose, etc.) queue their connections instead of getting refused.
        # Commands still serialize on Blender's main thread via bpy.app.timers
        # — the backlog just prevents connect-time rejection. Paired with the
        # MCP server's connect-per-command behavior in BlenderConnection.send,
        # this lets clients interleave fairly at the request granularity.
        _server_socket.listen(8)
        print(f"[Printable Bridge] Listening on {host}:{port}")

        while _running:
            try:
                conn, addr = _server_socket.accept()
                conn.settimeout(None)  # Blocking reads on client connection
                # Handle one client at a time. With short-lived per-command
                # connections from the MCP server, the next waiting client gets
                # picked up as soon as this one disconnects.
                _handle_client(conn, addr)
            except socket.timeout:
                continue
            except OSError:
                if _running:
                    traceback.print_exc()
                break
    finally:
        _server_socket.close()
        _server_socket = None
        print("[Printable Bridge] Server stopped")


# ---------------------------------------------------------------------------
# Blender addon preferences
# ---------------------------------------------------------------------------

class PrintableBridgePreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    host: bpy.props.StringProperty(
        name="Host",
        default="127.0.0.1",
        description="Bind address (use 127.0.0.1 for local only)",
    )
    port: bpy.props.IntProperty(
        name="Port",
        default=9876,
        min=1024, max=65535,
        description="TCP port for the Printable bridge",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "host")
        layout.prop(self, "port")
        if _running:
            layout.label(text=f"Status: listening on {self.host}:{self.port}", icon='CHECKMARK')
        else:
            layout.label(text="Status: stopped", icon='ERROR')


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _start_server():
    global _server_thread, _running
    if _running:
        return

    prefs = bpy.context.preferences.addons[__package__].preferences
    _running = True

    _server_thread = threading.Thread(
        target=_server_loop,
        args=(prefs.host, prefs.port),
        daemon=True,
        name="PrintableBridgeTCP",
    )
    _server_thread.start()
    bpy.app.timers.register(_process_commands, persistent=True)


def _stop_server():
    global _running, _server_socket
    _running = False
    if _server_socket:
        try:
            _server_socket.close()
        except OSError:
            pass
    if bpy.app.timers.is_registered(_process_commands):
        bpy.app.timers.unregister(_process_commands)


def register():
    bpy.utils.register_class(PrintableBridgePreferences)
    # Delay server start slightly so Blender is fully initialized
    bpy.app.timers.register(lambda: (_start_server(), None)[-1], first_interval=1.0)


def unregister():
    _stop_server()
    bpy.utils.unregister_class(PrintableBridgePreferences)
