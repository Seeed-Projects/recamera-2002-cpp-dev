# Official models: `fetch_models.py`

Downloads only from the official URLs recorded in `configs/model-assets.lock.json`, verifying
size and SHA-256 before writing. The script does not install or execute models.

```bash
python3 scripts/fetch_models.py --list
python3 scripts/fetch_models.py --name yolo11n --out ~/reCamera_demo/<demo>/models
python3 scripts/fetch_models.py --all --out ~/reCamera_demo/<demo>/models
python3 scripts/fetch_models.py --name person --dry-run
```

## Locked assets

| Name | Task | Size |
|---|---|---|
| `yolo11n` | detection (COCO 80) | 3.1 MB |
| `yolov8n` | detection (COCO 80) | 3.6 MB |
| `person` | detection (single class) | 2.9 MB |
| `gender` | classification | 0.8 MB |
| `gesture` | detection | 2.2 MB |
| `digital_meter` | detection | 2.2 MB |
| `yolo11n_drone` | detection | 3.1 MB |

## Behavior

- Target file exists and the hash matches: reused directly (`cached: true`).
- Exists but the hash differs: refused; requires `--force`.
- No `sha256` in the lock: refused unless `--allow-unpinned`.
- Downloads go to a temporary directory and move to the target only after validation passes; partial files are never left behind.

pose / segmentation models need qtable hybrid quantization and are not in the lock; convert them
yourself with [convert.md](convert.md).

## Wiring into a demo

After downloading, place the model in `models/` and declare it in `demo.json`:

```json
"model": {
  "file": "models/yolo11n_cv181x_int8.cvimodel",
  "sha256": "<sha256sum output>",
  "size": 3130896,
  "task": "detection"
}
```

`validate_demo.py --mode deliver` verifies the hash.
