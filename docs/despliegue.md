# Despliegue en Kubernetes (namespace `criquelme`)

| Archivo | Recursos |
|---|---|
| `postgres.yaml` | PostgreSQL 17 (StatefulSet `wf-postgres` + Service) con un contenedor `backup` que corre `pg_dump` una vez al día. Dos volúmenes de 2Gi en `csi-cephfs-sc`: datos y respaldos. |
| `deployment.yaml` | La app (Deployment + Service + Ingress) y un volumen de 1Gi para los resultados descargados del cluster (`/code/data/fe_results`). |

La app se conecta con `DATABASE_URL=postgresql://wf:<clave>@wf-postgres:5432/wf`, que se arma en
`deployment.yaml` con la clave del secret `wf-postgres`. Al arrancar aplica las migraciones de
Alembic (`app/db/migrations/`); no hay que correr nada a mano.

## Primer despliegue

1. **Respaldar la base SQLite actual**, si interesa conservarla (está dentro del pod y se pierde
   al reemplazarlo):

   ```bash
   kubectl get pods -n criquelme -l app=web-forgethreads
   kubectl cp criquelme/<pod>:/code/data/jobs.db ./jobs.db
   ```

2. **Crear la clave de PostgreSQL** (hexadecimal, para que no haya que escaparla en la URL):

   ```bash
   kubectl create secret generic wf-postgres -n criquelme \
     --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 24)"
   ```

3. **Levantar PostgreSQL** y esperar a que esté listo:

   ```bash
   kubectl apply -f postgres.yaml
   kubectl rollout status statefulset/wf-postgres -n criquelme
   ```

4. **Desplegar la app** (con `SEED_DEMO_DATA` desactivado si se va a copiar la base anterior: la
   copia exige una base vacía):

   ```bash
   kubectl apply -f deployment.yaml
   kubectl rollout status deployment/web-forgethreads -n criquelme
   kubectl logs deployment/web-forgethreads -n criquelme | grep "Base de datos"
   # → Base de datos lista: postgresql+asyncpg://wf:***@wf-postgres:5432/wf
   ```

5. **Copiar los datos de la base SQLite** respaldada en el paso 1:

   ```bash
   POD=$(kubectl get pods -n criquelme -l app=web-forgethreads -o name | head -1)
   kubectl cp ./jobs.db criquelme/${POD#pod/}:/tmp/jobs.db
   kubectl exec -n criquelme $POD -- python -m app.db.copy_sqlite /tmp/jobs.db
   ```

   El script copia proyectos, invitaciones, inputs y jobs, y ajusta los contadores de ids. Si la
   base destino ya tiene datos no escribe nada. El archivo de origen no se modifica.

## Respaldos

El contenedor `backup` deja `wf-AAAAMMDD-HHMMSS.sql.gz` en su volumen al arrancar y luego cada 24
horas, y borra los de más de 14 días (`RETENTION_DAYS` e `INTERVAL_SECONDS` en `postgres.yaml`).
Ese volumen está en el mismo storage que la base: para tener una copia fuera del cluster, bajar
uno de vez en cuando:

```bash
kubectl exec -n criquelme wf-postgres-0 -c backup -- ls -lh /backups
kubectl cp -c backup criquelme/wf-postgres-0:/backups/<archivo>.sql.gz ./<archivo>.sql.gz

# o un respaldo en el momento, directo a la máquina local
kubectl exec -n criquelme wf-postgres-0 -c postgres -- pg_dump -U wf --no-owner wf | gzip > wf.sql.gz
```

### Restaurar

La restauración reemplaza todo el contenido de la base. Con la app detenida:

```bash
kubectl scale deployment/web-forgethreads -n criquelme --replicas=0
kubectl exec -n criquelme wf-postgres-0 -c postgres -- \
  psql -U wf -d postgres -c "DROP DATABASE wf" -c "CREATE DATABASE wf OWNER wf"
gunzip -c wf.sql.gz | kubectl exec -i -n criquelme wf-postgres-0 -c postgres -- \
  psql -U wf -d wf -v ON_ERROR_STOP=1
kubectl scale deployment/web-forgethreads -n criquelme --replicas=1
```

## Cambios de esquema

Un cambio en `app/db/schema.py` va acompañado de una migración nueva:

```bash
uv run alembic revision --autogenerate -m "descripción"   # revisar el archivo generado
uv run alembic check                                       # el esquema y las migraciones coinciden
```

La app la aplica sola al arrancar con la nueva imagen.
