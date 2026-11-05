"""LOCAL-ONLY demo provisioning; never use for public hosting."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # run as: python scripts/<name>.py
import os,sys,uuid
from backend.db.store import init,connect
from backend.auth.security import hash_password
if os.getenv('FIXFLOW_LOCAL_DEMO')!='1':raise SystemExit('Set FIXFLOW_LOCAL_DEMO=1 to enable local-only provisioning')
if len(sys.argv)!=4 or sys.argv[1] not in ('admin','customer'):raise SystemExit('Usage: python -m scripts.provision_local admin|customer email password')
role,email,password=sys.argv[1:]
if len(password)<12:raise SystemExit('Password must be >=12 chars')
init()
with connect() as c:
 if role=='admin' and c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():raise SystemExit('Admin already exists')
 c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',(str(uuid.uuid4()),email.lower(),hash_password(password),role,1))
print('LOCAL verified account created:',role,email)
