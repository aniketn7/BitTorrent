import struct

from bittorrent.util import config


class Message:
    def to_bytes(self):
        raise NotImplementedError

    @staticmethod
    def from_bytes(data: bytes) -> "Message":
        message_type = data[4] if len(data) > 4 else -1
        match message_type:
            case -1:
                return KeepAlive()
            case 0:
                return Choke()
            case 1:
                return Unchoke()
            case 2:
                return Interested()
            case 3:
                return NotInterested()
            case 4:
                return Have.from_bytes(data)
            case 5:
                return Bitfield.from_bytes(data)
            case 6:
                return Request.from_bytes(data)
            case 7:
                return PieceMessage.from_bytes(data)
            case 8:
                return Cancel.from_bytes(data)
            case 9:
                return Port.from_bytes(data)
            case _:
                try:
                    return Handshake.from_bytes(data)
                except ValueError:
                    return KeepAlive()


class Handshake(Message):
    def __init__(self, info_hash: bytes, id: bytes):
        self.info_hash = info_hash
        self.id = id

    def to_bytes(self):
        return struct.pack(
            f">B{len(config.PROTOCOL)}s8x20s20s",
            len(config.PROTOCOL),
            config.PROTOCOL,
            self.info_hash,
            self.id,
        )

    @staticmethod
    def from_bytes(data: bytes):
        _, _, info_hash, id = struct.unpack(f">B{len(config.PROTOCOL)}s8x20s20s", data)
        return Handshake(info_hash, id)


class KeepAlive(Message):
    def to_bytes(self):
        return struct.pack(">I", 0)

    @staticmethod
    def from_bytes(data: bytes):
        return KeepAlive()


class Choke(Message):
    ID = 0

    def to_bytes(self):
        return struct.pack(">IB", 1, self.ID)

    @staticmethod
    def from_bytes(data: bytes):
        return Choke()


class Unchoke(Message):
    ID = 1

    def to_bytes(self):
        return struct.pack(">IB", 1, self.ID)

    @staticmethod
    def from_bytes(data: bytes):
        return Unchoke()


class Interested(Message):
    ID = 2

    def to_bytes(self):
        return struct.pack(">IB", 1, self.ID)

    @staticmethod
    def from_bytes(data: bytes):
        return Interested()


class NotInterested(Message):
    ID = 3

    def to_bytes(self):
        return struct.pack(">IB", 1, self.ID)

    @staticmethod
    def from_bytes(data: bytes):
        return NotInterested()


class Have(Message):
    ID = 4

    def __init__(self, piece_index: int):
        self.piece_index = piece_index

    def to_bytes(self):
        return struct.pack(">IBI", 5, self.ID, self.piece_index)

    @staticmethod
    def from_bytes(data: bytes):
        _, _, piece_index = struct.unpack(">IBI", data)
        return Have(piece_index)


class Bitfield(Message):
    ID = 5

    def __init__(self, bitfield: list[bool]):
        self.bitfield = bitfield

    def _bool_to_byte(self, bitfield: list[bool]) -> bytes:
        byte = 0
        for i, bit in enumerate(bitfield):
            byte |= bit << (7 - i)
        return byte.to_bytes(1, "big")

    @property
    def bitfield_bytes(
        self,
    ) -> bytes:
        return b"".join(
            self._bool_to_byte(self.bitfield[i : i + 8])
            for i in range(0, len(self.bitfield), 8)
        )

    def to_bytes(self):
        return struct.pack(
            f">IB{len(self.bitfield_bytes)}s",
            1 + len(self.bitfield_bytes),
            self.ID,
            self.bitfield_bytes,
        )

    @staticmethod
    def from_bytes(data: bytes):
        _, _, bitfield_bytes = struct.unpack(f">IB{len(data) - 5}s", data)
        bitfield = []
        for byte in bitfield_bytes:
            for i in range(8):
                bit = (byte >> (7 - i)) & 1
                bitfield.append(bool(bit))
        return Bitfield(bitfield)


class Request(Message):
    ID = 6

    def __init__(self, index: int, begin: int, length: int):
        self.index = index
        self.begin = begin
        self.length = length

    def to_bytes(self):
        return struct.pack(">IBIII", 13, self.ID, self.index, self.begin, self.length)

    @staticmethod
    def from_bytes(data: bytes):
        _, _, index, begin, length = struct.unpack(">IBIII", data)
        return Request(index, begin, length)


class PieceMessage(Message):
    ID = 7

    def __init__(self, index: int, begin: int, block: bytes):
        self.index = index
        self.begin = begin
        self.block = block

    def to_bytes(self):
        return struct.pack(
            f">IBII{len(self.block)}s",
            9 + len(self.block),
            self.ID,
            self.index,
            self.begin,
            self.block,
        )

    @staticmethod
    def from_bytes(data: bytes):
        _, _, index, begin, block = struct.unpack(f">IBII{len(data) - 13}s", data)
        return PieceMessage(index, begin, block)

    def __repr__(self):
        return f"PieceMessage(index={self.index}, begin={self.begin}, length={len(self.block)})"


class Cancel(Message):
    ID = 8

    def __init__(self, index: int, begin: int, length: int):
        self.index = index
        self.begin = begin
        self.length = length

    def to_bytes(self):
        return struct.pack(">IBIII", 13, self.ID, self.index, self.begin, self.length)

    @staticmethod
    def from_bytes(data: bytes):
        _, _, index, begin, length = struct.unpack(">IBIII", data)
        return Cancel(index, begin, length)


class Port(Message):
    ID = 9

    def __init__(self, port: int):
        self.port = port

    def to_bytes(self):
        return struct.pack(">IBH", 3, self.ID, self.port)

    @staticmethod
    def from_bytes(data: bytes):
        _, _, port = struct.unpack(">IBH", data)
        return Port(port)
