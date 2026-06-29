# Milestone 1–12 Denetimi — 2026-06-21

## Sonuç

Milestone 1–3 tamamlandı. Milestone 4–6, mock modunda mevcut platform hedeflerini karşılamakta ve GPU/candidate-serving kabul testleri doğrulanana kadar koşullu olarak tamamlanmış sayılmaktadır. Milestone 7–10, yerel implementasyonları itibarıyla koşullu olarak tamamlanmış olup belgelenen canlı GPU, latency/yük ve production-operations kabul testlerini hâlâ gerektirmektedir. Milestone 12 yerel ortamda tamamlandı. Tam değerlendirme akışı Docker üzerinden doğrulandı:

```text
POST /eval-runs
  -> Redis agent:eval_jobs
  -> eval-worker
  -> POST /agent-turn for 10 single-turn + 5 multi-turn scenarios
  -> eval_runs metrics/progress/log paths
  -> model_versions.eval_status
  -> supervisor panel evaluation list/detail pages
```

Uçtan uca çalıştırma 15 senaryo grubunu tamamladı, log ve sonuç artefaktlarını yazdı, yapılandırılmış her metriği üretti ve tarayıcı konsol hatası ya da DataTables uyarısı olmaksızın render edildi.

## Canlı GPU Eki — 2026-06-23

Production `fine-tuned-agent-v14` modeli, hedef NVIDIA RTX PRO 6000 Blackwell sunucusunda vLLM tarafından yüklendi ve `/v1/models`, doğrudan chat completion ve uygulama `/agent-turn` route'u üzerinden doğrulandı.

Ardışık 20-turn uygulama latency çalıştırması hiç request başarısızlığı olmadan tamamlandı. LLM p50/p95 değerleri 2072/2118 ms; toplam backend p50/p95 değerleri 2094/2146 ms; maksimum toplam ise 2155 ms oldu. Bu durum M1 gerçek-vLLM serving path'ini kanıtlamaktadır; ancak izole candidate değerlendirmesini, gerçek zamanlı ses latency'sini ya da kesme kabulünü kanıtlamamaktadır.

Canlı model aynı zamanda standart dışı fiyat intent/action alias'ları da üretti. Çalışma zamanı normalizasyonu ve daha katı kısa-çıktı politikası, nihai production kalite kabulünden önce hâlâ gereklidir. Bir sonraki canlı test, GPU Whisper, gerçek vLLM, Fish Audio streaming TTS, speech-end-to-first-audio latency ve kalıcı transkript/yanıt eşleşmesini kapsayan 10-turn tarayıcı mikrofon testidir.

## Milestone 1 — Core

Durum: denetim düzeltmelerinin ardından tamamlandı.

Doğrulandı:

- Backend, PostgreSQL, Redis, health endpoint, session'lar, turn'ler ve agent turn'leri.
- Stateful guardrail'ler ve sabit ürün gerçeği şablonları.
- Mock/gerçek vLLM geçişi.
- Gerçek production vLLM serving ve hedef GPU üzerinde 20-turn latency baseline.
- Düzeltme belleği, policy onarımından sonra ve guardrail'lerden önce uygulanmaktadır.

Denetim düzeltmeleri:

- Turn index'leri artık beşinci turn'den sonra tekrar etmiyor.
- Kimlik, yalnızca açık bir müşteri beyanından doğrulanıyor; agent yalnızca onay istediğinde değil.
- Mock `free_question` sınıflandırması artık genel fiyat anahtar kelimesi kontrolü tarafından gölgelenmiyor.
- Intent anahtarlı düzeltme belleği artık onarılmış policy intent'iyle eşleşiyor.

## Milestone 2 — Supervisor Panel

Durum: tamamlandı.

Doğrulandı:

- Session'lar, turn detayları, düzeltmeler, eğitim verisi ve eğitim işi sayfaları.
- Kimlik doğrulama, bildirimsel DataTables yapılandırması ve AJAX veri kaynakları.
- Kontrol edilen sayfalarda tarayıcı konsol hatası veya bilinmeyen-sütun uyarısı yok.

Denetim düzeltmeleri:

- Dahili backend proxy istekleri artık yapılandırıldığında `X-API-Key` içeriyor.
- Panel üzerinden oluşturulan düzeltme belleği kayıtları eşleşen bağlam içeriyor.

## Milestone 3 — Düzeltme ve Eğitim Candidate'leri

Durum: düzeltme-belleği eşleştirme düzeltmesinin ardından tamamlandı.

Doğrulandı:

- Düzeltmeler session'lara ve turn'lere izlenebilir.
- Anlık düzeltmeler sonraki policy çıktısını etkileyebiliyor.
- Eğitim candidate'leri üretilip JSONL olarak dışa aktarılıyor.

## Milestone 4 — Eğitim Worker'ı

Durum: uygulanan mock/gerçek pipeline için koşullu olarak tamamlandı.

Doğrulandı:

- Redis iş dispatch'i, ilerleme, log'lar, dataset oluşturma, LoRA eğitimi, birleştirme ve model kayıt path'leri Docker mock modunda uçtan uca tamamlandı.
- Gerçek-modda birleştirilen artefaktlar atomik olarak kararlı candidate serving path'ine yayınlanıyor. Önceki ağaç, ModelVersion commit'ine kadar kurtarılabilir durumda kalıyor; bir commit başarısızlığı geri yükler.
- Kuyruk başarısızlıkları artık veritabanı işini beklemede bırakmak yerine başarısız olarak işaretliyor.
- Mock Docker build'leri yalnızca worker/runtime bağımlılıklarını yükler; GPU eğitim bağımlılıkları `requirements-gpu.txt` içinde izole edilmiştir.

Sınırlılık:

- Bu Mac üzerinde gerçek GPU/Unsloth eğitimi çalıştırılmadı. Hedef NVIDIA sunucusunu ve model dosyalarını hâlâ gerektirmektedir.
- Gerçek GPU artefaktları aynı manifest ve atomik yayın kurallarına göre doğrulanmalıdır.

## Milestone 5 — Değerlendirme Worker'ı

Durum: koşullu olarak tamamlandı.

Uygulandı:

- Eval run CRUD/log/sonuç endpoint'leri.
- Idempotent `eval_runs` şema yükseltmesi.
- Veritabanı ilerleme, log'lar, atomik sonuç yazma ve başarısızlık durumlarını içeren Redis worker.
- `/agent-turn` üzerinden on sabit single-turn ve beş multi-turn senaryo grubu.
- JSON geçerliliği, zorunlu-anahtar kapsamı, next-action doğruluğu, hard-decline, identity-before-link, fiyat, güvenlik, döngü tekrarı ve latency metrikleri.
- Kalite skoru ve model geçti/kaldı durumu.
- Senaryo sonuçları ve canlı log'larla değerlendirme liste/detay sayfaları.

Sınırlılık:

- GPU sunucusunda gerçek izole candidate vLLM çalıştırması hâlâ yapılmadı.

## Milestone 6 — Model Yaşam Döngüsü ve Deployment

Durum: koşullu olarak tamamlandı.

Docker mock modunda doğrulandı:

- Candidate'e özgü eval yönlendirmesi ve turn düzeyinde model versiyonu kanıtı.
- Sürümlendirilmiş `m6-gate-v1` deployment kontrolleri.
- Artefakt doğrulama, onay yaşam döngüsü ve deployment denetimi.
- İki ardışık deployment ve ardından rollback.
- Normal agent trafiği deploy edilen modele geçirildi ve rollback sonrası geri döndürüldü.
- Production yapılandırması yalnızca mock değerlendirme kanıtını reddediyor.
- Supervisor UI, Sessions, Review & Train ve Models çalışma alanlarına indirgendi.
- Session review, candidate-ID kapsamlı bir eğitim batch'i oluşturup kalite kontrolünü otomatik olarak başlattı.
- Redis AOF kalıcılığı, kalıcı bir volume üzerinde etkinleştirildi.
- Candidate yayınlama, manuel kopyalama adımını kaldırıyor. Çalışan bir vLLM işlemi hâlâ start/restart gerektiriyor çünkü model ağırlıkları işlem başlangıcında yükleniyor; yayınlama hot reload değildir.

Sınırlılık:

- Blue/green vLLM serving ve rollback, hedef NVIDIA sunucusunda nihai doğrulamayı gerektiriyor.

## Milestone 7 — Tarayıcı Ses Temeli

Durum: koşullu olarak tamamlandı.

Yerel olarak doğrulandı:

- LiveKit 1.9.12 sunucusu ve LiveKit Agents 1.6.2 worker'ı Docker'da başlatıldı.
- Adlandırılmış worker kaydoldu ve bir tarayıcı token'ının oluşturduğu açık oda dispatch'ini kabul etti.
- Supervisor Sessions UI artık senaryo seçimini, mikrofon start/stop işlemlerini, transkript/yanıt olaylarını, uzak ses ve latency görüntülemeyi yönetiyor.
- Kimliği doğrulanmış panel, LiveKit oda token'ını oluşturuyor. Geçici bağımsız ses UI/API ve port 8030 kaldırıldı.
- Faster Whisper Almanca STT, `/agent-turn`, Fish Audio streaming PCM TTS ve LiveKit ses yayınlama tek bir çalışma zamanında bağlandı.
- Ses ve backend session'ları tek bir harici session ID paylaşıyor.
- Ses turn'leri, `stt_ms`, `backend_ms`, `llm_ms`, `tts_first_audio_ms`, `speech_end_to_first_audio_ms` ve `total_voice_turn_ms` değerlerini aynı turn'e karşı kalıcı olarak kaydediyor.
- Metrik kalıcılığı, eşleşmeyen nihai transkriptleri veya duyulan yanıtları reddediyor.
- Ses çalışma zamanı birim testleri ve Docker import'ları başarıyla geçti.

Mevcut canlı kanıt:

- Gerçek bir tarayıcı mikrofonu → yerel Whisper → backend → Fish Audio turn'ü, eşleşen transkript, yanıt ve latency verisiyle `voice-test-91f4c3b395` session'ı için kalıcı olarak kaydedildi.

Sınırlılık:

- Hedef GPU sunucusunun hâlâ 10-turn tarayıcı testini ve `services/voice-runtime/LIVE_ACCEPTANCE.md` içindeki p95 speech-end-to-first-audio eşiğini geçmesi gerekiyor.

Kanonik kapsam ve kalan milestone'lar `MILESTONES.md` içinde tanımlanmıştır.

## Milestone 8 — Gerçek Zamanlı Sıra Değişimi ve Kesme

Durum: yerel/mock implementasyon için koşullu olarak tamamlandı.

Uygulandı ve doğrulandı:

- Sınırlı utterance kuyruğu, önceki örtüşme-bırakma davranışının yerini aldı.
- Sürekli müşteri konuşması, aktif agent oynatmasını iptal ediyor.
- Muhafazakâr Almanca backchannel sınıflandırması, kısa onay ifadelerinin yeni agent turn'leri olarak değerlendirilmesini önlüyor.
- Yinelenen nihai transkript ve eski yanıt koruyucuları.
- Sürekli örtüşme başladığında yürütmedeki backend yanıtları geçersiz kılınıyor; örtüşme debounce'u sırasında başlayan oynatma da iptal ediliyor.
- Dayanıklı, idempotent `voice_events` denetim kayıtları ve canlı panel zaman çizelgesi.
- Gerçek mikrofon seviyesi ölçerli dinleme, duyma, işleme, konuşma ve kesilen kullanıcı arayüzü durumları.
- Pipeline düzeyinde iptal/eski-yanıt testleri ve 20 backchannel ile 20 gerçek kesmeyi içeren deterministik katalog.

Kalan canlı kabul testleri:

- Tarayıcı/Fish Audio kesme latency'si ve yanlış-kesme eşikleri gerçek bir ses çalıştırması gerektiriyor.
- Metin kısmi hipotezleri henüz üretilmiyor; çalışma zamanı şu anda speech-boundary ve final-transcript olayları yayınlıyor.

## Milestone 9 — Canlı Supervisor Kontrolü

Durum: uygulanan yerel/mock akış için koşullu olarak tamamlandı.

Doğrulandı:

- Panel, aktif ses odasına karşı `Stop Agent` ve `Replace Answer` komutları verebiliyor.
- Canlı bir değiştirme, çalışma zamanı kontrol komutunu yayınlamadan önce isteğe bağlı `apply_immediately`, isteğe bağlı `send_to_training` ve bir supervisor denetim olayıyla birlikte bağlantılı bir düzeltme isteği kalıcı olarak kaydediyor.
- Ses çalışma zamanı, `stop_agent` ve `replace_answer` komutlarını LiveKit veri kanalı üzerinden uyguluyor ve panel zaman çizelgesi için dayanıklı supervisor olayları yayınlıyor.
- Değiştirme oynatması, ses çalışma zamanı tarafından sentezleniyor ve normal agent yanıtlarının kullandığı aynı session path'i üzerinden ses olarak iletiliyor.

Sınırlılık:

- Hedef sunucuda gerçek bir tarayıcı mikrofon session'ının, durdurma/değiştirme kullanıcı deneyimi ve operatör iş akışı için hâlâ manuel bir kabul testinden geçmesi gerekiyor.

## Milestone 10 — Ses Sağlamlaştırma

Durum: yalnızca kısmi ilerleme.

Doğrulandı:

- `voice-runtime` içindeki agent-backend çağrıları artık bir circuit breaker kullanıyor; böylece tekrarlayan backend başarısızlıkları her turn'ü durdurmak yerine hızlıca başarısız oluyor.
- STT başarısızlıkları `stt_unavailable` yayınlıyor ve session'ı genel bir çalışma zamanı hatasına düşürmek yerine dinleme durumuna döndürüyor.
- TTS başarısızlıkları, açık bir `tts_fallback_activated` olayı ve panel uyarısıyla birlikte mock PCM çıktısına geri dönebiliyor.
- Session detayı artık ses sağlığı özet kartlarını, son-turn latency tablosunu ve kalıcı turn'lerden ile ses olaylarından türetilen bir kabul-hazırlığı denetim listesini gösteriyor.
- Tarayıcı ses konsolu artık hafif kurtarma durumunu kalıcı olarak kaydediyor ve beklenmedik oda bağlantı kesilmelerinden sonra resume-token yeniden bağlantılarını otomatik olarak yeniden deniyor; manuel durdurma/session-sonlandırma eylemleri ise kurtarmayı devre dışı bırakıyor.

Kalan:

- Eşzamanlılık/yük kanıtı, yeniden başlatma kurtarması, güvenlik/saklama politikası ve production operasyonları runbook'ları henüz tamamlanmadı.

## Milestone 12 — Doğal Dil İnceleme Derleyicisi

Durum: tamamlandı.

Yerel olarak doğrulandı:

- Deterministik Türkçe/Almanca kurallar, supervisor notlarını `product_fact_correction`, `missing_step`, `wrong_next_action` veya `tone_correction` olarak derliyor.
- Fiyat/deneme ve link-security düzeltmeleri, agent-backend'in yetkili `product_facts.py` şablonlarını kullanıyor.
- Review & Train, düzenlenebilir orijinal-ve-önerilen önizlemesi render ediyor.
- Onay, isteğe bağlı düzeltme belleği ve eğitim candidate'i oluşturma ile mevcut düzeltme işlemini kullanıyor.
- Reddetme veya eşleşmeyen bir not, kalıcı düzeltme veya eğitim verisi üretmiyor.