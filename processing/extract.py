from utils.helpers import hexStringToBinary

def dataExtractor(data_hex):
    # Expected min length: dest(8) + sender(8) + packetID(8) + flags(2) + hash(2) + reserved(4) = 32 chars
    if len(data_hex) < 32:
        raise ValueError(f"Packet too short: {len(data_hex)} chars")

    try:
        meshPacketHex = {
            'dest' : hexStringToBinary(data_hex[0:8]),
            'sender' : hexStringToBinary(data_hex[8:16]),
            'packetID' : hexStringToBinary(data_hex[16:24]),
            'flags' : hexStringToBinary(data_hex[24:26]),
            'channelHash' : hexStringToBinary(data_hex[26:28]),
            'reserved' : hexStringToBinary(data_hex[28:32]),
            'data' : hexStringToBinary(data_hex[32:])
        }
        return meshPacketHex
    except Exception as e:
        raise ValueError(f"Extraction failed: {e}")
