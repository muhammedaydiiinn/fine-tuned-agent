#!/bin/bash
# Anrufblocker platform modellerini indir
#   - LLM     : Google Drive → models/merged/anrufblocker-v14/
#   - Whisper : HuggingFace + CT2 convert → models/whisper/whisper-large-v3-turbo-german-ct2/
#
# Kullanım:
#   bash infra/scripts/download_models.sh [MODELS_DIR] [--llm-only|--whisper-only]
#
# Örnekler:
#   bash infra/scripts/download_models.sh ./models
#   bash infra/scripts/download_models.sh ./models --whisper-only
#   bash infra/scripts/download_models.sh ./models --llm-only
#   bash infra/scripts/download_models.sh --whisper-only

set -euo pipefail

# ── Argümanları ayrıştır ──────────────────────────────────────────────────
MODELS_DIR="/opt/anrufblocker/models"
DO_LLM=true
DO_WHISPER=true

for arg in "$@"; do
    case "$arg" in
        --llm-only)     DO_WHISPER=false ;;
        --whisper-only) DO_LLM=false ;;
        --*)            echo "Bilinmeyen parametre: $arg"; exit 1 ;;
        *)              MODELS_DIR="$arg" ;;
    esac
done

LLM_DIR="$MODELS_DIR/merged/anrufblocker-v14"
WHISPER_SRC_DIR="$MODELS_DIR/whisper/whisper-large-v3-turbo-german"
WHISPER_DIR="$MODELS_DIR/whisper/whisper-large-v3-turbo-german-ct2"
GDRIVE_FOLDER_ID="19eLgqB0W1UUNuFguwTGA6wnLGQmDHj3y"
HF_WHISPER_MODEL="primeline/whisper-large-v3-turbo-german"

# ── Renkli çıktı ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}==>${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠  $*${NC}"; }
success() { echo -e "${GREEN}✓  $*${NC}"; }

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   Anrufblocker Model İndirici                   ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo "Hedef: $MODELS_DIR"
echo "LLM   : $([ "$DO_LLM"     = "true" ] && echo "indirilecek" || echo "atlanıyor")"
echo "Whisper: $([ "$DO_WHISPER" = "true" ] && echo "indirilecek" || echo "atlanıyor")"
echo ""

# ── Python kontrolü ───────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}✗ python3 bulunamadı.${NC}"; exit 1
fi

# ── Bağımlılıkları sadece gerektiğinde kur ────────────────────────────────
PIP_CMD=""
if command -v pip3 &>/dev/null; then
    PIP_CMD="pip3"
elif command -v pip &>/dev/null; then
    PIP_CMD="pip"
elif python3 -m pip --version &>/dev/null 2>&1; then
    PIP_CMD="python3 -m pip"
else
    info "pip bulunamadı, kuruluyor..."
    python3 -m ensurepip --upgrade 2>/dev/null || apt-get install -y python3-pip -q
    PIP_CMD="python3 -m pip"
fi

PIP_INSTALL="$PIP_CMD install -q --break-system-packages"

if [ "$DO_LLM" = "true" ] && ! python3 -c "import gdown" &>/dev/null 2>&1; then
    info "gdown kuruluyor..."; $PIP_INSTALL gdown
fi

if [ "$DO_WHISPER" = "true" ] && ! python3 -c "import huggingface_hub" &>/dev/null 2>&1; then
    info "huggingface_hub kuruluyor..."; $PIP_INSTALL huggingface_hub
fi

# ═════════════════════════════════════════════════════════════════════════
#  1. LLM Modeli — Google Drive
# ═════════════════════════════════════════════════════════════════════════
echo "────────────────────────────────────────────────────"
if [ "$DO_LLM" = "false" ]; then
    echo "  1/2  LLM — atlanıyor (--whisper-only)"
    echo "────────────────────────────────────────────────────"
elif [ -d "$LLM_DIR" ] && [ -f "$LLM_DIR/config.json" ]; then
    echo "  1/2  LLM — zaten mevcut"
    echo "────────────────────────────────────────────────────"
    success "Atlanıyor: $LLM_DIR"
else
    echo "  1/2  LLM Modeli (Google Drive)"
    echo "       Hedef: $LLM_DIR"
    echo "────────────────────────────────────────────────────"
    mkdir -p "$MODELS_DIR/merged"
    info "İndiriliyor... (model boyutuna göre 10-30 dk sürebilir)"

    if python3 - <<PYEOF
import gdown, os, shutil, sys, tempfile

folder_id  = "$GDRIVE_FOLDER_ID"
target_dir = "$LLM_DIR"
merged_dir = os.path.dirname(target_dir)

# Geçici klasöre indir — gdown'ın subfolder davranışından bağımsız olalım
tmp_dir = os.path.join(merged_dir, ".dl_tmp")
if os.path.exists(tmp_dir):
    shutil.rmtree(tmp_dir)
os.makedirs(tmp_dir)

try:
    gdown.download_folder(id=folder_id, output=tmp_dir, quiet=False, use_cookies=False)
except Exception as e:
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"HATA: {e}", file=sys.stderr)
    sys.exit(1)

# tmp_dir içinde ne var?
items = os.listdir(tmp_dir)
subdirs = [x for x in items if os.path.isdir(os.path.join(tmp_dir, x))]
files   = [x for x in items if os.path.isfile(os.path.join(tmp_dir, x))]

# config.json direkt tmp_dir'deyse model dosyaları burada — subfolder'a bakma
if os.path.isfile(os.path.join(tmp_dir, "config.json")):
    src = tmp_dir
    print(f"Dosyalar doğrudan indirildi -> {target_dir}")
elif subdirs:
    # Hangi subfolder'da config.json var bul
    src = None
    for sd in subdirs:
        if os.path.isfile(os.path.join(tmp_dir, sd, "config.json")):
            src = os.path.join(tmp_dir, sd)
            print(f"Subfolder bulundu: {sd}  ->  {target_dir}")
            break
    if src is None:
        # config.json bulunamadı, ilk subfolder'ı dene
        src = os.path.join(tmp_dir, subdirs[0])
        print(f"config.json subfolder'da bulunamadı, deneniyor: {subdirs[0]}")
else:
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("İndirilen içerik bulunamadı.", file=sys.stderr)
    sys.exit(1)

# Varsa eski hedefi temizle, taşı
if os.path.exists(target_dir):
    shutil.rmtree(target_dir)
shutil.move(src, target_dir)
shutil.rmtree(tmp_dir, ignore_errors=True)

if not os.path.isfile(os.path.join(target_dir, "config.json")):
    print("UYARI: config.json hedef klasörde bulunamadı.", file=sys.stderr)
    sys.exit(1)

print(f"LLM modeli hazır: {target_dir}")
PYEOF
    then
        success "LLM modeli hazır: $LLM_DIR"
    else
        echo ""
        echo -e "${RED}╔══════════════════════════════════════════════════════╗${NC}"
        echo -e "${RED}║  Google Drive erişim hatası                          ║${NC}"
        echo -e "${RED}╚══════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo "Çözüm A — Drive klasörünü herkese aç:"
        echo "  drive.google.com → klasörü sağ tıkla → Paylaş"
        echo "  → 'Bağlantıya sahip herkes' → Görüntüleyici → Kaydet"
        echo ""
        echo "Çözüm B — Tarayıcıdan ZIP indir, şuraya aç:"
        echo "  $LLM_DIR/"
        echo ""
    fi
fi

# ═════════════════════════════════════════════════════════════════════════
#  2. Whisper Modeli — HuggingFace
# ═════════════════════════════════════════════════════════════════════════
echo ""
echo "────────────────────────────────────────────────────"
if [ "$DO_WHISPER" = "false" ]; then
    echo "  2/2  Whisper — atlanıyor (--llm-only)"
    echo "────────────────────────────────────────────────────"
elif [ -f "$WHISPER_DIR/model.bin" ]; then
    echo "  2/2  Whisper — zaten mevcut"
    echo "────────────────────────────────────────────────────"
    success "Atlanıyor: $WHISPER_DIR"
else
    echo "  2/2  Whisper STT (HuggingFace: $HF_WHISPER_MODEL)"
    echo "       Kaynak: $WHISPER_SRC_DIR"
    echo "       CT2    : $WHISPER_DIR"
    echo "────────────────────────────────────────────────────"
    mkdir -p "$WHISPER_SRC_DIR" "$WHISPER_DIR"
    info "İndiriliyor... (3-7 GB, birkaç dakika sürebilir)"

    python3 - <<PYEOF
import os
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="$HF_WHISPER_MODEL",
    local_dir="$WHISPER_SRC_DIR",
    local_dir_use_symlinks=False,
    ignore_patterns=[
        "*.msgpack", "flax_model*", "tf_model*", "rust_model*",
        "*.h5", "ot_model*",
    ],
)
# HuggingFace'in bıraktığı .cache klasörünü temizle
import shutil
cache_dir = os.path.join("$WHISPER_SRC_DIR", ".cache")
if os.path.exists(cache_dir):
    shutil.rmtree(cache_dir)
    print("HF cache temizlendi.")
print("Whisper modeli indirildi.")
PYEOF

    if [ ! -f "$WHISPER_SRC_DIR/model.safetensors" ] && [ ! -f "$WHISPER_SRC_DIR/pytorch_model.bin" ]; then
        echo -e "${RED}✗ Whisper kaynak modeli eksik indirildi: ağırlık dosyası bulunamadı.${NC}"
        exit 1
    fi

    python3 - <<PYEOF
import os, shutil
src = os.path.join("$WHISPER_SRC_DIR", "vocab.json")
dst = os.path.join("$WHISPER_SRC_DIR", "vocabulary.json")
if os.path.exists(src) and not os.path.exists(dst):
    shutil.copyfile(src, dst)
    print("Kaynak vocab.json -> vocabulary.json kopyalandı.")
PYEOF

    info "CT2 formatına dönüştürülüyor..."
    rm -rf "$WHISPER_DIR"
    ct2-transformers-converter \
        --model "$WHISPER_SRC_DIR" \
        --output_dir "$WHISPER_DIR" \
        --copy_files tokenizer_config.json preprocessor_config.json merges.txt added_tokens.json normalizer.json special_tokens_map.json

    python3 - <<PYEOF
import json, os, shutil
vocab = os.path.join("$WHISPER_DIR", "vocab.json")
target = os.path.join("$WHISPER_DIR", "vocabulary.json")
if os.path.exists(vocab) and not os.path.exists(target):
    shutil.copyfile(vocab, target)
    print("vocab.json -> vocabulary.json kopyalandı.")
PYEOF

    if [ ! -f "$WHISPER_DIR/model.bin" ]; then
        echo -e "${RED}✗ Whisper modeli eksik indirildi: model.bin bulunamadı.${NC}"
        exit 1
    fi

    success "Whisper modeli hazır: $WHISPER_DIR"
fi

# ═════════════════════════════════════════════════════════════════════════
#  Özet
# ═════════════════════════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════"
echo "  Disk kullanımı:"
du -sh "$LLM_DIR"     2>/dev/null | awk "{print \"  LLM    : \$1  $LLM_DIR\"}"     || true
du -sh "$WHISPER_DIR" 2>/dev/null | awk "{print \"  Whisper: \$1  $WHISPER_DIR\"}" || true
echo ""
echo "  .env değerleri:"
echo "  MODEL_MERGED_PATH=$LLM_DIR"
echo "  WHISPER_MODEL_PATH=$WHISPER_DIR"
echo "══════════════════════════════════════════"
echo ""
