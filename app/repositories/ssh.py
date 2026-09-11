"""
Backward compat: mantiene import desde app.repositories.ssh
Delega a app.repositories.slurm
"""
from app.repositories.slurm import SlurmRepository, parse_gres, parse_scontrol_output, parse_sinfo_line  # noqa: F401
