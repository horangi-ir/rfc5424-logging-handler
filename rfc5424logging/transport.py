import io
import socket
import ssl
import sys

if sys.version_info.major == 3:
    text_stream_types = io.TextIOBase
    bytes_stream_types = io.BufferedIOBase
else:
    text_stream_types = io.TextIOBase
    bytes_stream_types = io.BufferedIOBase, file  # noqa: F821

SYSLOG_PORT = 514

# RFC6587 framing
FRAMING_OCTET_COUNTING = 1
FRAMING_NON_TRANSPARENT = 2


class TCPSocketTransport:
    def __init__(self, address, timeout, framing):
        self.socket = None
        self.address = address
        self.timeout = timeout
        self.framing = framing
        self.open()

    def open(self):
        error = None
        host, port = self.address
        addrinfo = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        if not addrinfo:
            raise OSError("getaddrinfo returns an empty list")
        for entry in addrinfo:
            family, socktype, _, _, sockaddr = entry
            try:
                self.socket = socket.socket(family, socktype)
                self.socket.settimeout(self.timeout)
                self.socket.connect(sockaddr)
                # Connected successfully. Erase any previous errors.
                error = None
                break
            except OSError as e:
                error = e
                if self.socket is not None:
                    self.socket.close()
        if error is not None:
            raise error

    def transmit(self, syslog_msg):
        # RFC6587 framing
        if self.framing == FRAMING_NON_TRANSPARENT:
            syslog_msg = syslog_msg.replace(b"\n", b"\\n")
            syslog_msg = b"".join((syslog_msg, b"\n"))
        else:
            syslog_msg = b" ".join((str(len(syslog_msg)).encode("ascii"), syslog_msg))

        try:
            self.socket.sendall(syslog_msg)
        except (OSError, IOError):
            self.close()
            self.open()
            self.socket.sendall(syslog_msg)

    def close(self):
        self.socket.close()


class TLSSocketTransport(TCPSocketTransport):
    def __init__(
        self,
        address,
        timeout,
        framing,
        tls_ca_bundle,
        tls_verify,
        tls_client_cert,
        tls_client_key,
        tls_key_password,
    ):
        self.tls_ca_bundle = tls_ca_bundle
        self.tls_verify = tls_verify
        self.tls_client_cert = tls_client_cert
        self.tls_client_key = tls_client_key
        self.tls_key_password = tls_key_password
        super(TLSSocketTransport, self).__init__(address, timeout, framing=framing)

    def open(self):
        super(TLSSocketTransport, self).open()
        context = ssl.create_default_context(
            purpose=ssl.Purpose.SERVER_AUTH, cafile=self.tls_ca_bundle
        )
        # copied from https://github.com/jobec/rfc5424-logging-handler/pull/39
        # if/when this PR gets merged we should use the pypi provided rfc5424-logging-handler package
        context.check_hostname = self.tls_verify
        context.verify_mode = ssl.CERT_REQUIRED if self.tls_verify else ssl.CERT_NONE
        server_hostname, _ = self.address
        if self.tls_client_cert:
            context.load_cert_chain(
                self.tls_client_cert, self.tls_client_key, self.tls_key_password
            )
        self.socket = context.wrap_socket(self.socket, server_hostname=server_hostname)


class UDPSocketTransport:
    def __init__(self, address, timeout):
        self.socket = None
        self.address = address
        self.timeout = timeout
        self.open()

    def open(self):
        error = None
        host, port = self.address
        addrinfo = socket.getaddrinfo(host, port, 0, socket.SOCK_DGRAM)
        if not addrinfo:
            raise OSError("getaddrinfo returns an empty list")
        for entry in addrinfo:
            family, socktype, _, _, sockaddr = entry
            try:
                self.socket = socket.socket(family, socktype)
                self.socket.settimeout(self.timeout)
                self.address = sockaddr
                break
            except OSError as e:
                error = e
                if self.socket is not None:
                    self.socket.close()
        if error is not None:
            raise error

    def transmit(self, syslog_msg):
        try:
            self.socket.sendto(syslog_msg, self.address)
        except (OSError, IOError):
            self.close()
            self.open()
            self.socket.sendto(syslog_msg, self.address)

    def close(self):
        self.socket.close()


class UnixSocketTransport:
    def __init__(self, address, socket_type):
        self.socket = None
        self.address = address
        self.socket_type = socket_type
        self.open()

    def open(self):
        if self.socket_type is None:
            socket_types = [socket.SOCK_DGRAM, socket.SOCK_STREAM]
        else:
            socket_types = [self.socket_type]

        for type_ in socket_types:
            # Syslog server may be unavailable during handler initialisation.
            # So we ignore connection errors
            try:
                self.socket = socket.socket(socket.AF_UNIX, type_)
                self.socket.connect(self.address)
                self.socket_type = type_
                break
            except OSError:
                if self.socket is not None:
                    self.socket.close()

    def transmit(self, syslog_msg):
        try:
            self.socket.send(syslog_msg)
        except (OSError, IOError):
            self.close()
            self.open()
            self.socket.send(syslog_msg)

    def close(self):
        self.socket.close()


class StreamTransport:
    def __init__(self, stream):
        if isinstance(stream, text_stream_types):
            self.text_mode = True
        elif isinstance(stream, bytes_stream_types):
            self.text_mode = False
        else:
            raise ValueError("Stream is not of a valid stream type")

        if not stream.writable():
            raise ValueError("Stream is not a writeable stream")

        self.stream = stream

    def transmit(self, syslog_msg):
        syslog_msg = syslog_msg + b"\n"
        if self.text_mode:
            syslog_msg = syslog_msg.decode(self.stream.encoding, "replace")
        self.stream.write(syslog_msg)

    def close(self):
        # Closing the stream is left up to the user.
        pass
