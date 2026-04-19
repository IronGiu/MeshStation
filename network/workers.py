import threading
import time
import zmq
import socket
from core.state import state
from core.stats import mesh_stats
from processing.packets import parse_framed_stream_bytes

def zmq_worker(ip, port, log_to_console_callback=None):
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.setsockopt(zmq.SUBSCRIBE, b"")
    socket.setsockopt(zmq.RCVTIMEO, 1000) # 1s timeout to check for connected flag
    
    addr = f"tcp://{ip}:{port}"
    if log_to_console_callback:
        log_to_console_callback(f"ZMQ Subscriber connecting to {addr}")
    
    try:
        socket.connect(addr)
    except Exception as e:
        if log_to_console_callback:
            log_to_console_callback(f"ZMQ Connection Error: {e}")
        state.connected = False
        return

    rx_buf = bytearray()
    while state.connected:
        try:
            parts = socket.recv_multipart()
            if not parts:
                continue
            # The custom frame is usually in the last part or single part
            payload = parts[-1]
            rx_buf.extend(payload)
            parse_framed_stream_bytes(rx_buf, log_to_console_callback=log_to_console_callback)
        except zmq.Again:
            continue
        except Exception as e:
            if log_to_console_callback:
                log_to_console_callback(f"ZMQ Worker Error: {e}")
            break
    
    socket.close()
    context.term()
    if log_to_console_callback:
        log_to_console_callback("ZMQ Worker stopped")

def tcp_worker(ip, port, log_to_console_callback=None):
    if log_to_console_callback:
        log_to_console_callback(f"TCP Subscriber connecting to {ip}:{port}")
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect((ip, int(port)))
    except Exception as e:
        if log_to_console_callback:
            log_to_console_callback(f"TCP Connection Error: {e}")
        state.connected = False
        return

    rx_buf = bytearray()
    while state.connected:
        try:
            data = sock.recv(4096)
            if not data:
                if log_to_console_callback:
                    log_to_console_callback("TCP Connection closed by peer")
                break
            rx_buf.extend(data)
            parse_framed_stream_bytes(rx_buf, log_to_console_callback=log_to_console_callback)
        except socket.timeout:
            continue
        except Exception as e:
            if log_to_console_callback:
                log_to_console_callback(f"TCP Worker Error: {e}")
            break
            
    sock.close()
    if log_to_console_callback:
        log_to_console_callback("TCP Worker stopped")
