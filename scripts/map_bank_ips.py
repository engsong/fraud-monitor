import subprocess
import re

for i in range(1, 26):
    cmd = f"ssh -o BatchMode=yes root@175.2.0.23 grep -m 1 '175.28.{i}.' /root/LMPSCore/log/LMPS.log"
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    line = proc.stdout
    mem = re.findall(r'"(?:frommember|tomember)":"([^"]+)"', line)
    print(f"175.28.{i}. -> {mem if mem else 'NOT FOUND'}")
