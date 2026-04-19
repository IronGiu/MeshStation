import pytest
from processing.packets import decodeProtobuf
from core.state import state
from core.stats import mesh_stats
from meshtastic import mesh_pb2

def test_decode_text_message():
    # Setup state
    state.nodes = {}
    state.messages.clear()
    
    # Create a mock text packet
    data = mesh_pb2.Data()
    data.portnum = 1 # TEXT_MESSAGE_APP
    data.payload = "Hello World".encode('utf-8')
    packet_data = data.SerializeToString()
    
    result = decodeProtobuf(packet_data, "!1234", "!5678", "[MOCK]")
    
    assert "TEXT MSG from !1234: Hello World" in result
    assert len(state.messages) == 1
    assert state.messages[0]['text'] == "Hello World"
    assert state.messages[0]['from_id'] == "!1234"

def test_decode_invalid_protobuf():
    result = decodeProtobuf(b"\xff\xff\xff", "!1234", "!5678", "[MOCK]")
    assert "INVALID PROTOBUF" in result
