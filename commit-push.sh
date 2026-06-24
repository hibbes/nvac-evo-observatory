#!/bin/bash
# Periodic commit + push for nvac-evo-observatory (run hourly via cron, as neo).
# Gzips completed-day baseline CSVs (today's is still being written and stays
# live + gitignored), then commits any new wedge events / summaries and pushes.
set -o pipefail
cd /home/neo/nvac-evo-observatory || exit 1

TODAY=$(date +%F)
for f in data/samples-*.csv; do
	[ -e "$f" ] || continue
	d=$(basename "$f" .csv); d=${d#samples-}
	[ "$d" = "$TODAY" ] && continue
	gzip -f "$f"            # data/samples-YYYY-MM-DD.csv.gz (tracked; .csv is gitignored)
done

git add -A
if git diff --cached --quiet; then
	exit 0                 # nothing new this run
fi
N=$(git status --short | wc -l)
git commit -q -m "observatory: data update $(date -u +%FT%TZ) ($N files)"
git push -q origin master
