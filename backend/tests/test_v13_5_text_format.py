"""v0.13.5: highlighter colours and font sizes in text sections."""


def test_highlight_colours_and_font_sizes_are_kept_and_exported(client, users, notebook):
    h = users["alice"]
    e = client.post("/api/experiments", headers=h, json={"title": "formatting", "notebook_id": notebook["id"]}).json()
    html = ('<p><mark>yellow</mark> <mark data-hl="green" onclick="x">green</mark> <mark data-hl="purple">purple</mark>'
            ' <span style="font-size: 18pt">big</span> <span style="font-size: 16px">px</span>'
            ' <span style="font-size: 900px">huge</span> <span style="font-size: 3em">em</span>'
            ' <span style="font-size: expression(alert(1))">bad</span></p>')
    en = client.post(f"/api/experiments/{e['id']}/entries", headers=h, json={"kind": "text", "content": {"html": html}}).json()
    out = en["content"]["html"]
    assert '<mark data-hl="green">' in out and 'data-hl="purple"' in out and "onclick" not in out
    assert "font-size: 18pt" in out and "font-size: 12pt" in out          # px converted to pt
    assert "900px" not in out and "3em" not in out and "expression" not in out  # out of range or not a size
    report = client.get(f"/api/experiments/{e['id']}/export", headers=h).text
    assert '<mark style="background-color:#fff3a3">yellow' in report and '<mark style="background-color:#c8f2d6">green' in report
    assert client.get(f"/api/experiments/{e['id']}/export?format=pdf", headers=h).status_code == 200
