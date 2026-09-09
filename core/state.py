import threading
from collections import deque
from core.constants import CHAT_DOM_WINDOW

class AppState:
    def __init__(self):
        self.connect_mode = None  # None | "direct" | "external"
        self.engine_proc = None
        self.last_rx_ts = 0.0
        self.rx_seen_once = False
        self.autosave_interval_sec = 30
        self.autosave_last_ts = 0.0

        self.direct_region = "EU_868"
        self.direct_preset = "MEDIUM_FAST"
        self.direct_frequency_slot = 0        # 0 = auto (hash-based), 1..N = manual
        self.direct_channel_name = ""         # "" = use default name of the preset
        self.direct_ppm = 0
        self.direct_gain = 30
        self.direct_device_args = "rtl=0"
        self.direct_device_detected_args = []
        self.direct_bias_tee = False
        self.direct_port = "20002"
        self.direct_key_b64 = "AQ=="

        self.external_ip = "127.0.0.1"
        self.external_port = "20002"
        self.external_key_b64 = "AQ=="

        self.connected = False
        self.ip_address = "127.0.0.1"
        self.port = "20002"
        self.aes_key_b64 = "AQ==" # Default Meshtastic Key representation (means default)
        self.aes_key_bytes = None

        # Multi-channel monitoring
        self.extra_channels = []
        self.channel_messages = {}  # channel_id -> list of new messages to render
        self.channel_unread = {}    # channel_id -> bool (has unread)
        self.channel_unread_count = {}  # channel_id -> int
        self.active_channel_id = "default"
        self.channels_order = [] 
        
        # Data Stores
        self.nodes = {} # Key: NodeID (e.g., "!322530e5"), Value: Dict with info
        self.messages = deque(maxlen=100) # List of chat messages
        self.logs = deque(maxlen=500) # Raw logs
        self.seen_packets = deque(maxlen=300) # Deduplication buffer (Sender, PacketID)
        self.raw_packet_count = 0
        
        # UI Update Flags
        self.new_logs = []
        self.new_messages = []
        self.nodes_updated = False
        self.nodes_list_updated = False
        self.nodes_list_force_refresh = False
        self.chat_force_refresh = False
        self.chat_force_scroll = False
        self.dirty_nodes = set()
        self.lock = threading.Lock()
        self.verbose_logging = True
        self.theme = "dark"
        self.map_center_lat = None
        self.map_center_lng = None
        self.map_zoom = None

        # Error checking
        self.rtlsdr_error_pending = False
        self.rtlsdr_error_text = ""

        # Connection Popup auto state
        self.connection_dialog_shown = False

        self.update_check_done = False
        self.update_check_running = False
        self.update_available = False
        self.latest_version = None
        self.latest_release_url = None
        self.update_popup_shown = False
        self.update_popup_ack_version = None

        # Database
        try:
            from core.db import MeshDB
            self.db = MeshDB()
            # Load existing nodes from DB on start
            initial_nodes = self.db.get_all_nodes()
            for n in initial_nodes:
                self.nodes[n["id"]] = n
        except Exception as e:
            print(f"Database initialization error: {e}")
            self.db = None

# Global instance
state = AppState()
