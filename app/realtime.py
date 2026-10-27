"""Transactional invalidation for every business table, including background writes.

The feed exposes an opaque revision only. Pages still render through their normal
role/outlet/franchise authorization; revisions never contain business records.
"""
import hashlib
import re


IGNORED_TABLES = {
    'ui_revisions', 'sqlite_sequence', 'login_attempts', 'revoked_access_tokens',
    'audit_logs', 'mutation_requests', 'idempotency_keys', 'otp_challenges',
}


def install_change_tracking(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS ui_revisions (topic TEXT PRIMARY KEY, revision BIGINT NOT NULL DEFAULT 0)')
    postgres = hasattr(conn, 'raw')
    if postgres:
        tables = [row['tablename'] for row in conn.execute(
            'SELECT tablename FROM pg_tables WHERE schemaname=current_schema()').fetchall()]
        # Schema-qualified target works even when another client uses a different search_path.
        conn.raw.execute('''CREATE OR REPLACE FUNCTION ui_record_change() RETURNS trigger AS $$
            DECLARE ignored TEXT[] := ARRAY['updated_at','last_seen_at'];
            BEGIN
                IF TG_TABLE_NAME='delivery_tasks' THEN
                    ignored := ignored || ARRAY['latitude','longitude','location_at'];
                END IF;
                IF TG_OP = 'UPDATE' AND
                    (to_jsonb(NEW) - ignored) IS NOT DISTINCT FROM
                    (to_jsonb(OLD) - ignored) THEN RETURN NULL; END IF;
                EXECUTE format('UPDATE %I.ui_revisions SET revision=revision+1 WHERE topic=$1', TG_TABLE_SCHEMA)
                    USING TG_TABLE_NAME;
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql''')
    else:
        tables = [row['name'] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for table in tables:
        if table in IGNORED_TABLES or not re.fullmatch(r'[a-z][a-z0-9_]*', table):
            continue
        conn.execute('INSERT INTO ui_revisions(topic,revision) VALUES(?,0) ON CONFLICT(topic) DO NOTHING', (table,))
        if postgres:
            trigger = 'ui_change_' + table
            exists = conn.raw.execute('''SELECT 1 FROM pg_trigger WHERE tgname=%s
                AND tgrelid=%s::regclass AND NOT tgisinternal''', (trigger, table)).fetchone()
            if not exists:
                conn.raw.execute(f'CREATE TRIGGER "{trigger}" AFTER INSERT OR UPDATE OR DELETE ON "{table}" '
                                 'FOR EACH ROW EXECUTE FUNCTION ui_record_change()')
        else:
            columns = [row['name'] for row in conn.execute(f'PRAGMA table_info({table})').fetchall()]
            ignored = {'updated_at', 'last_seen_at'}
            if table == 'delivery_tasks':
                ignored.update({'latitude', 'longitude', 'location_at'})
            changed = ' OR '.join(f'NEW."{col}" IS NOT OLD."{col}"' for col in columns if col not in ignored) or '0'
            for operation in ('INSERT', 'UPDATE', 'DELETE'):
                condition = f' WHEN {changed}' if operation == 'UPDATE' else ''
                conn.execute(f'''CREATE TRIGGER IF NOT EXISTS "ui_{table}_{operation.lower()}"
                    AFTER {operation} ON "{table}"{condition} BEGIN
                    UPDATE ui_revisions SET revision=revision+1 WHERE topic='{table}'; END''')


def revision(conn):
    rows = conn.execute('SELECT topic,revision FROM ui_revisions ORDER BY topic').fetchall()
    value = '|'.join(f"{row['topic']}:{row['revision']}" for row in rows)
    return hashlib.sha256(value.encode()).hexdigest()
