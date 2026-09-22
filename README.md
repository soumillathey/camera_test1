# Camera Test & ANPR Service Client

A lightweight, standalone testing utility built directly from the `hermes master` CCTV frame-grabbing and ANPR microservice dispatch architecture.

## What It Does
1. **CCTV Frame Capture** (matches `hermes/src/devices/camera.py`):
   - Supports **HTTP JPEG snapshots** (e.g. Dahua/Hikvision `/cgi-bin/snapshot.cgi` or HTTP endpoints).
   - Supports **RTSP video streams** (e.g. `rtsp://...`) using OpenCV single-frame grabbing with minimal buffer latency.
   - Supports **local image fallback** (`--image test.jpg`) for testing without an active camera.
2. **ANPR Microservice Client** (matches `hermes/src/integrations/anpr.py`):
   - Dispatches the JPEG frame via `multipart/form-data` (`{"file": ("frame.jpg", img_bytes, "image/jpeg")}`).
   - Automatically maps `0.0.0.0` to `127.0.0.1` and appends `/recognize` if missing.
3. **Exact Response Display**:
   - Prints HTTP status code and exact round-trip latency in milliseconds.
   - Pretty-prints the **entire raw response JSON/body** returned by the ANPR service.
   - Summarizes model inference time, detected plates, vehicle types, confidence scores, and pre-screening filters.

---

## Configuration (`config.json`)

You can edit [`config.json`](./config.json) to set your default URLs:

```json
{
  "camera_url": "http://192.168.1.101/cgi-bin/snapshot.cgi",
  "anpr_server_url": "http://127.0.0.1:8000/recognize",
  "timeout": 30.0,
  "save_last_frame": true
}
```

---

## How to Run

### 1. Run (Recurring every 5 seconds by default)
Just run the command — it will automatically grab a frame and query your ANPR server every 5 seconds:
```bash
python3 camera_test.py
```
*(Press `Ctrl + C` anytime to stop)*

### 2. Custom Interval (e.g. 3s or 10s)
```bash
python3 camera_test.py --interval 3.0
```

### 3. Single-Shot Mode (Run only once)
```bash
python3 camera_test.py --once
```

### 4. Override Camera or Server URLs on the Fly
```bash
python3 camera_test.py --camera-url "rtsp://..." --server-url "http://127.0.0.1:8000/recognize"
```

### 5. Offline Testing with a Local Image File
```bash
python3 camera_test.py --image "sample_car.jpg"
```
