#!/usr/bin/env bash
# Generic Slurm adapter for sites with login-to-compute SSH access.
set -euo pipefail
config="${@PREFIX@_ALLOCATE_CONFIG:-$HOME/.config/@SITE@-allocate/allocate.env}"
set -a; source "$config"; set +a
core_bin="${HPC_CORE_BIN:-$HOME/.local/bin}"
state="${@PREFIX@_ALLOCATE_STATE_DIR:-$HOME/.local/state/@SITE@-allocate}"
export @PREFIX@_ALLOCATE_STATE_DIR="$state"
die() { echo '@SITE@-allocate: '"$*" >&2; exit 2; }
case "${1:-}" in
  allocation) shift; exec "$core_bin/@SITE@-allocation" get "$@" ;;
  status|doctor) mode="$1"; shift; [[ "${1:-}" != --json ]] || shift; exec "$core_bin/@SITE@-agent-state" "$mode" "$@" ;;
  task) shift; exec "$core_bin/@SITE@-agent-state" task "$@" ;;
  run)
    shift; job='' request='' wait_flag='' window=''
    while (($#)); do
      case "$1" in
        --job) job="$2"; shift 2 ;;
        --request) request="$2"; shift 2 ;;
        --wait) wait_flag=--wait; shift ;;
        *) window="$1"; shift; [[ "${1:-}" == -- ]] || die 'run requires WINDOW -- COMMAND'; shift; break ;;
      esac
    done
    [[ "$job" =~ ^[0-9]+$ && -n "$window" && $# -gt 0 ]] || die 'run requires --job ID and WINDOW -- COMMAND'
    IFS=$'\t' read -r route_job host socket session < "$state/routes/$job.route"
    [[ "$route_job" == "$job" && "$host" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || die 'invalid route'
    phase="$(squeue -h -u "$USER" -j "$job" -o '%T')" || die 'scheduler state unknown'
    [[ "$phase" == RUNNING ]] || die 'allocation is not running'
    printf -v command '%q ' "$@"
    payload="$(printf '%s' "${command% }" | base64 -w 0)"
    request="${request:-$(date +%s%N)-$$}"
    printf -v remote '%q ' /usr/bin/env "@PREFIX@_ALLOCATE_STATE_DIR=$state" "$core_bin/@SITE@-allocate-inner-run" --job "$job" --window "$window" --request "$request" --payload "$payload" ${wait_flag:+$wait_flag}
    exec ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" "${remote% }"
    ;;
  release)
    # Cancel one job this adapter allocated. Save the workspace first with
    # run --wait ... @SITE@-local-snapshot save when it must be preserved.
    shift; [[ "${1:-}" == --job && "${2:-}" =~ ^[0-9]+$ && $# -eq 2 ]] || die 'release requires --job ID'
    detail="$(scontrol show job -o "$2")" || die 'cannot read job'
    [[ " $detail " == *" UserId=$USER("* && " $detail " == *" Comment=hpc-core:@SITE@:"* ]] || \
      die 'job was not allocated by this adapter for this user; not cancelled'
    exec scancel "$2"
    ;;
  _allocate)
    shift
    [[ -n "${HPC_ALLOCATION_REQUEST_ID:-}" && -n "${HPC_ALLOCATION_COMMENT:-}" ]] || die 'internal allocation identity missing'
    exec salloc "$@" "--job-name=$HPC_CORE_JOB_NAME" "--comment=$HPC_ALLOCATION_COMMENT" \
      srun --pty --export=ALL "$core_bin/@SITE@-allocate-boot"
    ;;
esac
salloc_args=() allocation_fields=() allocation_env=()
plan="$("$core_bin/@SITE@-allocation" plan "$@")" || exit 2
eval "$plan"
outer=(tmux -L "$HPC_OUTER_SOCKET")
if ! "${outer[@]}" has-session -t "$HPC_OUTER_SESSION" 2>/dev/null; then
  "${outer[@]}" new-session -d -s "$HPC_OUTER_SESSION" -n control /bin/bash --noprofile --norc
fi
if ! "${outer[@]}" list-windows -t "$HPC_OUTER_SESSION" -F '#{window_name}' | grep -Fxq allocate; then
  "${outer[@]}" new-window -d -t "$HPC_OUTER_SESSION:" -n allocate /bin/bash --noprofile --norc
fi
exec "$core_bin/@SITE@-allocation" submit "$allocation_request" --socket "$HPC_OUTER_SOCKET" --target "$HPC_OUTER_SESSION:allocate" \
  "${allocation_fields[@]}" --field "profile_hash=$HPC_PROFILE_HASH" \
  -- /usr/bin/env "@PREFIX@_ALLOCATE_CONFIG=$config" "HPC_CORE_BIN=$core_bin" "@PREFIX@_ALLOCATE_STATE_DIR=$state" \
  "${allocation_env[@]}" "$core_bin/@SITE@-allocate" _allocate "${salloc_args[@]}"
