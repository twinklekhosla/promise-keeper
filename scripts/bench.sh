#!/bin/zsh
# Fresh (uncached) run on a dataset, then accuracy, privacy and cost. Usage: scripts/bench.sh [bench|demo] [runs]
cd "$(dirname "$0")/.."
ds=${1:-bench}; runs=${2:-1}
for i in $(seq 1 $runs); do
  before=$(sqlite3 data/$ds.db "SELECT COALESCE(SUM(usd),0) FROM ledger" 2>/dev/null || echo 0)
  t0=$(date +%s)
  PK_NO_CACHE=1 .venv/bin/python -m promise_keeper demo --dataset $ds --reset > /dev/null 2>&1
  t1=$(date +%s)
  after=$(sqlite3 data/$ds.db "SELECT SUM(usd) FROM ledger")
  sent=$(sqlite3 data/$ds.db "SELECT SUM(sent) || '/' || COUNT(*) FROM messages")
  .venv/bin/python -m promise_keeper eval --dataset $ds | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f\"run $i: recall {r['matched']}/{r['gold']}  precision {r['precision']:.2f} ({r['found']} found, {r['optional_found']} optional)  due {r['due_correct']}/{r['matched']}  status {r['status_correct']}/{r['matched']}  | sent $sent msgs | \$\" + f'{$after - $before:.4f}' + ' | $((t1 - t0))s')
for k in ('missed', 'extra', 'wrong_due', 'wrong_status', 'hidden_as_minor'):
    for x in r[k]: print(f'    {k}: {x}')
"
done
