import pytest
import sys
import socket

# Import test utilities from the ipyrf.test module
from ipyrf.test import IPyrfBuilder


def pick_free_port(shell=None):
    """
    Pick a free port on the system.

    Args:
        shell: Optional dummynet shell to use for picking the port.
               If provided, picks a port in that shell's context.

    Returns:
        int: A free port number.
    """
    if shell is not None:
        python = sys.executable
        cmd = (
            f"{python} -c 'import socket; s=socket.socket(); "
            f's.bind(("",0)); print(s.getsockname()[1]); s.close()\''
        )
        output = shell.run(cmd)
        return int(output.stdout.strip())

    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="function")
def ipyrf(testdirectory):
    return IPyrfBuilder(testdirectory)


@pytest.fixture(scope="function")
def free_port():
    return pick_free_port()


@pytest.fixture(scope="function")
def free_port_func():
    return pick_free_port
