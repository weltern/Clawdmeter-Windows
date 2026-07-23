"""Single-instance guard for Clawdmeter.

Without this, each relaunch starts a brand-new process while the previous one
is still alive in the system tray, so processes pile up. This uses a
QLocalServer named pipe (a Unix domain socket off Windows): the first instance
listens; later launches connect to it, ask it to show its window, and exit
immediately instead of duplicating.
"""

from __future__ import annotations

from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "Clawdmeter.singleton"


def activate_running_instance(timeout_ms: int = 300) -> bool:
    """If another instance is already running, ask it to show; return True.

    Returns False if no instance is running (caller should become primary).
    """
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(b"show")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class InstanceServer:
    """Listens for later-launch 'show' pings and invokes on_show() each time."""

    def __init__(self, on_show) -> None:
        self._on_show = on_show
        self._server = QLocalServer()
        # Clear any stale pipe left behind by a crash so listen() succeeds.
        QLocalServer.removeServer(SERVER_NAME)
        self._server.listen(SERVER_NAME)
        self._server.newConnection.connect(self._on_new_connection)

    def _on_new_connection(self) -> None:
        conn = self._server.nextPendingConnection()
        if conn is None:
            return
        conn.readAll()  # payload is irrelevant; any connection means "show"
        conn.disconnectFromServer()
        if self._on_show:
            self._on_show()

    def close(self) -> None:
        self._server.close()
