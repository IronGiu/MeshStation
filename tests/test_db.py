import os
import sys
import json
import time

# Add current dir to path
sys.path.append(os.getcwd())

from core.db import MeshDB
from utils.paths import get_data_path

def test_db_operations():
    db_name = "test_mesh.db"
    db_path = os.path.join(get_data_path(), db_name)
    if os.path.exists(db_path):
        os.remove(db_path)
        
    db = MeshDB(db_name=db_name)
    
    # Test Node Save/Load
    node = {"id": "!1234", "short_name": "TST", "last_seen_ts": time.time()}
    db.save_node(node)
    
    nodes = db.get_all_nodes()
    assert len(nodes) == 1
    assert nodes[0]["id"] == "!1234"
    assert nodes[0]["short_name"] == "TST"
    
    # Test Packet Save
    packet = {"from_id": "!1234", "to_id": "!ffff", "type": "TEXT", "text": "Hello DB"}
    db.save_packet(packet)
    
    # Verify file exists
    assert os.path.exists(db_path)
    
    # Cleanup
    os.remove(db_path)

if __name__ == "__main__":
    try:
        test_db_operations()
        print("DB Tests passed!")
    except Exception as e:
        print(f"DB Tests failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
