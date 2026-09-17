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
    auth_token: str,
) -> str:
    return (
        f"cd {shlex.quote(scripts_wf_dir)} && "
        f"sbatch new_job.sh --nodos {numero_nodos} --thr {threshold} "
        f"--conectividad {conectividad_promedio} --seed {seed} "
        f"--auth-token {shlex.quote(auth_token)}"
    )


def build_notifier_command(
    scripts_wf_dir: str,
    job_id: str,
    auth_token: str,
    callback_url: str,
) -> str:
    return (
        f"cd {shlex.quote(scripts_wf_dir)} && "
        f"sbatch --dependency=afterok:{shlex.quote(job_id)} notifier.sh "
        f"--job-id {shlex.quote(job_id)} --auth-token {shlex.quote(auth_token)} "
        f"--callback-url {shlex.quote(callback_url)}"
    )


def redact_token(command: str, auth_token: str) -> str:
    """Reemplaza el auth-token por '***' para loguear el comando sin exponer la sesión."""
    if not auth_token:
        return command
    return command.replace(auth_token, "***")
