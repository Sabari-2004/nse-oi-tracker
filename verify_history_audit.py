from pathlib import Path
import subprocess

root = Path(__file__).parent
html = (root / "static/index.html").read_text(encoding="utf-8")
script = html.split("<script>", 1)[1].split("</script>", 1)[0]
js_path = Path("/tmp/nse_oi_frontend.js")
js_path.write_text(script, encoding="utf-8")
subprocess.run(["node", "--check", str(js_path)], check=True)
assert "localStorage.removeItem(STORAGE_KEY);" not in html
assert "retains the complete trade history" in html
assert "const migrated = [];" in html
assert "localStorage.setItem(STORAGE_KEY, JSON.stringify(this.trades));" in html
print("frontend JS syntax: ok")
print("trade history persistence/migration: ok")
