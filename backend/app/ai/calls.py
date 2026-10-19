"""A record of every call to an AI service: one log line and one ai_calls row each.

This is how anyone can see what the AI side of the system is doing: how many calls, how
long they take, how many tokens they use, and how often they are refused or fail.

    python -m app.ai.calls            summary of the last hour (from backend/)
    python -m app.ai.calls --hours 24
"""

import argparse
import logging

from app.core.db import close_pool, transaction

log = logging.getLogger("ai")


def record(
    service: str,
    model: str | None,
    purpose: str,
    outcome: str,
    duration_s: float,
    waited_s: float = 0.0,
    tokens: int | None = None,
    error: str | None = None,
) -> None:
    """Note one AI call. Never raises: failing to write the record must not fail the call."""
    log.info(
        "%s %s %s: %s in %.1fs%s%s%s",
        service, purpose, model or "", outcome, duration_s,
        f", waited {waited_s:.1f}s" if waited_s >= 0.05 else "",
        f", {tokens} tokens" if tokens is not None else "",
        f" ({error})" if error else "",
    )
    try:
        _save(service, model, purpose, outcome, round(duration_s * 1000), round(waited_s * 1000), tokens,
              (error or "")[:500] or None)
    except Exception as failure:  # noqa: BLE001 - observability must never break the system it watches
        log.warning("could not record an AI call: %s", failure)


def _save(*row) -> None:
    with transaction() as conn:
        conn.execute(
            """
            insert into public.ai_calls (service, model, purpose, outcome, duration_ms, waited_ms, tokens, error)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            row,
        )


def summary(hours: float) -> list[dict]:
    with transaction() as conn:
        return conn.execute(
            """
            select service, purpose, count(*) as calls,
                   count(*) filter (where outcome = 'ok') as ok,
                   count(*) filter (where outcome = 'rate_limited') as rate_limited,
                   count(*) filter (where outcome = 'error') as errors,
                   percentile_cont(0.5) within group (order by duration_ms) as p50_ms,
                   percentile_cont(0.95) within group (order by duration_ms) as p95_ms,
                   max(waited_ms) as max_wait_ms, sum(tokens) as tokens
            from public.ai_calls
            where at > now() - make_interval(secs => %s)
            group by service, purpose order by service, purpose
            """,
            (hours * 3600,),
        ).fetchall()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m app.ai.calls")
    parser.add_argument("--hours", type=float, default=1)
    args = parser.parse_args()
    try:
        rows = summary(args.hours)
        print(f"AI calls in the last {args.hours:g} hour(s)")
        print(f"{'service':<8} {'purpose':<18} {'calls':>5} {'ok':>4} {'429':>4} {'err':>4} {'p50 s':>6} {'p95 s':>6} {'wait s':>6} {'tokens':>8}")
        for r in rows:
            print(f"{r['service']:<8} {r['purpose']:<18} {r['calls']:>5} {r['ok']:>4} {r['rate_limited']:>4} {r['errors']:>4} "
                  f"{r['p50_ms'] / 1000:>6.1f} {r['p95_ms'] / 1000:>6.1f} {(r['max_wait_ms'] or 0) / 1000:>6.1f} {r['tokens'] or 0:>8}")
        if not rows:
            print("(none)")
    finally:
        close_pool()
