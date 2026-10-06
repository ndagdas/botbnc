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

## İlk arşiv denemesi

1 Ekim 2026 00:00 UTC itibarıyla Eylül ayının resmi Binance arşiviyle 527 parite ölçüldü; ertesi 24 saat 1 dakikalık mumlarla izlendi. 360 paritenin spot verisi de vardı. Bu eski evrenin üyeliği geriye dönük seçildiği için survivorship yanlılığı mümkündür. Güncel liste veya canlı tahmin değildir.

16 parite %20 yükselişe ulaştı; 12'si önce referans fiyattan %3 düşmeden ulaştı. Yüksek puan grubunda 122 pariteden yalnızca biri %20 gördü ve o da önce %3 düştü. Dolayısıyla bu tek denemede yüksek puan seçimi fayda sağlamadı. Puan satın alma/işleme giriş kararı olarak kullanılmamalıdır. Tüm listeyi saklamak bu tür yanlış varsayımları görmemizi sağlar.

Arşiv denemesinde OI, funding, haber, balina ve grup verileri yoktur. %3 araştırma eşiği gerçek stop emri veya kullanıcıya önerilen risk seviyesi değildir. Komisyon, fonlama ve gerçekleşebilir emir dolumu ölçülmediği için bu bir kârlılık backtesti değildir.
