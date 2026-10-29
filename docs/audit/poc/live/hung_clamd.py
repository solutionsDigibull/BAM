import socket, threading, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", 45888)); s.listen(256)
conns = []
def acc():
    while True: conns.append(s.accept()[0])
threading.Thread(target=acc, daemon=True).start(); time.sleep(7200)
