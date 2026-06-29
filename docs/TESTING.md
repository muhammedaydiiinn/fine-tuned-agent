# Test Rehberi

Her service kendi testlerini kendi kodu yanında tutar. Root script tek giriş noktasıdır.

## Tüm Testleri Çalıştır

```bash
bash scripts/run_unit_tests.sh
```

Altı suite'i sırayla çalıştırır:

| Suite | Runner | Path |
|-------|--------|------|
| agent-backend | pytest | `services/agent-backend/tests/` |
| voice-runtime | pytest | `services/voice-runtime/tests/` |
| training-worker | pytest | `services/training-worker/tests/` |
| eval-worker | pytest | `services/eval-worker/tests/` |
| supervisor-panel | pytest | `services/supervisor-panel/tests/` |
| supervisor-panel (JS) | node --test | `services/supervisor-panel/tests/node/` |

## Tek Bir Service Çalıştır

```bash
# Python
PYTHONPATH=services/<name> python3 -m pytest -q services/<name>/tests

# Node
node --test services/supervisor-panel/tests/node/*.test.js
```

## Test Haritası

### agent-backend (`services/agent-backend/tests/`)

| Dosya | Kapsam |
|-------|--------|
| `test_m6_hardening.py` | Model registry deploy/rollback sağlamlaştırma |
| `test_review_compiler.py` | Deterministik M12 talimat sınıflandırması ve onaylı template'ler |
| `test_voice_events.py` | Ses olaylarının kalıcılığı ve sorgulanması |

### voice-runtime (`services/voice-runtime/tests/`)

| Dosya | Kapsam |
|-------|--------|
| `test_pipeline.py` | Turn-taking, barge-in testi, supervisor komutları, backchannel sınıflandırması |
| `test_backend_client.py` | Circuit breaker durum makinesi (open/half-open/reset) |
| `test_stt.py` | STT hata sarmalama (model yükleme hatası, transkripsiyon hatası) |
| `test_tts.py` | `pace_to_speed` eşlemesi, TTS fallback davranışı |
| `test_segmenter.py` | VAD segmenter mantığı |
| `test_turn_taking.py` | Turn-taking senaryo kataloğu |

### training-worker (`services/training-worker/tests/`)

| Dosya | Kapsam |
|-------|--------|
| `test_build_dataset.py` | Dataset doğrulama, aday kapsam belirleme ve manifest'ler |
| `test_artifacts.py` | Atomik aday yayınlama, checksum, rollback ve backup temizliği |
| `test_worker_publication.py` | Aday yayınlamayla ModelVersion commit başarı/hata koordinasyonu |

### supervisor-panel (`services/supervisor-panel/tests/`)

| Dosya | Kapsam |
|-------|--------|
| `test_ui_feedback.py` | Panel UI geri bildirimi ve toast bildirimleri |
| `test_review_compiler.py` | Kabul edilen compiler düzeltme türleri ve güvenli fallback |
| `test_voice_actions.py` | Stop-agent ve replace-answer aksiyon yönlendirmesi |
| `test_voice_observability.py` | `build_voice_health`, `build_recent_voice_turns`, `build_voice_acceptance` toplaması |

### supervisor-panel JS (`services/supervisor-panel/tests/node/`)

| Dosya | Kapsam |
|-------|--------|
| `voice-session-recovery.test.js` | Recovery durum makinesi: bağlantı/kopukluk takibi, retry limiti, üstel geri çekilme gecikmeleri |

## Canlı Kabul Testleri

Gerçek GPU, gerçek Whisper ve Fish Audio TTS gerektiren manuel testler
`docs/LIVE_ACCEPTANCE.md` dosyasında belgelenmiştir. Bu testler CI'da çalışamaz — GPU host üzerinde gerçekleştirilir.
