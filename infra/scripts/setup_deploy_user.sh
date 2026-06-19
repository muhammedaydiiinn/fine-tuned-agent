#!/usr/bin/env bash
# GPU sunucusunda deploy kullanıcısı oluşturur ve SSH güvenliğini sağlar.
# Root olarak çalıştır: sudo bash infra/scripts/setup_deploy_user.sh
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_PUB_KEY="${SSH_PUB_KEY:-}"  # Ortam değişkeni ya da manuel giriş

echo "=== Deploy kullanıcısı oluşturuluyor: $DEPLOY_USER ==="

# Kullanıcı yoksa oluştur
if ! id "$DEPLOY_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$DEPLOY_USER"
    echo "Kullanıcı oluşturuldu: $DEPLOY_USER"
else
    echo "Kullanıcı zaten mevcut: $DEPLOY_USER"
fi

# sudo yetkisi ver
usermod -aG sudo "$DEPLOY_USER"
echo "$DEPLOY_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/$DEPLOY_USER"
chmod 440 "/etc/sudoers.d/$DEPLOY_USER"
echo "Sudo yetkisi verildi."

# Docker grubuna ekle
if getent group docker &>/dev/null; then
    usermod -aG docker "$DEPLOY_USER"
    echo "Docker grubuna eklendi."
fi

# SSH public key kur
SSH_DIR="/home/$DEPLOY_USER/.ssh"
mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"

if [[ -n "$SSH_PUB_KEY" ]]; then
    echo "$SSH_PUB_KEY" >> "$SSH_DIR/authorized_keys"
elif [[ -f /root/.ssh/authorized_keys ]]; then
    # Root'un key'ini kopyala
    cp /root/.ssh/authorized_keys "$SSH_DIR/authorized_keys"
    echo "Root SSH key'i kopyalandı."
else
    echo "UYARI: SSH public key bulunamadı."
    echo "Manuel olarak ekle: nano $SSH_DIR/authorized_keys"
fi

chmod 600 "$SSH_DIR/authorized_keys" 2>/dev/null || true
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$SSH_DIR"

echo ""
echo "=== SSH güvenlik ayarları ==="

SSHD_CONFIG="/etc/ssh/sshd_config"

# Root girişini kapat
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' "$SSHD_CONFIG"
# Şifre ile girişi kapat (sadece key)
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' "$SSHD_CONFIG"
# Boş şifre yasak
sed -i 's/^#*PermitEmptyPasswords.*/PermitEmptyPasswords no/' "$SSHD_CONFIG"

systemctl reload sshd
echo "SSH yapılandırması güncellendi."
echo "  - Root girişi: KAPALI"
echo "  - Şifre ile giriş: KAPALI (sadece SSH key)"

echo ""
echo "=== fail2ban ==="
if command -v fail2ban-server &>/dev/null; then
    systemctl enable --now fail2ban
    echo "fail2ban aktif."
else
    echo "UYARI: fail2ban kurulu değil. install_host_dependencies.sh çalıştır."
fi

echo ""
echo "Kurulum tamamlandı."
echo "Yeni terminalde şu komutla bağlan (root bağlantısını KAPATMA):"
echo "  ssh $DEPLOY_USER@$(hostname -I | awk '{print $1}')"
echo ""
echo "Bağlantı başarılıysa root oturumunu kapat."
