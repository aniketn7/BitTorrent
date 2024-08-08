import logging
import socket
import time
from datetime import datetime
from threading import Thread
from typing import TYPE_CHECKING

from bittorrent.models.message import (
    Bitfield,
    Cancel,
    Choke,
    Handshake,
    Have,
    Interested,
    KeepAlive,
    Message,
    NotInterested,
    PieceMessage,
    Request,
    Unchoke,
)
from bittorrent.util import config

if TYPE_CHECKING:
    from bittorrent.bittorrentclient import BitTorrentClient
    from bittorrent.models.piece import Block, Piece

logger = logging.getLogger("Peer")


class PendingBlockRequest:
    def __init__(self, block: "Block"):
        self.block = block
        self.request_time = datetime.now()

    @property
    def expired(self) -> bool:
        return (
            datetime.now() - self.request_time
        ).total_seconds() > config.BLOCK_REQUEST_TIMEOUT


class Peer:
    def __init__(self, client: "BitTorrentClient", ip: str, port: int, id: bytes = b""):
        # Client Reference
        self.client = client
        self.client_id = client.id
        # Peer Information
        self.peer_id = b""
        self.ip = ip
        self.port = port
        self.id = id
        # Torrent Information
        self.info_hash = client.metainfo.info_hash
        # Peer State
        self.peer_choking = True
        self.peer_interested = False
        self.am_choking = True
        self.am_interested = False
        # Handshake State
        self.sent_handshake = False
        self.received_handshake = False
        # Pieces from the bitfield
        self.bitfield_pieces: list["Piece"] = []
        # socket, if socket is None, the peer is considered closed
        self.socket: socket.socket | None = None
        # Last received message time
        self.last_time = datetime.now()
        # Pending block requests
        self.pending_block_requests: list[PendingBlockRequest] = []
        # Keep track of threads
        self.listen_thread: Thread | None = None
        self.keep_alive_thread: Thread | None = None

    # --------------------
    # ------ Socket ------
    # --------------------

    @property
    def updated_pending_block_requests(self) -> list[PendingBlockRequest]:
        return [
            request for request in self.pending_block_requests if not request.expired
        ]

    def create_socket(self) -> bool:
        try:
            if self.socket is not None:
                return True
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(config.SOCKET_TIMEOUT)
            self.socket.connect((self.ip, self.port))
            logger.debug(f"Connected to {self.ip}:{self.port}")
            return True
        except Exception as e:
            logger.debug(f"Error connecting to {self.ip}:{self.port}: {e}")
            self.close_socket()
            return False

    def close_socket(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    # --------------------------------
    # ------ Download From Peer ------
    # --------------------------------

    # Try catch wrapper for download in case any unexpected errors occur
    def download(self):
        logger.info(f"Downloading from {self.ip}:{self.port}")
        try:
            self._download()
        except Exception as e:
            logger.error(f"Error downloading from {self.ip}:{self.port}: {e}")
        finally:
            self.close_socket()

    def _download(self):
        # Quickly check if we're able to connect
        if not self.create_socket():
            return
        # Create a listening thread
        self.start_listen_thread()
        # self.start_keep_alive_thread()
        # Reset last time
        self.last_time = datetime.now()
        while not self.client.finished_downloading and self.socket is not None:
            # Check if the thread is dead using the last time we received a message
            # if (
            #     datetime.now() - self.last_time
            # ).total_seconds() > config.DEAD_THREAD_TIME:
            #     logger.error(f"Dead thread for {self.ip}:{self.port}")
            #     break
            if any(request.expired for request in self.pending_block_requests):
                logger.info(
                    f"Expired block requests for {self.ip}:{self.port}, retiring peer"
                )
                self.close_socket()
                break
            if len(self.pending_block_requests) >= config.MAX_PENDING_BLOCK_REQUESTS:
                time.sleep(config.TIME_BETWEEN_PIECE_REQUESTS)
                continue
            # Ensure we are handshaked
            if not self.sent_handshake:
                self.send_handshake()
            if not self.handshaked:
                continue
            # Ensure we are interested
            if not self.am_interested:
                self.send_interested()
                continue
            # Ensure we are not being choked
            if self.peer_choking:
                continue
            # Ensure we have pieces to request
            if all(piece.finished for piece in self.bitfield_pieces):
                break
            # Send the piece request
            if self.client.in_end_game:
                requested_blocks = self.client.pieces_obj.send_missing_request(self)
                if requested_blocks:
                    self.pending_block_requests += [
                        PendingBlockRequest(block)
                        for block in requested_blocks
                        if block
                    ]

            else:
                requested_block = self.client.pieces_obj.send_best_piece_request(self)
                if requested_block:
                    self.pending_block_requests.append(
                        PendingBlockRequest(requested_block)
                    )
            time.sleep(config.TIME_BETWEEN_PIECE_REQUESTS)

    # -------------------------------
    # ------ Keep Alive Thread ------
    # -------------------------------

    def start_keep_alive_thread(self):
        if self.keep_alive_thread is None:
            self.keep_alive_thread = Thread(target=self.keep_alive, daemon=True)
            self.keep_alive_thread.start()

    def keep_alive(self):
        while self.socket is not None:
            time.sleep(config.KEEP_ALIVE_INTERVAL)
            try:
                self.send_keep_alive()
            except Exception as e:
                logger.error(f"Error sending keep alive to {self.ip}:{self.port}: {e}")
                self.close_socket()
                return

    # ---------------------------
    # ------ Listen Thread ------
    # ---------------------------

    def start_listen_thread(self):
        if not self.create_socket():
            return
        if self.listen_thread is None:
            self.listen_thread = Thread(target=self.listen, daemon=True)
            self.listen_thread.start()
            logger.debug(f"Started listen thread for {self.ip}:{self.port}")

    def listen(self):
        while self.socket is not None:
            time.sleep(config.TIME_BETWEEN_LISTENS)
            try:
                message = self.receive_message()
                if message is not None:
                    self.handle_message(message)
            except Exception as e:
                logger.error(f"Error receiving message from {self.ip}:{self.port}: {e}")
                self.close_socket()
                return

    def receive_message(self) -> Message | None:
        if not self.socket:
            return
        if self.received_handshake:
            message_length_bytes = self.socket.recv(4)
            if not message_length_bytes:
                logger.error(f"Got no message length bytes from {self.ip}:{self.port}")
                self.close_socket()
                return
            message_length = int.from_bytes(message_length_bytes, "big")
            message_data = b""
            while len(message_data) < message_length:
                new_message_data = self.socket.recv(message_length - len(message_data))
                if not new_message_data:
                    logger.error(
                        f"Got no message data post-handshake from {self.ip}:{self.port}"
                    )
                    self.close_socket()
                    return
                message_data += new_message_data
            return Message.from_bytes(message_length_bytes + message_data)
        try:
            message_data = self.socket.recv(49 + len(config.PROTOCOL))
        except Exception as e:
            logger.error(f"Error receiving handshake from {self.ip}:{self.port}: {e}")
            self.close_socket()
            return
        if not message_data:
            logger.error(f"Got no handshake data from {self.ip}:{self.port}")
            self.close_socket()
            return
        return Handshake.from_bytes(message_data)

    def handle_message(self, message: Message):
        match message:
            case Handshake():
                self.receive_handshake(message)
            case KeepAlive():
                self.receive_keep_alive(message)
            case Choke():
                self.receive_choke(message)
            case Unchoke():
                self.receive_unchoke(message)
            case Interested():
                self.receive_interested(message)
            case NotInterested():
                self.receive_not_interested(message)
            case Have():
                self.receive_have(message)
            case Bitfield():
                self.receive_bitfield(message)
            case Request():
                self.receive_request(message)
            case PieceMessage():
                self.receive_piece_message(message)
            case Cancel():
                self.receive_cancel(message)

    # ---------------------------------------
    # ------ Send and Receive Messages ------
    # ---------------------------------------

    def send_message(self, message: Message) -> None:
        if self.socket:
            self.socket.sendall(message.to_bytes())
            time.sleep(config.TIME_BETWEEN_SENDS)

    # Handshake

    def send_handshake(self) -> None:
        request_handshake = Handshake(self.info_hash, self.client_id)
        self.send_message(request_handshake)
        self.sent_handshake = True
        logger.debug(f"Sent handshake to {self.ip}:{self.port}")
        self.send_bitfield()

    def receive_handshake(self, handshake: Handshake) -> None:
        if handshake.info_hash != self.info_hash:
            raise ValueError("Invalid info hash")
        self.peer_id = handshake.id
        self.received_handshake = True
        logger.debug(f"Received handshake from {self.ip}:{self.port}")
        if not self.sent_handshake:
            self.send_handshake()

    @property
    def handshaked(self) -> bool:
        return self.sent_handshake and self.received_handshake

    # Keep Alive

    def send_keep_alive(self) -> None:
        self.send_message(KeepAlive())
        logger.debug(f"Sent keep alive to {self.ip}:{self.port}")

    def receive_keep_alive(self, keep_alive: KeepAlive) -> None:
        pass

    # Choke

    def send_choke(self) -> None:
        self.send_message(Choke())
        self.am_choking = True
        logger.debug(f"Sent choke to {self.ip}:{self.port}")

    def receive_choke(self, choke: Choke) -> None:
        self.peer_choking = True
        logger.debug(f"Received choke from {self.ip}:{self.port}")

    # Unchoke

    def send_unchoke(self) -> None:
        self.send_message(Unchoke())
        self.am_choking = False
        logger.debug(f"Sent unchoke to {self.ip}:{self.port}")

    def receive_unchoke(self, unchoke: Unchoke) -> None:
        self.peer_choking = False
        logger.debug(f"Received unchoke from {self.ip}:{self.port}")

    # Interested

    def send_interested(self) -> None:
        self.send_message(Interested())
        self.am_interested = True
        logger.debug(f"Sent interested to {self.ip}:{self.port}")

    def receive_interested(self, interested: Interested) -> None:
        self.peer_interested = True
        self.am_choking = False
        self.send_unchoke()
        logger.debug(f"Received interested from {self.ip}:{self.port}")

    # Not Interested

    def send_not_interested(self) -> None:
        self.send_message(NotInterested())
        self.am_interested = False
        logger.debug(f"Sent not interested to {self.ip}:{self.port}")

    def receive_not_interested(self, not_interested: NotInterested) -> None:
        self.peer_interested = False
        self.am_choking = True
        self.send_choke()
        logger.debug(f"Received not interested from {self.ip}:{self.port}")

    # Have

    def send_have(self, piece_index: int) -> None:
        self.send_message(Have(piece_index))
        logger.debug(f"Sent have to {self.ip}:{self.port}")

    def receive_have(self, have: Have) -> None:
        piece = self.client.pieces_obj.pieces[have.piece_index]
        if piece not in self.bitfield_pieces:
            self.bitfield_pieces.append(piece)

    # Bitfield

    def send_bitfield(self) -> None:
        bitfield = [False] * len(self.client.pieces_obj.pieces)
        for piece in self.client.pieces_obj.pieces:
            if piece.finished:
                bitfield[piece.index] = True
        self.send_message(Bitfield(bitfield))
        logger.debug(f"Sent bitfield to {self.ip}:{self.port}")

    def receive_bitfield(self, bitfield: Bitfield) -> None:
        for i, bit in enumerate(bitfield.bitfield):
            if bit:
                piece = self.client.pieces_obj.pieces[i]
                piece.peers.append(self)
                if piece not in self.bitfield_pieces:
                    self.bitfield_pieces.append(piece)
        logger.debug(
            f"Received bitfield from {self.ip}:{self.port}. "
            + f"Contains {len(self.bitfield_pieces)}/{len(self.client.pieces_obj.pieces)} pieces."
        )

    # Request

    def send_request(self, piece_index: int, begin: int, length: int) -> None:
        self.send_message(Request(piece_index, begin, length))
        logger.debug(f"Sent request to {self.ip}:{self.port}")

    def receive_request(self, request: Request) -> None:
        piece = self.client.pieces_obj.pieces[request.index]
        if not piece.finished:
            return
        data = piece.data[request.begin : request.begin + request.length]
        if data:
            self.send_piece_message(request.index, request.begin, data)

    # Piece Message

    def send_piece_message(self, piece_index: int, begin: int, block: bytes) -> None:
        self.send_message(PieceMessage(piece_index, begin, block))
        logger.debug(f"Sent piece message to {self.ip}:{self.port}")

    def receive_piece_message(self, piece_message: PieceMessage) -> None:
        self.client.pieces_obj.handle_piece_message(piece_message)
        # Remove the block request from the pending block requests
        pending_block_request = next(
            (
                request
                for request in self.pending_block_requests
                if request.block.offset == piece_message.begin
                and request.block.index == piece_message.index
            ),
            None,
        )
        if pending_block_request:
            self.pending_block_requests.remove(pending_block_request)

    # Cancel

    def send_cancel(self, piece_index: int, begin: int, length: int) -> None:
        self.send_message(Cancel(piece_index, begin, length))
        logger.debug(f"Sent cancel to {self.ip}:{self.port}")

    def receive_cancel(self, cancel: Cancel) -> None:
        raise NotImplementedError
