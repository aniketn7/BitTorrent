import hashlib
from enum import Enum
from typing import TYPE_CHECKING, Any

import bencoder

from bittorrent.models.torrentfile import TorrentFile

if TYPE_CHECKING:
    from bittorrent.bittorrentclient import BitTorrentClient

import logging

logger = logging.getLogger("Metainfo")


class MetainfoMode(Enum):
    SINGLE_FILE = 1
    MULTIPLE_FILE = 2


class Metainfo:
    def __init__(self, client: "BitTorrentClient"):
        self.client = client
        with open(client.metainfo_filepath, "rb") as f:
            self.metainfo = bencoder.decode(f.read())
        assert isinstance(self.metainfo, dict)
        # metainfo file is a dictionary with the following
        self.info: dict[bytes, Any] = self.metainfo[b"info"]
        self.announce: bytes = self.metainfo.get(b"announce", b"")
        self.announce_list: list[list[bytes]] = self.metainfo.get(b"announce-list", [])
        self.creation_date: int = self.metainfo.get(b"creation date", 0)
        self.comment: bytes = self.metainfo.get(b"comment", b"")
        self.created_by: bytes = self.metainfo.get(b"created by", b"")
        self.encoding: bytes = self.metainfo.get(b"encoding", b"")
        # info dictionary is a dictionary with the following
        self.info_hash: bytes = hashlib.sha1(bencoder.encode(self.info)).digest()
        self.piece_length: int = self.info[b"piece length"]
        piece_hashes_bytes: bytes = self.info[b"pieces"]
        # split the piece hashes into 20 byte chunks
        self.piece_hashes: list[bytes] = [
            piece_hashes_bytes[i : i + 20]
            for i in range(0, len(piece_hashes_bytes), 20)
        ]
        self.private: bool = self.info.get(b"private", 0) == 1
        # info in single file mode has the following
        self.mode = (
            MetainfoMode.SINGLE_FILE
            if b"length" in self.info
            else MetainfoMode.MULTIPLE_FILE
        )
        self.name: bytes = self.info[b"name"]
        if self.mode == MetainfoMode.SINGLE_FILE:
            length: int = self.info[b"length"]
            md5sum: bytes = self.info.get(b"md5sum", b"")
            self.files: list[TorrentFile] = [
                TorrentFile(client, length, md5sum, self.name)
            ]
        else:
            files: list[dict] = self.info[b"files"]
            self.files: list[TorrentFile] = [
                TorrentFile(
                    self.client,
                    file[b"length"],
                    b"/".join(file.get(b"md5sum", b"")),
                    file[b"path"],
                )
                for file in files
            ]
        logger.info(
            f"Initialized Metainfo with name '{self.name.decode()}' and {len(self.files)} files"
        )

    @property
    def total_length(self) -> int:
        return sum(file.length for file in self.files)

    @property
    def announce_urls(self) -> list[str]:
        announce_list_urls = [url for urls in self.announce_list for url in urls]
        announce_urls = [self.announce] + announce_list_urls
        return [url.decode() for url in announce_urls if url and isinstance(url, bytes)]
