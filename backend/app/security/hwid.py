import subprocess
import hashlib
import sys

# Target PC Hardware ID
ALLOWED_HWID = "09651b874308888868fd196ad3ba305dd9f393581e2d5d7141337ff7216516c9,8726470c3d675662b883c670ff272e66d2cddf59af04ff092dfea920fa6d626c"

def verify_hardware_lock():
    try:
        cmd = "wmic csproduct get uuid"
        output = subprocess.check_output(cmd, shell=True).decode().strip()
        lines = [line.strip() for line in output.split('\n') if line.strip()]
        raw_id = lines[1] if len(lines) > 1 else lines[0]
        
        current_hwid = hashlib.sha256(raw_id.encode('utf-8')).hexdigest()

        if current_hwid not in ALLOWED_HWID.split(','):
            print("CRITICAL ERROR: Unauthorized Machine. Software is locked to another system.")
            sys.exit(1)
    except Exception:
        sys.exit(1)