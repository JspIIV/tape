"""A stub of the GenLayer runtime, enough to run contracts/tape.py in plain Python.

The web serves from memory, or the real internet when live=True (reads only: no chain is ever written).
"""

import io
import json
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(HERE, "..", "contracts", "tape.py")


class _Store:
    def __init__(self, kind): self.kind = kind
    def __class_getitem__(cls, item): return cls("map" if isinstance(item, tuple) else "list")
    def make(self): return {} if self.kind == "map" else []


class _Address:
    def __init__(self, h): self.as_hex = str(h)


class _Return:
    def __init__(self, calldata): self.calldata = calldata


class RoundFailed(Exception):
    pass


class _Res:
    def __init__(self, status, body): self.status, self.body = status, body


def _curl(url, body=None, headers=None):
    # curl, not urllib: some of the sources refuse Python's default client
    cmd = ["curl", "-s", "-m", "60", "-w", "\n%{http_code}"]
    for k, v in (headers or {}).items():
        cmd += ["-H", k + ": " + (v.decode() if isinstance(v, bytes) else v)]
    if body is not None:
        cmd += ["-X", "POST", "--data-binary", "@-"]
    out = subprocess.run(cmd + [url], input=body, capture_output=True).stdout
    raw, _, code = out.rpartition(b"\n")
    return _Res(int(code or 0), raw)


class Web:
    def __init__(self, live=False):
        self.live = live
        self.pages = {}      # url prefix -> (status, doc)
        self.rpc = {}        # (url, method, first param) -> result, or "down"
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append(("GET", url))
        if self.live:
            return _curl(url, headers=headers)
        for prefix, (status, doc) in self.pages.items():
            if url.startswith(prefix):
                return _Res(status, json.dumps(doc).encode())
        return _Res(404, b'{"message":"not found"}')

    def post(self, url, body=b"", headers=None):
        self.calls.append(("POST", url))
        if self.live:
            return _curl(url, body=body, headers=headers)
        req = json.loads(body)
        got = self.rpc.get((url, req["method"], str(req["params"][0])), None)
        if got == "down":
            raise IOError("unreachable")
        return _Res(200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": got}).encode())


class _VM:
    Return = _Return

    def __init__(self):
        self.validator = None   # optional: a different web for the validator's own run

    def run_nondet(self, lead, agree):
        leader = lead()
        if not agree(_Return(leader)):
            raise RoundFailed("validators did not agree")
        return leader


def load(live=False):
    gl = types.ModuleType("genlayer")
    gl.public = types.SimpleNamespace(write=lambda fn: fn, view=lambda fn: fn)
    gl.message = types.SimpleNamespace(sender_address=_Address("0x" + "1" * 40))
    gl.nondet = types.SimpleNamespace(web=Web(live))
    gl.vm = _VM()
    gl.storage = types.SimpleNamespace(TreeMap=_Store, DynArray=_Store)
    gl.contract = types.SimpleNamespace(Contract=object)
    gl.Address = _Address
    sys.modules["genlayer"] = gl
    module = types.ModuleType("tape_under_test")
    exec(compile(io.open(CONTRACT, encoding="utf-8").read(), CONTRACT, "exec"), module.__dict__)
    return module, gl


def fresh(module):
    c = module.Tape.__new__(module.Tape)
    for field, declared in module.Tape.__annotations__.items():
        if isinstance(declared, _Store):
            setattr(c, field, declared.make())
    c.__init__()
    return c
