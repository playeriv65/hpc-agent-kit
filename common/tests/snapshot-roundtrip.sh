#!/usr/bin/env bash
# Test the installed operations against isolated synthetic data, without GPUs.
set -euo pipefail
helper=$1
fixture=$2
export fixture
mkdir -p "$fixture/base/workspace" "$fixture/archive"
mkdir -p "$fixture/base/venvs/legacy" "$fixture/base/workspace/.venv"
mkdir -p "$fixture/base/workspace/launches/test/uv-bootstrap" "$fixture/base/workspace/launches/test/python-managed"
printf 'rebuildable\n' > "$fixture/base/venvs/legacy/package.txt"
printf 'rebuildable\n' > "$fixture/base/workspace/.venv/package.txt"
printf 'rebuildable\n' > "$fixture/base/workspace/launches/test/uv-bootstrap/package.txt"
printf 'rebuildable\n' > "$fixture/base/workspace/launches/test/python-managed/package.txt"
# Import the operations without executing their CLI entry point.
source <(sed '$d' "$helper")
local_base() { printf '%s\n' "$fixture/base"; }
printf 'first\n' > "$fixture/base/workspace/sample.txt"
archive="$fixture/archive/test.tar.zst"
with_lock "$archive" save_cmd "$archive"
tar --use-compress-program 'zstd -q -d' -tf "$archive" > "$fixture/saved-files.txt"
! grep -q 'venvs/legacy\|\.venv/package\|uv-bootstrap/package\|python-managed/package' "$fixture/saved-files.txt"
printf 'latest\n' > "$fixture/base/workspace/sample.txt"
with_lock "$archive" incsave_cmd "$archive"
cp "$archive.inc/"incremental-*.tar.zst "$archive.inc/incremental-99999998.tar.zst"
printf 'changed locally\n' > "$fixture/base/workspace/sample.txt"
with_lock "$archive" restore_cmd "$archive" 2> "$fixture/restore.log"
grep -q 'Skipping identical consecutive incremental' "$fixture/restore.log"
grep -qx latest "$fixture/base/workspace/sample.txt"
printf 'sentinel\n' > "$fixture/base/workspace/sample.txt"
printf 'broken\n' > "$archive.inc/incremental-99999999.tar.zst"
set +e
( set -e; with_lock "$archive" restore_cmd "$archive" ) > "$fixture/bad-restore.log" 2>&1
rc=$?
set -e
test "$rc" != 0
grep -qx sentinel "$fixture/base/workspace/sample.txt"
test -f "$archive.inc/incremental-99999999.tar.zst"
fail_restore() { false; echo INCORRECT_SUCCESS; }
if with_lock "$archive" fail_restore > "$fixture/conditional-failure.log" 2>&1; then
    echo 'Conditional invocation swallowed a failure' >&2
    exit 1
fi
! grep -q INCORRECT_SUCCESS "$fixture/conditional-failure.log"
old_hash=$(sha256sum "$archive")
verify_pack() { return 19; }
if with_lock "$archive" save_cmd "$archive" > "$fixture/failed-save.log" 2>&1; then
    echo 'Save promoted an unverified pack' >&2
    exit 1
fi
test "$(sha256sum "$archive")" = "$old_hash"
echo SNAPSHOT_FULL_INCREMENTAL_AND_CORRUPTION_TESTS_PASSED
