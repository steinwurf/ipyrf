import socket
from unittest.mock import MagicMock

import pytest

from ipyrf.utils import bind_to_device


def test_bind_to_device_sets_so_bindtodevice():
    if not hasattr(socket, "SO_BINDTODEVICE"):
        pytest.skip("SO_BINDTODEVICE not available on this platform")

    sock = MagicMock()
    bind_to_device(sock, "eth0")
    sock.setsockopt.assert_called_once_with(
        socket.SOL_SOCKET,
        socket.SO_BINDTODEVICE,
        b"eth0",
    )


def test_bind_to_device_wraps_oserror():
    if not hasattr(socket, "SO_BINDTODEVICE"):
        pytest.skip("SO_BINDTODEVICE not available on this platform")

    sock = MagicMock()
    sock.setsockopt.side_effect = OSError(1, "Operation not permitted")
    with pytest.raises(OSError, match="Failed to bind socket to device 'eth0'"):
        bind_to_device(sock, "eth0")
