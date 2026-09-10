from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.ssh.connection import get_new_ssh_connection
@asynccontextmanager
async def global_lifespan(app: FastAPI):
  print("Opening shh connection")
  ssh_conn =  await get_new_ssh_connection()
  print("Opening shh connection")
  
  yield {"ssh_conn":ssh_conn}

  print("Closing shh connection")
  ssh_conn.close()