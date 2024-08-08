from typing import TYPE_CHECKING

import bencoder

from bittorrent.models.peer import Peer
from bittorrent.util import http

if TYPE_CHECKING:
    from bittorrent.bittorrentclient import BitTorrentClient

import logging

logger = logging.getLogger("HTTPTracker")

SPOOF_PEERS = False


class HTTPTracker:
    def __init__(self, client: "BitTorrentClient", announce: str = "") -> None:
        self.client = client
        self.info_hash = client.metainfo.info_hash
        self.client_id = client.id
        self.port = client.port
        self.left = client.metainfo.total_length
        self.announce = announce or client.metainfo.announce.decode()

    def get_peers(self) -> list[Peer]:
        if SPOOF_PEERS:
            return [Peer(self.client, ip, port) for ip, port in [("127.0.0.1", 6889)]]
        params = {
            "info_hash": self.info_hash,
            "peer_id": self.client_id,
            "port": self.port,
            "uploaded": 0,
            "downloaded": 0,
            "left": self.left,
            "event": "started",
            # "compact": 1,
        }
        raw_response_data = http.get(self.announce, params)
        response_data = bencoder.decode(raw_response_data)
        assert isinstance(response_data, dict)
        peers_data = response_data[b"peers"]
        if isinstance(peers_data, list):
            return self._get_normal_peers(peers_data)
        elif isinstance(peers_data, bytes):
            return self._get_compact_peers(peers_data)
        else:
            raise ValueError("Invalid response data")

    def _get_normal_peers(self, peers_data: list) -> list[Peer]:
        peers = []
        for peer_data in peers_data:
            assert isinstance(peer_data, dict)
            ip = peer_data[b"ip"].decode()
            port = peer_data[b"port"]
            peer_id = peer_data.get(b"peer id", b"")
            peers.append(Peer(self.client, ip, port, peer_id))
        logger.debug(f"Got {len(peers)} peers from {self.announce} (normal)")
        return peers

    def _get_compact_peers(self, peers_data: bytes) -> list[Peer]:
        peers = []
        for i in range(0, len(peers_data), 6):
            # ip_int = int.from_bytes(peers_data[i : i + 4], "big")
            # ip = ".".join(str((ip_int >> i) & 0xFF) for i in (24, 16, 8, 0))
            ip = ".".join(str(byte) for byte in peers_data[i : i + 4])
            port = int.from_bytes(peers_data[i + 4 : i + 6], "big")
            peers.append(Peer(self.client, ip, port))
        logger.debug(f"Got {len(peers)} peers from {self.announce} (compact)")
        return peers
