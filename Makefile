installer: archlinux-x86_64.iso disk.qcow2 OVMF_CODE.fd
	(sleep 5 && echo 'sendkey ret' && sleep 30 && ./sendkeys.py "echo $$(cat ~/.ssh/id_ed25519.pub) > .ssh/authorized_keys") | qemu-system-x86_64 -enable-kvm -m 4G -smp 4 -nic user,hostfwd=tcp::2222-:22 -hda disk.qcow2 -boot d -cdrom archlinux-x86_64.iso -display vnc=:0 -monitor stdio -bios ./OVMF_CODE.fd --no-reboot

run-installation:
	scp -P 2222 ./arch-install.sh root@127.0.0.1:/root/install.sh
	ssh -p 2222 root@127.0.0.1 'bash /root/install.sh "/dev/sda" ""'

up-install: OVMF_CODE.fd
	(sleep 15 && ./sendkeys.py 'riky' && sleep 1 && ./sendkeys.py 'q' && sleep 1 && ./sendkeys.py 'sudo mount -t 9p -o trans=virtio,version=9p2000.L host0 /mnt && cd /mnt && bash install.sh') | qemu-system-x86_64 -enable-kvm -m 4G -smp 4 -nic user,hostfwd=tcp::2222-:22 -hda disk.qcow2 -display vnc=:0 -monitor stdio -bios ./OVMF_CODE.fd -virtfs local,path=../,mount_tag=host0,security_model=passthrough,id=host0

up: OVMF_CODE.fd
	/bin/qemu-system-x86_64 -enable-kvm -m 4G -smp 4 -nic user,hostfwd=tcp::2222-:22,model=virtio-net-pci -hda disk.qcow2 -display vnc=:0 -display egl-headless -monitor stdio -bios ./OVMF_CODE.fd -virtfs local,path=../,mount_tag=host0,security_model=passthrough,id=host0 -device virtio-vga-gl -audio pipewire,model=virtio

archlinux-x86_64.iso: sha256sums.txt
	curl -o archlinux-x86_64.iso https://archmirror.it/repos/iso/2024.07.01/archlinux-x86_64.iso
	sha256sum -c sha256sums.txt || (rm archlinux-x86_64.iso && exit 1)

OVMF_CODE.fd:
	curl -Lo OVMF_CODE.fd https://github.com/kholia/OSX-KVM/raw/master/OVMF_CODE.fd

sha256sums.txt:
	curl https://archlinux.org/iso/2024.07.01/sha256sums.txt | head -n 2 | tail -n 1 > sha256sums.txt

disk.qcow2:
	qemu-img create -f qcow2 disk.qcow2 40G
