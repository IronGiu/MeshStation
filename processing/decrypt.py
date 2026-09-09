from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

def dataDecryptor(meshPacketHex, aesKey):
    # Nonce must be 16 bytes.
    # Structure: packetID (4) + 0000 (4) + sender (4) + 0000 (4)
    
    p_id = meshPacketHex['packetID']
    sender = meshPacketHex['sender']
    
    # Ensure 4 bytes each
    if len(p_id) < 4: p_id = p_id.rjust(4, b'\x00')
    if len(sender) < 4: sender = sender.rjust(4, b'\x00')
    
    aesNonce = p_id + b'\x00\x00\x00\x00' + sender + b'\x00\x00\x00\x00'
    
    if len(aesNonce) != 16:
        raise ValueError(f"Invalid nonce size constructed: {len(aesNonce)}")

    cipher = Cipher(algorithms.AES(aesKey), modes.CTR(aesNonce), backend=default_backend())
    decryptor = cipher.decryptor()
    decryptedOutput = decryptor.update(meshPacketHex['data']) + decryptor.finalize()
    return decryptedOutput
