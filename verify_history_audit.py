from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).parent
html = (root / "static/index.html").read_text(encoding="utf-8")
script = html.rsplit("<script>", 1)[1].split("</script>", 1)[0]
with tempfile.TemporaryDirectory(prefix="nse_oi_frontend_") as temp_dir:
    js_path = Path(temp_dir) / "nse_oi_frontend.js"
    js_path.write_text(script, encoding="utf-8")
    subprocess.run(["node", "--check", str(js_path)], check=True)
assert "if (marker && marker !== today) localStorage.removeItem(STORAGE_KEY);" in html
assert "one independent trade list per IST day" in html
assert "clearOldBrowserHistory(today);" in html
assert "localStorage.setItem(STORAGE_KEY, JSON.stringify(this.trades));" in html
print("frontend JS syntax: ok")
print("trade history persistence/migration: ok")
