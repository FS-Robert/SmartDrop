import os
import psycopg2
from psycopg2.extras import RealDictCursor

conn = psycopg2.connect(
    dbname=os.environ.get('POSTGRES_DB','smartdrop'),
    user=os.environ.get('POSTGRES_USER','postgres'),
    password=os.environ.get('POSTGRES_PASSWORD','123456'),
    host=os.environ.get('POSTGRES_HOST','localhost'),
    port=os.environ.get('POSTGRES_PORT','5432'),
)
cur = conn.cursor()
# Add missing columns if they don't exist
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='usuario' and column_name='last_login';")
if not cur.fetchone():
    cur.execute("ALTER TABLE usuario ADD COLUMN last_login TIMESTAMP WITH TIME ZONE NULL;")
    print('Added column last_login')
else:
    print('last_login already exists')

cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='usuario' and column_name='is_superuser';")
if not cur.fetchone():
    cur.execute("ALTER TABLE usuario ADD COLUMN is_superuser boolean NOT NULL DEFAULT false;")
    print('Added column is_superuser')
else:
    print('is_superuser already exists')

cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='usuario' and column_name='is_staff';")
if not cur.fetchone():
    cur.execute("ALTER TABLE usuario ADD COLUMN is_staff boolean NOT NULL DEFAULT false;")
    print('Added column is_staff')
else:
    print('is_staff already exists')

conn.commit()
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='usuario' ORDER BY ordinal_position;")
cols = cur.fetchall()
print('COLUMNS:', cols)
cur.close()
conn.close()
