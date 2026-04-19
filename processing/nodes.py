import time
from datetime import datetime
from core.state import state
from core.stats import mesh_stats

def update_node(node_id, log_to_console_callback=None, **kwargs):
    node_id = str(node_id)
    if not node_id.startswith("!"):
        try:
            node_id = f"!{int(node_id, 16):x}"
        except Exception:
            try:
                node_id = f"!{int(node_id):x}"
            except Exception:
                pass

    is_new = node_id not in state.nodes
    now_ts = time.time()
    
    if is_new:
        state.nodes[node_id] = {
            "id": node_id, 
            "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_seen_ts": now_ts,
            "lat": None, "lon": None, "location_source": "Unknown", "altitude": None,
            "short_name": "???", "long_name": "Unknown",
            "hw_model": "Unknown", "role": "Unknown",
            "public_key": None, "macaddr": None, "is_unmessagable": False,
            "battery": None, "voltage": None,
            "snr": None, "rssi": None,
            "snr_indirect": None, "rssi_indirect": None,
            "hops": None, "hop_label": None,
            "temperature": None, "relative_humidity": None, "barometric_pressure": None,
            "channel_utilization": None, "air_util_tx": None, "uptime_seconds": None,
            "preset": None,
        }
        state.nodes_updated = True
        state.nodes_list_updated = True # Ensure new nodes appear immediately
        if log_to_console_callback:
            log_to_console_callback(f"New node {node_id}")

    if "hops" in kwargs:
        new_hops = kwargs.get("hops")
        if new_hops is None:
            kwargs.pop("hops", None)
            kwargs.pop("hop_label", None)
        else:
            prev_hops = state.nodes[node_id].get("hops")
            prev_ts = state.nodes[node_id].get("last_seen_ts")
            if prev_hops is not None and prev_ts is not None:
                window = 10 * 60
                if now_ts - prev_ts < window and new_hops > prev_hops:
                    kwargs.pop("hops", None)
                    kwargs.pop("hop_label", None)
    
    # Check if anything actually changed to avoid redundant updates
    changed = False
    
    # If it's a new node, we definitely have changes (initial values)
    if is_new:
        changed = True
        
    for k, v in kwargs.items():
        if state.nodes[node_id].get(k) != v:
            state.nodes[node_id][k] = v
            changed = True
    
    # Always update last seen
    state.nodes[node_id]["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state.nodes[node_id]["last_seen_ts"] = now_ts
    
    # Force update if new data arrived or it's a new node
    if changed or is_new:
        state.nodes_updated = True
        with state.lock:
            state.dirty_nodes.add(node_id) # Track specific node for efficient delta update
        state.nodes_list_updated = True
        
        # If name changed, we might need to refresh chat history to reflect new name
        if changed and ('short_name' in kwargs or 'long_name' in kwargs):
            state.chat_force_refresh = True
    
    # Persist to database if enabled
    if state.db:
        state.db.save_node(state.nodes[node_id])
