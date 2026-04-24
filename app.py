import os, io, base64
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
import fitz

app = Flask(__name__, static_folder="static")
CORS(app)

print("[PDF Studio] Ready — using insert_htmlbox for Thai text")

BUILTIN = {"Helvetica":"helv","Times-Roman":"tiro","Courier":"cour"}

def font_kw(name):
    return {"fontname": BUILTIN.get(name, "helv")}

def insert_thai_text(page, point, text, fontsize=12, color=(0,0,0), fontname="Sarabun"):
    """Insert text with full Thai/Unicode support using insert_htmlbox"""
    r, g, b = color
    hex_c = "#{:02x}{:02x}{:02x}".format(int(r*255), int(g*255), int(b*255))
    ff = "Sarabun, sans-serif" if fontname.startswith("Sarabun") else fontname
    # point.y is baseline in PDF coords (Y-up)
    # Give generous rect so text fits
    w = max(len(text) * fontsize + 200, 300)
    h = fontsize * 2.5
    # Place rect so text baseline aligns with point.y
    rect = fitz.Rect(point.x, point.y - fontsize * 1.8,
                     point.x + w, point.y + fontsize * 0.8)
    html = (f'<p style="font-family:{ff};font-size:{fontsize}pt;'
            f'color:{hex_c};margin:0;padding:0;white-space:nowrap;line-height:1;">'
            f'{text}</p>')
    page.insert_htmlbox(rect, html)

def b64_to_pdf(b64):
    return base64.b64decode(b64)

def pdf_response(doc, name="output.pdf"):
    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    doc.close()
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True, download_name=name)

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/info", methods=["POST"])
def api_info():
    doc = fitz.open(stream=b64_to_pdf(request.get_json()["pdf"]), filetype="pdf")
    data = {"page_count": doc.page_count}
    doc.close()
    return jsonify(data)

@app.route("/api/render", methods=["POST"])
def api_render():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    pn = int(d.get("page", 1)) - 1
    sc = float(d.get("scale", 1.5))
    page = doc[pn]
    pix = page.get_pixmap(matrix=fitz.Matrix(sc, sc), alpha=False)
    png = base64.b64encode(pix.tobytes("png")).decode()
    blocks = get_lines(page)
    pc = doc.page_count
    doc.close()
    return jsonify({"png": png, "w": pix.width, "h": pix.height,
                    "scale": sc, "blocks": blocks, "page_count": pc})

def get_lines(page):
    words = page.get_text("words")
    if not words:
        return []
    ld = {}
    for w in words:
        x0, y0, x1, y1, text, bn, ln, wn = w
        k = (bn, ln)
        if k not in ld:
            ld[k] = {"x0":x0,"y0":y0,"x1":x1,"y1":y1,"ws":[]}
        else:
            ld[k]["x0"] = min(ld[k]["x0"], x0)
            ld[k]["y0"] = min(ld[k]["y0"], y0)
            ld[k]["x1"] = max(ld[k]["x1"], x1)
            ld[k]["y1"] = max(ld[k]["y1"], y1)
        ld[k]["ws"].append((wn, text))
    lines = []
    for k, ln in sorted(ld.items()):
        txt = " ".join(w[1] for w in sorted(ln["ws"]))
        fs = 12
        d = page.get_text("dict", clip=fitz.Rect(ln["x0"]-1, ln["y0"]-1,
                                                  ln["x1"]+1, ln["y1"]+1))
        for b in d.get("blocks", []):
            for sl in b.get("lines", []):
                for sp in sl.get("spans", []):
                    if sp["text"].strip():
                        fs = round(sp["size"], 1)
                        break
        lines.append({"text":txt,
                      "x0":round(ln["x0"],2),"y0":round(ln["y0"],2),
                      "x1":round(ln["x1"],2),"y1":round(ln["y1"],2),
                      "fs":fs})
    return lines

@app.route("/api/edit", methods=["POST"])
def api_edit():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    for ed in d.get("edits", []):
        page = doc[int(ed["page"]) - 1]
        rect = fitz.Rect(ed["x0"], ed["y0"], ed["x1"], ed["y1"])
        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        hex_ = ed.get("color", "#000000").lstrip("#")
        cr = tuple(int(hex_[i:i+2], 16)/255 for i in (0, 2, 4))
        fn = ed.get("font", "Sarabun")
        nt = ed.get("newText") or ed.get("new_text", "")
        insert_thai_text(page, fitz.Point(ed["x0"], ed["y1"]),
                        nt, fontsize=float(ed.get("fs", 12)),
                        color=cr, fontname=fn)
    return pdf_response(doc, "edited.pdf")

@app.route("/api/search", methods=["POST"])
def api_search():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    query = d.get("query", "")
    results = []
    for pn in range(doc.page_count):
        hits = doc[pn].search_for(query)
        for h in hits:
            results.append({"page":pn+1,"x0":round(h.x0,2),"y0":round(h.y0,2),
                             "x1":round(h.x1,2),"y1":round(h.y1,2)})
    doc.close()
    return jsonify({"results": results, "count": len(results)})

@app.route("/api/replace", methods=["POST"])
def api_replace():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    find = d.get("find", "")
    replace = d.get("replace", "")
    hex_ = d.get("color", "#000000").lstrip("#")
    cr = tuple(int(hex_[i:i+2], 16)/255 for i in (0, 2, 4))
    fn = d.get("font", "Sarabun")
    count = 0
    for pn in range(doc.page_count):
        page = doc[pn]
        hits = page.search_for(find)
        for rect in hits:
            fs = 12
            spans = page.get_text("dict", clip=rect)
            for b in spans.get("blocks", []):
                for l in b.get("lines", []):
                    for sp in l.get("spans", []):
                        if sp["text"].strip():
                            fs = sp["size"]
                            break
            page.add_redact_annot(rect, fill=(1, 1, 1))
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            insert_thai_text(page, fitz.Point(rect.x0, rect.y1),
                            replace, fontsize=float(d.get("fs", 0)) or fs,
                            color=cr, fontname=fn)
            count += 1
    if count:
        return pdf_response(doc, "replaced.pdf")
    return jsonify({"error": "ไม่พบคำที่ค้นหา"}), 404

@app.route("/api/addtext", methods=["POST"])
def api_addtext():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    for a in d.get("annotations", []):
        page = doc[int(a["page"]) - 1]
        hex_ = a.get("color", "#000000").lstrip("#")
        cr = tuple(int(hex_[i:i+2], 16)/255 for i in (0, 2, 4))
        fn = a.get("font", "Sarabun")
        insert_thai_text(page, fitz.Point(float(a["x"]), float(a["y"])),
                        a["text"], fontsize=float(a.get("fs", 14)),
                        color=cr, fontname=fn)
    return pdf_response(doc, "with_text.pdf")

@app.route("/api/annotate", methods=["POST"])
def api_annotate():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    mode = d.get("mode", "highlight")
    hex_ = d.get("color", "#FFD700").lstrip("#")
    cr = tuple(int(hex_[i:i+2], 16)/255 for i in (0, 2, 4))
    for a in d.get("rects", []):
        page = doc[int(a["page"]) - 1]
        rect = fitz.Rect(a["x0"], a["y0"], a["x1"], a["y1"])
        if mode == "highlight":
            ann = page.add_highlight_annot(rect)
            ann.set_colors(stroke=cr)
            ann.update()
        elif mode == "underline":
            page.add_underline_annot(rect)
        elif mode == "strikethrough":
            page.add_strikeout_annot(rect)
    return pdf_response(doc, "annotated.pdf")

@app.route("/api/redact", methods=["POST"])
def api_redact():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    for a in d.get("rects", []):
        page = doc[int(a["page"]) - 1]
        rect = fitz.Rect(a["x0"], a["y0"], a["x1"], a["y1"])
        page.add_redact_annot(rect, fill=(0, 0, 0))
    for pn in range(doc.page_count):
        doc[pn].apply_redactions()
    return pdf_response(doc, "redacted.pdf")

@app.route("/api/sign", methods=["POST"])
def api_sign():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    for s in d.get("signatures", []):
        page = doc[int(s["page"]) - 1]
        img = base64.b64decode(s["image"].split(",")[-1])
        rect = fitz.Rect(float(s["x"]), float(s["y"]),
                         float(s["x"])+float(s["w"]), float(s["y"])+float(s["h"]))
        page.insert_image(rect, stream=img)
    return pdf_response(doc, "signed.pdf")

@app.route("/api/insertimage", methods=["POST"])
def api_insertimage():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    page = doc[int(d.get("page", 1)) - 1]
    img = base64.b64decode(d["image"].split(",")[-1])
    x, y, w, h = float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"])
    page.insert_image(fitz.Rect(x, y, x+w, y+h), stream=img)
    return pdf_response(doc, "with_image.pdf")

@app.route("/api/watermark", methods=["POST"])
def api_watermark():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    text = d.get("text", "CONFIDENTIAL")
    hex_ = d.get("color", "#AAAAAA").lstrip("#")
    cr = tuple(int(hex_[i:i+2], 16)/255 for i in (0, 2, 4))
    pages = d.get("pages", "all")
    rng = range(doc.page_count) if pages == "all" else [int(p)-1 for p in pages]
    for pn in rng:
        page = doc[pn]
        rect = fitz.Rect(50, page.rect.height*0.35,
                         page.rect.width-50, page.rect.height*0.65)
        page.insert_textbox(rect, text, fontname="helv",
                            fontsize=float(d.get("fontsize", 60)),
                            color=cr, align=fitz.TEXT_ALIGN_CENTER)
    return pdf_response(doc, "watermarked.pdf")

@app.route("/api/password", methods=["POST"])
def api_password():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    pw = d.get("password", "")
    buf = io.BytesIO()
    doc.save(buf, encryption=fitz.PDF_ENCRYPT_AES_256,
             user_pw=pw, owner_pw=pw+"_owner", garbage=4)
    doc.close()
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True, download_name="protected.pdf")

@app.route("/api/rotate", methods=["POST"])
def api_rotate():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    pages = d.get("pages", "all")
    angle = int(d.get("angle", 90))
    rng = range(doc.page_count) if pages == "all" else [int(p)-1 for p in pages]
    for pn in rng:
        cur = doc[pn].rotation
        doc[pn].set_rotation((cur + angle) % 360)
    return pdf_response(doc, "rotated.pdf")

@app.route("/api/deletepages", methods=["POST"])
def api_deletepages():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    pages = sorted([int(p)-1 for p in d.get("pages", [])], reverse=True)
    for pn in pages:
        if 0 <= pn < doc.page_count:
            doc.delete_page(pn)
    return pdf_response(doc, "deleted_pages.pdf")

@app.route("/api/reorder", methods=["POST"])
def api_reorder():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    order = [int(p)-1 for p in d.get("order", [])]
    if len(order) == doc.page_count:
        doc.select(order)
    return pdf_response(doc, "reordered.pdf")

@app.route("/api/merge", methods=["POST"])
def api_merge():
    files = request.files.getlist("files")
    if len(files) < 2:
        return jsonify({"error": "Need 2+ files"}), 400
    merged = fitz.open()
    for f in files:
        d = fitz.open(stream=f.read(), filetype="pdf")
        merged.insert_pdf(d)
        d.close()
    return pdf_response(merged, "merged.pdf")

@app.route("/api/split", methods=["POST"])
def api_split():
    f = request.files.get("file")
    fr = int(request.form.get("from", 1)) - 1
    to = int(request.form.get("to", 1)) - 1
    doc = fitz.open(stream=f.read(), filetype="pdf")
    nd = fitz.open()
    nd.insert_pdf(doc, from_page=fr, to_page=to)
    return pdf_response(nd, f"split_p{fr+1}-{to+1}.pdf")

@app.route("/api/toimage", methods=["POST"])
def api_toimage():
    d = request.get_json()
    doc = fitz.open(stream=b64_to_pdf(d["pdf"]), filetype="pdf")
    pn = int(d.get("page", 1)) - 1
    sc = float(d.get("scale", 2.0))
    fmt = d.get("format", "png")
    pix = doc[pn].get_pixmap(matrix=fitz.Matrix(sc, sc), alpha=False)
    img = pix.tobytes(fmt)
    doc.close()
    buf = io.BytesIO(img)
    buf.seek(0)
    return send_file(buf, mimetype=f"image/{fmt}",
                     as_attachment=True, download_name=f"page_{pn+1}.{fmt}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
