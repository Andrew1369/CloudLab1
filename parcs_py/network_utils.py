import socket


def find_free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def get_ip():
    # Explicit --host is used in GCP; local launches bind only to loopback.
    return "127.0.0.1"
