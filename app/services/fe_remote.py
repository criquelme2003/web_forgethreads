"""Transferencias SFTP con el cluster: input de fe_job, CSV de caminos y CSV del barrido."""

import io
import json
import posixpath
from pathlib import Path

import numpy as np

from app.ssh.pool import SSHConnectionPool


def npy_bytes(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, array, allow_pickle=False)
    return buffer.getvalue()


def build_fe_input_files(
    cc: np.ndarray,
    ce: np.ndarray,
    ee: np.ndarray,
    meta: dict,
) -> dict[str, bytes]:
    """Arma el contenido de jobs_inputs/<request_id>/ que espera fe_job.py (tensores (k, filas, cols)).

    Una matriz 2D es un solo experto (k = 1); un tensor 3D son `k` expertos apilados (RF-24).
    """
    files = {}
    for name, matrix in (("CC", cc), ("CE", ce), ("EE", ee)):
        tensor = np.asarray(matrix, dtype=np.float32)
        if tensor.ndim == 2:
            tensor = tensor.reshape(1, *tensor.shape)
        files[f"{name}.npy"] = npy_bytes(tensor)
    files["meta.json"] = json.dumps(meta, ensure_ascii=False).encode("utf-8")
    return files


async def upload_fe_input(
    pool: SSHConnectionPool,
    node: str,
    scripts_wf_dir: str,
    request_id: str,
    files: dict[str, bytes],
) -> str:
    """Sube los archivos a <scripts_wf>/jobs_inputs/<request_id>/ y devuelve la ruta relativa al repo."""
    rel_dir = posixpath.join("jobs_inputs", request_id)
    remote_dir = posixpath.join(scripts_wf_dir, rel_dir)
    conn = await pool.get_connection(node)
    async with conn.start_sftp_client() as sftp:
        await sftp.makedirs(remote_dir, exist_ok=True)
        for filename, content in files.items():
            async with sftp.open(posixpath.join(remote_dir, filename), "wb") as remote_file:
                await remote_file.write(content)
    return rel_dir


async def download_fe_results(
    pool: SSHConnectionPool,
    node: str,
    scripts_wf_dir: str,
    job_id: str,
    orders: list[int],
    dest_dir: Path,
) -> None:
    """Descarga jobs_results/<job_id>/paths_order_<o>.csv de cada orden a `dest_dir`."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    remote_dir = posixpath.join(scripts_wf_dir, "jobs_results", job_id)
    conn = await pool.get_connection(node)
    async with conn.start_sftp_client() as sftp:
        for order in orders:
            filename = f"paths_order_{order}.csv"
            async with sftp.open(posixpath.join(remote_dir, filename), "rb") as remote_file:
                content = await remote_file.read()
            # Escritura atómica: un CSV a medio descargar nunca queda como caché válida.
            tmp = dest_dir / f".{filename}.tmp"
            tmp.write_bytes(content)
            tmp.replace(dest_dir / filename)


async def download_sweep_results(
    pool: SSHConnectionPool,
    node: str,
    scripts_wf_dir: str,
    job_id: str,
    dest: Path,
) -> None:
    """Descarga jobs_results/<job_id>.csv (CSV crudo del barrido) a `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    remote = posixpath.join(scripts_wf_dir, "jobs_results", f"{job_id}.csv")
    conn = await pool.get_connection(node)
    async with conn.start_sftp_client() as sftp, sftp.open(remote, "rb") as remote_file:
        content = await remote_file.read()
    tmp = dest.parent / f".{dest.name}.tmp"
    tmp.write_bytes(content)
    tmp.replace(dest)
