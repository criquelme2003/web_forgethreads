import pytest

from app.services.slurm_submit import (
    JobIdParseError,
    build_new_job_command,
    build_notifier_command,
    parse_job_id,
    redact_token,
)


def test_parse_job_id_simple() -> None:
    assert parse_job_id("Submitted batch job 12345\n") == "12345"


def test_parse_job_id_tolerates_preceding_banner_text() -> None:
    stdout = "algún banner MOTD\nmás texto\nSubmitted batch job 999\n"
    assert parse_job_id(stdout) == "999"


def test_parse_job_id_raises_when_no_match() -> None:
    with pytest.raises(JobIdParseError):
        parse_job_id("error: algo salió mal")


def test_parse_job_id_raises_on_empty_stdout() -> None:
    with pytest.raises(JobIdParseError):
        parse_job_id("")


def test_build_new_job_command_shape() -> None:
    cmd = build_new_job_command(
        scripts_wf_dir="scripts_wf",
        numero_nodos=5000,
        threshold=0.15,
        conectividad_promedio=8,
        seed=42,
        auth_token="simple-token",
    )
    assert cmd == (
        "cd scripts_wf && sbatch new_job.sh --nodos 5000 --thr 0.15 "
        "--conectividad 8 --seed 42 --auth-token simple-token"
    )


def test_build_new_job_command_quotes_dangerous_token() -> None:
    dangerous_token = "a.b.c'; rm -rf /"
    cmd = build_new_job_command(
        scripts_wf_dir="scripts_wf",
        numero_nodos=5,
        threshold=0.1,
        conectividad_promedio=2,
        seed=1,
        auth_token=dangerous_token,
    )
    # el token completo debe aparecer como un único argumento shell-quoted
    assert shlex_split_last_arg_matches(cmd, dangerous_token)


def test_build_new_job_command_quotes_dangerous_scripts_dir() -> None:
    cmd = build_new_job_command(
        scripts_wf_dir="scripts wf; rm -rf /",
        numero_nodos=5,
        threshold=0.1,
        conectividad_promedio=2,
        seed=1,
        auth_token="tok",
    )
    assert "cd 'scripts wf; rm -rf /'" in cmd


def test_build_notifier_command_shape() -> None:
    cmd = build_notifier_command(
        scripts_wf_dir="scripts_wf",
        job_id="937",
        auth_token="tok",
        callback_url="https://api.example.com/app/job_callback",
    )
    assert cmd == (
        "cd scripts_wf && sbatch --dependency=afterok:937 notifier.sh "
        "--job-id 937 --auth-token tok "
        "--callback-url https://api.example.com/app/job_callback"
    )


def test_build_notifier_command_quotes_dangerous_token() -> None:
    dangerous_token = "a.b.c'; rm -rf /"
    cmd = build_notifier_command(
        scripts_wf_dir="scripts_wf",
        job_id="937",
        auth_token=dangerous_token,
        callback_url="https://api.example.com/app/job_callback",
    )
    assert shlex_split_last_arg_matches(cmd, dangerous_token)


def test_redact_token_replaces_token_value() -> None:
    cmd = "sbatch new_job.sh --auth-token supersecrettoken"
    redacted = redact_token(cmd, "supersecrettoken")
    assert "supersecrettoken" not in redacted
    assert "***" in redacted


def test_redact_token_noop_on_empty_token() -> None:
    cmd = "sbatch new_job.sh --auth-token x"
    assert redact_token(cmd, "") == cmd


def shlex_split_last_arg_matches(cmd: str, expected: str) -> bool:
    import shlex

    parts = shlex.split(cmd)
    return expected in parts
