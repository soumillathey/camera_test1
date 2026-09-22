#!/usr/bin/env python3
"""
camera_test.py — CCTV Frame Grabber & ANPR Microservice Tester
=============================================================
Mirrors the camera grabbing and ANPR dispatch mechanisms from hermes:
  1. Grabs snapshot frames from CCTV (HTTP JPEG snapshot or RTSP stream via OpenCV).
  2. Submits multipart/form-data image frame to the ANPR microservice (/recognize).
  3. Displays the EXACT response (status, latency, full raw JSON/body, and parsed plate).

Usage:
  python camera_test.py
  python camera_test.py --camera-url "http://192.168.1.101/cgi-bin/snapshot.cgi"
  python camera_test.py --camera-url "rtsp://admin:pass@192.168.1.101:554/h264Preview_01_main"
  python camera_test.py --server-url "http://127.0.0.1:8000/recognize"
  python camera_test.py --continuous --interval 2.0
  python camera_test.py --image test_car.jpg
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
    # Suppress InsecureRequestWarning when hitting CCTV cameras with self-signed SSL
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    print("[ERROR] 'requests' library is not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)


CONFIG_FILE = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    "camera_url": "http://127.0.0.1:8999/front",
    "anpr_server_url": "http://127.0.0.1:8000/recognize",
    "interval_seconds": 5.0,
    "continuous": True,
    "timeout": 30.0,
    "save_last_frame": True,
}


def load_config() -> dict:
    """Load configuration from config.json with fallback defaults."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_CONFIG, **data}
        except Exception as e:
            print(f"[WARN] Failed to read {CONFIG_FILE}: {e}. Using defaults.")
    return DEFAULT_CONFIG.copy()


def resolve_anpr_endpoint(url: str | None) -> str:
    """
    Resolves ANPR server URL, replacing 0.0.0.0 with 127.0.0.1
    and appending /recognize if omitted. (Matches hermes implementation).
    """
    raw = (url or "http://127.0.0.1:8000/recognize").strip()
    raw = raw.replace("://0.0.0.0", "://127.0.0.1")
    raw = raw.rstrip("/")
    if not raw.endswith("/recognize"):
        raw = f"{raw}/recognize"
    return raw


def fetch_http_snapshot(url: str, timeout: float = 5.0) -> tuple[bytes | None, float]:
    """Fetch HTTP JPEG snapshot from camera."""
    t0 = time.perf_counter()
    try:
        response = requests.get(url, timeout=timeout, verify=False)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if response.status_code == 200 and response.content:
            return response.content, latency_ms
        print(f"[ERROR] Camera HTTP fetch returned status {response.status_code} ({response.reason})", file=sys.stderr)
        return None, latency_ms
    except requests.RequestException as e:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        print(f"[ERROR] Exception fetching snapshot from {url}: {e}", file=sys.stderr)
        return None, latency_ms


def fetch_rtsp_frame(rtsp_url: str, timeout: float = 5.0) -> tuple[bytes | None, float]:
    """On-demand single frame capture from RTSP stream using OpenCV (matches hermes)."""
    try:
        import cv2
    except ImportError:
        print("[ERROR] OpenCV (cv2) is required for RTSP URLs. Install with: pip install opencv-python", file=sys.stderr)
        return None, 0.0

    t0 = time.perf_counter()
    try:
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            print(f"[ERROR] Unable to open RTSP stream: {rtsp_url}", file=sys.stderr)
            return None, (time.perf_counter() - t0) * 1000.0

        # Set low buffer size to ensure freshest frame
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ret, frame = cap.read()
        cap.release()

        latency_ms = (time.perf_counter() - t0) * 1000.0
        if ret and frame is not None:
            success, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if success:
                return buffer.tobytes(), latency_ms
        print(f"[ERROR] Failed to read frame from RTSP stream: {rtsp_url}", file=sys.stderr)
        return None, latency_ms
    except Exception as e:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        print(f"[ERROR] OpenCV RTSP exception: {e}", file=sys.stderr)
        return None, latency_ms


def fetch_frame(camera_url: str, timeout: float = 5.0) -> tuple[bytes | None, float]:
    """Fetch raw JPEG bytes from camera URL (HTTP or RTSP)."""
    clean_url = camera_url.strip()
    if clean_url.lower().startswith("rtsp://"):
        return fetch_rtsp_frame(clean_url, timeout)
    return fetch_http_snapshot(clean_url, timeout)


def send_frame_to_anpr(
    image_bytes: bytes,
    server_url: str,
    timeout: float = 30.0,
) -> tuple[requests.Response | None, float]:
    """
    Sends raw JPEG image bytes to the Argus ANPR FastAPI microservice (/recognize).
    Matches the exact multipart/form-data upload format used in hermes:
      files = {"file": ("frame.jpg", image_bytes, "image/jpeg")}
    """
    target_url = resolve_anpr_endpoint(server_url)
    files = {"file": ("frame.jpg", image_bytes, "image/jpeg")}

    t0 = time.perf_counter()
    try:
        response = requests.post(target_url, files=files, timeout=timeout)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return response, latency_ms
    except requests.exceptions.Timeout:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        print(f"[ERROR] Timeout ({timeout}s) contacting ANPR server at {target_url}", file=sys.stderr)
        return None, latency_ms
    except requests.exceptions.ConnectionError:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        print(f"[ERROR] Failed to connect to ANPR server at {target_url}. Is the service running?", file=sys.stderr)
        return None, latency_ms
    except requests.RequestException as e:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        print(f"[ERROR] Exception sending frame to ANPR ({target_url}): {e}", file=sys.stderr)
        return None, latency_ms


def format_separator(title: str = "", char: str = "─", width: int = 65) -> str:
    if not title:
        return char * width
    padding = width - len(title) - 4
    left = padding // 2
    right = padding - left
    return f"{char * left} [ {title} ] {char * right}"


def display_results(response: requests.Response, latency_ms: float, image_size_bytes: int):
    """Formats and prints the exact response and parsed metadata."""
    print()
    print("=" * 65)
    print(format_separator("ANPR SERVICE RESPONSE"))
    print(f"HTTP Status      : {response.status_code} {response.reason}")
    print(f"Round-Trip Time  : {latency_ms:.2f} ms")
    print(f"Uploaded Payload : {image_size_bytes:,} bytes")
    print("─" * 65)
    print("EXACT RAW RESPONSE BODY:")
    print("─" * 65)

    is_json = False
    parsed_json = None
    try:
        parsed_json = response.json()
        is_json = True
        print(json.dumps(parsed_json, indent=2))
    except Exception:
        print(response.text if response.text else "<Empty response body>")

    print("─" * 65)

    # If response is JSON, provide a parsed summary matching hermes extraction
    if is_json and isinstance(parsed_json, dict):
        print(format_separator("PARSED RESULTS SUMMARY"))
        status = parsed_json.get("status")
        status_msg = parsed_json.get("status_message")
        rejected = parsed_json.get("rejected")
        exec_time = parsed_json.get("execution_time_ms")
        results = parsed_json.get("results")

        if exec_time is not None:
            print(f"• Model Inference Time : {exec_time} ms")
        if status is not None:
            print(f"• Status Code          : {status}")
        if status_msg:
            print(f"• Status Message       : {status_msg}")
        if rejected:
            print(f"• Pre-screening        : REJECTED ({status_msg})")

        if isinstance(results, list) and len(results) > 0:
            print(f"\n• Detected Vehicles ({len(results)} total):")
            for idx, item in enumerate(results, 1):
                plate = item.get("plate") or "N/A"
                conf = item.get("confidence")
                vtype = item.get("vehicle_type") or "vehicle"
                conf_str = f" (conf: {conf:.2%})" if isinstance(conf, (int, float)) else ""
                tag = " [PRIMARY / SELECTED]" if idx == 1 else ""
                print(f"   [{idx}] Plate: '{plate}' | Type: {vtype}{conf_str}{tag}")
        elif not rejected:
            # Check flat keys
            flat_plate = (
                parsed_json.get("plate")
                or parsed_json.get("number_plate")
                or parsed_json.get("plate_number")
                or parsed_json.get("text")
                or parsed_json.get("result")
            )
            if flat_plate:
                print(f"• Plate Number Detected: '{flat_plate}'")
            else:
                print("• Plate Detection      : NO PLATE DETECTED")

    print("=" * 65)


def run_single_test(
    camera_url: str | None,
    server_url: str,
    image_file: str | None = None,
    save_captured: str | None = None,
    timeout: float = 30.0,
) -> bool:
    """Runs a single capture-and-send test pass."""
    img_bytes: bytes | None = None
    source_desc = ""

    if image_file:
        path = Path(image_file)
        if not path.exists():
            print(f"[ERROR] Image file does not exist: {image_file}", file=sys.stderr)
            return False
        print(f"[1/2] Loading image from disk: {path.resolve()} ...")
        t0 = time.perf_counter()
        img_bytes = path.read_bytes()
        load_ms = (time.perf_counter() - t0) * 1000.0
        source_desc = f"Disk File ({path.name})"
        print(f"      Loaded {len(img_bytes):,} bytes in {load_ms:.2f} ms")
    else:
        if not camera_url:
            print("[ERROR] No camera URL specified. Set 'camera_url' in config.json or use --camera-url.", file=sys.stderr)
            return False
        print(f"[1/2] Grabbing frame from CCTV: {camera_url} ...")
        img_bytes, grab_ms = fetch_frame(camera_url, timeout=5.0)
        if not img_bytes:
            print(f"[FAIL] Could not capture frame from camera {camera_url}", file=sys.stderr)
            return False
        source_desc = f"CCTV ({camera_url})"
        print(f"      Successfully captured {len(img_bytes):,} bytes in {grab_ms:.2f} ms")

    # Optionally save captured frame to disk
    if save_captured and img_bytes:
        save_path = Path(save_captured)
        save_path.write_bytes(img_bytes)
        print(f"      [Saved captured frame -> {save_path.resolve()}]")

    # Send to ANPR server
    target_url = resolve_anpr_endpoint(server_url)
    print(f"[2/2] Sending frame to ANPR service at {target_url} ...")
    response, latency_ms = send_frame_to_anpr(img_bytes, target_url, timeout=timeout)

    if response is None:
        print("[FAIL] ANPR request failed (no response received).")
        return False

    display_results(response, latency_ms, len(img_bytes))
    return response.status_code in (200, 201)


def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(
        description="Grab CCTV frame and test ANPR microservice endpoint (mirrors hermes master).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--camera-url",
        default=cfg.get("camera_url", ""),
        help="CCTV camera URL (HTTP snapshot or RTSP stream). Overrides config.json.",
    )
    parser.add_argument(
        "--server-url",
        default=cfg.get("anpr_server_url", "http://127.0.0.1:8000/recognize"),
        help="ANPR microservice endpoint URL. Overrides config.json.",
    )
    parser.add_argument(
        "--image",
        default=None,
        help="Path to a local JPEG/PNG image file to send instead of grabbing from CCTV.",
    )
    parser.add_argument(
        "--save-captured",
        default="captured_frame.jpg" if cfg.get("save_last_frame") else None,
        help="Path to save the captured frame locally for verification.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(cfg.get("interval_seconds", 5.0)),
        help="Interval in seconds between ANPR requests (default: 5.0s).",
    )
    parser.add_argument(
        "--once",
        "--single-shot",
        action="store_true",
        help="Run only once instead of continuous 5-second looping.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(cfg.get("timeout", 30.0)),
        help="Timeout in seconds for ANPR server response.",
    )

    args = parser.parse_args()

    is_continuous = not args.once and cfg.get("continuous", True)

    print("=" * 65)
    print(" CCTV -> ANPR Test Client (hermes master architecture)")
    print("=" * 65)
    print(f"ANPR Server Target : {resolve_anpr_endpoint(args.server_url)}")
    if args.image:
        print(f"Input Source       : Local File ({args.image})")
    else:
        print(f"Input Source       : CCTV Stream ({args.camera_url or 'NOT CONFIGURED'})")
    print(f"ANPR Timeout       : {args.timeout}s")
    if is_continuous:
        print(f"Mode               : Recurring every {args.interval:.1f}s (Press Ctrl+C to stop)")
    else:
        print("Mode               : Single Shot (--once)")
    print("=" * 65)

    if not args.image and not args.camera_url:
        print("\n[ERROR] No camera URL or local image specified!", file=sys.stderr)
        print("Please provide one via:")
        print("  1. --camera-url <URL> (e.g. --camera-url http://192.168.1.101/cgi-bin/snapshot.cgi)")
        print("  2. Edit 'camera_url' in config.json")
        print("  3. --image <path/to/image.jpg>")
        sys.exit(1)

    if is_continuous:
        iteration = 1
        try:
            while True:
                print(f"\n{'━'*65}")
                print(f" >>> [CYCLE #{iteration}] Capturing & Sending to ANPR @ {time.strftime('%H:%M:%S')} <<<")
                print(f"{'━'*65}")
                run_single_test(
                    camera_url=args.camera_url,
                    server_url=args.server_url,
                    image_file=args.image,
                    save_captured=args.save_captured,
                    timeout=args.timeout,
                )
                iteration += 1
                print(f"\n⏳ Sleeping {args.interval:.1f}s before next capture cycle... (Press Ctrl+C to stop)")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n\n[INFO] Stopped by user (Ctrl+C). Exiting.")
            sys.exit(0)
    else:
        success = run_single_test(
            camera_url=args.camera_url,
            server_url=args.server_url,
            image_file=args.image,
            save_captured=args.save_captured,
            timeout=args.timeout,
        )
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
