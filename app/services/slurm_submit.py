import re
import shlex

_JOBID_RE = re.compile(r"Submitted batch job (\d+)")


class JobIdParseError(Exception):
    pass


def parse_job_id(stdout: str) -> str:
    """Extrae el JOBID de la salida de `sbatch`, ej. 'Submitted batch job 12345'."""
    match = _JOBID_RE.search(stdout or "")
    if not match:
        raise JobIdParseError(f"No se pudo extraer JOBID de stdout: {stdout!r}")
    return match.group(1)


def build_new_job_command(
    scripts_wf_dir: str,
    numero_nodos: int,
    threshold: float,
    conectividad_promedio: float,
    seed: int,
) -> str:
    # c entero se pasa sin decimales ("8", no "8.0"), igual que antes de que fuera decimal.
    c = float(conectividad_promedio)
    conectividad = str(int(c)) if c.is_integer() else repr(c)
    return (
        f"cd {shlex.quote(scripts_wf_dir)} && "
        f"sbatch new_job.sh --nodos {numero_nodos} --thr {threshold} "
        f"--conectividad {conectividad} --seed {seed}"
    )


def build_fe_job_command(scripts_wf_dir: str, input_dir: str) -> str:
    """sbatch de fe_job.sh; `input_dir` es relativo a scripts_wf (ej. jobs_inputs/<request_id>)."""
    return f"cd {shlex.quote(scripts_wf_dir)} && sbatch fe_job.sh --input-dir {shlex.quote(input_dir)}"


def build_sweep_job_command(
    scripts_wf_dir: str,
    time_limit: str,
    ns: list[int],
    cs: list[str],
    reps: int,
    threshold: float,
    seed_base: int,
) -> str:
    """sbatch de sweep_job.sh (RF-33); `cs` ya formateados y `time_limit` en formato de --time."""
    return (
        f"cd {shlex.quote(scripts_wf_dir)} && "
        f"sbatch --time={shlex.quote(time_limit)} sweep_job.sh "
        f"--ns {shlex.quote(','.join(str(n) for n in ns))} --cs {shlex.quote(','.join(cs))} "
        f"--reps {int(reps)} --thr {shlex.quote(repr(float(threshold)))} --seed-base {int(seed_base)}"
    )


def build_notifier_command(
    scripts_wf_dir: str,
    job_id: str,
    auth_token: str,
    callback_url: str,
) -> str:
    # afterany: el notifier corre aunque el job falle o se corte por tiempo, y reporta error/partial.
    # Con afterok la dependencia nunca se cumpliría y el notifier quedaría pendiente para siempre.
    return (
        f"cd {shlex.quote(scripts_wf_dir)} && "
        f"sbatch --dependency=afterany:{shlex.quote(job_id)} notifier.sh "
        f"--job-id {shlex.quote(job_id)} --auth-token {shlex.quote(auth_token)} "
        f"--callback-url {shlex.quote(callback_url)}"
    )


def redact_token(command: str, auth_token: str) -> str:
    """Reemplaza el auth-token por '***' para loguear el comando sin exponer la sesión."""
    if not auth_token:
        return command
    return command.replace(auth_token, "***")


# Motivos de PENDING que no se resuelven solos (requerimientos §6.1): el job no correrá en este
# nodo. squeue puede mostrar el código ("ReqNodeNotAvail, UnavailableNodes:cuda1") o la
# descripción larga ("Nodes required for job are DOWN, DRAINED or reserved ...").
BLOCKING_REASONS = (
    "ReqNodeNotAvail",
    "Nodes required for job are DOWN",
    "NodeDown",
    "PartitionDown",
    "PartitionInactive",
    "PartitionConfig",
    "BadConstraints",
    "JobHeldUser",
    "JobHeldAdmin",
    "launch failed requeued held",
    "DependencyNeverSatisfied",
)


def build_job_state_command(job_id: str) -> str:
    """Estado y motivo de un job: 'PENDING|ReqNodeNotAvail, UnavailableNodes:cuda1'."""
    return f"squeue -h -j {shlex.quote(job_id)} -o '%T|%r'"


def build_scancel_command(*job_ids: str) -> str:
    return "scancel " + " ".join(shlex.quote(j) for j in job_ids)


def parse_job_state(stdout: str) -> tuple[str, str] | None:
    """(estado, motivo) de la primera línea de squeue; None si el job no aparece."""
    for line in (stdout or "").splitlines():
        if "|" in line:
            state, _, reason = line.strip().partition("|")
            return state.strip(), reason.strip()
    return None


def blocking_reason(state: str, reason: str) -> str | None:
    """Devuelve el motivo si el job quedó PENDING por algo que no se resuelve solo."""
    if state.upper() != "PENDING":
        return None
    lowered = reason.lower()
    if any(r.lower() in lowered for r in BLOCKING_REASONS):
        return reason
    return None
