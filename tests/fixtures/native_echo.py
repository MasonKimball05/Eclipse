# A native messaging helper for the tests: answers each message with {"echo": message, "origin": argv[1]},
# and exits when asked to (or when stdin closes).
import json
import struct
import sys

while True:
    header = sys.stdin.buffer.read(4)
    if len(header) < 4:
        break
    message = json.loads(sys.stdin.buffer.read(struct.unpack("<I", header)[0]))
    if message == "quit":
        break
    reply = json.dumps({"echo": message, "origin": sys.argv[1]}).encode()
    sys.stdout.buffer.write(struct.pack("<I", len(reply)) + reply)
    sys.stdout.buffer.flush()
