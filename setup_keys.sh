#!/bin/bash
set -e

if [ ! -f ~/.ssh/id_ed25519 ]; then
    ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
fi

PUB_KEY=$(cat ~/.ssh/id_ed25519.pub)

for IP in 175.2.0.23 175.2.0.24; do
    echo "Configuring SSH key for $IP..."
    SSH_ASKPASS=/home/admin/askpass_vm.sh SSH_ASKPASS_REQUIRE=force DISPLAY=dummy:0 ssh -o StrictHostKeyChecking=no root@$IP "mkdir -p /root/.ssh && chmod 700 /root/.ssh && grep -qF '$PUB_KEY' /root/.ssh/authorized_keys 2>/dev/null || echo '$PUB_KEY' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys"
    echo "Testing passwordless SSH to $IP..."
    ssh -o BatchMode=yes -o StrictHostKeyChecking=no root@$IP "echo SUCCESS from \$(hostname)"
done

rm -f /home/admin/askpass_vm.sh
echo "All SSH keys successfully configured!"
