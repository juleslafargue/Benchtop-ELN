"""v0.13.4: pictures in text sections can be aligned left, centre or right."""

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def test_picture_alignment_is_kept_and_exported(client, users, notebook):
    h = users["alice"]
    e = client.post("/api/experiments", headers=h, json={"title": "aligned picture", "notebook_id": notebook["id"]}).json()
    att = client.post(f"/api/experiments/{e['id']}/attachments", headers=h, files={"file": ("tlc.png", PNG, "image/png")}).json()
    html = (f'<p>TLC after 1 h</p><img data-attachment-id="{att["id"]}" alt="tlc.png" data-align="center" onerror="alert(1)">'
            f'<img data-attachment-id="{att["id"]}" data-align="right">')
    en = client.post(f"/api/experiments/{e['id']}/entries", headers=h, json={"kind": "text", "content": {"html": html}}).json()
    stored = en["content"]["html"]
    assert 'data-align="center"' in stored and 'data-align="right"' in stored and "onerror" not in stored
    report = client.get(f"/api/experiments/{e['id']}/export", headers=h).text
    assert '<div style="text-align:center"><img src="data:image/png' in report
    assert '<div style="text-align:right"><img src="data:image/png' in report
    assert client.get(f"/api/experiments/{e['id']}/export?format=pdf", headers=h).status_code == 200


def test_picture_size_and_wrap_are_kept_and_exported(client, users, notebook):
    h = users["alice"]
    e = client.post("/api/experiments", headers=h, json={"title": "sized picture", "notebook_id": notebook["id"]}).json()
    att = client.post(f"/api/experiments/{e['id']}/attachments", headers=h, files={"file": ("plate.png", PNG, "image/png")}).json()
    i = att["id"]
    html = (f'<img data-attachment-id="{i}" data-width="50">'
            f'<img data-attachment-id="{i}" data-align="wrap-left" data-width="33"><p>text beside it</p>'
            f'<img data-attachment-id="{i}" data-align="wrap-right">'                 # wrap with no size: 40 %
            f'<img data-attachment-id="{i}" data-width="999" data-align="sideways">')  # nonsense: shown at natural size
    en = client.post(f"/api/experiments/{e['id']}/entries", headers=h, json={"kind": "text", "content": {"html": html}}).json()
    assert 'data-width="50"' in en["content"]["html"] and 'data-align="wrap-left"' in en["content"]["html"]
    report = client.get(f"/api/experiments/{e['id']}/export", headers=h).text
    assert "width:50%;height:auto" in report
    assert "float:left;width:33%" in report and "float:right;width:40%" in report
    assert report.count("max-width:100%;max-height:420px") == 1  # the nonsense one
    pdf = client.get(f"/api/experiments/{e['id']}/export?format=pdf", headers=h)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
