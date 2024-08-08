import logging
import random
import socket
import string
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock, Thread

from bittorrent.models import Block, Metainfo, Peer, Piece, Tracker
from bittorrent.models.message import PieceMessage
from bittorrent.util import config

logger = logging.getLogger("BitTorrentClient")


class BitTorrentClient:
    def __init__(self, metainfo_filepath: str, port: int = config.PORT) -> None:
        logger.debug(f"Initializing with metainfo file {metainfo_filepath}")
        self.metainfo_filepath = metainfo_filepath
        # Generate a random peer ID
        self.id = (
            config.PEER_ID_PREFIX
            + "".join(
                random.choices(string.ascii_letters + string.digits, k=12)
            ).encode()
        )
        logger.debug(f"Generated peer ID: {self.id}")
        # Create the output directory
        self.output_dir = Path(config.OUTPUT_DIR).absolute()
        # Initialize the socket
        self.port = port
        # Initialize the metainfo and trackers
        self.metainfo = Metainfo(self)

        self.trackers = [Tracker(self, url) for url in self.metainfo.announce_urls]
        self.peers_obj = Peers(self)
        self.pieces_obj = Pieces(self)
        # Start listening for incoming connections
        Thread(target=self.listen_for_connections, daemon=True).start()

    def download(self):
        download_thread = Thread(target=self.peers_obj.download, daemon=True)
        download_thread.start()
        progress_thread = Thread(
            target=self.pieces_obj.log_download_progress, daemon=True
        )
        progress_thread.start()
        download_thread.join()
        logger.info(f"Downloaded all pieces {self.finished_downloading}")
        if self.finished_downloading:
            self.write_files()

    def listen_for_connections(self):
        # listen for incoming connections
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(config.SOCKET_TIMEOUT)
        sock.bind((socket.gethostname(), self.port))
        sock.listen()
        while True:
            try:
                conn, addr = sock.accept()
                peer = Peer(self, *addr)
                peer.socket = conn
                peer.start_listen_thread()
            except socket.timeout:
                pass

    def broadcast_have(self, piece: "Piece"):
        for peer in self.peers_obj.peers:
            if piece not in peer.bitfield_pieces:
                peer.send_have(piece.index)

    def write_files(self):
        for file in self.metainfo.files:
            file.write_file()

    @property
    def data(self) -> bytes:
        return b"".join(piece.data for piece in self.pieces_obj.pieces)

    @property
    def finished_downloading(self) -> bool:
        return all(piece.finished for piece in self.pieces_obj.pieces)

    @property
    def in_end_game(self) -> bool:
        unfinished_pieces = [
            piece for piece in self.pieces_obj.pieces if not piece.finished
        ]
        unfinished_blocks = [
            block
            for piece in unfinished_pieces
            for block in piece.blocks
            if not block.data
        ]
        enter_threshold = config.MAX_PEERS * config.MAX_PENDING_BLOCK_REQUESTS * 2
        return len(unfinished_blocks) <= enter_threshold
        # return all(
        #     all([block.requested for block in piece.blocks])
        #     for piece in self.pieces_obj.pieces
        # )


peers_logger = logging.getLogger("Peers")


class Peers:
    def __init__(self, client: BitTorrentClient) -> None:
        self.client = client
        self.peers = self._get_peers()

    def _get_peers(self) -> list[Peer]:
        peers: list[Peer] = []
        for tracker in self.client.trackers:
            try:
                peers.extend(tracker.get_peers())
            except Exception as e:
                print(f"Failed to get peers from {tracker.announce}: {e}")
        peers_logger.debug(f"Got {len(peers)} peers")
        return peers

    def download(self):
        with ThreadPoolExecutor(max_workers=config.MAX_PEERS) as executor:

            def _download(peer: Peer) -> Peer:
                peer.download()
                return peer

            new_futures = [executor.submit(_download, peer) for peer in self.peers]
            while not self.client.finished_downloading:
                futures = new_futures
                new_futures = []
                for result in as_completed(futures):
                    if res := result.result():
                        new_futures.append(executor.submit(res.download))


pieces_logger = logging.getLogger("Pieces")


class Pieces:
    def __init__(self, client: BitTorrentClient) -> None:
        self.client = client
        self.pieces = self._get_pieces()
        self.lock = Lock()

    def _get_pieces(self) -> list[Piece]:
        pieces: list[Piece] = []
        num_pieces = -(
            -self.client.metainfo.total_length // self.client.metainfo.piece_length
        )
        for i in range(0, num_pieces):
            offset = i * self.client.metainfo.piece_length
            length = min(
                self.client.metainfo.piece_length,
                self.client.metainfo.total_length
                - (i * self.client.metainfo.piece_length),
            )
            pieces.append(
                Piece(
                    self.client, i, offset, length, self.client.metainfo.piece_hashes[i]
                )
            )
        pieces_logger.info(f"Generated {len(pieces)} pieces")
        return pieces

    def send_missing_request(self, peer: Peer) -> list[Block] | None:
        if not any(peer.bitfield_pieces):
            return
        with self.lock:
            ready_pieces = [piece for piece in peer.bitfield_pieces if piece.ready]
            if not ready_pieces:
                return

        peer_pending_pieces = [
            self.pieces[pending_block_request.block.index]
            for pending_block_request in peer.updated_pending_block_requests
        ]
        filtered_pieces = [
            piece for piece in ready_pieces if piece not in peer_pending_pieces
        ]

        blocks: list[Block] = []
        for ready_piece in filtered_pieces:
            missing_blocks = [block for block in ready_piece.blocks if not block.data]
            for missing_block in missing_blocks:
                message = missing_block.get_request_message(True)

                if message:
                    peer.send_message(message)
                    pieces_logger.debug(
                        f"Sent request for piece {ready_piece.index} block {missing_block.offset} to {peer.ip}:{peer.port}"
                    )
                blocks.append(missing_block)

        return blocks

    def send_best_piece_request(self, peer: Peer) -> Block | None:
        if not any(peer.bitfield_pieces):
            return
        with self.lock:
            ready_pieces = [piece for piece in peer.bitfield_pieces if piece.ready]
            if not ready_pieces:
                return
            rarest_piece = min(ready_pieces, key=lambda piece: len(piece.peers))
            rarest_pieces = [
                piece
                for piece in ready_pieces
                if len(piece.peers) == len(rarest_piece.peers)
            ]
            chosen_piece = random.choice(rarest_pieces)
            # Have to set request_pending to prevent multiple requests for the same piece from different threads
            block = random.choice(chosen_piece.ready_blocks)
            message = block.get_request_message()  # marks the block as requested
        # drop the lock before sending the request to improve performance
        if message:
            peer.send_message(message)
            pieces_logger.debug(
                f"Sent request for piece {chosen_piece.index} block {block.offset} to {peer.ip}:{peer.port}"
            )
        return block

    def handle_piece_message(self, message: PieceMessage):
        self.pieces[message.index].receive_data(message)
        pieces_logger.debug(f"Received piece {message.index} block {message.begin}")

    def log_download_progress(self):
        while not self.client.finished_downloading:
            time.sleep(config.TIME_BETWEEN_PROGRESS_UPDATES)
            num_downloaded_blocks = sum(
                len(piece.finished_blocks) for piece in self.pieces
            )
            total_blocks = sum(len(piece.blocks) for piece in self.pieces)
            pieces_logger.info(
                f"Downloaded {num_downloaded_blocks}/{total_blocks} blocks"
            )
