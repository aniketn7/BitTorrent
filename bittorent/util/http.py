import socket
from urllib.parse import urlencode, urlparse


def get(url: str, params: dict = {}) -> bytes:
    # Parse the URL
    parsed_url = urlparse(url)

    # Get the hostname, port, and path
    hostname = parsed_url.hostname
    port = parsed_url.port if parsed_url.port else 80
    path = parsed_url.path if parsed_url.path else "/"

    # Encode the query parameters
    query_params = urlencode(params)
    if query_params:
        path += f"?{query_params}"

    # Create a socket and connect to the server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((hostname, port))

    # Send the HTTP request
    request_line = f"GET {path} HTTP/1.1\r\n"
    headers = f"Host: {hostname}\r\n"
    headers += "Connection: close\r\n\r\n"
    request = request_line + headers
    sock.sendall(request.encode())

    # Receive the response
    response = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk

    # Close the socket
    sock.close()

    # Split the response into headers and body
    headers, body = response.split(b"\r\n\r\n", 1)

    return body
