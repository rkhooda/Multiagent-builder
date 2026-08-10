#!/usr/bin/env python3
"""Token consumption audit — read-only, zero API calls.

Answers Task 0 of the token-cost work from data that already exists:
metrics.db (per-attempt tokens, cache column, context_chars) and the LangGraph
checkpoints (per-file context_builder log lines, fast_mode flag). Prints a
markdown report; redirect to docs/TOKEN_AUDIT.md.

    venv/bin/python scripts/audit_tokens.py > ../docs/TOKEN_AUDIT.md

WHY a script and not a service (ponytail #4): the questions are answerable by
SQL over a store that already records everything needed. A script re-runs for
free after any change; a subsystem would be new surface area for a
measurement problem.
"""
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

METRICS_DB = BACKEND / "metrics.db"
PROJECTS_DB = BACKEND / "projects.db"

# A real pipeline run carries a UUID project id. Everything else in metrics.db
# is a suite, fault-injection harness, or standalone dev script (the ceiling
# audit's denylist, generalised): reported separately, never silently dropped.
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def q(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def connect(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def fmt(n):
    return f"{n:,}" if isinstance(n, int) else (f"{n:,.1f}" if n is not None else "—")


def p95(values):
    if not values:
        return None
    values = sorted(values)
    return values[min(int(len(values) * 0.95), len(values) - 1)]


# ── Checkpoint access (fast_mode flag + context_builder log lines) ───────────

def latest_checkpoint_values(conn, thread_id):
    row = conn.execute(
        "SELECT checkpoint, type FROM checkpoints WHERE thread_id = ?"
        " ORDER BY checkpoint_id DESC LIMIT 1", (thread_id,)).fetchone()
    if row is None:
        return {}
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    try:
        cp = JsonPlusSerializer().loads_typed((row["type"], row["checkpoint"]))
        return cp.get("channel_values", {}) or {}
    except Exception as e:                      # noqa: BLE001 — report, don't die
        print(f"<!-- checkpoint unreadable for {thread_id}: {e} -->")
        return {}


CB_LINE = re.compile(r"^context_builder: (\S+)(?: \[\w+\])? — (\d+) tok / (\d+) chars(?: \(trimmed: (.+)\))?$")


def parse_context_lines(log):
    out = []
    for line in log or []:
        m = CB_LINE.match(line)
        if m:
            out.append({"file": m.group(1), "tok": int(m.group(2)),
                        "chars": int(m.group(3)), "trims": m.group(4)})
    return out


# ── Report sections ──────────────────────────────────────────────────────────

def section_scope(m):
    total = q(m, "SELECT COUNT(*) n FROM agent_runs")[0]["n"]
    ids = [r["project_id"] for r in q(m, "SELECT DISTINCT project_id FROM agent_runs")]
    real = sorted(i for i in ids if i and UUID_RE.match(i))
    other = sorted(i or "(null)" for i in ids if not (i and UUID_RE.match(i)))
    real_rows = q(m, "SELECT COUNT(*) n FROM agent_runs WHERE project_id IN (%s)"
                  % ",".join("?" * len(real)), tuple(real))[0]["n"] if real else 0
    print("## Scope\n")
    print(f"- {fmt(total)} attempt rows total; **{fmt(real_rows)} belong to real pipeline "
          f"runs** (UUID project ids: {', '.join(i[:8] for i in real)}).")
    print(f"- The remaining rows are suites/harnesses/dev scripts "
          f"({len(other)} ids, e.g. {', '.join(other[:6])}…) — excluded from every "
          f"'real run' number below, reported separately where relevant.\n")
    return real


def section_split(m, real):
    ph = ",".join("?" * len(real))
    rows = q(m, f"""
        SELECT agent, COUNT(*) calls,
               SUM(COALESCE(prompt_tokens,0)) pt, SUM(COALESCE(completion_tokens,0)) ct,
               ROUND(AVG(prompt_tokens),0) avg_pt, ROUND(AVG(completion_tokens),0) avg_ct
        FROM agent_runs
        WHERE outcome='ok' AND COALESCE(model,'') != 'cache' AND project_id IN ({ph})
        GROUP BY agent ORDER BY pt+ct DESC""", tuple(real))
    gp = sum(r["pt"] for r in rows) or 1
    gc = sum(r["ct"] for r in rows) or 1
    print("## 1. Prompt vs completion split per agent (real runs, successful calls)\n")
    print("| agent | calls | prompt tok | completion tok | avg prompt | avg compl | prompt share of agent |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        tot = (r["pt"] + r["ct"]) or 1
        print(f"| {r['agent']} | {r['calls']} | {fmt(r['pt'])} | {fmt(r['ct'])} "
              f"| {fmt(r['avg_pt'])} | {fmt(r['avg_ct'])} | {r['pt']*100//tot}% |")
    print(f"| **total** | {sum(r['calls'] for r in rows)} | **{fmt(gp)}** | **{fmt(gc)}** "
          f"| | | **{gp*100//(gp+gc)}%** |")
    print()


def section_providers(m, real):
    ph = ",".join("?" * len(real))
    rows = q(m, f"""
        SELECT model, agent,
               SUM(COALESCE(prompt_tokens,0)) pt, SUM(COALESCE(completion_tokens,0)) ct,
               COUNT(*) calls
        FROM agent_runs WHERE project_id IN ({ph}) AND COALESCE(model,'') NOT IN ('', 'cache')
        GROUP BY model, agent""", tuple(real))
    prov = defaultdict(lambda: {"pt": 0, "ct": 0, "calls": 0})
    groq_agents = defaultdict(lambda: {"pt": 0, "ct": 0, "calls": 0})
    for r in rows:
        p = r["model"].split("/", 1)[0].lower()
        prov[p]["pt"] += r["pt"]; prov[p]["ct"] += r["ct"]; prov[p]["calls"] += r["calls"]
        if p == "groq":
            a = groq_agents[r["agent"]]
            a["pt"] += r["pt"]; a["ct"] += r["ct"]; a["calls"] += r["calls"]
    print("## 2. Provider view — where the scarce pool goes (all attempts, incl. failed)\n")
    print("| provider | attempts | prompt tok | completion tok | prompt share |")
    print("|---|---|---|---|---|")
    for p, v in sorted(prov.items(), key=lambda kv: -(kv[1]["pt"] + kv[1]["ct"])):
        tot = (v["pt"] + v["ct"]) or 1
        print(f"| {p} | {v['calls']} | {fmt(v['pt'])} | {fmt(v['ct'])} | {v['pt']*100//tot}% |")
    print("\n**Groq (100k/day, scarce) by agent:**\n")
    print("| agent | attempts | prompt tok | completion tok | prompt share |")
    print("|---|---|---|---|---|")
    for a, v in sorted(groq_agents.items(), key=lambda kv: -(kv[1]["pt"] + kv[1]["ct"])):
        tot = (v["pt"] + v["ct"]) or 1
        print(f"| {a} | {v['calls']} | {fmt(v['pt'])} | {fmt(v['ct'])} | {v['pt']*100//tot}% |")
    print()


def section_runs(m, real):
    print("## 3. Per-run cost (per real project, per UTC day — a day is the session proxy)\n")
    print("| project | day | attempts | ok | cache hits | prompt tok | completion tok | total | groq share |")
    print("|---|---|---|---|---|---|---|---|---|")
    for pid in real:
        rows = q(m, """
            SELECT substr(created_at,1,10) day, COUNT(*) n,
                   SUM(outcome='ok') ok, SUM(cache='hit') hits,
                   SUM(COALESCE(prompt_tokens,0)) pt, SUM(COALESCE(completion_tokens,0)) ct,
                   SUM(CASE WHEN model LIKE 'groq/%' THEN
                       COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0) ELSE 0 END) groq
            FROM agent_runs WHERE project_id = ? GROUP BY day ORDER BY day""", (pid,))
        for r in rows:
            print(f"| {pid[:8]} | {r['day']} | {r['n']} | {r['ok']} | {r['hits'] or 0} "
                  f"| {fmt(r['pt'])} | {fmt(r['ct'])} | {fmt(r['pt'] + r['ct'])} "
                  f"| {fmt(r['groq'])} |")
    print()


def section_context(m, real, pconn):
    print("## 4. Coder context sizes\n")
    ph = ",".join("?" * len(real))
    print("### 4a. context_chars from metrics (full messages incl. system prompt)\n")
    print("| agent | ok calls | avg chars | p95 chars | max chars | >16k chars |")
    print("|---|---|---|---|---|---|")
    for agent in ("frontend_code", "backend_code", "database", "devops"):
        rows = q(m, f"""SELECT context_chars c FROM agent_runs
                        WHERE agent=? AND outcome='ok' AND context_chars IS NOT NULL
                        AND project_id IN ({ph})""", (agent,) + tuple(real))
        vals = [r["c"] for r in rows]
        if not vals:
            print(f"| {agent} | 0 | — | — | — | — |")
            continue
        over = sum(1 for v in vals if v > 16000)
        print(f"| {agent} | {len(vals)} | {fmt(sum(vals)//len(vals))} | {fmt(p95(vals))} "
              f"| {fmt(max(vals))} | {over} |")
    print("\n### 4b. context_builder log lines from checkpoints (user context only, ≤4k-token budget)\n")
    print("| project | files logged | avg tok | max tok | over 4k budget | trim events |")
    print("|---|---|---|---|---|---|")
    for pid in real:
        vals = latest_checkpoint_values(pconn, pid)
        lines = parse_context_lines(vals.get("log"))
        if not lines:
            print(f"| {pid[:8]} | 0 | — | — | — | — |")
            continue
        toks = [l["tok"] for l in lines]
        trims = [l for l in lines if l["trims"]]
        print(f"| {pid[:8]} | {len(lines)} | {fmt(sum(toks)//len(toks))} | {fmt(max(toks))} "
              f"| {sum(1 for t in toks if t > 4000)} | {len(trims)} |")
        for t in trims[:5]:
            print(f"<!-- trim: {t['file']}: {t['trims']} -->")
    print("\n### 4c. System prompt sizes (sent verbatim on EVERY call of that agent)\n")
    print("| prompt file | chars | ~tokens (chars/4) |")
    print("|---|---|---|")
    for p in sorted((BACKEND.parent / "prompts").glob("*.md")):
        n = len(p.read_text(encoding="utf-8"))
        print(f"| {p.name} | {fmt(n)} | {fmt(n // 4)} |")
    print()


def section_cache(m, real):
    ph = ",".join("?" * len(real))
    print("## 5. Cache effectiveness\n")
    rows = q(m, f"""SELECT agent, SUM(cache='hit') hits, SUM(cache='miss') misses
                    FROM agent_runs WHERE cache IS NOT NULL AND project_id IN ({ph})
                    GROUP BY agent ORDER BY agent""", tuple(real))
    print("### 5a. Real pipeline runs, by agent\n")
    print("| agent | hits | misses | hit rate |")
    print("|---|---|---|---|")
    th = tm = 0
    for r in rows:
        h, mi = r["hits"] or 0, r["misses"] or 0
        th += h; tm += mi
        rate = f"{h*100//(h+mi)}%" if h + mi else "—"
        print(f"| {r['agent']} | {h} | {mi} | {rate} |")
    print(f"| **total** | **{th}** | **{tm}** | **{th*100//max(1,th+tm)}%** |")
    suite = q(m, f"""SELECT SUM(cache='hit') hits, SUM(cache='miss') misses
                     FROM agent_runs WHERE cache IS NOT NULL
                     AND project_id NOT IN ({ph})""", tuple(real))[0]
    print(f"\nSuite/harness rows for contrast: {suite['hits'] or 0} hits / "
          f"{suite['misses'] or 0} misses — the cache mechanism works under test; "
          f"real runs are where it is not earning.\n")

    print("### 5b. Re-run case study — the one real project that ran twice\n")
    rows = q(m, """SELECT project_id, substr(created_at,1,10) day, agent,
                          SUM(cache='hit') hits, SUM(cache='miss') misses
                   FROM agent_runs
                   WHERE cache IS NOT NULL AND agent IN
                         ('frontend_code','backend_code','database','research',
                          'requirements','architecture','planning')
                   GROUP BY project_id, day, agent
                   HAVING project_id IN (
                       SELECT project_id FROM agent_runs
                       WHERE project_id IN (%s)
                       GROUP BY project_id
                       HAVING COUNT(DISTINCT substr(created_at,1,10)) > 1)
                   ORDER BY project_id, day, agent""" % ",".join("?" * len(real)),
             tuple(real))
    if rows:
        print("| project | day | agent | hits | misses |")
        print("|---|---|---|---|---|")
        for r in rows:
            print(f"| {r['project_id'][:8]} | {r['day']} | {r['agent']} "
                  f"| {r['hits'] or 0} | {r['misses'] or 0} |")
    else:
        print("(no real project has metrics on more than one day)")
    print()


def section_stage_history(pconn, real):
    print("### 5c. What the multi-day runs actually were (stage_history from checkpoints)\n")
    for pid in real:
        vals = latest_checkpoint_values(pconn, pid)
        hist = vals.get("stage_history") or []
        if not hist:
            continue
        print(f"**{pid[:8]}:**")
        for e in hist:
            print(f"- {e.get('timestamp', '?')[:16]} stage={e.get('stage')} "
                  f"attempt={e.get('attempt')} trigger={e.get('trigger')}")
        print()


def section_fast_mode(m, pconn, real):
    print("## 6. Fast mode application\n")
    print("| project | fast_mode | metrics attempts |")
    print("|---|---|---|")
    any_fast = False
    for pid in real:
        vals = latest_checkpoint_values(pconn, pid)
        fm = vals.get("fast_mode")
        n = q(m, "SELECT COUNT(*) n FROM agent_runs WHERE project_id=?", (pid,))[0]["n"]
        any_fast = any_fast or bool(fm)
        print(f"| {pid[:8]} | {fm} | {n} |")
    print(f"\n**Fast mode was {'used' if any_fast else 'NEVER used'} on any recorded real run.**\n")


def section_defect_checks():
    print("## 7. Defect checks\n")
    # (1) Does the cache key change when the system prompt content changes?
    # Pure experiment on the real key function — no scratch branch needed,
    # because make_key is deterministic over its inputs.
    # app modules print status lines at import time; keep them out of the report.
    import contextlib
    import io
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        import app.llm_router  # noqa: F401 — trigger the noisy import once, silenced
    from app.observability.llm_cache import make_key
    prompt_file = BACKEND.parent / "prompts" / "backend_coder_agent.md"
    system = prompt_file.read_text(encoding="utf-8")
    msgs_a = [{"role": "system", "content": system},
              {"role": "user", "content": "generate app/models/note.py"}]
    msgs_b = [{"role": "system", "content": system + "\nEDIT: prefer async SQLAlchemy."},
              {"role": "user", "content": "generate app/models/note.py"}]
    key_a = make_key("backend_code", msgs_a, 1500)
    key_b = make_key("backend_code", msgs_b, 1500)
    print("### 7a. Cache key vs system-prompt content (experiment, not code reading)\n")
    print(f"- key(real backend coder prompt): `{key_a[:16]}…`")
    print(f"- key(same prompt + one edited line): `{key_b[:16]}…`")
    verdict = "PASS — a prompt edit changes the key; no stale hit possible" \
        if key_a != key_b else "**FAIL — prompt edits are invisible to the cache key**"
    print(f"- **{verdict}.** The key hashes the full `messages` list "
          f"(`llm_cache.make_key`), and every agent sends its system prompt as "
          f"`messages[0]` — so system-prompt content is inside the hash.\n")

    # (2) Fast-mode ceilings vs the ceiling audit's measured requirements.
    from app.llm_router import resolve_max_tokens, FAST_MODE_SCALABLE
    from app.agents.architecture_agent import ARCHITECTURE_MAX_TOKENS
    from app.agents.backend_coder_agent import BACKEND_FILE_MAX_TOKENS
    from app.agents.database_agent import DATABASE_MAX_TOKENS
    from app.agents.devops_agent import DEVOPS_MAX_TOKENS
    from app.agents.frontend_coder_agent import FRONTEND_FILE_MAX_TOKENS
    from app.agents.frontend_reviewer import REVIEW_MAX_TOKENS
    from app.agents.planning_agent import PLANNING_TOKENS_CAP
    from app.agents.qa_agent import QA_MAX_TOKENS
    from app.agents.requirements_agent import REQUIREMENTS_MAX_TOKENS
    from app.agents.research_agent import RESEARCH_MAX_TOKENS

    # Measured worst complete outputs — docs/CEILING_AUDIT.md, 2026-08-03/08.
    measured = [
        ("research", RESEARCH_MAX_TOKENS, 4368), ("requirements", REQUIREMENTS_MAX_TOKENS, 4496),
        ("architecture", ARCHITECTURE_MAX_TOKENS, 11996), ("planning", PLANNING_TOKENS_CAP, 26894),
        ("frontend_code", FRONTEND_FILE_MAX_TOKENS, 829), ("frontend_review", REVIEW_MAX_TOKENS, 3075),
        ("backend_code", BACKEND_FILE_MAX_TOKENS, 96), ("database", DATABASE_MAX_TOKENS, 722),
        ("devops", DEVOPS_MAX_TOKENS, 64), ("qa", QA_MAX_TOKENS, 4949),
    ]
    print("### 7b. Fast-mode ceilings vs measured output requirements\n")
    print("| agent | call-site ceiling | fast-mode effective | measured requirement | headroom (fast) | scaled? |")
    print("|---|---|---|---|---|---|")
    ok = True
    for agent, ceiling, req in measured:
        eff = resolve_max_tokens(agent, ceiling, fast_mode=True)
        ok = ok and eff >= req
        print(f"| {agent} | {ceiling} | {eff} | {req} | {eff - req} "
              f"| {'yes' if agent in FAST_MODE_SCALABLE else 'no'} |")
    print(f"\n**{'PASS — no agent starves under fast mode' if ok else 'FAIL — see rows above'}"
          f"** (pinned by `test_token_budgets.py::test_ceilings_cover_measured_requirements`, "
          f"which asserts both profiles).\n")


def main():
    m = connect(METRICS_DB)
    pconn = connect(PROJECTS_DB)
    print("# Token Consumption Audit\n")
    print(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%MZ')} by "
          f"`backend/scripts/audit_tokens.py` (read-only; re-run any time).\n")
    real = section_scope(m)
    if not real:
        print("No real pipeline runs recorded — nothing to audit.")
        return 0
    section_split(m, real)
    section_providers(m, real)
    section_runs(m, real)
    section_context(m, real, pconn)
    section_cache(m, real)
    section_stage_history(pconn, real)
    section_fast_mode(m, pconn, real)
    section_defect_checks()
    return 0


if __name__ == "__main__":
    sys.exit(main())
