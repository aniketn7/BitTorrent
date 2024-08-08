import hashlib
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from bittorrent.models.message import Have, Request
from bittorrent.util import config

if TYPE_CHECKING:
    from bittorrent.bittorrentclient import BitTorrentClient
    from bittorrent.models.message import PieceMessage
    from bittorrent.models.peer import Peer

logger = logging.getLogger("Piece")


class Block:
    def __init__(self, index: int, offset: int, length: int) -> None:
        # Index of the piece this block belongs to
        self.index = index
        # Offset of this block relative to the piece
        self.offset = offset
        # Length of this block in bytes
        self.length = length
        # Data of this block
        self.data = b""
        # Last time a request was made for this block
        self.last_request_time = datetime.min

        self.requested = False

    @property
    def ready(self) -> bool:
        elapsed = (datetime.now() - self.last_request_time).total_seconds()
        return elapsed >= config.BLOCK_REQUEST_TIMEOUT and not self.data

    def get_request_message(self, force=False) -> Request | None:
        if self.ready or (force and not self.data):
            self.last_request_time = datetime.now()
            self.requested = True
            return Request(self.index, self.offset, self.length)


class Piece:
    def __init__(
        self,
        client: "BitTorrentClient",
        index: int,
        offset: int,
        length: int,
        hash: bytes,
    ) -> None:
        # logger.debug(
        #     f"Initializing piece {index} at offset {offset} with length {length}"
        # )
        # Client
        self.client = client
        # Index of this piece relative to the torrent
        self.index = index
        # Offset of this piece relative to the torrent
        self.offset = offset
        # Length of this piece in bytes
        self.length = length
        # SHA1 hash of the piece data for verification
        self.hash = hash
        # List of peers that have this piece
        self.peers: list["Peer"] = []

        # Generate blocks that make up this piece
        def generate_blocks() -> list[Block]:
            blocks = []
            for offset in range(0, self.length, config.BLOCK_SIZE):
                block_length = min(config.BLOCK_SIZE, self.length - offset)
                blocks.append(Block(self.index, offset, block_length))
            return blocks

        self.blocks: list[Block] = generate_blocks()

    # Request a block from a peer
    def request_from_peer(self, peer: "Peer"):
        # Check we have blocks to request and the peer is interested
        if not self.ready_blocks or not peer.am_interested:
            return
        # Request a block from the peer
        if message := self.ready_blocks[0].get_request_message():
            peer.send_message(message)

    # Receive a block from a peer
    def receive_data(self, message: "PieceMessage"):
        # Find the block that this message corresponds to
        block = next(
            (block for block in self.blocks if block.offset == message.begin), None
        )
        # If the block doesn't exist, log an error and return
        if not block:
            logger.error(
                f"Received block {message.begin} that doesn't exist (piece {self.index})"
            )
            return
        # If the block is of an invalid length, log an error and return
        if not len(message.block) == block.length:
            logger.error(
                f"Received block {message.begin} with invalid length (piece {self.index}) {len(message.block)} != {block.length}"
            )
            return
        # Set the block data to the message data
        block.data = message.block

        if self.client.in_end_game:
            for peer in self.client.peers_obj.peers:
                peer.send_cancel(self.index, message.begin, block.length)

        # Check if the piece is finished and verify the hash
        self.finalize()

    def finalize(self):
        if self.finished:
            if self.hash_is_valid:
                for peer in self.client.peers_obj.peers:
                    if peer not in self.peers and self not in peer.bitfield_pieces:
                        peer.send_message(Have(self.index))
                # self.client.broadcast_have(self)  # TODO: UNCOMMENT
                # logger.info(f"Piece {self.index} has been downloaded successfully")
                pass
            else:
                logger.error(f"Piece {self.index} has an invalid hash")
                # Reset the piece data if the hash is invalid
                for block in self.blocks:
                    block.data = b""

    # Helper properties

    @property
    def ready_blocks(self) -> list[Block]:
        return [block for block in self.blocks if block.ready]

    @property
    def finished_blocks(self) -> list[Block]:
        return [block for block in self.blocks if block.data]

    @property
    def ready(self) -> bool:
        return any(block.ready for block in self.blocks)

    @property
    def finished(self) -> bool:
        return all(block.data for block in self.blocks)

    @property
    def data(self) -> bytes:
        return b"".join(block.data for block in self.blocks)

    @property
    def hash_is_valid(self) -> bool:
        return self.finished and self.hash == hashlib.sha1(self.data).digest()
