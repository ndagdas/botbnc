# Para Akışı Gözlemcisi v1

Mevcut işlem botundan bağımsız, emir göndermeyen araştırma servisi. Binance'ın güncel exchangeInfo listesindeki bütün TRADING / USDT / PERPETUAL pariteler rapora girer. İlk üç veya ilk beş sınırı yoktur. Kripto dışı perpetual ürünler tür etiketiyle görünür. Veri alamayan parite rapordan sessizce çıkarılmaz.

## Ölçüm

- Tamamlanmış saatlerden son 24 saat ve son 7 gün; bunlar takvim günlük/haftalık mumları değildir. Bütün paritelerde tarama başlangıcındaki aynı saat sınırı kullanılır.
- Gerçekleşmiş USDT işlem tutarı ve taker-buy quote tutarı. Satış = toplam − taker alış; fark = alış − satış. Bu fark dışarıdan yatırılan net para değildir.
- Spot ayrı, futures ayrı ölçülür. Spot eşleşmesi olmayan coin futures-only etiketi alır. 1000-token futures eşleşmesi açık biçimde kaydedilir.
- OI değişimi dolar tutarı yerine base-asset miktarından ölçülür. OI puan vermez; yön veya teminat miktarı olarak sunulmaz. Funding ek bağlamdır.
- Puan 0–100 arasında sabit araştırma kurallarıyla oluşturulur; başarı olasılığı değildir. Haber, balina ve grup kaynakları henüz bağlı değildir ve eksik veri olarak açıkça gösterilir.

## 24 saat takip

Tarama bitişinden sonraki ilk tam 1 dakikalık mumun açılışı referanstır. Takip 1440 adet tam mum kapsar. Bu varsayımsal referans gerçekleşmiş işlem veya P&L değildir. Her başarılı veri satırı izlenir; düşük puanlılar da dahil.

Varsayılan pump eşiği %20, araştırma stop eşiği %3. Her ikisi ortam değişkeniyle ayarlanabilir. Pump görüldü mü, maksimum yükseliş/düşüş, ilk pump/stop mumu ve hangisinin önce olduğu kaydedilir. Aynı mumda her iki eşik görülürse SAME_BAR_UNCERTAIN yazılır; sıralama uydurulmaz. Gerçek emir/stop yoktur. Boşluklu mum geçmişi başarı/başarısızlık olarak tamamlanmaz. SQLite/PostgreSQL cursor sayesinde kapanma sonrası takip kalan yerden devam eder.

## Başlatma

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
python3 flow_observer.py scan --db flow.sqlite3 --output reports
python3 flow_observer.py worker --db flow.sqlite3 --output reports
```

Yerel kod günlük yeni tüm-piyasa taraması, beş dakikada bir takip çalıştırır. HTTP 403/418/429/451 erişim engelinde otomatik deneme döngüsü durur; kaynak BLOCKED olarak görünür. Alternatif adres/proxy ile engel aşılmaz.

Heroku için bağımsız uygulama/branch kullanılmalı. Mevcut botun app.py ve Procfile dosyaları değiştirilmez. Heroku'nun geçici diski takip geçmişini korumaz: kalıcı PostgreSQL FLOW_DATABASE_URL zorunludur. Bu sağlanmadan üretim servisi başlamaz. FLOW_RUN_WORKER=true, FLOW_SCAN_SECONDS=86400, FLOW_FOLLOW_SECONDS=300, FLOW_PUMP_PCT=20, FLOW_RESEARCH_STOP_PCT=3. Tek gunicorn worker kullanılmalı; --preload kullanılmamalı. Uygun sunucu erişimi ve çalışma planı kurulmadan kesintisiz takip aktif değildir.

Ekran /, tüm tablo /api/snapshot, sağlık /health. Yalnız halka açık piyasa metrikleri sunulur; hesap anahtarı kullanılmaz. Telegram'a mesaj göndermek bu sürümde yoktur; bütün liste ekranda/CSV'de görünür.

## Doğrulama

`python3 -m unittest -v test_flow_observer.py`

Canlı tarama başlaması, geçmişte pump tahmini başarısının kanıtlandığı anlamına gelmez. İlk 24 saatlik cohort tamamlanmadan ileri sonuç raporu üretilmez.
