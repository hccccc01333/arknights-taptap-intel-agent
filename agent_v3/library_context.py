"""Read-only delivery context shared by the local and public libraries."""
import json


def attach(store, data):
    for key in ('game_signals', 'materials', 'creatives'):
        for item in data.get(key, []):
            tid = item.get('topic_id')
            created_at = item.get('created_at'); source_count = len(item.get('evidence_ids') or item.get('payload', {}).get('facts', []))
            if key == 'creatives':
                basis=store.creative_basis(item);tid = basis.get('topic_id');source_count=len(basis.get('evidence_ids',[]))
            elif key == 'materials':
                application = store.conn.execute(
                    'SELECT topic_id,created_at FROM material_application_run WHERE material_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',
                    (item['material_id'],)).fetchone()
                if application: tid = application[0];created_at=application[1]
                elif item.get('creative_id'):
                    row = store.conn.execute('SELECT * FROM creative WHERE creative_id=?', (item['creative_id'],)).fetchone()
                    if row: tid = store.creative_basis(row).get('topic_id')
            topic = store.conn.execute('SELECT fingerprint FROM topic WHERE topic_id=?', (tid,)).fetchone()
            interpreted = store.interpretation(tid, topic[0]) if topic else None
            payload = interpreted['payload'] if interpreted else {}
            item['delivery_meta'] = {'topic_id': tid, 'event_title': payload.get('headline', ''),
                'created_at': created_at, 'event_time': payload.get('recency', {}).get('date_iso'), 'source_count':source_count}
    return data


def evidence_ids(value):
    """Collect only explicit source references, never model text or identifiers."""
    result = set()
    if isinstance(value, dict):
        if isinstance(value.get('evidence_id'), str): result.add(value['evidence_id'])
        if isinstance(value.get('evidence_ids'), list): result.update(v for v in value['evidence_ids'] if isinstance(v, str))
        if isinstance(value.get('source_versions'), dict): result.update(value['source_versions'])
        for child in value.values(): result.update(evidence_ids(child))
    elif isinstance(value, list):
        for child in value: result.update(evidence_ids(child))
    return result


def publication_status(store):
    from .model import gate
    row = store.conn.execute("SELECT value FROM settings WHERE key='schedule'").fetchone()
    schedule = json.loads(row[0]) if row else {}
    model = gate(store)
    return {'collection': 'running' if schedule.get('enabled') else 'paused',
        'ai': 'waiting_quota' if model.get('manual_resume') else 'waiting_provider' if model['status'] == 'deferred' else 'ready'}
