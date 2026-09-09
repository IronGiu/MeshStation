import threading
from core.state import state
from core.stats import mesh_stats
from utils.helpers import parseAESKey
from network.workers import zmq_worker, tcp_worker

def start_connection(mode, ip, port, key_b64, log_to_console_callback=None, set_ui_status_callback=None, start_engine_direct_callback=None):
    if state.connected:
        return
    
    state.connect_mode = mode
    state.ip_address = ip
    state.port = port
    state.aes_key_b64 = key_b64
    state.aes_key_bytes = parseAESKey(key_b64)
    state.connected = True
    
    mesh_stats.set_enabled(True)
    
    if set_ui_status_callback:
        set_ui_status_callback(True, mode)
    
    if mode == "direct":
        if start_engine_direct_callback:
            start_engine_direct_callback()
        # Direct mode typically uses a local TCP stream from the engine
        t = threading.Thread(target=tcp_worker, args=(ip, port, log_to_console_callback), daemon=True)
        t.start()
    else:
        # External mode usually uses ZMQ
        t = threading.Thread(target=zmq_worker, args=(ip, port, log_to_console_callback), daemon=True)
        t.start()

def stop_connection(log_to_console_callback=None, set_ui_status_callback=None, stop_engine_direct_callback=None):
    mode = state.connect_mode
    state.connected = False
    mesh_stats.set_enabled(False)
    
    if mode == "direct":
        if stop_engine_direct_callback:
            stop_engine_direct_callback()
            
    state.connect_mode = None
    if set_ui_status_callback:
        set_ui_status_callback(False, mode)
