#!/usr/bin/env bash
set -euo pipefail
config_file="${PSC_ALLOCATE_CONFIG:-$HOME/.config/psc-allocate/allocate.env}"
if [[ -r "$config_file" ]]; then set -a; source "$config_file"; set +a; fi

state_dir="${PSC_ALLOCATE_STATE_DIR:-$HOME/.local/state/psc-allocate}"
export PSC_ALLOCATE_STATE_DIR="$state_dir"
route_dir="$state_dir/routes"
ack_dir="$state_dir/acks"
completion_dir="$state_dir/completions"
inner_runner="${PSC_ALLOCATE_INNER_RUNNER:-${HPC_CORE_BIN:-$HOME/.local/bin}/psc-allocate-inner-run}"
default_window="${PSC_ALLOCATE_DEFAULT_WINDOW:-zovllm-run}"
outer_label="${PSC_ALLOCATE_OUTER_LABEL:-psc-login}"
outer_session="${PSC_ALLOCATE_OUTER_SESSION:-psc-login}"
outer_window="${PSC_ALLOCATE_OUTER_WINDOW:-allocate}"

usage() {
    cat <<'USAGE'
Usage:
  psc-allocate gpu [TYPE [COUNT [WALLTIME]]] [OPTIONS]
  psc-allocate cpu [CORES [WALLTIME]] [OPTIONS]
  psc-allocate run [--job JOB_ID] [--request REQUEST_ID] [--wait] WINDOW -- COMMAND [ARG...]
  psc-allocate release [--job JOB_ID] [--no-save]
  psc-allocate allocation REQUEST_ID
  psc-allocate task REQUEST_ID
  psc-allocate close [--job JOB_ID] WINDOW
  psc-allocate status

Options: --request ID  --begin TIME  --tmp SIZE  --mem SIZE
         --partition NAME  --constraint FEATURES
Partitions, GPU types, limits and defaults come from allocate.env.

Bootstraps the compute-node tmux, snapshot restore, five-minute autosave, and
the configured GCC toolchain (plus CUDA for GPU allocations).
`psc-allocate run` only forwards commands to that existing tmux environment.
USAGE
}

die() {
    echo "psc-allocate: $*" >&2
    exit 1
}

route_is_active() {
    local route_job="$1"
    local route_socket="$2"
    local route_target="$3"
    local route_pane_pid="${4:-}"
    local pane_dead pane_pid slurm_state

    # The route is registered once by psc-allocate-boot. Keep command dispatch on
    # the existing tmux bridge and do not query Slurm on every invocation or
    # completion poll; a slow controller must not delay a finished command.
    pane_dead="$(/usr/bin/tmux -S "$route_socket" display-message -p -t "$route_target" '#{pane_dead}' 2>/dev/null)" || return 1
    [[ "$pane_dead" == 0 ]] || return 1
    pane_pid="$(/usr/bin/tmux -S "$route_socket" display-message -p -t "$route_target" '#{pane_pid}' 2>/dev/null)" || return 1
    if [[ -n "$route_pane_pid" ]]; then
        [[ "$pane_pid" == "$route_pane_pid" ]]
        return
    fi

    # Compatibility for routes created before pane identity was recorded.
    slurm_state="$(/usr/bin/squeue -h -j "$route_job" -o '%T' 2>/dev/null | head -n 1 || true)"
    [[ "$slurm_state" == RUNNING ]]
}

collect_active_routes() {
    local requested_job="$1"
    local route_path route_job route_host route_socket route_target route_inner route_pane_pid
    local -a route_paths=()

    active_routes=()
    shopt -s nullglob
    route_paths=("$route_dir"/*.route)
    shopt -u nullglob

    for route_path in "${route_paths[@]}"; do
        IFS=$'\t' read -r route_job route_host route_socket route_target route_inner route_pane_pid < "$route_path" || continue
        [[ "$route_job" =~ ^[0-9]+$ ]] || continue
        [[ -z "$requested_job" || "$route_job" == "$requested_job" ]] || continue
        route_is_active "$route_job" "$route_socket" "$route_target" "$route_pane_pid" || continue
        active_routes+=("$route_job"$'\t'"$route_host"$'\t'"$route_socket"$'\t'"$route_target"$'\t'"$route_inner"$'\t'"$route_pane_pid")
    done
}

run_command() {
    local requested_job=""
    local window="$default_window"
    local window_set=false
    local wait_for_completion=false
    local command_string payload request_id='' ack_path status message dispatch_id
    local completion_path exit_code completed_job completed_window completed_at
    local selected_job selected_host selected_socket selected_target selected_inner selected_pane_pid
    local runner_shell tmux_command
    local dispatch_lock="$state_dir/dispatch.lock"
    local -a command_args=()

    while (($#)); do
        case "$1" in
            --request)
                request_id="$2"
                shift 2
                ;;
            --job)
                (($# >= 2)) || die "--job requires a job ID"
                requested_job="$2"
                shift 2
                ;;
            --wait)
                wait_for_completion=true
                shift
                ;;
            --window)
                (($# >= 2)) || die "--window requires a name"
                window="$2"
                window_set=true
                shift 2
                ;;
            --)
                shift
                break
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                [[ "$window_set" == false ]] || die "expected -- before command"
                window="$1"
                window_set=true
                shift
                [[ "${1:-}" == -- ]] || die "expected -- after window name"
                shift
                break
                ;;
        esac
    done

    (($# > 0)) || die "run requires a command"
    [[ -z "$requested_job" || "$requested_job" =~ ^[0-9]+$ ]] || die "invalid job ID: $requested_job"
    [[ "$window" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "invalid window name: $window"
    [[ -x "$inner_runner" ]] || die "inner runner is missing: $inner_runner"

    collect_active_routes "$requested_job"
    if ((${#active_routes[@]} == 0)); then
        if [[ -n "$requested_job" ]]; then
            die "job $requested_job has no active READY route"
        fi
        die "no active READY allocation"
    fi
    if ((${#active_routes[@]} > 1)); then
        echo "psc-allocate: multiple active allocations; choose one with --job:" >&2
        for status in "${active_routes[@]}"; do
            IFS=$'\t' read -r selected_job selected_host _ <<< "$status"
            printf '  %s  %s\n' "$selected_job" "$selected_host" >&2
        done
        exit 2
    fi

    IFS=$'\t' read -r selected_job selected_host selected_socket selected_target selected_inner selected_pane_pid <<< "${active_routes[0]}"
    command_args=("$@")
    printf -v command_string '%q ' "${command_args[@]}"
    command_string="${command_string% }"
    payload="$(printf '%s' "$command_string" | /usr/bin/base64 -w 0)"
    request_id="${request_id:-$(date +%s%N)-$$}"
    [[ "$request_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || die 'invalid request ID'
    mkdir -p "$ack_dir"
    chmod 700 "$state_dir" "$ack_dir" 2>/dev/null || true
    dispatch_id="$(date +%s%N)-$$"
    ack_path="$ack_dir/$dispatch_id.ack"
    completion_path="$completion_dir/$request_id.done"

    printf -v runner_shell '%q ' /usr/bin/env "PSC_ALLOCATE_STATE_DIR=$state_dir" "$inner_runner" --job "$selected_job" --window "$window" --payload "$payload" --request "$request_id" --ack-request "$dispatch_id"
    runner_shell="${runner_shell% }"
    tmux_command="run-shell -b \"$runner_shell\""

    exec 9>"$dispatch_lock"
    /usr/bin/flock -w 10 9 || die "timed out waiting for the terminal dispatch lock"
    /usr/bin/tmux -S "$selected_socket" send-keys -t "$selected_target" C-b ':'
    sleep 0.1
    /usr/bin/tmux -S "$selected_socket" send-keys -t "$selected_target" -l "$tmux_command"
    /usr/bin/tmux -S "$selected_socket" send-keys -t "$selected_target" Enter

    for ((attempt = 0; attempt < 50; attempt++)); do
        [[ -s "$ack_path" ]] && break
        sleep 0.1
    done
    [[ -s "$ack_path" ]] || die "dispatch timed out for job $selected_job on $selected_host"

    IFS=$'\t' read -r status message < "$ack_path"
    /usr/bin/flock -u 9
    [[ "$status" == ok ]] || die "$message"
    printf '%s:%s request=%s\n' "$selected_host" "$window" "$request_id"

    if [[ "$wait_for_completion" == true ]]; then
        while [[ ! -s "$completion_path" ]]; do
            route_is_active "$selected_job" "$selected_socket" "$selected_target" "$selected_pane_pid" || \
                die "job $selected_job ended before request $request_id completed"
            sleep 0.2
        done
        IFS=$'\t' read -r exit_code completed_job completed_window completed_at < "$completion_path"
        [[ "$exit_code" =~ ^[0-9]+$ ]] || die "invalid completion record for request $request_id"
        printf 'completed job=%s window=%s exit=%s at=%s\n' \
            "$completed_job" "$completed_window" "$exit_code" "$completed_at"
        [[ ! -f "$state_dir/requests/$request_id/output.log" ]] || cat "$state_dir/requests/$request_id/output.log"
        return "$exit_code"
    fi
}

close_window() {
    local requested_job=""
    local window=""
    local request_id ack_path status message
    local selected_job selected_host selected_socket selected_target selected_inner selected_pane_pid
    local runner_shell tmux_command
    local dispatch_lock="$state_dir/dispatch.lock"

    while (($#)); do
        case "$1" in
            --job)
                (($# >= 2)) || die "--job requires a job ID"
                requested_job="$2"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                [[ -z "$window" ]] || die "close accepts one window name"
                window="$1"
                shift
                ;;
        esac
    done

    [[ -n "$window" ]] || die "close requires a window name"
    [[ -z "$requested_job" || "$requested_job" =~ ^[0-9]+$ ]] || die "invalid job ID: $requested_job"
    [[ "$window" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "invalid window name: $window"

    collect_active_routes "$requested_job"
    ((${#active_routes[@]} > 0)) || die "no active READY allocation"
    if ((${#active_routes[@]} > 1)); then
        die "multiple active allocations; choose one with --job"
    fi

    IFS=$'\t' read -r selected_job selected_host selected_socket selected_target selected_inner selected_pane_pid <<< "${active_routes[0]}"
    request_id="$(date +%s%N)-$$"
    mkdir -p "$ack_dir"
    chmod 700 "$state_dir" "$ack_dir" 2>/dev/null || true
    ack_path="$ack_dir/$request_id.ack"

    printf -v runner_shell '%q ' "$inner_runner" --job "$selected_job" --window "$window" --request "$request_id" --close
    runner_shell="${runner_shell% }"
    tmux_command="run-shell -b \"$runner_shell\""

    exec 9>"$dispatch_lock"
    /usr/bin/flock -w 10 9 || die "timed out waiting for the terminal dispatch lock"
    /usr/bin/tmux -S "$selected_socket" send-keys -t "$selected_target" C-b ':'
    sleep 0.1
    /usr/bin/tmux -S "$selected_socket" send-keys -t "$selected_target" -l "$tmux_command"
    /usr/bin/tmux -S "$selected_socket" send-keys -t "$selected_target" Enter

    for ((attempt = 0; attempt < 50; attempt++)); do
        [[ -s "$ack_path" ]] && break
        sleep 0.1
    done
    [[ -s "$ack_path" ]] || die "close timed out for job $selected_job on $selected_host"
    IFS=$'\t' read -r status message < "$ack_path"
    /usr/bin/flock -u 9
    [[ "$status" == ok ]] || die "$message"
    printf '%s:%s closed\n' "$selected_host" "$window"
}

show_status() {
    local route_path route_job route_host route_socket route_target route_inner route_pane_pid state
    local -a route_paths=()

    shopt -s nullglob
    route_paths=("$route_dir"/*.route)
    shopt -u nullglob
    if ((${#route_paths[@]} == 0)); then
        echo "No registered PSC allocation routes."
        return 0
    fi

    printf 'JOB_ID\tHOST\tSTATE\n'
    for route_path in "${route_paths[@]}"; do
        IFS=$'\t' read -r route_job route_host route_socket route_target route_inner route_pane_pid < "$route_path" || continue
        state="$(/usr/bin/squeue -h -j "$route_job" -o '%T' 2>/dev/null | head -n 1 || true)"
        [[ -n "$state" ]] || state=INACTIVE
        printf '%s\t%s\t%s\n' "$route_job" "$route_host" "$state"
    done
}

request_allocation() {
    local current_socket current_target
    local core_bin="${HPC_CORE_BIN:-$HOME/.local/bin}"
    if [[ "${allocation_internal:-0}" != 1 ]]; then
        local plan allocation_request allocation_kind
        local -a salloc_args=() allocation_fields=() allocation_env=()
        plan="$("$core_bin/psc-allocation" plan "$@")" || exit 2
        eval "$plan"
        if ! /usr/bin/tmux -L "$outer_label" has-session -t "$outer_session" 2>/dev/null; then
            /usr/bin/tmux -L "$outer_label" -f "$HOME/.tmux.conf" \
                new-session -d -s "$outer_session" -n "$outer_window" \
                /bin/bash --noprofile --norc
        elif ! /usr/bin/tmux -L "$outer_label" list-windows -t "$outer_session" \
                -F '#{window_name}' | /usr/bin/grep -Fxq "$outer_window"; then
            /usr/bin/tmux -L "$outer_label" new-window -d -t "$outer_session" \
                -n "$outer_window" /bin/bash --noprofile --norc
        fi

        # Detached panes can inherit a previous allocation's route and defaults.
        # Carry the requested outer identity explicitly and rebuild route metadata
        # only after the delegated process identifies its own pane.
        local -a core_env=()
        local key
        for key in HPC_CORE_BIN HPC_CORE_JOB_NAME PSC_ALLOCATE_CONFIG PSC_LOCAL_SNAPSHOT_ARCHIVE PSC_ALLOCATE_AUTOSAVE_INTERVAL PSC_ALLOCATE_MIRROR_DISABLED PSC_LOCAL_SNAPSHOT_READY_SECONDS; do
            [[ -z "${!key:-}" ]] || core_env+=("$key=${!key}")
        done
        PSC_ALLOCATE_STATE_DIR="$state_dir" "$core_bin/psc-allocation" submit "$allocation_request" \
            --socket "$outer_label" --target "$outer_session:$outer_window" \
            "${allocation_fields[@]}" --field "job_name=${HPC_CORE_JOB_NAME:-}" \
            -- /usr/bin/env \
            -u PSC_ALLOCATE_OUTER_SOCKET -u PSC_ALLOCATE_OUTER_TARGET -u PSC_ALLOCATE_OUTER_PANE_PID \
            "${core_env[@]}" "${allocation_env[@]}" \
            "PSC_ALLOCATE_OUTER_LABEL=$outer_label" \
            "PSC_ALLOCATE_OUTER_SESSION=$outer_session" \
            "PSC_ALLOCATE_OUTER_WINDOW=$outer_window" \
            "PSC_ALLOCATE_STATE_DIR=$state_dir" \
            "$core_bin/psc-allocate" _allocate "${salloc_args[@]}"
        return 0
    fi

    current_socket="${TMUX:-}"
    current_socket="${current_socket%%,*}"
    current_target=""
    # The calling pane, not the server's active pane, owns this allocation.
    if [[ -n "$current_socket" && -n "${TMUX_PANE:-}" ]]; then
        current_target="$(/usr/bin/tmux -S "$current_socket" display-message -p \
            -t "$TMUX_PANE" '#{session_name}:#{window_name}.#{pane_index}')"
    fi
    [[ "$current_socket" == */"$outer_label" && "$current_target" == "${outer_session}:${outer_window}."* ]] || \
        die 'internal allocation must run in its named allocation pane'

    export PSC_ALLOCATE_OUTER_SOCKET="$current_socket"
    export PSC_ALLOCATE_OUTER_TARGET="$current_target"
    export PSC_ALLOCATE_OUTER_PANE_PID
    PSC_ALLOCATE_OUTER_PANE_PID="$(/usr/bin/tmux -S "$current_socket" display-message -p \
        -t "$TMUX_PANE" '#{pane_pid}')"

    mkdir -p "$state_dir/allocations"
    # The durable holder tees the controlling PTY into this request log.
    # A stdout pipe here would disable srun --pty and break the terminal bridge.
    local allocation_log="$state_dir/allocations/${HPC_ALLOCATION_REQUEST_ID:?durable holder required}.log"
    printf 'ALLOCATE_REQUEST host=%s kind=%s args=%s log=%s\n' \
        "$(hostname -s)" "${HPC_ALLOCATION_KIND:?allocation kind required}" "$*" "$allocation_log"
    local -a salloc_args=("$@" "--job-name=${HPC_CORE_JOB_NAME:-psc-allocate}")
    [[ -z "${HPC_ALLOCATION_COMMENT:-}" ]] || salloc_args+=("--comment=$HPC_ALLOCATION_COMMENT")
    exec /usr/bin/salloc "${salloc_args[@]}" \
        /usr/bin/srun --pty --export=ALL "$core_bin/psc-allocate-boot"
}

# End one job this helper allocated: save the workspace, then cancel it.
release_job() {
    local requested_job="" save=true detail state attempt
    local selected_job selected_host selected_socket selected_target selected_inner selected_pane_pid
    while (($#)); do
        case "$1" in
            --job) (($# >= 2)) || die '--job requires an ID'; requested_job="$2"; shift 2 ;;
            --no-save) save=false; shift ;;
            *) die 'usage: psc-allocate release [--job JOB_ID] [--no-save]' ;;
        esac
    done
    if [[ -z "$requested_job" ]]; then
        collect_active_routes ""
        ((${#active_routes[@]} == 1)) || die 'release needs --job unless exactly one allocation is active'
        IFS=$'\t' read -r requested_job _ <<< "${active_routes[0]}"
    fi
    [[ "$requested_job" =~ ^[0-9]+$ ]] || die "invalid job ID: $requested_job"
    detail="$(/usr/bin/scontrol show job -o "$requested_job")" || die "cannot read job $requested_job"
    [[ " $detail " == *" UserId=$USER("* ]] || die "job $requested_job does not belong to $USER"
    [[ " $detail " == *" Comment=hpc-core:psc:"* ]] || \
        die "job $requested_job was not allocated by psc-allocate; not cancelled"
    # Bind the job to its request before cancelling, so the request can finish.
    "${HPC_CORE_BIN:-$HOME/.local/bin}/psc-allocation" adopt --job "$requested_job" >/dev/null || \
        die "cannot bind job $requested_job to its allocation request; not cancelled"
    if [[ "$save" == true && " $detail " == *" JobState=RUNNING "* ]]; then
        run_command --job "$requested_job" --request "release-save-$requested_job-$(date +%s)" --wait release-save -- \
            "${HPC_CORE_BIN:-$HOME/.local/bin}/psc-local-snapshot" save || \
            die "final snapshot save failed; job $requested_job is still running (--no-save skips the save)"
    fi
    /usr/bin/scancel "$requested_job"
    for ((attempt = 0; attempt < 60; attempt++)); do
        state="$(/usr/bin/squeue -h -j "$requested_job" -o '%T' 2>/dev/null || true)"
        [[ -n "$state" ]] || break
        sleep 2
    done
    [[ -z "$state" ]] || die "job $requested_job is still $state after scancel"
    "${HPC_CORE_BIN:-$HOME/.local/bin}/psc-allocation" settle --job "$requested_job" || \
        die "job $requested_job was cancelled but its allocation request could not be reconciled"
    printf 'released job=%s\n' "$requested_job"
}

case "${1:-}" in
    _allocate) allocation_internal=1; shift; request_allocation "$@" ;;
    allocation) shift; exec "${HPC_CORE_BIN:-$HOME/.local/bin}/psc-allocation" get "$@" ;;
    task)
        shift
        exec "$HOME/.local/bin/psc-agent-state" task "$@"
        ;;
    run)
        shift
        run_command "$@"
        ;;
    close)
        shift
        close_window "$@"
        ;;
    release)
        shift
        release_job "$@"
        ;;
    status)
        shift
        (($# == 0)) || die "status takes no arguments"
        show_status
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        request_allocation "$@"
        ;;
esac
