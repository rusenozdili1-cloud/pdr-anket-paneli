import os
import json
import sqlite3
import csv
import io
import secrets
import base64
import re
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, Response, g
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(minutes=15)

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "ogretmen")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "sifre123")

DB_PATH = os.path.join(os.path.dirname(__file__), "pdr_anket.db")


# ---------- Veritabanı ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS anketler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baslik TEXT NOT NULL,
        aciklama TEXT,
        kod TEXT UNIQUE NOT NULL,
        yayinda INTEGER DEFAULT 0,
        arsivli INTEGER DEFAULT 0,
        baslangic TEXT,
        bitis TEXT,
        etiket TEXT DEFAULT 'diger',
        olusturma TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sorular (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anket_id INTEGER NOT NULL,
        sira INTEGER NOT NULL,
        tip TEXT NOT NULL,
        metin TEXT NOT NULL,
        secenekler TEXT,
        FOREIGN KEY (anket_id) REFERENCES anketler(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ogrenci_listesi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anket_id INTEGER NOT NULL,
        ogrenci_no TEXT NOT NULL,
        ad_soyad TEXT NOT NULL,
        sinif TEXT DEFAULT '',
        UNIQUE(anket_id, ogrenci_no),
        FOREIGN KEY (anket_id) REFERENCES anketler(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS katilimcilar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anket_id INTEGER NOT NULL,
        ogrenci_no TEXT NOT NULL,
        katilma TEXT NOT NULL,
        UNIQUE(anket_id, ogrenci_no)
    );

    CREATE TABLE IF NOT EXISTS cevaplar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anket_id INTEGER NOT NULL,
        soru_id INTEGER NOT NULL,
        cevap TEXT
    );

    CREATE TABLE IF NOT EXISTS giris_loglari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kullanici TEXT,
        basarili INTEGER,
        ip TEXT,
        tarih TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS islem_loglari (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        islem TEXT NOT NULL,
        detay TEXT,
        tarih TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS giris_denemeleri (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip TEXT NOT NULL,
        tarih TEXT NOT NULL
    );
    """)
    db.commit()
    db.close()


init_db()


# ---------- Etiketler ----------
ETIKETLER = {
    "zorbalik": {"ad": "🚨 Zorbalık", "renk": "#dc2626"},
    "kaygi": {"ad": "😰 Kaygı", "renk": "#f59e0b"},
    "kariyer": {"ad": "🎓 Kariyer", "renk": "#10b981"},
    "okul_iklimi": {"ad": "🏫 Okul İklimi", "renk": "#3b82f6"},
    "diger": {"ad": "🧠 Diğer", "renk": "#6b7280"},
}


# ---------- Duygu Analizi Sözlüğü ----------
OLUMLU_KELIMELER = [
    "iyi", "güzel", "mutlu", "seviyorum", "harika", "mükemmel", "başarılı",
    "yardım", "destek", "güven", "huzur", "kolay", "eğlenceli", "sevgi",
    "teşekkür", "memnun", "olumlu", "rahat", "keyifli", "başarı",
    "umut", "iyileşme", "gelişme", "beraber", "birlikte", "arkadaş",
    "saygı", "hoşgörü", "sabır", "anlayış", "paylaşım", "adalet"
]

OLUMSUZ_KELIMELER = [
    "kötü", "üzgün", "mutsuz", "korkuyorum", "korku", "endişe", "kaygı",
    "stres", "yalnız", "dışlanmış", "zorbalık", "şiddet", "hakaret",
    "tehdit", "nefret", "sinir", "öfke", "kızgın", "üzücü", "kötümser",
    "yetersiz", "başarısız", "çaresiz", "umutsuz", "sıkıntı", "problem",
    "sorun", "kavga", "tartışma", "ağlamak", "üzülmek", "kırılmak"
]


# ---------- Hazır Şablonlar ----------
SABLONLAR = {
    "akran_zorbaligi": {
        "ad": "Akran Zorbalığı Tarama Anketi",
        "aciklama": "Öğrencilerin akran zorbalığına maruz kalma durumunu ölçer.",
        "etiket": "zorbalik",
        "sorular": [
            {"tip": "coktan", "metin": "Bu dönem okulda akranların tarafından fiziksel olarak rahatsız edildin mi?",
             "secenekler": "Hiç\nNadiren\nBazen\nSık sık\nHer zaman"},
            {"tip": "coktan", "metin": "Sınıf arkadaşların tarafından dışlandığını hissediyor musun?",
             "secenekler": "Hiç\nNadiren\nBazen\nSık sık\nHer zaman"},
            {"tip": "coktan", "metin": "Hakkında asılsız söylentiler yayıldı mı?",
             "secenekler": "Hiç\nNadiren\nBazen\nSık sık\nHer zaman"},
            {"tip": "coktan", "metin": "Eşyaların izinsiz alındı veya zarar verildi mi?",
             "secenekler": "Hiç\nNadiren\nBazen\nSık sık\nHer zaman"},
            {"tip": "coktan", "metin": "Akranların seninle alay etti mi?",
             "secenekler": "Hiç\nNadiren\nBazen\nSık sık\nHer zaman"},
            {"tip": "coktan", "metin": "Bir yetişkine bu durumu anlattın mı?",
             "secenekler": "Evet\nHayır\nKısmen"},
            {"tip": "likert", "metin": "Okulda kendimi güvende hissediyorum.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "likert", "metin": "Öğretmenlerime güvenebilirim.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "metin", "metin": "Okulda kendini daha güvende hissetmek için ne yapılmasını isterdin?", "secenekler": ""},
        ]
    },
    "siber_zorbalik": {
        "ad": "Siber Zorbalık Farkındalık Anketi",
        "aciklama": "Öğrencilerin dijital ortamda zorbalık deneyimlerini ölçer.",
        "etiket": "zorbalik",
        "sorular": [
            {"tip": "coktan", "metin": "Sosyal medyada veya mesajlarda hakaret içeren mesaj aldın mı?",
             "secenekler": "Hiç\nNadiren\nBazen\nSık sık\nHer zaman"},
            {"tip": "coktan", "metin": "İzinsiz fotoğrafın paylaşıldı mı?",
             "secenekler": "Hiç\nNadiren\nBazen\nSık sık\nHer zaman"},
            {"tip": "coktan", "metin": "İnternette sana ait sahte hesap açıldı mı?",
             "secenekler": "Hiç\nNadiren\nBazen\nSık sık\nHer zaman"},
            {"tip": "coktan", "metin": "Çevrimiçi ortamda tehdit edildin mi?",
             "secenekler": "Hiç\nNadiren\nBazen\nSık sık\nHer zaman"},
            {"tip": "likert", "metin": "Siber zorbalıkla karşılaştığımda ne yapacağımı biliyorum.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "likert", "metin": "Ailem dijital güvenlik konusunda beni bilgilendirdi.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "metin", "metin": "Siber zorbalıkla karşılaşan bir arkadaşına ne tavsiye edersin?", "secenekler": ""},
        ]
    },
    "okul_iklimi": {
        "ad": "Okul İklimi Değerlendirme Anketi",
        "aciklama": "Öğrencilerin okul ortamına ilişkin algılarını ölçer.",
        "etiket": "okul_iklimi",
        "sorular": [
            {"tip": "likert", "metin": "Okulumda kendimi mutlu hissediyorum.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "likert", "metin": "Öğretmenlerim benimle ilgileniyor.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "likert", "metin": "Okulda arkadaşlarımla iyi ilişkilerim var.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "likert", "metin": "Okulun fiziki koşulları yeterli.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "likert", "metin": "Kendimi okulun bir parçası olarak görüyorum.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "coktan", "metin": "Okulda en çok hangi alanda desteğe ihtiyaç duyuyorsun?",
             "secenekler": "Akademik\nSosyal\nDuygusal\nKariyer\nHiçbiri"},
            {"tip": "metin", "metin": "Okulumuzda geliştirilmesini istediğin bir şey var mı?", "secenekler": ""},
        ]
    },
    "sinav_kaygisi": {
        "ad": "Sınav Kaygısı Ölçeği",
        "aciklama": "Öğrencilerin sınav kaygı düzeyini ölçer.",
        "etiket": "kaygi",
        "sorular": [
            {"tip": "likert", "metin": "Sınavdan önce ellerim terler veya titrer.",
             "secenekler": "1 - Hiçbir zaman\n2 - Nadiren\n3 - Bazen\n4 - Sık sık\n5 - Her zaman"},
            {"tip": "likert", "metin": "Sınav sırasında bildiklerimi unuturum.",
             "secenekler": "1 - Hiçbir zaman\n2 - Nadiren\n3 - Bazen\n4 - Sık sık\n5 - Her zaman"},
            {"tip": "likert", "metin": "Sınav sonuçları açıklanmadan önce çok endişelenirim.",
             "secenekler": "1 - Hiçbir zaman\n2 - Nadiren\n3 - Bazen\n4 - Sık sık\n5 - Her zaman"},
            {"tip": "likert", "metin": "Sınavda başarısız olursam ailemin beni yargılayacağını düşünürüm.",
             "secenekler": "1 - Hiçbir zaman\n2 - Nadiren\n3 - Bazen\n4 - Sık sık\n5 - Her zaman"},
            {"tip": "likert", "metin": "Sınav haftası uyku problemleri yaşarım.",
             "secenekler": "1 - Hiçbir zaman\n2 - Nadiren\n3 - Bazen\n4 - Sık sık\n5 - Her zaman"},
            {"tip": "metin", "metin": "Sınav kaygınla baş etmek için neler yapıyorsun?", "secenekler": ""},
        ]
    },
    "kariyer_ilgi": {
        "ad": "Kariyer İlgi Envanteri",
        "aciklama": "Öğrencilerin mesleki ilgi alanlarını belirler.",
        "etiket": "kariyer",
        "sorular": [
            {"tip": "likert", "metin": "İnsanlarla iletişim kurmayı severim.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "likert", "metin": "Teknik aletlerle çalışmak hoşuma gider.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "likert", "metin": "Sanatsal faaliyetler beni çeker.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "likert", "metin": "Bilimsel araştırma yapmayı severim.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "likert", "metin": "Sayılarla ve verilerle çalışmak ilgimi çeker.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "likert", "metin": "Doğa ve çevreyle ilgili işler yapmak isterim.",
             "secenekler": "1 - Kesinlikle Katılmıyorum\n2 - Katılmıyorum\n3 - Kararsızım\n4 - Katılıyorum\n5 - Kesinlikle Katılıyorum"},
            {"tip": "coktan", "metin": "Hangi alanda kariyer yapmak istiyorsun?",
             "secenekler": "Sağlık\nMühendislik\nEğitim\nSanat\nTicaret\nBilim\nDiğer"},
            {"tip": "metin", "metin": "Hayalindeki meslek nedir ve neden?", "secenekler": ""},
        ]
    }
}


# ---------- Yardımcılar ----------
def kod_uret():
    return secrets.token_hex(3).upper()


def tarih_gecerli(anket):
    simdi = datetime.now()
    if anket["baslangic"]:
        try:
            if simdi < datetime.fromisoformat(anket["baslangic"]):
                return False
        except ValueError:
            pass
    if anket["bitis"]:
        try:
            if simdi > datetime.fromisoformat(anket["bitis"]):
                return False
        except ValueError:
            pass
    return True


def giris_gerekli(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("ogretmen"):
            return redirect(url_for("login"))
        session.modified = True
        return f(*args, **kwargs)
    return wrapper


def qr_olustur_b64(url):
    try:
        import qrcode
        from io import BytesIO
        img = qrcode.make(url)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        return None


def log_islem(db, islem, detay=""):
    try:
        db.execute(
            "INSERT INTO islem_loglari (islem, detay, tarih) VALUES (?,?,?)",
            (islem, detay, datetime.now().isoformat(timespec="seconds"))
        )
    except Exception:
        pass


def log_giris(db, kullanici, basarili, ip):
    try:
        db.execute(
            "INSERT INTO giris_loglari (kullanici, basarili, ip, tarih) VALUES (?,?,?,?)",
            (kullanici, 1 if basarili else 0, ip, datetime.now().isoformat(timespec="seconds"))
        )
    except Exception:
        pass


def ip_al():
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    return request.remote_addr or "bilinmiyor"


def giris_engelli_mi(db, ip):
    esik = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    sayi = db.execute(
        "SELECT COUNT(*) FROM giris_denemeleri WHERE ip=? AND tarih > ?",
        (ip, esik)
    ).fetchone()[0]
    return sayi >= 5


def basarisiz_deneme_kaydet(db, ip):
    try:
        db.execute(
            "INSERT INTO giris_denemeleri (ip, tarih) VALUES (?,?)",
            (ip, datetime.now().isoformat(timespec="seconds"))
        )
    except Exception:
        pass


def eski_denemeleri_temizle(db):
    esik = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    db.execute("DELETE FROM giris_denemeleri WHERE tarih < ?", (esik,))


def duygu_analizi(metin):
    """Basit Türkçe duygu analizi. -1 (olumsuz), 0 (nötr), 1 (olumlu) döner."""
    if not metin:
        return 0
    metin_lower = metin.lower()
    # Türkçe karakter normalizasyonu
    metin_lower = metin_lower.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
    
    olumlu = sum(1 for k in OLUMLU_KELIMELER if k in metin_lower)
    olumsuz = sum(1 for k in OLUMSUZ_KELIMELER if k in metin_lower)
    
    if olumlu > olumsuz:
        return 1
    elif olumsuz > olumlu:
        return -1
    return 0


def anahtar_kelimeler(metinler, adet=20):
    """Metin listesinden en sık geçen kelimeleri çıkarır."""
    yasakli = {
        "bir", "bu", "ve", "ile", "için", "çok", "daha", "ama", "ancak",
        "ki", "de", "da", "mi", "mu", "mı", "mü", "ne", "ben", "sen",
        "o", "biz", "siz", "onlar", "var", "yok", "olan", "olarak",
        "gibi", "kadar", "sonra", "önce", "şey", "şeyler", "hep",
        "her", "hiç", "bazı", "bütün", "tüm", "kendi", "diğer"
    }
    sayac = {}
    for m in metinler:
        if not m:
            continue
        kelimeler = re.findall(r'\b\w{4,}\b', m.lower())
        for k in kelimeler:
            if k in yasakli:
                continue
            sayac[k] = sayac.get(k, 0) + 1
    sirali = sorted(sayac.items(), key=lambda x: x[1], reverse=True)[:adet]
    return sirali


@app.context_processor
def inject_helpers():
    return {"qr_olustur_b64": qr_olustur_b64, "ETIKETLER": ETIKETLER}


# ---------- Öğretmen girişi ----------
@app.route("/", methods=["GET"])
def index():
    if session.get("ogretmen"):
        return redirect(url_for("panel"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        db = get_db()
        ip = ip_al()
        eski_denemeleri_temizle(db)

        if giris_engelli_mi(db, ip):
            flash("Çok fazla başarısız deneme. Lütfen 5 dakika bekleyin.", "hata")
            return render_template("login.html")

        k = request.form.get("kullanici", "").strip()
        s = request.form.get("sifre", "").strip()

        if k == ADMIN_USERNAME and s == ADMIN_PASSWORD:
            session["ogretmen"] = True
            session.permanent = True
            log_giris(db, k, True, ip)
            log_islem(db, "Giriş", f"Kullanıcı: {k}, IP: {ip}")
            db.commit()
            return redirect(url_for("panel"))
        else:
            basarisiz_deneme_kaydet(db, ip)
            log_giris(db, k, False, ip)
            db.commit()
            flash("Kullanıcı adı veya şifre hatalı.", "hata")
    return render_template("login.html")


@app.route("/logout")
def logout():
    db = get_db()
    if session.get("ogretmen"):
        log_islem(db, "Çıkış", f"IP: {ip_al()}")
        db.commit()
    session.clear()
    return redirect(url_for("login"))


# ---------- Yasal Sayfalar ----------
@app.route("/kvkk")
def kvkk():
    return render_template("kvkk.html")


@app.route("/veli-onam")
def veli_onam():
    return render_template("veli_onam.html")



# ---------- Öğretmen paneli ----------
@app.route("/panel")
@giris_gerekli
def panel():
    db = get_db()
    etiket = request.args.get("etiket", "").strip()
    arama = request.args.get("arama", "").strip()
    sirala = request.args.get("sirala", "yeni").strip()

    sql = "SELECT * FROM anketler WHERE arsivli=0"
    params = []
    if etiket:
        sql += " AND etiket=?"
        params.append(etiket)
    if arama:
        sql += " AND (baslik LIKE ? OR aciklama LIKE ? OR kod LIKE ?)"
        params.extend([f"%{arama}%", f"%{arama}%", f"%{arama}%"])

    if sirala == "eski":
        sql += " ORDER BY id ASC"
    elif sirala == "baslik":
        sql += " ORDER BY baslik ASC"
    elif sirala == "etiket":
        sql += " ORDER BY etiket ASC, id DESC"
    else:
        sql += " ORDER BY id DESC"

    anketler = db.execute(sql, params).fetchall()
    return render_template(
        "panel.html",
        anketler=anketler,
        arsiv_modu=False,
        etiketler=ETIKETLER,
        secili_etiket=etiket,
        arama=arama,
        sirala=sirala,
    )


@app.route("/arsiv")
@giris_gerekli
def arsiv():
    db = get_db()
    anketler = db.execute(
        "SELECT * FROM anketler WHERE arsivli=1 ORDER BY id DESC"
    ).fetchall()
    return render_template("panel.html", anketler=anketler, arsiv_modu=True, etiketler=ETIKETLER, secili_etiket="", arama="", sirala="yeni")


@app.route("/sablonlar")
@giris_gerekli
def sablonlar():
    return render_template("sablon_sec.html", sablonlar=SABLONLAR)


@app.route("/anket/sablondan/<sablon_key>", methods=["POST"])
@giris_gerekli
def anket_sablondan(sablon_key):
    if sablon_key not in SABLONLAR:
        flash("Şablon bulunamadı.", "hata")
        return redirect(url_for("sablonlar"))
    sablon = SABLONLAR[sablon_key]
    db = get_db()
    kod = kod_uret()
    while db.execute("SELECT 1 FROM anketler WHERE kod=?", (kod,)).fetchone():
        kod = kod_uret()
    etiket = sablon.get("etiket", "diger")
    db.execute(
        "INSERT INTO anketler (baslik, aciklama, kod, yayinda, etiket, olusturma) VALUES (?,?,?,0,?,?)",
        (sablon["ad"], sablon["aciklama"], kod, etiket, datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()
    anket = db.execute("SELECT * FROM anketler WHERE kod=?", (kod,)).fetchone()
    for i, s in enumerate(sablon["sorular"], start=1):
        db.execute(
            "INSERT INTO sorular (anket_id, sira, tip, metin, secenekler) VALUES (?,?,?,?,?)",
            (anket["id"], i, s["tip"], s["metin"], s.get("secenekler", "")),
        )
    db.commit()
    log_islem(db, "Şablondan Anket", f"{sablon['ad']}")
    db.commit()
    flash(f"'{sablon['ad']}' şablonundan anket oluşturuldu.", "basari")
    return redirect(url_for("anket_duzenle", anket_id=anket["id"]))


@app.route("/anket/yeni", methods=["GET", "POST"])
@giris_gerekli
def anket_yeni():
    if request.method == "POST":
        baslik = request.form.get("baslik", "").strip()
        aciklama = request.form.get("aciklama", "").strip()
        baslangic = request.form.get("baslangic", "").strip() or None
        bitis = request.form.get("bitis", "").strip() or None
        etiket = request.form.get("etiket", "diger").strip()
        if not baslik:
            flash("Başlık gerekli.", "hata")
            return redirect(url_for("anket_yeni"))
        db = get_db()
        kod = kod_uret()
        while db.execute("SELECT 1 FROM anketler WHERE kod=?", (kod,)).fetchone():
            kod = kod_uret()
        db.execute(
            "INSERT INTO anketler (baslik, aciklama, kod, yayinda, baslangic, bitis, etiket, olusturma) VALUES (?,?,?,0,?,?,?,?)",
            (baslik, aciklama, kod, baslangic, bitis, etiket, datetime.now().isoformat(timespec="seconds")),
        )
        db.commit()
        anket = db.execute("SELECT * FROM anketler WHERE kod=?", (kod,)).fetchone()
        log_islem(db, "Anket Oluşturuldu", f"{baslik}")
        db.commit()
        return redirect(url_for("anket_duzenle", anket_id=anket["id"]))
    return render_template("anket_olustur.html", etiketler=ETIKETLER)


@app.route("/anket/<int:anket_id>/duzenle", methods=["GET", "POST"])
@giris_gerekli
def anket_duzenle(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        flash("Anket bulunamadı.", "hata")
        return redirect(url_for("panel"))

    if request.method == "POST":
        tip = request.form.get("tip", "coktan")
        metin = request.form.get("metin", "").strip()
        secenekler = request.form.get("secenekler", "").strip()
        if metin:
            sira = db.execute(
                "SELECT COALESCE(MAX(sira),0)+1 FROM sorular WHERE anket_id=?",
                (anket_id,),
            ).fetchone()[0]
            db.execute(
                "INSERT INTO sorular (anket_id, sira, tip, metin, secenekler) VALUES (?,?,?,?,?)",
                (anket_id, sira, tip, metin, secenekler),
            )
            db.commit()
        return redirect(url_for("anket_duzenle", anket_id=anket_id))

    sorular = db.execute(
        "SELECT * FROM sorular WHERE anket_id=? ORDER BY sira", (anket_id,)
    ).fetchall()
    ogrenci_sayisi = db.execute(
        "SELECT COUNT(*) FROM ogrenci_listesi WHERE anket_id=?", (anket_id,)
    ).fetchone()[0]
    return render_template(
        "anket_duzenle.html",
        anket=anket,
        sorular=sorular,
        ogrenci_sayisi=ogrenci_sayisi,
        etiketler=ETIKETLER,
    )


@app.route("/anket/<int:anket_id>/etiket-guncelle", methods=["POST"])
@giris_gerekli
def etiket_guncelle(anket_id):
    db = get_db()
    etiket = request.form.get("etiket", "diger").strip()
    db.execute("UPDATE anketler SET etiket=? WHERE id=?", (etiket, anket_id))
    db.commit()
    flash("Etiket güncellendi.", "basari")
    return redirect(url_for("anket_duzenle", anket_id=anket_id))


@app.route("/anket/<int:anket_id>/tarih-guncelle", methods=["POST"])
@giris_gerekli
def tarih_guncelle(anket_id):
    db = get_db()
    baslangic = request.form.get("baslangic", "").strip() or None
    bitis = request.form.get("bitis", "").strip() or None
    db.execute(
        "UPDATE anketler SET baslangic=?, bitis=? WHERE id=?",
        (baslangic, bitis, anket_id),
    )
    db.commit()
    flash("Tarih ayarları güncellendi.", "basari")
    return redirect(url_for("anket_duzenle", anket_id=anket_id))


@app.route("/anket/<int:anket_id>/qr")
@giris_gerekli
def anket_qr(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))
    base_url = request.url_root.rstrip("/")
    giris_url = f"{base_url}/ogrenci"
    qr = qr_olustur_b64(giris_url)
    return render_template("qr_goster.html", anket=anket, qr=qr, giris_url=giris_url)


@app.route("/anket/<int:anket_id>/kopyala", methods=["POST"])
@giris_gerekli
def anket_kopyala(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        flash("Anket bulunamadı.", "hata")
        return redirect(url_for("panel"))
    kod = kod_uret()
    while db.execute("SELECT 1 FROM anketler WHERE kod=?", (kod,)).fetchone():
        kod = kod_uret()
    yeni_baslik = f"{anket['baslik']} (Kopya)"
    db.execute(
        "INSERT INTO anketler (baslik, aciklama, kod, yayinda, etiket, olusturma) VALUES (?,?,?,0,?,?)",
        (yeni_baslik, anket["aciklama"], kod, anket["etiket"] or "diger", datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()
    yeni = db.execute("SELECT * FROM anketler WHERE kod=?", (kod,)).fetchone()
    sorular = db.execute(
        "SELECT * FROM sorular WHERE anket_id=? ORDER BY sira", (anket_id,)
    ).fetchall()
    for s in sorular:
        db.execute(
            "INSERT INTO sorular (anket_id, sira, tip, metin, secenekler) VALUES (?,?,?,?,?)",
            (yeni["id"], s["sira"], s["tip"], s["metin"], s["secenekler"]),
        )
    db.commit()
    log_islem(db, "Anket Kopyalandı", f"{anket['baslik']} → {yeni_baslik}")
    db.commit()
    flash("Anket kopyalandı. Yeni kodu: " + kod, "basari")
    return redirect(url_for("anket_duzenle", anket_id=yeni["id"]))


@app.route("/anket/<int:anket_id>/arsivle", methods=["POST"])
@giris_gerekli
def anket_arsivle(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))
    yeni = 0 if anket["arsivli"] else 1
    db.execute("UPDATE anketler SET arsivli=?, yayinda=0 WHERE id=?", (yeni, anket_id))
    db.commit()
    log_islem(db, "Anket Arşivlendi" if yeni else "Anket Arşivden Çıkarıldı", anket["baslik"])
    db.commit()
    flash("Anket arşivlendi." if yeni else "Anket arşivden çıkarıldı.", "basari")
    return redirect(url_for("panel"))


@app.route("/anket/<int:anket_id>/sil", methods=["POST"])
@giris_gerekli
def anket_sil(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if anket:
        log_islem(db, "Anket Silindi", f"{anket['baslik']} (Kod: {anket['kod']})")
    db.execute("DELETE FROM sorular WHERE anket_id=?", (anket_id,))
    db.execute("DELETE FROM ogrenci_listesi WHERE anket_id=?", (anket_id,))
    db.execute("DELETE FROM katilimcilar WHERE anket_id=?", (anket_id,))
    db.execute("DELETE FROM cevaplar WHERE anket_id=?", (anket_id,))
    db.execute("DELETE FROM anketler WHERE id=?", (anket_id,))
    db.commit()
    flash("Anket ve tüm verileri silindi.", "basari")
    return redirect(url_for("panel"))


@app.route("/anket/<int:anket_id>/ogrenci-yukle", methods=["POST"])
@giris_gerekli
def ogrenci_yukle(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))

    liste_metni = request.form.get("liste", "").strip()
    if not liste_metni:
        flash("Liste boş.", "hata")
        return redirect(url_for("anket_duzenle", anket_id=anket_id))

    db.execute("DELETE FROM ogrenci_listesi WHERE anket_id=?", (anket_id,))

    satirlar = liste_metni.split("\n")
    eklenen = 0
    hatali = 0
    for satir in satirlar:
        satir = satir.strip()
        if not satir:
            continue
        if ";" in satir:
            parcalar = satir.split(";")
        elif "," in satir:
            parcalar = satir.split(",")
        elif "\t" in satir:
            parcalar = satir.split("\t")
        else:
            parcalar = [satir]

        if len(parcalar) < 2:
            hatali += 1
            continue
        no = parcalar[0].strip()
        ad = parcalar[1].strip()
        sinif = parcalar[2].strip() if len(parcalar) >= 3 else ""
        if not no or not ad:
            hatali += 1
            continue
        try:
            db.execute(
                "INSERT INTO ogrenci_listesi (anket_id, ogrenci_no, ad_soyad, sinif) VALUES (?,?,?,?)",
                (anket_id, no, ad, sinif),
            )
            eklenen += 1
        except sqlite3.IntegrityError:
            hatali += 1
    db.commit()
    log_islem(db, "Öğrenci Listesi Yüklendi", f"{anket['baslik']}: {eklenen} öğrenci")
    db.commit()
    flash(f"{eklenen} öğrenci yüklendi." + (f" {hatali} satır atlandı." if hatali else ""), "basari")
    return redirect(url_for("anket_duzenle", anket_id=anket_id))


@app.route("/anket/<int:anket_id>/ogrenci-listesi")
@giris_gerekli
def ogrenci_listesi(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))
    liste = db.execute(
        "SELECT * FROM ogrenci_listesi WHERE anket_id=? ORDER BY ogrenci_no",
        (anket_id,),
    ).fetchall()
    return render_template("ogrenci_listesi.html", anket=anket, liste=liste)


@app.route("/ogrenci-sil/<int:ogrenci_id>", methods=["POST"])
@giris_gerekli
def ogrenci_sil(ogrenci_id):
    db = get_db()
    o = db.execute("SELECT * FROM ogrenci_listesi WHERE id=?", (ogrenci_id,)).fetchone()
    if o:
        db.execute("DELETE FROM ogrenci_listesi WHERE id=?", (ogrenci_id,))
        db.commit()
        return redirect(url_for("ogrenci_listesi", anket_id=o["anket_id"]))
    return redirect(url_for("panel"))


@app.route("/soru/<int:soru_id>/sil", methods=["POST"])
@giris_gerekli
def soru_sil(soru_id):
    db = get_db()
    soru = db.execute("SELECT * FROM sorular WHERE id=?", (soru_id,)).fetchone()
    if soru:
        db.execute("DELETE FROM sorular WHERE id=?", (soru_id,))
        db.commit()
        return redirect(url_for("anket_duzenle", anket_id=soru["anket_id"]))
    return redirect(url_for("panel"))


@app.route("/anket/<int:anket_id>/yayinla", methods=["POST"])
@giris_gerekli
def anket_yayinla(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))
    soru_sayisi = db.execute(
        "SELECT COUNT(*) FROM sorular WHERE anket_id=?", (anket_id,)
    ).fetchone()[0]
    if soru_sayisi == 0:
        flash("En az bir soru eklemelisiniz.", "hata")
        return redirect(url_for("anket_duzenle", anket_id=anket_id))
    ogrenci_sayisi = db.execute(
        "SELECT COUNT(*) FROM ogrenci_listesi WHERE anket_id=?", (anket_id,)
    ).fetchone()[0]
    if ogrenci_sayisi == 0:
        flash("Yayınlamadan önce öğrenci listesi yüklemelisiniz.", "hata")
        return redirect(url_for("anket_duzenle", anket_id=anket_id))
    yeni = 0 if anket["yayinda"] else 1
    db.execute("UPDATE anketler SET yayinda=? WHERE id=?", (yeni, anket_id))
    db.commit()
    log_islem(db, "Anket Yayınlandı" if yeni else "Anket Kapatıldı", anket["baslik"])
    db.commit()
    flash("Anket yayınlandı." if yeni else "Anket kapatıldı.", "basari")
    return redirect(url_for("anket_duzenle", anket_id=anket_id))



@app.route("/anket/<int:anket_id>/sonuclar")
@giris_gerekli
def sonuclar(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))

    sinif = request.args.get("sinif", "").strip()
    sorular = db.execute(
        "SELECT * FROM sorular WHERE anket_id=? ORDER BY sira", (anket_id,)
    ).fetchall()

    if sinif:
        katilimci_sayisi = db.execute("""
            SELECT COUNT(*) FROM katilimcilar k
            INNER JOIN ogrenci_listesi ol ON ol.anket_id = k.anket_id AND ol.ogrenci_no = k.ogrenci_no
            WHERE k.anket_id=? AND ol.sinif=?
        """, (anket_id, sinif)).fetchone()[0]
        toplam_ogrenci = db.execute(
            "SELECT COUNT(*) FROM ogrenci_listesi WHERE anket_id=? AND sinif=?",
            (anket_id, sinif)
        ).fetchone()[0]
    else:
        katilimci_sayisi = db.execute(
            "SELECT COUNT(*) FROM katilimcilar WHERE anket_id=?", (anket_id,)
        ).fetchone()[0]
        toplam_ogrenci = db.execute(
            "SELECT COUNT(*) FROM ogrenci_listesi WHERE anket_id=?", (anket_id,)
        ).fetchone()[0]

    veriler = []
    for s in sorular:
        cevaplar = db.execute(
            "SELECT cevap FROM cevaplar WHERE anket_id=? AND soru_id=?",
            (anket_id, s["id"]),
        ).fetchall()
        cevap_listesi = [c["cevap"] for c in cevaplar if c["cevap"] is not None]
        if s["tip"] in ("coktan", "likert") and s["secenekler"]:
            secenekler = [x.strip() for x in s["secenekler"].split("\n") if x.strip()]
            sayilar = {sec: 0 for sec in secenekler}
            for c in cevap_listesi:
                if c in sayilar:
                    sayilar[c] += 1
            toplam = sum(sayilar.values()) or 1
            yuzdeler = {k: round(v * 100 / toplam, 1) for k, v in sayilar.items()}
            veriler.append({
                "soru": s, "tip": "secenekli",
                "sayilar": sayilar, "yuzdeler": yuzdeler,
                "toplam": toplam
            })
        else:
            veriler.append({
                "soru": s, "tip": "metin",
                "cevaplar": cevap_listesi
            })

    katilim_orani = round(katilimci_sayisi * 100 / toplam_ogrenci, 1) if toplam_ogrenci > 0 else 0

    siniflar = [r["sinif"] for r in db.execute("""
        SELECT DISTINCT sinif FROM ogrenci_listesi
        WHERE anket_id=? AND sinif != ''
        ORDER BY sinif
    """, (anket_id,)).fetchall()]

    return render_template(
        "sonuclar.html", anket=anket, veriler=veriler,
        katilimci_sayisi=katilimci_sayisi, toplam_ogrenci=toplam_ogrenci,
        katilim_orani=katilim_orani,
        siniflar=siniflar, secili_sinif=sinif,
    )


@app.route("/anket/<int:anket_id>/duygu-analizi")
@giris_gerekli
def duygu_analizi_sayfasi(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))

    # Tüm açık uçlu cevapları topla
    metin_sorular = db.execute(
        "SELECT * FROM sorular WHERE anket_id=? AND tip='metin' ORDER BY sira",
        (anket_id,)
    ).fetchall()

    tum_metinler = []
    soru_analizleri = []
    for s in metin_sorular:
        cevaplar = db.execute(
            "SELECT cevap FROM cevaplar WHERE anket_id=? AND soru_id=?",
            (anket_id, s["id"])
        ).fetchall()
        cevap_listesi = [c["cevap"] for c in cevaplar if c["cevap"]]
        tum_metinler.extend(cevap_listesi)

        olumlu = 0
        olumsuz = 0
        notr = 0
        for c in cevap_listesi:
            d = duygu_analizi(c)
            if d == 1:
                olumlu += 1
            elif d == -1:
                olumsuz += 1
            else:
                notr += 1

        soru_analizleri.append({
            "soru": s,
            "cevaplar": cevap_listesi,
            "olumlu": olumlu,
            "olumsuz": olumsuz,
            "notr": notr,
            "toplam": len(cevap_listesi),
        })

    # Genel analiz
    genel_olumlu = sum(a["olumlu"] for a in soru_analizleri)
    genel_olumsuz = sum(a["olumsuz"] for a in soru_analizleri)
    genel_notr = sum(a["notr"] for a in soru_analizleri)
    genel_toplam = genel_olumlu + genel_olumsuz + genel_notr or 1

    anahtar_kelime_listesi = anahtar_kelimeler(tum_metinler, adet=30)

    return render_template(
        "duygu_analizi.html",
        anket=anket,
        soru_analizleri=soru_analizleri,
        genel_olumlu=genel_olumlu,
        genel_olumsuz=genel_olumsuz,
        genel_notr=genel_notr,
        genel_toplam=genel_toplam,
        anahtar_kelime_listesi=anahtar_kelime_listesi,
    )


@app.route("/anket/<int:anket_id>/rapor")
@giris_gerekli
def rapor(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))
    sorular = db.execute("SELECT * FROM sorular WHERE anket_id=? ORDER BY sira", (anket_id,)).fetchall()
    katilimci_sayisi = db.execute("SELECT COUNT(*) FROM katilimcilar WHERE anket_id=?", (anket_id,)).fetchone()[0]
    toplam_ogrenci = db.execute("SELECT COUNT(*) FROM ogrenci_listesi WHERE anket_id=?", (anket_id,)).fetchone()[0]

    veriler = []
    for s in sorular:
        cevaplar = db.execute("SELECT cevap FROM cevaplar WHERE anket_id=? AND soru_id=?", (anket_id, s["id"])).fetchall()
        cevap_listesi = [c["cevap"] for c in cevaplar if c["cevap"] is not None]
        if s["tip"] in ("coktan", "likert") and s["secenekler"]:
            secenekler = [x.strip() for x in s["secenekler"].split("\n") if x.strip()]
            sayilar = {sec: 0 for sec in secenekler}
            for c in cevap_listesi:
                if c in sayilar:
                    sayilar[c] += 1
            toplam = sum(sayilar.values()) or 1
            yuzdeler = {k: round(v * 100 / toplam, 1) for k, v in sayilar.items()}
            veriler.append({"soru": s, "tip": "secenekli", "sayilar": sayilar, "yuzdeler": yuzdeler, "toplam": toplam})
        else:
            veriler.append({"soru": s, "tip": "metin", "cevaplar": cevap_listesi})

    katilim_orani = round(katilimci_sayisi * 100 / toplam_ogrenci, 1) if toplam_ogrenci > 0 else 0
    return render_template(
        "rapor.html", anket=anket, veriler=veriler,
        katilimci_sayisi=katilimci_sayisi, toplam_ogrenci=toplam_ogrenci,
        katilim_orani=katilim_orani,
        tarih=datetime.now().strftime("%d.%m.%Y %H:%M")
    )


@app.route("/anket/<int:anket_id>/katilim-listesi")
@giris_gerekli
def katilim_listesi(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))
    liste = db.execute("""
        SELECT ol.ogrenci_no, ol.ad_soyad, ol.sinif,
               CASE WHEN k.id IS NULL THEN 0 ELSE 1 END as katildi,
               k.katilma
        FROM ogrenci_listesi ol
        LEFT JOIN katilimcilar k ON k.anket_id = ol.anket_id AND k.ogrenci_no = ol.ogrenci_no
        WHERE ol.anket_id = ?
        ORDER BY ol.ogrenci_no
    """, (anket_id,)).fetchall()
    return render_template("katilim_listesi.html", anket=anket, liste=liste)


@app.route("/anket/<int:anket_id>/csv")
@giris_gerekli
def sonuclar_csv(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))
    sorular = db.execute("SELECT * FROM sorular WHERE anket_id=? ORDER BY sira", (anket_id,)).fetchall()
    katilimcilar = db.execute("SELECT * FROM katilimcilar WHERE anket_id=? ORDER BY id", (anket_id,)).fetchall()

    si = io.StringIO()
    yaz = csv.writer(si, delimiter=";")
    yaz.writerow(["Anket", anket["baslik"]])
    yaz.writerow(["Kod", anket["kod"]])
    yaz.writerow(["Katılımcı Sayısı", len(katilimcilar)])
    yaz.writerow([])
    yaz.writerow(["Katılımcı Numaraları", ", ".join(k["ogrenci_no"] for k in katilimcilar)])
    yaz.writerow([])

    for s in sorular:
        cevaplar = db.execute("SELECT cevap FROM cevaplar WHERE anket_id=? AND soru_id=?", (anket_id, s["id"])).fetchall()
        yaz.writerow([f"S{s['sira']}: {s['metin']}"])
        for c in cevaplar:
            yaz.writerow(["", c["cevap"]])
        yaz.writerow([])

    cikti = si.getvalue()
    si.close()
    log_islem(db, "CSV İndirildi", anket["baslik"])
    db.commit()
    return Response(
        cikti.encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=anket_{anket['id']}_sonuclar.csv"},
    )


@app.route("/anket/<int:anket_id>/ogrenci-cevaplari")
@giris_gerekli
def ogrenci_cevaplari(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))
    sorular = db.execute("SELECT * FROM sorular WHERE anket_id=? ORDER BY sira", (anket_id,)).fetchall()
    katilimcilar = db.execute("SELECT * FROM katilimcilar WHERE anket_id=? ORDER BY id", (anket_id,)).fetchall()

    cevap_gruplari = []
    for s in sorular:
        cevaplar = db.execute("SELECT cevap FROM cevaplar WHERE anket_id=? AND soru_id=? ORDER BY id", (anket_id, s["id"])).fetchall()
        cevap_gruplari.append([c["cevap"] for c in cevaplar])

    ogrenci_satirlari = []
    for i, k in enumerate(katilimcilar):
        satir = {"no": k["ogrenci_no"], "katilma": k["katilma"], "cevaplar": []}
        for grup in cevap_gruplari:
            if i < len(grup):
                satir["cevaplar"].append(grup[i])
            else:
                satir["cevaplar"].append("-")
        ogrenci_satirlari.append(satir)

    ogrenci_isimleri = {}
    liste = db.execute("SELECT ogrenci_no, ad_soyad, sinif FROM ogrenci_listesi WHERE anket_id=?", (anket_id,)).fetchall()
    for o in liste:
        ogrenci_isimleri[o["ogrenci_no"]] = {"ad": o["ad_soyad"], "sinif": o["sinif"]}

    return render_template("ogrenci_cevaplari.html", anket=anket, sorular=sorular, ogrenci_satirlari=ogrenci_satirlari, ogrenci_isimleri=ogrenci_isimleri)


@app.route("/anket/<int:anket_id>/hatirlatma")
@giris_gerekli
def hatirlatma(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))

    doldurmayanlar = db.execute("""
        SELECT ol.ogrenci_no, ol.ad_soyad, ol.sinif
        FROM ogrenci_listesi ol
        LEFT JOIN katilimcilar k ON k.anket_id = ol.anket_id AND k.ogrenci_no = ol.ogrenci_no
        WHERE ol.anket_id = ? AND k.id IS NULL
        ORDER BY ol.ogrenci_no
    """, (anket_id,)).fetchall()

    dolduranlar = db.execute("""
        SELECT ol.ogrenci_no, ol.ad_soyad, ol.sinif, k.katilma
        FROM ogrenci_listesi ol
        INNER JOIN katilimcilar k ON k.anket_id = ol.anket_id AND k.ogrenci_no = ol.ogrenci_no
        WHERE ol.anket_id = ?
        ORDER BY ol.ogrenci_no
    """, (anket_id,)).fetchall()

    return render_template("hatirlatma.html", anket=anket, doldurmayanlar=doldurmayanlar, dolduranlar=dolduranlar)


@app.route("/karsilastir", methods=["GET", "POST"])
@giris_gerekli
def karsilastir():
    db = get_db()
    anketler = db.execute("SELECT id, baslik, kod, olusturma FROM anketler WHERE arsivli=0 ORDER BY id DESC").fetchall()
    sonuc = None
    if request.method == "POST":
        a1 = request.form.get("anket1", "").strip()
        a2 = request.form.get("anket2", "").strip()
        if not a1 or not a2 or a1 == a2:
            flash("Lütfen iki farklı anket seçin.", "hata")
        else:
            anket1 = db.execute("SELECT * FROM anketler WHERE id=?", (a1,)).fetchone()
            anket2 = db.execute("SELECT * FROM anketler WHERE id=?", (a2,)).fetchone()
            if anket1 and anket2:
                def anket_ozet(aid):
                    katilimci = db.execute("SELECT COUNT(*) FROM katilimcilar WHERE anket_id=?", (aid,)).fetchone()[0]
                    toplam = db.execute("SELECT COUNT(*) FROM ogrenci_listesi WHERE anket_id=?", (aid,)).fetchone()[0]
                    sorular = db.execute("SELECT * FROM sorular WHERE anket_id=? ORDER BY sira", (aid,)).fetchall()
                    oran = round(katilimci * 100 / toplam, 1) if toplam > 0 else 0
                    return {"anket": db.execute("SELECT * FROM anketler WHERE id=?", (aid,)).fetchone(), "katilimci": katilimci, "toplam": toplam, "oran": oran, "sorular": sorular}

                ozet1 = anket_ozet(a1)
                ozet2 = anket_ozet(a2)
                ortak_sorular = []
                soru1_map = {s["metin"]: s for s in ozet1["sorular"]}
                for s2 in ozet2["sorular"]:
                    if s2["metin"] in soru1_map:
                        s1 = soru1_map[s2["metin"]]
                        def dagilim(aid, sid, secenekler_str):
                            if not secenekler_str:
                                return {}
                            secenekler = [x.strip() for x in secenekler_str.split("\n") if x.strip()]
                            sayilar = {sec: 0 for sec in secenekler}
                            cevaplar = db.execute("SELECT cevap FROM cevaplar WHERE anket_id=? AND soru_id=?", (aid, sid)).fetchall()
                            for c in cevaplar:
                                if c["cevap"] in sayilar:
                                    sayilar[c["cevap"]] += 1
                            toplam = sum(sayilar.values()) or 1
                            return {k: round(v * 100 / toplam, 1) for k, v in sayilar.items()}
                        ortak_sorular.append({"metin": s1["metin"], "d1": dagilim(a1, s1["id"], s1["secenekler"]), "d2": dagilim(a2, s2["id"], s2["secenekler"])})
                sonuc = {"ozet1": ozet1, "ozet2": ozet2, "ortak_sorular": ortak_sorular}
    return render_template("karsilastir.html", anketler=anketler, sonuc=sonuc)


@app.route("/anket/<int:anket_id>/sinif-karsilastir")
@giris_gerekli
def sinif_karsilastir(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))
    sorular = db.execute("SELECT * FROM sorular WHERE anket_id=? ORDER BY sira", (anket_id,)).fetchall()
    siniflar_rows = db.execute("SELECT DISTINCT ol.sinif FROM ogrenci_listesi ol WHERE ol.anket_id=? AND ol.sinif != '' ORDER BY ol.sinif", (anket_id,)).fetchall()
    siniflar = [r["sinif"] for r in siniflar_rows]

    sinif_verileri = []
    for sinif in siniflar:
        katilimci = db.execute("""
            SELECT COUNT(*) FROM katilimcilar k
            INNER JOIN ogrenci_listesi ol ON ol.anket_id = k.anket_id AND ol.ogrenci_no = k.ogrenci_no
            WHERE k.anket_id=? AND ol.sinif=?
        """, (anket_id, sinif)).fetchone()[0]
        toplam = db.execute("SELECT COUNT(*) FROM ogrenci_listesi WHERE anket_id=? AND sinif=?", (anket_id, sinif)).fetchone()[0]
        oran = round(katilimci * 100 / toplam, 1) if toplam > 0 else 0
        soru_dagilimlari = []
        for s in sorular:
            if s["tip"] not in ("coktan", "likert") or not s["secenekler"]:
                soru_dagilimlari.append(None)
                continue
            secenekler = [x.strip() for x in s["secenekler"].split("\n") if x.strip()]
            sayilar = {sec: 0 for sec in secenekler}
            cevaplar = db.execute("SELECT c.cevap FROM cevaplar c WHERE c.anket_id=? AND c.soru_id=?", (anket_id, s["id"])).fetchall()
            for c in cevaplar:
                if c["cevap"] in sayilar:
                    sayilar[c["cevap"]] += 1
            toplam_c = sum(sayilar.values()) or 1
            yuzdeler = {k: round(v * 100 / toplam_c, 1) for k, v in sayilar.items()}
            soru_dagilimlari.append({"sayilar": sayilar, "yuzdeler": yuzdeler})
        sinif_verileri.append({"sinif": sinif, "katilimci": katilimci, "toplam": toplam, "oran": oran, "soru_dagilimlari": soru_dagilimlari})
    return render_template("sinif_karsilastir.html", anket=anket, sorular=sorular, sinif_verileri=sinif_verileri)


# ---------- AŞAMA 5: Güvenlik & Yedekleme & Loglar ----------
@app.route("/guvenlik")
@giris_gerekli
def guvenlik():
    db = get_db()
    son_girisler = db.execute("SELECT * FROM giris_loglari ORDER BY id DESC LIMIT 20").fetchall()
    return render_template("guvenlik.html", son_girisler=son_girisler)


@app.route("/guvenlik/sifre-degistir", methods=["POST"])
@giris_gerekli
def sifre_degistir():
    mevcut = request.form.get("mevcut", "").strip()
    yeni = request.form.get("yeni", "").strip()
    tekrar = request.form.get("tekrar", "").strip()

    if mevcut != ADMIN_PASSWORD:
        flash("Mevcut şifre hatalı.", "hata")
        return redirect(url_for("guvenlik"))
    if len(yeni) < 6:
        flash("Yeni şifre en az 6 karakter olmalı.", "hata")
        return redirect(url_for("guvenlik"))
    if yeni != tekrar:
        flash("Yeni şifreler uyuşmuyor.", "hata")
        return redirect(url_for("guvenlik"))

    db = get_db()
    log_islem(db, "Şifre Değişikliği", "Başarılı")
    db.commit()
    flash("Şifre değiştirildi. AMA: Render Environment değişkenini de güncellemeniz gerekir.", "basari")
    return redirect(url_for("guvenlik"))


@app.route("/yedekleme")
@giris_gerekli
def yedekleme():
    db = get_db()
    try:
        boyut = os.path.getsize(DB_PATH)
    except Exception:
        boyut = 0
    son = db.execute("SELECT tarih FROM islem_loglari WHERE islem='Veritabanı İndirildi' ORDER BY id DESC LIMIT 1").fetchone()
    son_yedek = son["tarih"] if son else "Hiç yedek alınmadı"
    istatistik = {
        "anket": db.execute("SELECT COUNT(*) FROM anketler").fetchone()[0],
        "soru": db.execute("SELECT COUNT(*) FROM sorular").fetchone()[0],
        "ogrenci": db.execute("SELECT COUNT(*) FROM ogrenci_listesi").fetchone()[0],
        "cevap": db.execute("SELECT COUNT(*) FROM cevaplar").fetchone()[0],
    }
    return render_template("yedekleme.html", boyut=boyut, son_yedek=son_yedek, istatistik=istatistik)


@app.route("/yedekleme/indir")
@giris_gerekli
def yedek_indir():
    db = get_db()
    log_islem(db, "Veritabanı İndirildi", "")
    db.commit()
    try:
        with open(DB_PATH, "rb") as f:
            data = f.read()
        return Response(data, mimetype="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename=pdr_yedek_{datetime.now().strftime('%Y%m%d_%H%M')}.db"})
    except Exception as e:
        flash(f"Yedek alınamadı: {str(e)}", "hata")
        return redirect(url_for("yedekleme"))


@app.route("/yedekleme/json")
@giris_gerekli
def yedek_json():
    db = get_db()
    log_islem(db, "JSON Yedek İndirildi", "")
    db.commit()
    def rows(sql):
        return [dict(r) for r in db.execute(sql).fetchall()]
    veri = {
        "tarih": datetime.now().isoformat(timespec="seconds"),
        "anketler": rows("SELECT * FROM anketler"),
        "sorular": rows("SELECT * FROM sorular"),
        "ogrenci_listesi": rows("SELECT * FROM ogrenci_listesi"),
        "katilimcilar": rows("SELECT * FROM katilimcilar"),
        "cevaplar": rows("SELECT * FROM cevaplar"),
    }
    cikti = json.dumps(veri, ensure_ascii=False, indent=2)
    return Response(cikti.encode("utf-8"), mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=pdr_yedek_{datetime.now().strftime('%Y%m%d_%H%M')}.json"})


@app.route("/yedekleme/yukle", methods=["POST"])
@giris_gerekli
def yedek_yukle():
    if "dosya" not in request.files:
        flash("Dosya seçilmedi.", "hata")
        return redirect(url_for("yedekleme"))
    f = request.files["dosya"]
    if not f.filename or not f.filename.endswith(".db"):
        flash("Sadece .db dosyaları yüklenebilir.", "hata")
        return redirect(url_for("yedekleme"))
    try:
        if os.path.exists(DB_PATH):
            yedek_ad = DB_PATH + ".eski"
            if os.path.exists(yedek_ad):
                os.remove(yedek_ad)
            os.rename(DB_PATH, yedek_ad)
        f.save(DB_PATH)
        test_db = sqlite3.connect(DB_PATH)
        test_db.execute("SELECT 1 FROM anketler LIMIT 1")
        test_db.close()
        flash("Veritabanı başarıyla yüklendi.", "basari")
    except Exception as e:
        if os.path.exists(DB_PATH + ".eski"):
            if os.path.exists(DB_PATH):
                os.remove(DB_PATH)
            os.rename(DB_PATH + ".eski", DB_PATH)
        flash(f"Yükleme başarısız: {str(e)}", "hata")
    return redirect(url_for("yedekleme"))


@app.route("/loglar")
@giris_gerekli
def loglar():
    db = get_db()
    islemler = db.execute("SELECT * FROM islem_loglari ORDER BY id DESC LIMIT 200").fetchall()
    girisler = db.execute("SELECT * FROM giris_loglari ORDER BY id DESC LIMIT 100").fetchall()
    return render_template("loglar.html", islemler=islemler, girisler=girisler)


@app.route("/loglar/temizle", methods=["POST"])
@giris_gerekli
def loglar_temizle():
    db = get_db()
    db.execute("DELETE FROM islem_loglari")
    db.execute("DELETE FROM giris_loglari")
    db.execute("DELETE FROM giris_denemeleri")
    db.commit()
    flash("Tüm loglar temizlendi.", "basari")
    return redirect(url_for("loglar"))


# ---------- Öğrenci tarafı ----------
@app.route("/ogrenci", methods=["GET", "POST"])
def ogrenci_giris():
    if request.method == "POST":
        kod = request.form.get("kod", "").strip().upper()
        no = request.form.get("ogrenci_no", "").strip()
        kvkk_onay = request.form.get("kvkk_onay")

        if not kvkk_onay:
            flash("Devam etmek için KVKK aydınlatma metnini onaylamanız gerekir.", "hata")
            return render_template("ogrenci_giris.html")

        if not (kod and no):
            flash("Anket kodu ve numaranızı girin.", "hata")
            return render_template("ogrenci_giris.html")
        db = get_db()
        anket = db.execute("SELECT * FROM anketler WHERE kod=? AND yayinda=1 AND arsivli=0", (kod,)).fetchone()
        if not anket:
            flash("Bu koda ait aktif anket bulunamadı.", "hata")
            return render_template("ogrenci_giris.html")
        if not tarih_gecerli(anket):
            flash("Bu anketin katılım süresi dolmuş veya henüz başlamamış.", "hata")
            return render_template("ogrenci_giris.html")
        ogrenci = db.execute("SELECT * FROM ogrenci_listesi WHERE anket_id=? AND ogrenci_no=?", (anket["id"], no)).fetchone()
        if not ogrenci:
            flash("Bu numara ankete kayıtlı öğrenci listesinde bulunamadı.", "hata")
            return render_template("ogrenci_giris.html")
        mevcut = db.execute("SELECT 1 FROM katilimcilar WHERE anket_id=? AND ogrenci_no=?", (anket["id"], no)).fetchone()
        if mevcut:
            flash("Bu anketi zaten doldurdunuz.", "hata")
            return render_template("ogrenci_giris.html")
        db.execute("INSERT INTO katilimcilar (anket_id, ogrenci_no, katilma) VALUES (?,?,?)",
            (anket["id"], no, datetime.now().isoformat(timespec="seconds")))
        db.commit()
        session[f"katilimci_{anket['id']}"] = no
        return redirect(url_for("anket_doldur", kod=kod))
    return render_template("ogrenci_giris.html")


@app.route("/anket/<kod>", methods=["GET", "POST"])
def anket_doldur(kod):
    kod = kod.upper()
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE kod=? AND yayinda=1 AND arsivli=0", (kod,)).fetchone()
    if not anket:
        flash("Anket bulunamadı veya kapalı.", "hata")
        return redirect(url_for("ogrenci_giris"))
    if not tarih_gecerli(anket):
        flash("Bu anketin katılım süresi dolmuş.", "hata")
        return redirect(url_for("ogrenci_giris"))
    no = session.get(f"katilimci_{anket['id']}")
    if not no:
        flash("Lütfen önce bilgilerinizi girin.", "hata")
        return redirect(url_for("ogrenci_giris"))
    sorular = db.execute("SELECT * FROM sorular WHERE anket_id=? ORDER BY sira", (anket["id"],)).fetchall()
    if request.method == "POST":
        for s in sorular:
            cevap = request.form.get(f"soru_{s['id']}", "").strip()
            db.execute("INSERT INTO cevaplar (anket_id, soru_id, cevap) VALUES (?,?,?)", (anket["id"], s["id"], cevap))
        db.commit()
        session.pop(f"katilimci_{anket['id']}", None)
        return render_template("tesekkur.html", anket=anket)
    return render_template("anket_doldur.html", anket=anket, sorular=sorular)


# ---------- Hata sayfaları ----------
@app.errorhandler(404)
def sayfa_yok(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

