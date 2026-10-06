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
    return (
        f"cd {shlex.quote(scripts_wf_dir)} && "
        f"sbatch new_job.sh --nodos {numero_nodos} --thr {threshold} "
        f"--conectividad {conectividad_promedio} --seed {seed}"
    )


def build_fe_job_command(scripts_wf_dir: str, input_dir: str) -> str:
    """sbatch de fe_job.sh; `input_dir` es relativo a scripts_wf (ej. jobs_inputs/<request_id>)."""
    return f"cd {shlex.quote(scripts_wf_dir)} && sbatch fe_job.sh --input-dir {shlex.quote(input_dir)}"


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
