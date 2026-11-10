#!/usr/bin/env python3
"""Durable Slurm request identities; compatible with Python 3.6."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import pty
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time

SITE = '@SITE@'
PREFIX = '@PREFIX@'


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def atomic(path, data):
    temporary = path.with_name(path.name + '.tmp.' + str(os.getpid()))
    with temporary.open('w') as stream:
        json.dump(data, stream, sort_keys=True)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)
    fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def command(argv):
    return subprocess.check_output(argv, stderr=subprocess.STDOUT,
                                   universal_newlines=True, timeout=30).strip()


def queue():
    rows = command(['squeue', '-h', '-u', os.environ['USER'], '-o', '%i|%T|%j|%N'])
    jobs = []
    for row in rows.splitlines():
        job, phase, name, node = row.split('|', 3)
        if not re.match(r'^[0-9]+$', job):
            raise RuntimeError('Unsupported scheduler job identity: ' + job)
        jobs.append(dict(job_id=job, slurm_state=phase, name=name, node=node))
    return jobs


TERMINAL = ('COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT', 'NODE_FAIL', 'OUT_OF_MEMORY',
            'PREEMPTED', 'BOOT_FAIL', 'DEADLINE', 'REVOKED')
# salloc reports the job ID as soon as Slurm accepts the request, before it starts.
JOB_LINE = re.compile(r'salloc: (Pending|Granted) job allocation ([0-9]+)')
CLOCK_SKEW = datetime.timedelta(minutes=5)


def local_time(stamp):
    """Convert a stored UTC timestamp to naive local time, as sacct prints and parses it."""
    value = datetime.datetime.strptime(stamp[:19], '%Y-%m-%dT%H:%M:%S')
    return value.replace(tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None)


def accounting_by_name(record):
    """Jobs of this user named after the request and submitted after it was reserved."""
    since = local_time(record['created_at']) - CLOCK_SKEW
    rows = command(['sacct', '-nXP', '-u', os.environ['USER'], '--name=' + record['job_name'],
                    '-S', since.strftime('%Y-%m-%dT%H:%M:%S'), '-E', 'now',
                    '-o', 'JobID,JobName,State,ExitCode,Submit'])
    matches = []
    for row in rows.splitlines():
        fields = row.split('|')
        if len(fields) != 5 or fields[1] != record['job_name']:
            continue
        try:
            submitted = datetime.datetime.strptime(fields[4], '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            submitted = None  # Unparseable submit time: keep it as evidence, never drop it.
        if submitted is None or submitted >= since:
            matches.append(fields)
    return matches


def resolve_unbound(record):
    """Resolve a request with no job ID that squeue does not list; never guess."""
    name, user = record['job_name'], os.environ['USER']
    check = ("sacct -X -u %s --name=%s -S %s -o JobID,JobName,State,Submit"
             % (user, name, (local_time(record['created_at']) - CLOCK_SKEW).strftime('%Y-%m-%dT%H:%M:%S')))
    rows = accounting_by_name(record)
    if len(rows) > 1:
        record.update(phase='submission_unknown', recovery='several jobs named ' + name + ' ('
                      + ', '.join(row[0] for row in rows) + '); inspect them with: ' + check)
    elif rows and rows[0][2].split()[0] in TERMINAL:
        job, state, code = rows[0][0], rows[0][2], rows[0][3]
        record.update(job_id=job, phase='terminal', slurm_state=state, exit_code=code)
    elif rows:
        record.update(phase='submission_unknown', slurm_state=rows[0][2],
                      recovery='sacct shows job ' + rows[0][0] + ' ' + rows[0][2] + ' but squeue does not list it; '
                      'check: squeue -j ' + rows[0][0] + ' and scontrol show job ' + rows[0][0])
    elif record.get('holder_ended_at'):
        # The holder ran salloc to completion and Slurm never accounted a job for it.
        record.update(phase='terminal', slurm_state='NOT_SUBMITTED')
    else:
        record.update(phase='submission_unknown', recovery=(
            'no job is queued or accounted yet and the holder has not finished; check the '
            + record['identity'].get('target', 'allocation') + ' tmux window, holder pid '
            + str(record.get('holder_pid')) + ' on ' + str(record.get('holder_host'))
            + ', the log ' + record.get('log_path', '') + ' and: ' + check))


def reconcile(record, jobs):
    record.pop('recovery', None)
    candidates = [job for job in jobs if job['job_id'] == record['job_id']] if record.get('job_id') else [job for job in jobs if job['name'] == record['job_name']]
    if len(candidates) > 1:
        raise RuntimeError('Multiple jobs match request; inspect before continuing')
    if candidates:
        job = candidates[0]
        detail = command(['scontrol', 'show', 'job', job['job_id'], '-o'])
        if ('Comment=' + record['comment']) not in detail.split():
            raise RuntimeError('Slurm request comment mismatch; refusing to adopt job')
        if not re.search(r'\bUserId=' + re.escape(os.environ['USER']) + r'\(', detail):
            raise RuntimeError('Slurm job owner mismatch')
        if record.get('job_id') and record['job_id'] != job['job_id']:
            raise RuntimeError('Request job identity changed')
        record.update(job_id=job['job_id'], slurm_state=job['slurm_state'], node=job['node'],
                      phase='ready' if record.get('ready') else 'allocated' if job['slurm_state'] == 'RUNNING' else 'pending')
    elif record.get('job_id'):
        rows = command(['sacct', '-nXP', '-j', record['job_id'], '-o', 'JobID,State,ExitCode'])
        match = next((r.split('|') for r in rows.splitlines()
                      if r.split('|')[0] == record['job_id']), None)
        if match and match[1].split()[0] in TERMINAL:
            record.update(phase='terminal', slurm_state=match[1], exit_code=match[2])
        else:
            record.update(phase='unknown', slurm_state=match[1] if match else 'UNKNOWN',
                          recovery='job ' + record['job_id'] + ' is neither queued nor terminal in accounting; '
                          'check: sacct -X -j ' + record['job_id'] + ' -o JobID,State,End')
    else:
        resolve_unbound(record)
    record['observed_at'] = now()
    return record


def directory():
    return Path(os.environ.get(PREFIX + '_ALLOCATE_STATE_DIR', str(Path.home() / '.local/state' / (SITE + '-allocate')))) / 'allocations'


def request_path(root, request):
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$', request):
        raise ValueError('Invalid allocation request ID')
    return root / (request + '.json')


USAGE = ('allocate gpu [TYPE [COUNT [WALLTIME]]] | allocate cpu [CORES [WALLTIME]]; options: '
         '--request ID --begin TIME --tmp SIZE --mem SIZE --partition NAME --constraint FEATURES')
WALLTIME = r'^[0-9]+:[0-5][0-9]:[0-5][0-9]$'
SIZE = r'^[1-9][0-9]*[KMGT]?$'
NAME = r'^[A-Za-z0-9][A-Za-z0-9_,-]*$'


def setting(scope, name, default=''):
    """Read PREFIX_ALLOCATE_[SCOPE_]NAME from the sourced site configuration."""
    key = PREFIX + '_ALLOCATE_' + (scope + '_' if scope else '') + name
    return os.environ.get(key, default).strip()


def plan(argv):
    """Validate one allocate request against site configuration; return salloc arguments."""
    flags = {'--request': 'request', '--begin': 'begin', '--tmp': 'tmp', '--mem': 'mem',
             '--partition': 'partition', '--constraint': 'constraint'}
    options = dict(request='', begin='', tmp=None, mem=None, partition=None, constraint=None)
    positional = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in flags:
            if index + 1 >= len(argv):
                raise ValueError(item + ' requires a value')
            options[flags[item]] = argv[index + 1]
            index += 2
        elif item.startswith('-'):
            raise ValueError('unknown option ' + item + '; usage: ' + USAGE)
        else:
            positional.append(item)
            index += 1
    if not positional or positional[0] not in ('gpu', 'cpu'):
        raise ValueError('usage: ' + USAGE)
    kind = positional.pop(0)
    family = kind.upper()
    env = dict(HPC_ALLOCATION_KIND=kind)
    fields = dict(kind=kind)
    if kind == 'gpu':
        if len(positional) > 3:
            raise ValueError('too many arguments; usage: ' + USAGE)
        types = setting('GPU', 'TYPES').split()
        gpu = (positional[0] if positional else setting('GPU', 'DEFAULT_TYPE')).lower()
        if gpu not in types:
            raise ValueError('GPU type must be one of: ' + ', '.join(types))
        scope = 'GPU_' + re.sub(r'[^A-Z0-9]', '_', gpu.upper())
        count = positional[1] if len(positional) > 1 else setting('GPU', 'DEFAULT_COUNT')
        counts = setting(scope, 'COUNTS').split()
        if count not in counts:
            raise ValueError('GPU count for ' + gpu + ' must be one of: ' + ', '.join(counts))
        walltime = positional[2] if len(positional) > 2 else setting('GPU', 'WALLTIME')
        partition, qos = setting(scope, 'PARTITION'), setting(scope, 'QOS')
        gres = setting(scope, 'GRES') or gpu
        cpus = setting('GPU', 'CPUS')
        resources = (['--cpus-per-task=' + cpus] if cpus else []) + ['--gres=gpu:' + gres + ':' + count]
        fields.update(gpu=gres, count=count, cpus=cpus)
        pattern = setting(scope, 'NAME_PATTERN')
        if pattern:
            env['HPC_EXPECTED_GPU_NAME_PATTERN'] = pattern
    else:
        if len(positional) > 2:
            raise ValueError('too many arguments; usage: ' + USAGE)
        cores = positional[0] if positional else setting('CPU', 'CORES')
        walltime = positional[1] if len(positional) > 1 else setting('CPU', 'WALLTIME')
        scope = 'CPU'
        partition, qos = setting('CPU', 'PARTITION'), setting('CPU', 'QOS')
        resources = ['--cpus-per-task=' + cores]
        fields.update(cores=cores, cpus=cores)
    if options['partition'] is not None:
        # Only partitions listed for this kind in the site configuration are accepted.
        allowed = setting(scope, 'PARTITIONS').split() or [partition]
        if options['partition'] not in allowed:
            raise ValueError('--partition for ' + kind + ' must be one of: ' + ', '.join(allowed))
        partition = options['partition']
    constraint = options['constraint'] if options['constraint'] is not None else setting(scope, 'CONSTRAINT')
    if constraint and not re.match(r'^[A-Za-z0-9_&|,()\[\]*-]+$', constraint):
        raise ValueError('--constraint must use Slurm feature syntax')
    if not re.match(NAME, partition):
        raise ValueError('No valid ' + kind + ' partition is configured')
    if kind == 'cpu':
        # A partition may cap cores below the site-wide maximum (shared nodes).
        limit = setting('CPU', 'MAX_CORES_' + re.sub(r'[^A-Z0-9]', '_', partition.upper())) or setting('CPU', 'MAX_CORES')
        if not re.match(r'^[1-9][0-9]*$', cores) or not limit.isdigit() or int(cores) > int(limit):
            raise ValueError('CPU cores on ' + partition + ' must be between 1 and ' + (limit or 'the configured maximum'))
    if qos and not re.match(NAME, qos):
        raise ValueError('Invalid configured QoS: ' + qos)
    if not re.match(WALLTIME, walltime):
        raise ValueError('WALLTIME must use HH:MM:SS')
    memory = setting(family, 'MEMORY')
    if setting(family, 'MEMORY_REQUEST', '1') == '0':
        # Some partitions derive memory from cores or GPUs and reject --mem.
        if options['mem'] is not None:
            raise ValueError(partition + ' assigns memory per core or GPU; request more of those instead of --mem')
        memory = ''
    elif options['mem'] is not None:
        memory = options['mem']
    tmp = options['tmp'] if options['tmp'] is not None else setting(family, 'TMP')
    for label, value in (('--mem', memory), ('--tmp', tmp)):
        if value and not re.match(SIZE, value):
            raise ValueError(label + ' must be a size such as 8G')
    begin = options['begin']
    if begin and not re.match(r'^[A-Za-z0-9:/ .+-]+$', begin):
        raise ValueError('--begin must use Slurm time syntax')
    request = options['request'] or '%d-%d' % (int(datetime.datetime.now().timestamp() * 1e9), os.getppid())
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$', request):
        raise ValueError('Invalid allocation request ID')
    account = setting('', 'ACCOUNT')
    salloc = ['--partition=' + partition, '--nodes=1', '--ntasks=1'] + resources
    salloc += ['--mem=' + memory] if memory else []
    salloc += ['--time=' + walltime]
    salloc += ['--tmp=' + tmp] if tmp else []
    salloc += ['--constraint=' + constraint] if constraint else []
    salloc += ['--qos=' + qos] if qos else []
    salloc += ['--account=' + account] if account else []
    salloc += ['--begin=' + begin] if begin else []
    fields.update(account=account, partition=partition, qos=qos, time=walltime, begin=begin,
                  memory=memory, scratch=tmp, constraint=constraint)
    return dict(request=request, kind=kind, salloc=salloc, fields=fields, env=env)


def shell_plan(result):
    """Render a plan as Bash assignments for the site allocator to eval."""
    def array(name, items):
        return name + '=(' + ' '.join(shlex.quote(item) for item in items) + ')'
    fields = []
    for key in sorted(result['fields']):
        fields += ['--field', key + '=' + result['fields'][key]]
    return '\n'.join([
        'allocation_request=' + shlex.quote(result['request']),
        'allocation_kind=' + shlex.quote(result['kind']),
        array('salloc_args', result['salloc']),
        array('allocation_fields', fields),
        array('allocation_env', [key + '=' + value for key, value in sorted(result['env'].items())]),
    ]) + '\n'


def submit(root, args):
    journal = root.parent / 'deployment.json'
    if journal.exists() and json.loads(journal.read_text()).get('phase') in ('installing', 'rolling_back', 'rollback_failed'):
        raise RuntimeError('Incomplete deployment; recover the saved release before allocating')
    path = request_path(root, args.request)
    spec = dict(field.split('=', 1) for field in args.field)
    identity = dict(spec=spec, argv=args.command, socket=args.socket, target=args.target)
    jobs = queue()  # Failure is unknown, never an empty queue.
    if path.exists():
        record = json.loads(path.read_text())
        if record['identity'] != identity:
            raise RuntimeError('Allocation request ID reused with changed configuration')
        record = reconcile(record, jobs)
        atomic(path, record)
        return record
    for previous in root.glob('*.json'):
        record = reconcile(json.loads(previous.read_text()), jobs)
        atomic(previous, record)
        if record['phase'] != 'terminal':
            raise RuntimeError('Existing allocation request ' + record['request_id'] + ' requires recovery ('
                               + record['phase'] + (': ' + record['recovery'] if record.get('recovery') else '')
                               + '); no submission')
    if jobs:
        raise RuntimeError('Existing jobs found; inspect and reuse with --job; no submission')
    name = spec.get('job_name') or 'hpc-' + SITE + '-' + hashlib.sha256(args.request.encode()).hexdigest()[:20]
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$', name):
        raise ValueError('Invalid allocation job name')
    comment = 'hpc-core:' + SITE + ':' + hashlib.sha256(args.request.encode()).hexdigest()
    # Check the pane before reserving, so an ordinary busy-pane refusal is safe.
    pane = command(['tmux', '-L', args.socket, 'display-message', '-p', '-t', args.target,
                    '#{pane_current_command}|#{pane_pid}']).split('|')
    if pane[0] != 'bash':
        raise RuntimeError('Allocation pane is busy: ' + pane[0])
    children = subprocess.run(['pgrep', '-P', pane[1]], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if children.returncode == 0:
        raise RuntimeError('Allocation pane has running children')
    if children.returncode != 1:
        raise RuntimeError('Cannot inspect allocation pane children')
    record = dict(schema_version=1, cluster=SITE, request_id=args.request, identity=identity,
                  job_name=name, comment=comment, phase='reserved', created_at=now(), job_id=None,
                  log_path=str(root / (args.request + '.log')))
    atomic(path, record)  # After this point, uncertainty never causes resubmission.
    env = ['/usr/bin/env', 'HPC_ALLOCATION_REQUEST_ID=' + args.request,
           PREFIX + '_ALLOCATE_STATE_DIR=' + str(root.parent),
           'HPC_CORE_JOB_NAME=' + name, 'HPC_ALLOCATION_COMMENT=' + comment]
    holder = str(Path(__file__).resolve())
    shell = 'exec ' + ' '.join(shlex.quote(x) for x in env + [holder, 'hold', args.request, '--'] + args.command)
    subprocess.check_call(['tmux', '-L', args.socket, 'send-keys', '-t', args.target, '-l', '--', shell])
    subprocess.check_call(['tmux', '-L', args.socket, 'send-keys', '-t', args.target, 'Enter'])
    record.update(phase='dispatched', observed_at=now())
    atomic(path, record)
    return record


def bind_submitted(root, path, job, granted):
    """Store the job ID salloc printed; a conflicting stored ID is kept and reported by reconcile."""
    try:
        with (root / 'submit.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            record = json.loads(path.read_text())
            if record.get('job_id') not in (None, job):
                return
            record['job_id'] = job
            if record.get('phase') in ('holding', 'pending'):
                record['phase'] = 'allocated' if granted else 'pending'
            record['observed_at'] = now()
            atomic(path, record)
    except (OSError, ValueError) as error:
        # Never break the holder's terminal; reconcile still finds the job by name.
        sys.stderr.write(SITE + '-allocation: cannot record job ' + job + ': ' + str(error) + '\n')


def managed_records(root):
    for path in sorted(root.glob('*.json')):
        yield path, json.loads(path.read_text())


def adopt(root, job):
    """Bind a queued job to its request through owner and comment, before it is cancelled."""
    detail = command(['scontrol', 'show', 'job', job, '-o'])
    if not re.search(r'\bUserId=' + re.escape(os.environ['USER']) + r'\(', detail):
        raise RuntimeError('Slurm job owner mismatch')
    comment = next((item[len('Comment='):] for item in detail.split() if item.startswith('Comment=')), '')
    matches = [(path, record) for path, record in managed_records(root) if record.get('comment') == comment]
    if len(matches) > 1:
        raise RuntimeError('Several allocation requests carry the comment of job ' + job)
    if not matches:
        return []
    path, record = matches[0]
    if record.get('job_id') not in (None, job):
        raise RuntimeError('Request ' + record['request_id'] + ' is bound to job ' + record['job_id'] + ', not ' + job)
    record['job_id'] = job
    record = reconcile(record, queue())
    atomic(path, record)
    return [record]


def settle(root, job, attempts=5):
    """Reconcile the requests bound to a job that was just cancelled."""
    for attempt in range(attempts):
        records = []
        for path, record in managed_records(root):
            if record.get('job_id') == job:
                record = reconcile(record, queue())
                atomic(path, record)
                records.append(record)
        if all(record['phase'] == 'terminal' for record in records) or attempt + 1 == attempts:
            return records
        time.sleep(2)  # Accounting can trail the queue by a few seconds.


def hold(root, args):
    path = request_path(root, args.request)
    with (root / 'submit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = json.loads(path.read_text())
        if record.get('holder_started_at'):
            raise RuntimeError('Allocation holder already started; refusing another submit')
        record.update(phase='holding', holder_host=socket.gethostname(), holder_pid=os.getpid(), holder_started_at=now())
        atomic(path, record)
    seen = set()
    tail = [b'']
    with open(record['log_path'], 'ab', buffering=0) as log:
        # Preserve a controlling terminal for srun --pty and the PSC bridge.
        # Redirecting the delegated process to a log silently disables --pty.
        def read_master(fd):
            data = os.read(fd, 65536)
            log.write(data)
            # Record the job ID while it is still pending, so release and
            # reconcile work for jobs that never start.
            lines = (tail[0] + data).replace(b'\r', b'\n').split(b'\n')
            tail[0] = lines.pop()[-4096:]
            for line in lines:
                match = JOB_LINE.search(line.decode('utf-8', 'replace'))
                if match and match.group(2) not in seen:
                    seen.add(match.group(2))
                    bind_submitted(root, path, match.group(2), match.group(1) == 'Granted')
            return data
        status = pty.spawn(args.command, master_read=read_master)
        rc = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)
    with (root / 'submit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = json.loads(path.read_text())
        record.update(holder_exit_code=rc, holder_ended_at=now())
        try:
            record = reconcile(record, queue())
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            record.update(phase='unknown', observation_error=str(error))
        atomic(path, record)
    if os.environ.get('TMUX_PANE') and os.environ.get('HPC_ALLOCATION_KEEP_PANE', '1') == '1':
        # Keep the sole allocation window reusable after Slurm releases it.
        for key in ('HPC_ALLOCATION_REQUEST_ID', 'HPC_ALLOCATION_COMMENT', 'HPC_CORE_JOB_NAME'):
            os.environ.pop(key, None)
        os.execv('/bin/bash', ['/bin/bash', '--noprofile', '--norc'])
    return rc


def main():
    if sys.argv[1:2] == ['plan']:
        try:
            sys.stdout.write(shell_plan(plan(sys.argv[2:])))
            return 0
        except ValueError as error:
            print(SITE + '-allocate: ' + str(error), file=sys.stderr)
            return 2
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['submit', 'hold', 'get', 'bind', 'adopt', 'settle'])
    parser.add_argument('request', nargs='?', default='')
    parser.add_argument('--socket'); parser.add_argument('--target')
    parser.add_argument('--field', action='append', default=[])
    parser.add_argument('--job'); parser.add_argument('--ready', action='store_true')
    args, command_args = parser.parse_known_args()
    args.command = command_args[1:] if command_args[:1] == ['--'] else command_args
    root = directory(); root.mkdir(parents=True, exist_ok=True); os.chmod(str(root), 0o700)
    try:
        if args.mode == 'hold':
            return hold(root, args)
        with (root / 'submit.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if args.mode in ('adopt', 'settle'):
                if not args.job or not re.match(r'^[0-9]+$', args.job):
                    raise ValueError(args.mode + ' requires --job ID')
                print(json.dumps(adopt(root, args.job) if args.mode == 'adopt' else settle(root, args.job), sort_keys=True))
                return 0
            if args.mode == 'submit':
                if not args.socket or not args.target or not args.command:
                    raise ValueError('submit requires socket, target and command')
                record = submit(root, args)
            else:
                path = request_path(root, args.request)
                record = json.loads(path.read_text())
                if args.mode == 'bind':
                    if not args.job or args.job != os.environ.get('SLURM_JOB_ID'):
                        raise RuntimeError('bind must run inside the matching allocation')
                    if os.environ.get('HPC_ALLOCATION_COMMENT') != record['comment']:
                        raise RuntimeError('Allocation comment identity mismatch')
                    if record.get('job_id') and record['job_id'] != args.job:
                        raise RuntimeError('Allocation already bound to another job')
                    record.update(job_id=args.job, phase='ready' if args.ready else 'allocated', ready=args.ready)
                else:
                    record = reconcile(record, queue())
                atomic(path, record)
        print(json.dumps(record, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(cluster=SITE, request_id=args.request, phase='unknown', error=str(error))), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
