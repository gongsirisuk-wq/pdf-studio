import os, io, base64
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
import fitz

app = Flask(__name__, static_folder="static")
CORS(app)

# ── Thai font (download Sarabun on startup) ─────────────────
THAI_FONT = None
FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Sarabun-Regular.ttf")
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/sarabun/Sarabun-Regular.ttf"

if not os.path.exists(FONT_PATH):
    try:
        import urllib.request
        urllib.request.urlretrieve(FONT_URL, FONT_PATH)
        print(f"[PDF Studio] Downloaded Sarabun font")
    except Exception as e:
        print(f"[PDF Studio] Font download failed: {e}")

for c in [
    FONT_PATH,
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/tlwg/Garuda.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]:
    if os.path.exists(c):
        THAI_FONT = c
        break
print(f"[PDF Studio] Thai font: {THAI_FONT}")

BUILTIN = {"Helvetica":"helv","Times-Roman":"tiro","Courier":"cour"}

def font_kw(name):
    if name.startswith("Sarabun") and THAI_FONT:
        return {"fontfile": THAI_FONT, "fontname": "ThaiF"}
    return {"fontname": BUILTIN.get(name, "helv")}

def b64_to_pdf(b64): return base64.b64decode(b64)
def pdf_response(doc, name="output.pdf"):
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    doc.close(); buf.seek(0)
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True, download_name=name)

# ── Static ───────────────────────────────────────────────────
@app.route("/")
def index(): return send_from_directory("static", "index.html")

# ── Info ─────────────────────────────────────────────────────
@app.route("/api/info", methods=["POST"])
def api_info():
    doc = fitz.open(stream=b64_to_pdf(request.get_json()["pdf"]), filetype="pdf")
    data = {"page_count": doc.page_count, "pages": []}
    for i in range(doc.page_count):
        pg = doc[i]
        data["pages"].append({"w": pg.rect.width, "h": pg.rect.height, "rotation": pg.rotation})
    doc.close()
    return jsonify(data)

# ── Render page ──────────────────────────────────────────────
@app.route("/api/render", methods=["POST"])
def api_render():
    d = request.get_json()
    doc  = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    pn   = int(d.get("page", 1)) - 1
    sc   = float(d.get("scale", 1.5))
    page = doc[pn]
    pix  = page.get_pixmap(matrix=fitz.Matrix(sc, sc), alpha=False)
    png  = base64.b64encode(pix.tobytes("png")).decode()
    blocks = get_lines(page)
    pc = doc.page_count; doc.close()
    return jsonify({"png": png, "w": pix.width, "h": pix.height,
                    "scale": sc, "blocks": blocks, "page_count": pc})

def get_lines(page):
    words = page.get_text("words")
    if not words: return []
    ld = {}
    for w in words:
        x0,y0,x1,y1,text,bn,ln,wn = w
        k=(bn,ln)
        if k not in ld: ld[k]={"x0":x0,"y0":y0,"x1":x1,"y1":y1,"ws":[]}
        else:
            ld[k]["x0"]=min(ld[k]["x0"],x0); ld[k]["y0"]=min(ld[k]["y0"],y0)
            ld[k]["x1"]=max(ld[k]["x1"],x1); ld[k]["y1"]=max(ld[k]["y1"],y1)
        ld[k]["ws"].append((wn,text))
    lines=[]
    for k,ln in sorted(ld.items()):
        txt=" ".join(w[1] for w in sorted(ln["ws"]))
        fs=12
        d=page.get_text("dict",clip=fitz.Rect(ln["x0"]-1,ln["y0"]-1,ln["x1"]+1,ln["y1"]+1))
        for b in d.get("blocks",[]):
            for sl in b.get("lines",[]):
                for sp in sl.get("spans",[]):
                    if sp["text"].strip(): fs=round(sp["size"],1); break
        lines.append({"text":txt,"x0":round(ln["x0"],2),"y0":round(ln["y0"],2),
                      "x1":round(ln["x1"],2),"y1":round(ln["y1"],2),"fs":fs})
    return lines

# ── Edit wording ─────────────────────────────────────────────
@app.route("/api/edit", methods=["POST"])
def api_edit():
    d   = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    for ed in d.get("edits", []):
        page = doc[int(ed["page"])-1]
        rect = fitz.Rect(ed["x0"],ed["y0"],ed["x1"],ed["y1"])
        page.add_redact_annot(rect, fill=(1,1,1))
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        hex_=ed.get("color","#000000").lstrip("#")
        cr=tuple(int(hex_[i:i+2],16)/255 for i in (0,2,4))
        fk=font_kw(ed.get("font","Sarabun"))
        nt=ed.get("newText") or ed.get("new_text","")
        page.insert_text(fitz.Point(ed["x0"],ed["y1"]),nt,fontsize=float(ed.get("fs",12)),color=cr,**fk)
    return pdf_response(doc,"edited.pdf")

# ── Search ───────────────────────────────────────────────────
@app.route("/api/search", methods=["POST"])
def api_search():
    d     = request.get_json()
    doc   = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    query = d.get("query","")
    results = []
    for pn in range(doc.page_count):
        hits = doc[pn].search_for(query, quads=False)
        for h in hits:
            results.append({"page":pn+1,"x0":round(h.x0,2),"y0":round(h.y0,2),
                             "x1":round(h.x1,2),"y1":round(h.y1,2)})
    doc.close()
    return jsonify({"results": results, "count": len(results)})

# ── Search & Replace ─────────────────────────────────────────
@app.route("/api/replace", methods=["POST"])
def api_replace():
    d       = request.get_json()
    doc     = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    find    = d.get("find","")
    replace = d.get("replace","")
    font    = d.get("font","Sarabun")
    color_h = d.get("color","#000000").lstrip("#")
    cr      = tuple(int(color_h[i:i+2],16)/255 for i in (0,2,4))
    fk      = font_kw(font)
    count   = 0
    for pn in range(doc.page_count):
        page = doc[pn]
        hits = page.search_for(find)
        for rect in hits:
            fs = 12
            spans = page.get_text("dict", clip=rect)
            for b in spans.get("blocks",[]):
                for l in b.get("lines",[]):
                    for sp in l.get("spans",[]):
                        if sp["text"].strip(): fs=sp["size"]; break
            page.add_redact_annot(rect, fill=(1,1,1))
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            page.insert_text(fitz.Point(rect.x0, rect.y1), replace,
                             fontsize=float(d.get("fs",0)) or fs, color=cr, **fk)
            count += 1
    return pdf_response(doc, "replaced.pdf") if count else (jsonify({"error":"ไม่พบคำที่ค้นหา"}),404)

# ── Add text ─────────────────────────────────────────────────
@app.route("/api/addtext", methods=["POST"])
def api_addtext():
    d   = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    for a in d.get("annotations",[]):
        page=doc[int(a["page"])-1]
        hex_=a.get("color","#000000").lstrip("#")
        cr=tuple(int(hex_[i:i+2],16)/255 for i in (0,2,4))
        fk=font_kw(a.get("font","Sarabun"))
        page.insert_text(fitz.Point(float(a["x"]),float(a["y"])),
                         a["text"],fontsize=float(a.get("fs",14)),color=cr,**fk)
    return pdf_response(doc,"with_text.pdf")

# ── Highlight / Underline / Strikethrough ────────────────────
@app.route("/api/annotate", methods=["POST"])
def api_annotate():
    d    = request.get_json()
    doc  = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    mode = d.get("mode","highlight")  # highlight | underline | strikethrough
    hex_ = d.get("color","#FFD700").lstrip("#")
    cr   = tuple(int(hex_[i:i+2],16)/255 for i in (0,2,4))
    for a in d.get("rects",[]):
        page = doc[int(a["page"])-1]
        rect = fitz.Rect(a["x0"],a["y0"],a["x1"],a["y1"])
        if mode=="highlight":
            ann=page.add_highlight_annot(rect); ann.set_colors(stroke=cr); ann.update()
        elif mode=="underline":
            page.add_underline_annot(rect)
        elif mode=="strikethrough":
            page.add_strikeout_annot(rect)
    return pdf_response(doc,"annotated.pdf")

# ── Redact (permanent black box) ─────────────────────────────
@app.route("/api/redact", methods=["POST"])
def api_redact():
    d   = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    for a in d.get("rects",[]):
        page=doc[int(a["page"])-1]
        rect=fitz.Rect(a["x0"],a["y0"],a["x1"],a["y1"])
        page.add_redact_annot(rect, fill=(0,0,0))
    for pn in range(doc.page_count):
        doc[pn].apply_redactions()
    return pdf_response(doc,"redacted.pdf")

# ── Signature ────────────────────────────────────────────────
@app.route("/api/sign", methods=["POST"])
def api_sign():
    d   = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    for s in d.get("signatures",[]):
        page=doc[int(s["page"])-1]
        img=base64.b64decode(s["image"].split(",")[-1])
        rect=fitz.Rect(float(s["x"]),float(s["y"]),
                       float(s["x"])+float(s["w"]),float(s["y"])+float(s["h"]))
        page.insert_image(rect, stream=img)
    return pdf_response(doc,"signed.pdf")

# ── Insert image ─────────────────────────────────────────────
@app.route("/api/insertimage", methods=["POST"])
def api_insertimage():
    d    = request.get_json()
    doc  = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    page = doc[int(d.get("page",1))-1]
    img  = base64.b64decode(d["image"].split(",")[-1])
    x,y,w,h = float(d["x"]),float(d["y"]),float(d["w"]),float(d["h"])
    page.insert_image(fitz.Rect(x,y,x+w,y+h), stream=img)
    return pdf_response(doc,"with_image.pdf")

# ── Watermark ────────────────────────────────────────────────
@app.route("/api/watermark", methods=["POST"])
def api_watermark():
    d    = request.get_json()
    doc  = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    text = d.get("text","CONFIDENTIAL")
    opacity = float(d.get("opacity",0.25))
    pages = d.get("pages","all")
    hex_ = d.get("color","#AAAAAA").lstrip("#")
    cr   = tuple(int(hex_[i:i+2],16)/255 for i in (0,2,4))
    rng  = range(doc.page_count) if pages=="all" else [int(p)-1 for p in pages]
    for pn in rng:
        page=doc[pn]
        rect=fitz.Rect(50, page.rect.height*0.35, page.rect.width-50, page.rect.height*0.65)
        page.insert_textbox(rect, text, fontname="helv",
                            fontsize=float(d.get("fontsize",60)),
                            color=cr, align=fitz.TEXT_ALIGN_CENTER)
    return pdf_response(doc,"watermarked.pdf")

# ── Password ─────────────────────────────────────────────────
@app.route("/api/password", methods=["POST"])
def api_password():
    d    = request.get_json()
    doc  = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    pw   = d.get("password","")
    buf  = io.BytesIO()
    doc.save(buf, encryption=fitz.PDF_ENCRYPT_AES_256,
             user_pw=pw, owner_pw=pw+"_owner", garbage=4)
    doc.close(); buf.seek(0)
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True, download_name="protected.pdf")

# ── Rotate pages ─────────────────────────────────────────────
@app.route("/api/rotate", methods=["POST"])
def api_rotate():
    d     = request.get_json()
    doc   = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    pages = d.get("pages","all")
    angle = int(d.get("angle",90))
    rng   = range(doc.page_count) if pages=="all" else [int(p)-1 for p in pages]
    for pn in rng:
        cur = doc[pn].rotation
        doc[pn].set_rotation((cur+angle)%360)
    return pdf_response(doc,"rotated.pdf")

# ── Delete pages ─────────────────────────────────────────────
@app.route("/api/deletepages", methods=["POST"])
def api_deletepages():
    d     = request.get_json()
    doc   = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    pages = sorted([int(p)-1 for p in d.get("pages",[])], reverse=True)
    for pn in pages:
        if 0 <= pn < doc.page_count:
            doc.delete_page(pn)
    return pdf_response(doc,"deleted_pages.pdf")

# ── Reorder pages ────────────────────────────────────────────
@app.route("/api/reorder", methods=["POST"])
def api_reorder():
    d     = request.get_json()
    doc   = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    order = [int(p)-1 for p in d.get("order",[])]
    if len(order)==doc.page_count:
        doc.select(order)
    return pdf_response(doc,"reordered.pdf")

# ── Merge ────────────────────────────────────────────────────
@app.route("/api/merge", methods=["POST"])
def api_merge():
    files=request.files.getlist("files")
    if len(files)<2: return jsonify({"error":"Need 2+ files"}),400
    merged=fitz.open()
    for f in files:
        d=fitz.open(stream=f.read(),filetype="pdf"); merged.insert_pdf(d); d.close()
    return pdf_response(merged,"merged.pdf")

# ── Split ────────────────────────────────────────────────────
@app.route("/api/split", methods=["POST"])
def api_split():
    f=request.files.get("file")
    fr=int(request.form.get("from",1))-1
    to=int(request.form.get("to",1))-1
    doc=fitz.open(stream=f.read(),filetype="pdf")
    nd=fitz.open(); nd.insert_pdf(doc,from_page=fr,to_page=to)
    return pdf_response(nd,f"split_p{fr+1}-{to+1}.pdf")

# ── Export page as image ─────────────────────────────────────
@app.route("/api/toimage", methods=["POST"])
def api_toimage():
    d    = request.get_json()
    doc  = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    pn   = int(d.get("page",1))-1
    sc   = float(d.get("scale",2.0))
    fmt  = d.get("format","png")
    pix  = doc[pn].get_pixmap(matrix=fitz.Matrix(sc,sc), alpha=False)
    img  = pix.tobytes(fmt)
    doc.close()
    buf  = io.BytesIO(img); buf.seek(0)
    return send_file(buf, mimetype=f"image/{fmt}",
                     as_attachment=True, download_name=f"page_{pn+1}.{fmt}")

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port,debug=False)
