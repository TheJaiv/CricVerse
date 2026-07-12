"""Subprocess entry point for tools/lineup_optimizer jobs launched by the bot.

The optimizer seeds the GLOBAL `random` module (common random numbers), so it
only gives repeatable results in a process of its own: inside the bot process,
any concurrent thread touching random.* mid-run shifts the seeded stream and
the same command returns a different XI every time. Do not fold this back into
asyncio.to_thread.

Protocol: pickled (fn_name, args) on stdin -> pickled (ok, result|traceback)
on stdout.
"""
import os
import pickle
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    fn_name, args = pickle.load(sys.stdin.buffer)
    from tools import lineup_optimizer
    kwargs = {}
    if fn_name == "recommend_xi":
        # Progress goes to stderr as "P <pct>" lines (stdout carries the pickled
        # result). Only whole-percent steps are emitted to keep the stream small.
        last = [-1]

        def _prog(f):
            pct = int(f * 100)
            if pct > last[0]:
                last[0] = pct
                sys.stderr.write(f"P {pct}\n")
                sys.stderr.flush()
        kwargs["progress"] = _prog
    try:
        result = (True, getattr(lineup_optimizer, fn_name)(*args, **kwargs))
    except Exception:
        result = (False, traceback.format_exc())
    sys.stdout.buffer.write(pickle.dumps(result))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
