from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "dev"  # "dev" o "prod"; controla flags dependientes de HTTPS (ej. cookie secure)
    auth_username: str
    auth_password: str
    session_secret_key: str
    # --- Cluster homogéneo 3 nodos (conexión directa) ---
    cuda1_ip: str | None = None
    cuda1_username: str | None = None
    cuda1_password: str | None = None
    cuda2_ip: str | None = None
    cuda2_username: str | None = None
    cuda2_password: str | None = None
    cuda3_ip: str | None = None
    cuda3_username: str | None = None
    cuda3_password: str | None = None

    # Tuning SLURM / SSH pool
    slurm_poll_interval: int = 15  # TTL cache selector (s)
    ssh_connect_timeout: int = 10
    ssh_keepalive_interval: int = 30
    # GPU idle detection
    gpu_idle_threshold: int = 5  # % util por debajo del cual GPU se considera idle
    # Espera tras el sbatch antes de revisar con squeue que el job no quedó bloqueado en el nodo
    # (ReqNodeNotAvail, etc.); si quedó bloqueado se cancela y se prueba el siguiente nodo (RF-46).
    submit_check_delay_s: float = 2.0
    # Crea al iniciar los proyectos de muestra que falten (ver app/services/demo_seed.py)
    seed_demo_data: bool = False
    # Crea al iniciar los proyectos por defecto que falten (ver app/services/default_projects.py)
    seed_default_projects: bool = True
    # Jobs notifier
    callback_base_url: str | None = None  # ej http://host:8000, si None se deduce de request.host

    # --- Flujo de jobs SLURM (new_job + notifier) ---
    public_callback_base_url: str | None = None  # URL pública de este backend, alcanzable desde el cluster (ej https://web-forgethreads.inf.uct.cl). Si None se deduce de request
    scripts_wf_dir: str = "scripts_wf"  # ruta remota (relativa a $HOME del usuario SSH) del repo scripts_wf
    session_max_age_seconds: int = 15 * 24 * 60 * 60  # debe coincidir con SessionMiddleware(max_age=...)
    session_idle_timeout_seconds: int = 24 * 60 * 60  # inactividad máxima antes de expirar la sesión (sliding window)
    # Base de datos: URL de SQLAlchemy (ej. postgresql://usuario:clave@host:5432/base).
    # Sin DATABASE_URL se usa SQLite en jobs_db_path (desarrollo local y tests).
    database_url: str | None = None
    jobs_db_path: str = "data/jobs.db"
    fe_results_dir: str = "data/fe_results"  # caché local de los CSV de caminos descargados de fe_job
    sweep_results_dir: str = "data/sweep_results"  # caché local de los CSV crudos de los barridos

    # --- Barridos (RNF-02) ---
    # Duración estimada de una repetición: base + coeficiente × (N/1000)³ segundos. Son valores de
    # partida: calibrarlos con el `computation-time(s)` de barridos reales.
    sweep_run_base_s: float = 1.0
    sweep_run_s_per_kn3: float = 2.0
    sweep_time_margin: float = 1.5  # el --time pedido a SLURM es la estimación × margen + sobrecarga
    sweep_overhead_s: int = 600  # carga del entorno y calentamiento de la GPU
    sweep_max_hours: float = 72  # se rechazan los barridos cuyo --time supere este máximo

    # --- Invitaciones a expertos ---
    invitation_ttl_days: int = 14  # vigencia del enlace de invitación (RF-10)
    input_edit_days: int = 7  # plazo para que el experto modifique su input tras enviarlo (RF-52)
    activation_ttl_hours: int = 72  # vigencia del enlace de activación de un admin (RF-48)
    # "manual": el admin copia el enlace y lo envía; "smtp": además se envía por correo (RF-12)
    invitation_delivery: str = "manual"
    smtp_host: str | None = None
    smtp_port: int = 465  # 465 = SSL implícito (SMTPS); con smtp_use_ssl=False se usa STARTTLS
    smtp_use_ssl: bool = True
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None  # remitente; si falta se usa smtp_username
    smtp_timeout: int = 15

    @property
    def effective_database_url(self) -> str:
        return self.database_url or self.jobs_db_path

    @property
    def smtp_enabled(self) -> bool:
        return self.invitation_delivery.lower() == "smtp" and bool(self.smtp_host)

    @property
    def session_cookie_secure(self) -> bool:
        """Cookie de sesión con flag Secure solo si environment=prod (requiere HTTPS real)."""
        return self.environment.lower() == "prod"

    def get_cluster_nodes(self) -> list["NodeConfig"]:
        """Retorna solo nodos configurados (ip + credenciales)."""
        from app.ssh.models import NodeConfig

        nodes: list[NodeConfig] = []
        for idx, (ip, user, pwd) in enumerate(
            [
                (self.cuda1_ip, self.cuda1_username, self.cuda1_password),
                (self.cuda2_ip, self.cuda2_username, self.cuda2_password),
                (self.cuda3_ip, self.cuda3_username, self.cuda3_password),
            ],
            start=1,
        ):
            if ip:
                if not user or not pwd:
                    # Sin credenciales explícitas el nodo no se puede usar (no hay fallback legacy)
                    continue
                nodes.append(
                    NodeConfig(
                        name=f"cuda{idx}",
                        host=ip,
                        username=user,
                        password=pwd,
                    )
                )
        return nodes
    
# ENABLE CACHE PERSISTENCE AND SINGLETON PATTERN
@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
