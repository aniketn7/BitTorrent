import hashlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bittorrent.bittorrentclient import BitTorrentClient

logger = logging.getLogger("TorrentFile")


class TorrentFile:
    def __init__(
        self, client: "BitTorrentClient", length: int, md5sum: bytes, path: bytes
    ):
        self.client = client
        self.length = length
        self.md5sum = md5sum
        self.path = path

    @property
    def start_byte(self) -> int:
        return sum(
            [
                file.length
                for file in self.client.metainfo.files[
                    : self.client.metainfo.files.index(self)
                ]
            ]
        )

    @property
    def end_byte(self) -> int:
        return self.start_byte + self.length

    @property
    def data(self) -> bytes:
        return self.client.data[self.start_byte : self.end_byte]

    def verify(self) -> bool:
        return self.md5sum == hashlib.md5(self.data).digest()

    def load_file(self):
        if os.path.exists(self.path):
            with open(self.path, "rb") as f:
                data = f.read()
        for piece in self.client.pieces_obj.pieces:
            piece_start_byte = piece.index * self.client.metainfo.piece_length
            for block in piece.blocks:
                block_start_byte = piece_start_byte + block.offset
                block_end_byte = block_start_byte + block.length
                if (
                    block_start_byte >= self.start_byte
                    and block_end_byte <= self.end_byte
                ):
                    block.data = data[
                        block_start_byte
                        - self.start_byte : block_end_byte
                        - self.start_byte
                    ]

    def write_file(self):
        # if not self.verify():
        #     return
        path = self.client.output_dir / Path(self.path.decode())
        logger.info(
            f"Writing file {path} ({self.length} bytes) ({self.verify()} verified)"
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(self.path, "wb") as f:
            f.write(self.data)
