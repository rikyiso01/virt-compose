#!/usr/bin/env bash

set -euo pipefail

part="$1"
name="$2"

sed -e 's/\s*\([\+0-9a-zA-Z]*\).*/\1/' << EOF | fdisk "$part"
  g # clear the in memory partition table
  n # new partition
  1 # partition number 1
    # default - start at beginning of disk 
  +1G # 1 GB boot parttion
  n # new partition
  2 # partion number 2
    # default, start immediately after preceding partition
    # default, extend partition to end of disk
  t
  1
  1
  w # write the partition table
EOF

mkfs.ext4 "${part}${name}2"
mount "${part}${name}2" /mnt

mkfs.fat -F32 "${part}${name}1"
mount --mkdir "${part}${name}1" /mnt/boot
pacstrap -K /mnt base linux linux-firmware sof-firmware networkmanager zsh sudo
genfstab -U /mnt > /mnt/etc/fstab

arch-chroot /mnt ln -sf /usr/share/zoneinfo/Europe/Rome /etc/localtime
arch-chroot /mnt hwclock --systohc
arch-chroot /mnt sed -i 's/#en_US.UTF-8/en_US.UFT-8/' /etc/locale.gen
arch-chroot /mnt locale-gen
echo 'LANG=en_US.UTF-8' > /mnt/etc/locale.conf
echo 'KEYMAP=us' > /mnt/etc/vconsole.conf
echo 'arch' > /mnt/etc/hostname
arch-chroot /mnt useradd -m -G wheel -s /bin/zsh riky
arch-chroot /mnt passwd -d riky
arch-chroot /mnt sed -i 's/# %wheel ALL=(ALL:ALL) ALL/%wheel ALL=(ALL:ALL) ALL/' /etc/sudoers
arch-chroot /mnt systemctl enable NetworkManager
arch-chroot /mnt bootctl install
cat > /mnt/boot/loader/entries/arch.conf <<EOF
title   Arch Linux
linux   /vmlinuz-linux
initrd  /initramfs-linux.img
EOF
echo "options root=UUID=$(blkid "${part}${name}2" -o value -s UUID) rw" >> /mnt/boot/loader/entries/arch.conf
cat > /mnt/boot/loader/loader.conf << EOF
default arch
timeout 0
console-mode max
editor yes
EOF
arch-chroot /mnt bootctl update || true

