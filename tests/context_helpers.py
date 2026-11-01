def context_for(store):
    state=store.state()
    task=next((p for p in state['packs'] if p['status']!='COMPLETED'),state['packs'][0] if state['packs'] else None)
    return dict(company_id=state['active']['id'] if state['active'] else None,
                snapshot_id=state['snapshot']['id'] if state['snapshot'] else None,
                task=dict(id=task['id'],revision=task['revision'],snapshot_id=task['snapshot_id']) if task else None)
