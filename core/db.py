import sqlite3
import json
import time
import os
from utils.paths import get_data_path

class MeshDB:
    def __init__(self, db_name="mesh_data.db"):
        self.db_path = os.path.join(get_data_path(), db_name)
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Packets table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL,
                    from_id TEXT,
                    to_id TEXT,
                    type TEXT,
                    text TEXT,
                    channel_id TEXT,
                    raw_json TEXT
                )
            ''')
            # Nodes table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    last_seen REAL,
                    data_json TEXT
                )
            ''')
            # Stats snapshots
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS stats_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL,
                    data_json TEXT
                )
            ''')
            conn.commit()

    def save_packet(self, packet_dict):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO packets (ts, from_id, to_id, type, text, channel_id, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                packet_dict.get("ts", time.time()),
                packet_dict.get("from_id"),
                packet_dict.get("to_id"),
                packet_dict.get("type"),
                packet_dict.get("text"),
                packet_dict.get("channel_id"),
                json.dumps(packet_dict)
            ))
            conn.commit()

    def save_node(self, node_dict):
        node_id = node_dict.get("id")
        if not node_id:
            return
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO nodes (id, last_seen, data_json)
                VALUES (?, ?, ?)
            ''', (
                node_id,
                node_dict.get("last_seen_ts", time.time()),
                json.dumps(node_dict)
            ))
            conn.commit()

    def get_all_nodes(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT data_json FROM nodes')
            rows = cursor.fetchall()
            return [json.loads(row[0]) for row in rows]

    def save_stats_snapshot(self, stats_dict):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO stats_snapshots (ts, data_json)
                VALUES (?, ?)
            ''', (time.time(), json.dumps(stats_dict)))
            conn.commit()
