#!/bin/bash
# Real agy-call runs against the test suite's stand-in for agy, so the demo spends no Gemini quota.
set -u
R=$(cd "$(dirname "$0")/../.." && pwd)
W=$(mktemp -d)
cd "$W"
cp "$R/tests/fake_agy.py" "$W/agy" && chmod +x "$W/agy"
export AGYCALL_HOME="$W/home" AGYCALL_AGY="$W/agy" FAKE_STATE="$W/state" FAKE_LOG="$W/log.jsonl"
show() { printf '\n$ %s\n' "$*"; eval "$@"; }

echo '# 1. A reply in the wrong format: resume the same conversation, keep both replies'
cat > "$W/s1.json" <<'EOF'
[{"response": "Sure! The capital of France is Paris.\n", "conversation": "conv-7f3a"},
 {"response": "```json\n{\"country\": \"France\", \"capital\": \"Paris\"}\n```", "conversation": "conv-7f3a"}]
EOF
export FAKE_SCENARIO="$W/s1.json"
show 'agy-call run --job capital-fr --store jobs --model gemini-4-argon-high --require country,capital --prompt "Capital of France, as JSON"'
show 'ls jobs/capital-fr/attempts      # both replies kept on disk'
show 'cat jobs/capital-fr/attempts/001/reply.txt'
show "grep -E '\"(kind|message|conversation)\"' jobs/capital-fr/attempts/002/launch.json"

echo
echo '# 2. Quota runs out: one hold stops every other job'
cat > "$W/s2.json" <<'EOF'
[{"exit": 1, "status": "ERROR", "response": "", "error": "RESOURCE_EXHAUSTED: quota exceeded for this model. Resets in 1h12m27s"}]
EOF
export FAKE_SCENARIO="$W/s2.json" FAKE_STATE="$W/state2" FAKE_LOG="$W/log2.jsonl"
printf '{"job": "q%d", "prompt": "item %d"}\n' 1 1 2 2 3 3 4 4 5 5 > jobs.jsonl
show 'agy-call batch jobs.jsonl --workers 1 --store jobs --model gemini-4-argon-high'
show 'grep -c . log2.jsonl      # launches made'
rm -rf "$W"
