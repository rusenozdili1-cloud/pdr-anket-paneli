import os
import sqlite3
import csv
import io
import secrets
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, Response, g
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

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
    """)
    db.commit()
    db.close()


init_db()


# ---------- Yardımcılar ----------
def kod_uret():
    return secrets.token_hex(3).upper()


def giris_gerekli(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("ogretmen"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ---------- Öğretmen girişi ----------
@app.route("/", methods=["GET"])
def index():
    if session.get("ogretmen"):
        return redirect(url_for("panel"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        k = request.form.get("kullanici", "").strip()
        s = request.form.get("sifre", "").strip()
        if k == ADMIN_USERNAME and s == ADMIN_PASSWORD:
            session["ogretmen"] = True
            return redirect(url_for("panel"))
        flash("Kullanıcı adı veya şifre hatalı.", "hata")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- Öğretmen paneli ----------
@app.route("/panel")
@giris_gerekli
def panel():
    db = get_db()
    anketler = db.execute(
        "SELECT * FROM anketler ORDER BY id DESC"
    ).fetchall()
    return render_template("panel.html", anketler=anketler)


@app.route("/anket/yeni", methods=["GET", "POST"])
@giris_gerekli
def anket_yeni():
    if request.method == "POST":
        baslik = request.form.get("baslik", "").strip()
        aciklama = request.form.get("aciklama", "").strip()
        if not baslik:
            flash("Başlık gerekli.", "hata")
            return redirect(url_for("anket_yeni"))
        db = get_db()
        kod = kod_uret()
        while db.execute("SELECT 1 FROM anketler WHERE kod=?", (kod,)).fetchone():
            kod = kod_uret()
        db.execute(
            "INSERT INTO anketler (baslik, aciklama, kod, yayinda, olusturma) VALUES (?,?,?,0,?)",
            (baslik, aciklama, kod, datetime.now().isoformat(timespec="seconds")),
        )
        db.commit()
        anket = db.execute("SELECT * FROM anketler WHERE kod=?", (kod,)).fetchone()
        return redirect(url_for("anket_duzenle", anket_id=anket["id"]))
    return render_template("anket_olustur.html")


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
    )


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

    # Eski listeyi sil
    db.execute("DELETE FROM ogrenci_listesi WHERE anket_id=?", (anket_id,))

    satirlar = liste_metni.split("\n")
    eklenen = 0
    hatali = 0
    for satir in satirlar:
        satir = satir.strip()
        if not satir:
            continue
        # Ayraç: ; veya , veya TAB
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
        if not no or not ad:
            hatali += 1
            continue
        try:
            db.execute(
                "INSERT INTO ogrenci_listesi (anket_id, ogrenci_no, ad_soyad) VALUES (?,?,?)",
                (anket_id, no, ad),
            )
            eklenen += 1
        except sqlite3.IntegrityError:
            hatali += 1
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
    flash("Anket yayınlandı." if yeni else "Anket kapatıldı.", "basari")
    return redirect(url_for("anket_duzenle", anket_id=anket_id))


@app.route("/anket/<int:anket_id>/sonuclar")
@giris_gerekli
def sonuclar(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))
    sorular = db.execute(
        "SELECT * FROM sorular WHERE anket_id=? ORDER BY sira", (anket_id,)
    ).fetchall()
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

    return render_template(
        "sonuclar.html", anket=anket, veriler=veriler,
        katilimci_sayisi=katilimci_sayisi, toplam_ogrenci=toplam_ogrenci
    )


@app.route("/anket/<int:anket_id>/katilim-listesi")
@giris_gerekli
def katilim_listesi(anket_id):
    db = get_db()
    anket = db.execute("SELECT * FROM anketler WHERE id=?", (anket_id,)).fetchone()
    if not anket:
        return redirect(url_for("panel"))
    liste = db.execute("""
        SELECT ol.ogrenci_no, ol.ad_soyad,
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
    sorular = db.execute(
        "SELECT * FROM sorular WHERE anket_id=? ORDER BY sira", (anket_id,)
    ).fetchall()

    katilimcilar = db.execute(
        "SELECT * FROM katilimcilar WHERE anket_id=? ORDER BY id",
        (anket_id,),
    ).fetchall()

    si = io.StringIO()
    yaz = csv.writer(si, delimiter=";")
    yaz.writerow(["Anket", anket["baslik"]])
    yaz.writerow(["Kod", anket["kod"]])
    yaz.writerow(["Katılımcı Sayısı", len(katilimcilar)])
    yaz.writerow([])
    yaz.writerow(["Katılımcı Numaraları", ", ".join(k["ogrenci_no"] for k in katilimcilar)])
    yaz.writerow([])

    for s in sorular:
        cevaplar = db.execute(
            "SELECT cevap FROM cevaplar WHERE anket_id=? AND soru_id=?",
            (anket_id, s["id"]),
        ).fetchall()
        yaz.writerow([f"S{s['sira']}: {s['metin']}"])
        for c in cevaplar:
            yaz.writerow(["", c["cevap"]])
        yaz.writerow([])

    cikti = si.getvalue()
    si.close()
    return Response(
        cikti.encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=anket_{anket['id']}_sonuclar.csv"},
    )


# ---------- Öğrenci tarafı ----------
@app.route("/ogrenci", methods=["GET", "POST"])
def ogrenci_giris():
    if request.method == "POST":
        kod = request.form.get("kod", "").strip().upper()
        no = request.form.get("ogrenci_no", "").strip()
        if not (kod and no):
            flash("Anket kodu ve numaranızı girin.", "hata")
            return render_template("ogrenci_giris.html")
        db = get_db()
        anket = db.execute(
            "SELECT * FROM anketler WHERE kod=? AND yayinda=1", (kod,)
        ).fetchone()
        if not anket:
            flash("Bu koda ait aktif anket bulunamadı.", "hata")
            return render_template("ogrenci_giris.html")

        # Öğrenci listede var mı?
        ogrenci = db.execute(
            "SELECT * FROM ogrenci_listesi WHERE anket_id=? AND ogrenci_no=?",
            (anket["id"], no),
        ).fetchone()
        if not ogrenci:
            flash("Bu numara ankete kayıtlı öğrenci listesinde bulunamadı.", "hata")
            return render_template("ogrenci_giris.html")

        # Daha önce katılmış mı?
        mevcut = db.execute(
            "SELECT 1 FROM katilimcilar WHERE anket_id=? AND ogrenci_no=?",
            (anket["id"], no),
        ).fetchone()
        if mevcut:
            flash("Bu anketi zaten doldurdunuz.", "hata")
            return render_template("ogrenci_giris.html")

        db.execute(
            "INSERT INTO katilimcilar (anket_id, ogrenci_no, katilma) VALUES (?,?,?)",
            (anket["id"], no, datetime.now().isoformat(timespec="seconds")),
        )
        db.commit()
        session[f"katilimci_{anket['id']}"] = no
        return redirect(url_for("anket_doldur", kod=kod))
    return render_template("ogrenci_giris.html")


@app.route("/anket/<kod>", methods=["GET", "POST"])
def anket_doldur(kod):
    kod = kod.upper()
    db = get_db()
    anket = db.execute(
        "SELECT * FROM anketler WHERE kod=? AND yayinda=1", (kod,)
    ).fetchone()
    if not anket:
        flash("Anket bulunamadı veya kapalı.", "hata")
        return redirect(url_for("ogrenci_giris"))

    no = session.get(f"katilimci_{anket['id']}")
    if not no:
        flash("Lütfen önce bilgilerinizi girin.", "hata")
        return redirect(url_for("ogrenci_giris"))

    sorular = db.execute(
        "SELECT * FROM sorular WHERE anket_id=? ORDER BY sira", (anket["id"],)
    ).fetchall()

    if request.method == "POST":
        for s in sorular:
            cevap = request.form.get(f"soru_{s['id']}", "").strip()
            db.execute(
                "INSERT INTO cevaplar (anket_id, soru_id, cevap) VALUES (?,?,?)",
                (anket["id"], s["id"], cevap),
            )
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
