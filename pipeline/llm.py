"""Minimal client for the Claude API (standard library only), with a spending cap per run.

The key comes from the environment variable ANTHROPIC_API_KEY (a repository secret on GitHub).
Prices are US dollars per million tokens as published on 22 November 2026; they are used only to
keep a running estimate and to stop before the cap. Change them when the price list changes.
"""
import json, os, time, urllib.error, urllib.request

API = "https://api.anthropic.com/v1/messages"
MODELS = {"small": "claude-haiku-5-5", "writer": "claude-sonnet-5-5"}
PRICE = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00)}


class CapReached(Exception):
    pass


class Client:
    def __init__(self, cap_usd, key=None, transport=None):
        self.key = key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.cap, self.spent, self.calls, self.tokens = cap_usd, 0.0, 0, [0, 0]
        self.transport = transport or self._post

    def available(self):
        return bool(self.key) or self.transport != self._post

    def _post(self, payload):
        req = urllib.request.Request(API, data=json.dumps(payload).encode(), method="POST", headers={
            "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json",
            **({"anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]} if os.environ.get("ANTHROPIC_WORKSPACE_ID") else {})})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 529) and attempt < 3:
                    time.sleep(8 * (attempt + 1))
                    continue
                raise RuntimeError("API error %d: %s" % (e.code, e.read()[:900].decode("utf-8", "replace")))

    def ask(self, model, system, user, max_tokens=2000):
        if self.spent >= self.cap:
            raise CapReached("spending cap of $%.2f reached" % self.cap)
        out = self.transport({"model": model, "max_tokens": max_tokens, "system": system,
                              "messages": [{"role": "user", "content": user}]})
        u = out.get("usage", {})
        i, o = u.get("input_tokens", 0), u.get("output_tokens", 0)
        pi, po = PRICE.get(model, (2.0, 10.0))
        self.spent += (i * pi + o * po) / 1e6
        self.calls += 1
        self.tokens[0] += i
        self.tokens[1] += o
        return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")

    def report(self):
        return {"calls": self.calls, "input_tokens": self.tokens[0], "output_tokens": self.tokens[1], "usd": round(self.spent, 4)}


def json_block(text):
    """The first JSON array or object in a model reply."""
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON in reply")
    return json.JSONDecoder().raw_decode(text[start:])[0]
