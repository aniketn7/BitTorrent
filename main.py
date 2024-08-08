from bittorrent import BitTorrentClient


def main():
    metainfo_filepath = "http_examples/debian1.torrent"
    client = BitTorrentClient(metainfo_filepath)
    client.download()


if __name__ == "__main__":
    main()
