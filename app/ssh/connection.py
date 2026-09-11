import asyncssh
from app.core.config import get_settings

async def get_new_ssh_connection():
  return await asyncssh.connect(get_settings().cuda3_ip,
                              username=get_settings().cuda3_username,
                              password=get_settings().cuda3_password)