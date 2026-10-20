import subprocess
import threading
import time
import sys
import os

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.main_pipeline import FraudDetectionPipeline
from src.common.logger import get_logger

logger = get_logger("RemoteStreamer")

SWITCHING_VMS = [
    {"name": "VM1-175.2.0.23", "ip": "175.2.0.23", "log_path": "/root/LMPSCore/log/LMPS.log"},
    {"name": "VM2-175.2.0.24", "ip": "175.2.0.24", "log_path": "/root/LMPSCore/log/LMPS.log"}
]

class RemoteLogStreamer:
    """
    Connects via SSH to Switching System VMs (VM1 & VM2) and tails LMPS.log in real-time.
    Pushes live transactions to the FraudDetectionPipeline.
    """

    def __init__(self, pipeline: FraudDetectionPipeline):
        self.pipeline = pipeline
        self.running = True

    def stream_vm_log(self, vm_info: dict):
        vm_name = vm_info["name"]
        vm_ip = vm_info["ip"]
        log_path = vm_info["log_path"]

        logger.info(f"[{vm_name}] Starting live log stream from {vm_ip}:{log_path}...")

        while self.running:
            cmd = [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3",
                f"root@{vm_ip}",
                f"tail -n 0 -F {log_path}"
            ]

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1
                )

                for line in process.stdout:
                    if not self.running:
                        break
                    
                    # Filter for transaction events to optimize CPU
                    if "[POST /transfer]" in line or "/transfer CONTENT:" in line:
                        try:
                            self.pipeline.process_raw_message(line, node=vm_name)
                        except Exception as e:
                            logger.error(f"[{vm_name}] Error processing line: {e}")

                process.terminate()
            except Exception as e:
                logger.warning(f"[{vm_name}] Stream disconnected ({e}). Reconnecting in 3 seconds...")
                time.sleep(3)

    def start(self):
        threads = []
        for vm in SWITCHING_VMS:
            t = threading.Thread(target=self.stream_vm_log, args=(vm,), daemon=True)
            t.start()
            threads.append(t)

        logger.info(f"All {len(threads)} VM streaming workers started.")
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping streamer...")
            self.running = False

if __name__ == "__main__":
    pipeline = FraudDetectionPipeline()
    streamer = RemoteLogStreamer(pipeline)
    streamer.start()
