import argparse
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait


COMPARE_DB_MARKER = b"ts_stock_compare_db_v1"


def detect_profile(browser_root: Path) -> Path | None:
    if not browser_root.exists():
        return None
    candidates = []
    for profile_dir in browser_root.iterdir():
        if not profile_dir.is_dir():
            continue
        compare_db = profile_dir / "IndexedDB" / "file__0.indexeddb.leveldb"
        if not compare_db.exists():
            continue
        for file_path in compare_db.glob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in {".ldb", ".log"} and "MANIFEST" not in file_path.name:
                continue
            try:
                data = file_path.read_bytes()
            except OSError:
                continue
            if COMPARE_DB_MARKER in data:
                candidates.append(profile_dir)
                break
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def build_file_uri(html_path: Path) -> str:
    return html_path.resolve().as_uri()


def extract_state(profile_dir: Path, html_uri: str) -> dict:
    temp_root = Path(tempfile.mkdtemp(prefix="ts-stock-compare-"))
    try:
        temp_profile = temp_root / "Default"
        temp_profile.mkdir(parents=True, exist_ok=True)
        for rel in ("Local Storage", "IndexedDB"):
            src = profile_dir / rel
            if src.exists():
                shutil.copytree(src, temp_profile / rel, dirs_exist_ok=True)

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1280,900")
        options.add_argument(f"--user-data-dir={temp_root}")

        driver = webdriver.Chrome(options=options)
        try:
            driver.get(html_uri)
            WebDriverWait(driver, 30).until(
                lambda d: d.execute_script(
                    "return Boolean(window.__stockCompareApi && window.__stockCompareApi.getState && window.__stockCompareApi.getState().current);"
                )
            )
            state = driver.execute_script("return window.__stockCompareApi.getState()")
            if not isinstance(state, dict) or not state.get("current"):
                raise RuntimeError("ไม่พบ stock compare state ใน local profile")
            return state
        finally:
            driver.quit()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def to_shared_payload(state: dict) -> dict:
    snapshots = [snapshot for snapshot in (state.get("previous"), state.get("current")) if snapshot]
    return {
        "seq": int(state.get("seq") or len(snapshots)),
        "snapshots": snapshots[-2:],
        "source": "local-browser-profile",
        "exportedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export stock compare state from a local browser profile into stock-compare.shared.json")
    parser.add_argument("--browser-root", default=str(Path.home() / "AppData/Local/Google/Chrome/User Data"))
    parser.add_argument("--profile", default="", help="Chrome profile directory name เช่น Default หรือ Profile 1")
    parser.add_argument("--html", default=str(Path(__file__).resolve().parents[1] / "index.html"))
    parser.add_argument("--output", default=str(Path(__file__).resolve().parents[1] / "stock-compare.shared.json"))
    args = parser.parse_args()

    browser_root = Path(args.browser_root)
    profile_dir = browser_root / args.profile if args.profile else detect_profile(browser_root)
    if not profile_dir or not profile_dir.exists():
        raise SystemExit("ไม่พบ browser profile ที่มี stock compare state")

    html_path = Path(args.html)
    if not html_path.exists():
        raise SystemExit(f"ไม่พบไฟล์ HTML: {html_path}")

    state = extract_state(profile_dir, build_file_uri(html_path))
    payload = to_shared_payload(state)
    output_path = Path(args.output)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Exported {len(payload['snapshots'])} snapshot(s) from {profile_dir.name} -> {output_path}")
    if payload["snapshots"]:
        current = payload["snapshots"][-1]
        previous = payload["snapshots"][-2] if len(payload["snapshots"]) > 1 else None
        print(f"Current: Cache {current.get('id')} @ {current.get('updatedAt')}")
        if previous:
            print(f"Previous: Cache {previous.get('id')} @ {previous.get('updatedAt')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
