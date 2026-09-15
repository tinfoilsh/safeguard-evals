#!/bin/bash
# Download all 127 WildChat-4.8M-Full shards into data/original/. Resumable + idempotent:
# skips shards whose local size matches the remote size, resumes partials. Needs HF_KEY.
#   nohup ./download_all.sh > data/logs/download.log 2>&1 &
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
set -a; . "$DIR/../../.env"; set +a
TOK="$HF_KEY"
BASE="https://huggingface.co/datasets/yuntian-deng/WildChat-4.8M-Full/resolve/main/data"

OUT="$DIR/data/original"
mkdir -p "$OUT"
for n in $(seq 0 126); do
  i=$(printf "%05d" "$n")
  f="$OUT/train-${i}-of-00127.parquet"
  url="$BASE/train-${i}-of-00127.parquet"
  remote=$(curl -sIL -H "Authorization: Bearer $TOK" "$url" \
            | awk 'tolower($1)=="x-linked-size:"{print $2}' | tr -d '\r')
  local=$(stat -f%z "$f" 2>/dev/null || echo 0)
  if [ -n "$remote" ] && [ "$local" = "$remote" ]; then
    echo "[$i] skip (complete, $remote bytes)"; continue
  fi
  echo "[$i] downloading (have $local / want ${remote:-?})"
  curl -sL -C - -H "Authorization: Bearer $TOK" -o "$f" "$url"
done
echo "ALL DONE $(date)"
