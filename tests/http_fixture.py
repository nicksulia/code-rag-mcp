from email.message import Message
from types import SimpleNamespace


def attach_http_context(handler, address=("127.0.0.1", 8000)):
    handler.server = SimpleNamespace(server_address=address)
    headers = handler.headers
    if not isinstance(headers, Message):
        message = Message()
        for key, value in headers.items():
            message[key] = value
        handler.headers = message
    if "Host" not in handler.headers:
        handler.headers["Host"] = "localhost:" + str(address[1])
