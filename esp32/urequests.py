import usocket


class Response:
    def __init__(self, sock):
        self.raw = sock
        self.encoding = "utf-8"

    def close(self):
        if self.raw:
            self.raw.close()
            self.raw = None

    @property
    def text(self):
        return self.content.decode(self.encoding)

    @property
    def content(self):
        try:
            data = self.raw.read()
        finally:
            self.close()
        return data


def request(method, url, data=None, json=None, headers={}):
    proto, _, host, path = url.split('/', 3)
    addr = usocket.getaddrinfo(host, 80)[0][-1]
    s = usocket.socket()
    s.connect(addr)

    s.write(b"%s /%s HTTP/1.0\r\n" % (method, path))
    s.write(b"Host: %s\r\n" % host)

    for k in headers:
        s.write(k.encode() + b": " + headers[k].encode() + b"\r\n")

    if json is not None:
        import ujson
        data = ujson.dumps(json)
        s.write(b"Content-Type: application/json\r\n")

    if data:
        if isinstance(data, str):
            data = data.encode()
        s.write(b"Content-Length: %d\r\n" % len(data))

    s.write(b"\r\n")

    if data:
        s.write(data)

    while True:
        line = s.readline()
        if not line or line == b"\r\n":
            break

    return Response(s)


def get(url, **kw):
    return request("GET", url, **kw)


def post(url, **kw):
    return request("POST", url, **kw)
