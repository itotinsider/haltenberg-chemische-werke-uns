"""A small Modbus TCP server (the plant's field I/O rack).

Hand-written rather than pymodbus' server on purpose: pymodbus 3.x has deprecated its whole
server datastore API ahead of v4, and the subset a remote I/O rack needs (FC1-6, 15, 16) is
~100 lines of framing. The DCS side still uses the real pymodbus client, so every scan is an
interoperability check of this implementation against a third-party stack.
"""

import logging
import socketserver
import struct
import threading

log = logging.getLogger("plant.modbus")

ILLEGAL_FUNCTION = 1
ILLEGAL_ADDRESS = 2
ILLEGAL_VALUE = 3


class RegisterBank:
    """The four Modbus tables, guarded by one lock (plant loop writes, server threads read)."""

    def __init__(self, n_coils, n_discrete, n_holding, n_input):
        self.lock = threading.Lock()
        self.coils = [False] * n_coils
        self.discrete = [False] * n_discrete
        self.holding = [0] * n_holding
        self.input = [0] * n_input
        self.requests = 0  # served request counter, shown on the instructor station


def _pack_bits(bits):
    out = bytearray((len(bits) + 7) // 8)
    for i, b in enumerate(bits):
        if b:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


def _unpack_bits(data, count):
    return [bool(data[i // 8] & (1 << (i % 8))) for i in range(count)]


def handle_pdu(bank, pdu):
    fc = pdu[0]

    def error(code):
        return bytes([fc | 0x80, code])

    try:
        if fc in (1, 2, 3, 4):
            addr, count = struct.unpack(">HH", pdu[1:5])
            with bank.lock:
                table = {1: bank.coils, 2: bank.discrete, 3: bank.holding, 4: bank.input}[fc]
                if count < 1 or addr + count > len(table):
                    return error(ILLEGAL_ADDRESS)
                values = table[addr:addr + count]
            if fc in (1, 2):
                data = _pack_bits(values)
            else:
                data = struct.pack(f">{count}H", *values)
            return bytes([fc, len(data)]) + data
        if fc == 5:
            addr, raw = struct.unpack(">HH", pdu[1:5])
            if raw not in (0x0000, 0xFF00):
                return error(ILLEGAL_VALUE)
            with bank.lock:
                if addr >= len(bank.coils):
                    return error(ILLEGAL_ADDRESS)
                bank.coils[addr] = raw == 0xFF00
            return pdu[:5]
        if fc == 6:
            addr, value = struct.unpack(">HH", pdu[1:5])
            with bank.lock:
                if addr >= len(bank.holding):
                    return error(ILLEGAL_ADDRESS)
                bank.holding[addr] = value
            return pdu[:5]
        if fc == 15:
            addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
            with bank.lock:
                if addr + count > len(bank.coils):
                    return error(ILLEGAL_ADDRESS)
                bank.coils[addr:addr + count] = _unpack_bits(pdu[6:6 + nbytes], count)
            return pdu[:5]
        if fc == 16:
            addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
            values = struct.unpack(f">{count}H", pdu[6:6 + nbytes])
            with bank.lock:
                if addr + count > len(bank.holding):
                    return error(ILLEGAL_ADDRESS)
                bank.holding[addr:addr + count] = list(values)
            return pdu[:5]
    except (struct.error, IndexError):
        return error(ILLEGAL_VALUE)
    return error(ILLEGAL_FUNCTION)


def serve(bank, host, port):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            peer = "%s:%s" % self.client_address
            log.info("Modbus client connected: %s", peer)
            sock = self.request
            try:
                while True:
                    header = _recv_exact(sock, 7)
                    if not header:
                        break
                    tid, pid, length, unit = struct.unpack(">HHHB", header)
                    pdu = _recv_exact(sock, length - 1)
                    if not pdu:
                        break
                    reply = handle_pdu(bank, pdu)
                    with bank.lock:
                        bank.requests += 1
                    sock.sendall(struct.pack(">HHHB", tid, pid, len(reply) + 1, unit) + reply)
            except (ConnectionError, OSError):
                pass
            log.info("Modbus client disconnected: %s", peer)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = Server((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf
