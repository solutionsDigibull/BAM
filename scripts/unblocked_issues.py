# /// script
# requires-python = ">=3.11"
# ///
"""List SBAM issues that are safe to start right now.

An issue is workable when it is open, not an epic or future item, unassigned, has no status:in-progress /
status:in-review label, and every issue it is blocked by is closed. Blockers come from GitHub's native
"Blocked by" relationship and from the `Blocked by: #12, #15` (or `Depends on:`) line in the body.

Sorted by: milestone (M0 → M5), priority (P0 first), number of open issues it unblocks (more first),
the planned wave in docs/team-lanes.json, then issue number.

Usage (needs only gh, authenticated; no jq):
  uv run scripts/unblocked_issues.py                 # workable issues, best first
  uv run scripts/unblocked_issues.py --lane D2       # only your lane (docs/TEAM_PLAN.md)
  uv run scripts/unblocked_issues.py --all           # also non-workable candidates and why
  uv run scripts/unblocked_issues.py --check 61      # exit 0 if #61 is workable, 1 otherwise
Env: REPO=owner/name to override solutionsDigibull/BAM.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = os.environ.get("REPO", "solutionsDigibull/BAM")
ROOT = Path(__file__).resolve().parent.parent
BUSY = {"status:in-progress", "status:in-review"}
DEP_LINE = re.compile(r"^\s*(?:\*\*)?(blocked by|depends on)(?:\*\*)?\s*:", re.I)

QUERY = """
query($owner:String!,$name:String!,$after:String){ repository(owner:$owner,name:$name){
  issues(first:100, states:OPEN, after:$after){ pageInfo{hasNextPage endCursor}
    nodes{ number title body milestone{title} assignees(first:5){nodes{login}} labels(first:30){nodes{name}}
           blockedBy(first:50){nodes{number state}} } } } }"""


def gh_bin():
    exe = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
    if not Path(exe).exists() and not shutil.which("gh"):
        sys.exit("gh CLI not found. Install it and run 'gh auth login'.")
    return exe


def fetch_open_issues():
    owner, name = REPO.split("/")
    out, after = [], None
    while True:
        cmd = [gh_bin(), "api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={owner}", "-F", f"name={name}"]
        if after:
            cmd += ["-F", f"after={after}"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if r.returncode:
            sys.exit(f"gh api failed: {r.stderr.strip()}")
        page = json.loads(r.stdout)["data"]["repository"]["issues"]
        out += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return out
        after = page["pageInfo"]["endCursor"]


def body_deps(body):
    deps = set()
    for line in (body or "").splitlines():
        if DEP_LINE.match(line):
            deps |= {int(n) for n in re.findall(r"#(\d+)", line)}
    return deps


def ms_rank(title):
    m = re.match(r"M(\d+)", title or "")
    return int(m.group(1)) if m else 99


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lane", choices=["D1", "D2", "D3"], help="only issues planned for this developer lane")
    ap.add_argument("--all", action="store_true", help="also show non-workable candidates and why")
    ap.add_argument("--check", type=int, metavar="N", help="exit 0 if issue N is workable, 1 otherwise")
    a = ap.parse_args()

    lanes_file = ROOT / "docs" / "team-lanes.json"
    lanes = json.loads(lanes_file.read_text(encoding="utf-8")) if lanes_file.exists() else {}

    issues = fetch_open_issues()
    open_nums = {i["number"] for i in issues}
    blockers = {}
    for i in issues:
        native = {b["number"] for b in i["blockedBy"]["nodes"] if b["state"] == "OPEN"}
        blockers[i["number"]] = sorted((native | body_deps(i["body"])) & open_nums)
    fanout = {n: 0 for n in open_nums}
    for n, bs in blockers.items():
        for b in bs:
            fanout[b] = fanout.get(b, 0) + 1

    rows = []
    for i in issues:
        n = i["number"]
        labels = {l["name"] for l in i["labels"]["nodes"]}
        lane = lanes.get(str(n), {})
        why = []
        if "type:epic" in labels:
            why.append("epic (closes via its sub-issues)")
        if "future" in labels:
            why.append("future scope")
        who = [x["login"] for x in i["assignees"]["nodes"]]
        if who:
            why.append("assigned to " + ",".join(who))
        if labels & BUSY:
            why.append("labelled " + ",".join(sorted(labels & BUSY)))
        if blockers[n]:
            why.append("blocked by open " + ", ".join(f"#{b}" for b in blockers[n]))
        pri = next((l.split(":")[1] for l in labels if l.startswith("priority:")), "P9")
        area = next((l.split(":")[1] for l in labels if l.startswith("area:")), "-")
        ms = (i["milestone"] or {}).get("title", "")
        rows.append(dict(n=n, title=i["title"], ok=not why, why="; ".join(why), ms=ms, pri=pri, area=area,
                         fan=fanout.get(n, 0), lane=lane.get("dev", "-"), wave=lane.get("wave", 99),
                         stale_blocked="blocked" in labels and not blockers[n]))

    if a.check:
        r = next((r for r in rows if r["n"] == a.check), None)
        if r is None:
            print(f"#{a.check} is NOT workable: closed or does not exist")
            sys.exit(1)
        if r["ok"]:
            note = " (its 'blocked' label is stale; remove it)" if r["stale_blocked"] else ""
            print(f"#{r['n']} is workable, lane {r['lane']}, unblocks {r['fan']} open issue(s): {r['title']}{note}")
            sys.exit(0)
        print(f"#{r['n']} is NOT workable: {r['why']}")
        sys.exit(1)

    rows = [r for r in rows if (a.all or r["ok"]) and "epic" not in r["why"] and "future" not in r["why"]]
    if a.lane:
        rows = [r for r in rows if r["lane"] == a.lane]
    rows.sort(key=lambda r: (not r["ok"], ms_rank(r["ms"]), r["pri"], -r["fan"], r["wave"], r["n"]))
    if not rows:
        print("No workable issues" + (f" in lane {a.lane}" if a.lane else "") + ". Try without --lane, or --all.")
        return
    head = f"{'OK  ' if a.all else ''}{'MS':<4}{'PRI':<5}{'UNBLK':<7}{'LANE':<6}{'WAVE':<6}{'ISSUE':<8}{'AREA':<15}TITLE"
    print(head)
    for r in rows:
        ok = ("YES " if r["ok"] else "NO  ") if a.all else ""
        ms = r["ms"].split()[0] if r["ms"] else "-"
        wave = f"W{r['wave']}" if r["wave"] != 99 else "-"
        tail = f"  <- {r['why']}" if a.all and not r["ok"] else ""
        print(f"{ok}{ms:<4}{r['pri']:<5}{r['fan']:<7}{r['lane']:<6}{wave:<6}#{r['n']:<7}{r['area']:<15}{r['title'][:70]}{tail}")
    stale = [f"#{r['n']}" for r in rows if r["ok"] and r["stale_blocked"]]
    if stale:
        print(f"\nnote: {', '.join(stale)} still carry a 'blocked' label although all blockers are closed; remove it.")


if __name__ == "__main__":
    main()
