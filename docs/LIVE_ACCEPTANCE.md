# Canlı Kabul Testleri (GPU Host)

Mock/birim testlerle doğrulanamayan kabul kriterleri. Gerçek ses, GPU Whisper ve Fish Audio TTS gerektirir.

---

## Production vLLM Baseline — Tamamlandı (23 Haziran 2026)

| Metrik | Sonuç |
|---|---:|
| LLM p50 | 2072 ms |
| LLM p95 | 2118 ms |
| Toplam p50 | 2094 ms |
| Toplam p95 | 2146 ms |
| Hata | 0 / 20 |

- Backend ek yükü ~28 ms; gecikmeyi model üretimi belirliyor.
- Model standart dışı alias'lar (`price_inquiry`, `pricing_inquiry`) üretti — normalizasyon gerekli.

---

## M7 — Tarayıcı Ses Temeli

### Fonksiyonel Kabul

- Panel'den `Sessions → Start Voice Test` ile oturum aç.
- Klavyeye dokunmadan en az 10 ardışık tur tamamla.
- Her müşteri cümlesi nihai transkript olarak görünmeli.
- Duyulan yanıt `agent_response` ile eşleşmeli.
- Tur indeksleri 0–9 arasında boşluksuz ilerlenmeli.

### Latency Kabulü

Son 10 tur için:

```sql
SELECT
  percentile_cont(0.5) WITHIN GROUP (ORDER BY lm.value_ms) AS p50_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY lm.value_ms) AS p95_ms,
  count(*) AS n
FROM latency_metrics lm
JOIN sessions s ON s.id = lm.session_id
WHERE lm.metric_name = 'speech_end_to_first_audio_ms'
  AND s.external_session_id = '<session-id>';
```

Hedef: `n >= 10`, `p95 < 2500 ms`.

### Test Sonuçları

**23 Haziran 2026 — İlk Canlı Test (kısmi, 4 tur):**

| Tur | STT ms | Backend ms | TTS ms | Konuşma→Ses ms |
|---|---:|---:|---:|---:|
| 23 | 87 | 25 | 491 | 603 |
| 24 | 128 | 17 | 444 | 589 |
| 27 | 193 | 24 | 485 | 702 |
| p95 | 193 | 25 | 666 | **702** |

- GPU Whisper yüklendi; STT p95 193 ms (soğuk ilk tur: 5986 ms, hariç).
- Fish Audio TTS Almanca ses üretti; speech-end → first-audio p95 **702 ms**.
- Barge-in tetiklendi (1→2→3).

Sorunlar:
- `source=probe` kaynaklı sahte barge-in → M8 kapsamı.
- LLM fiyat sorusunda bozuk Almanca üretti.
- LLM bir turda İngilizceye geçti.

**29 Haziran 2026 — İkinci Test (kısmi, tek tur):**

| Metrik | Değer |
|---|---:|
| STT | 210 ms |
| Backend | 20 ms |
| LLM | 2304 ms |
| TTS First Audio | 513 ms |
| End → First Audio | 3185 ms |
| Turn Duration | 22677 ms (oynatma süresi dahil — normal) |
| Barge-in Latency | — (ölçülmedi) |

- Turn Duration = End→First Audio + ~19s ajan konuşma süresi. Bug değil; `wait_for_playout` dahil.
- End → First Audio 3185 ms, hedefin (2500 ms) üzerinde.
- LLM 2304 ms, baseline'a göre yükseliyor.

### Kalan Kabul Kriterleri

- [ ] Tek oturumda kesintisiz 10 ardışık tur; `p95 < 2500 ms`.
- [ ] End → First Audio < 2500 ms. Darboğaz: LLM (~2300 ms). Seçenek: ilk cümle TTS streaming.
- [ ] `source=probe` sahte barge-in düzeltilmeli.
- [ ] LLM sadece Almanca kısıtlaması ve fiyat kalitesi düzeltilmeli.

---

## M8 — Kesinti Sertleştirme

Birim testleri: 56/56 passed.

Servisleri başlat:

```bash
docker compose up -d postgres redis livekit-server agent-backend supervisor-panel voice-runtime-worker
```

### Test 1 — Barge-in Latency

Ajan konuşurken "Moment, was kostet das?" de. Panel session detayında `interruption_latency_ms` değerini oku.

**Kabul:** `interruption_latency_ms < 600 ms`.

### Test 2 — Backchannel vs Kesinti

| İfade | Beklenen |
|---|---|
| "ja ja" | `backchannel_detected` |
| "mhm okay" | `backchannel_detected` |
| "ja genau" | `backchannel_detected` |
| "alles klar ja" | `backchannel_detected` |
| "ja aber nein" | `interruption_detected` |
| "okay aber warum" | `interruption_detected` |

### Test 3 — Adaptif VAD (İsteğe Bağlı)

`.env`'e `SPEECH_ADAPTIVE_VAD=true` ekle. Ajan konuşurken sessiz kal (klavye/oda gürültüsü). Yanlış `speech_started` eventi gelmemeli.

### Test 4 — Kısmi Transkript Erken İptal (İsteğe Bağlı)

`.env`'e `ENABLE_PARTIAL_TRANSCRIPTS=true`, `PARTIAL_INTERVAL_MS=300`, `EARLY_INTERRUPT_MIN_SPEECH_MS=500` ekle. Ajan uzun cümle sunarken 500ms konuş. `interruption_latency_ms` Test 1 baseline'ından düşük olmalı.

### Panel Kontrol Listesi

- [ ] `interruption_latency_ms` dolu
- [ ] Barge-in sayacı görünüyor ("N barge-ins")
- [ ] `backchannel_detected` vurgu renginde
- [ ] `interruption_detected` kırmızı

---

## 30 Haziran 2026 Test Planı

Tüm birim testleri: 111/111 passed.

### Kontrol 1 — End → First Audio < 2500 ms (M7 Kapısı)

10 ardışık tur çalıştır (ısınmış oturum, soğuk ilk tur hariç). SQL sorgusunu çalıştır (yukarıda). p95 < 2500 ms değilse LLM çıktısını kısalt (sistem promptuna max 2 cümle kuralı ekle).

### Kontrol 2 — Barge-in Latency (M8 Test 1)

Ajan uzun cümle sunarken "Moment, was kostet das genau?" de. `interruption_latency_ms < 600 ms`.

### Kontrol 3 — Backchannel (M8 Test 2)

Yukarıdaki 6 ifadeyi test et. Tüm ifadeler doğru eventi üretmeli.

### Kontrol 4 — Review → Pipeline

Tamamlanmış turları olan bir oturum aç → Review'da **Good** seç → kaydet → Pipeline'da Training Data sayısı > 0 olmalı.

### Panel Kontrol Listesi

- [ ] `speech_end_to_first_audio_ms` dolu
- [ ] Turn Duration değeri makul (< End→First Audio + konuşma süresi + 2s)
- [ ] Barge-in sayacı görünüyor
- [ ] `backchannel_detected` vurgu renginde
- [ ] `interruption_detected` kırmızı
- [ ] Review → Pipeline Training Data sayısı artıyor
