# Document Router

Read only the documents your task needs — do not load everything at once.

| What you want to do | Read first | Read when needed |
|---|---|---|
| The user wants a clickable live view from one sentence | [../workflows/live-demo.md](../workflows/live-demo.md) | [tools/launch.md](tools/launch.md) |
| The URL opens but there is no camera picture | [tools/device-pipeline.md](tools/device-pipeline.md) | [tools/sdk.md](tools/sdk.md) |
| Create a new demo | [tools/scaffold.md](tools/scaffold.md) | [demo-contract.md](demo-contract.md) |
| Integrate the SSCMA SDK / EngineCVI | [tools/sdk.md](tools/sdk.md) | `solutions/model_detector` in `sscma-example-sg200x` |
| Judge whether the device/hardware can do it | [capability-routing.md](capability-routing.md) | [knowhubs overview](../knowhubs/reCamera_KnowHub/overview.md) |
| Use an official ready-made model | [tools/fetch-models.md](tools/fetch-models.md) | [model-conversion.md](../knowhubs/reCamera_KnowHub/model-conversion.md) |
| Convert your own ONNX | [tools/convert.md](tools/convert.md) | [model-conversion.md](../knowhubs/reCamera_KnowHub/model-conversion.md) |
| Cross-compilation failed | [tools/build.md](tools/build.md) | the `build_demo.py` section of [scripts.md](scripts.md) |
| Want to verify logic without a device | [tools/smoke.md](tools/smoke.md) | the `smoke` section of [demo-contract.md](demo-contract.md) |
| Package for a colleague / publish | [tools/package.md](tools/package.md) | [evidence.md](evidence.md) |
| Deploy to the device | [tools/deploy.md](tools/deploy.md) | [overview.md](../knowhubs/reCamera_KnowHub/overview.md) |
| Want to watch the demo live in a browser and tune parameters | [tools/serve.md](tools/serve.md) | the `serve_demo.py` section of [scripts.md](scripts.md) |
| One command to deploy and get a URL | [tools/launch.md](tools/launch.md) | the `launch_demo.py` section of [scripts.md](scripts.md) |
| Capture picture/log evidence | [evidence.md](evidence.md) | the `capture_evidence.py` section of [scripts.md](scripts.md) |
| The score did not pass | [evaluation.md](evaluation.md) | [model-conversion.md](../knowhubs/reCamera_KnowHub/model-conversion.md) |
| Write back to the knowledge base | [knowledge-base.md](knowledge-base.md) | [records](../knowhubs/reCamera_KnowHub/demo-records.md) |

## Pipeline lanes

- **Development**: `new_demo` → `validate_demo` → `build_demo` → `smoke_demo` → `package_demo`. All offline; everything must pass.
- **Model**: `fetch_models` / `convert_model` / `inspect`. A successful conversion only proves it compiled — it does not prove accuracy.
- **Acceptance**: `probe_device` → `deploy_demo` → `capture_evidence` → `evaluate_demo` → `record_demo`. When no device is available these stages are recorded as `not_run`; the other two lanes are unaffected.
- **Host-side viewer (optional)**: `serve_demo` → browser live view/logs/parameter tuning → `capture_evidence --source live`. It does not replace acceptance and does not produce `task-quality` evidence.

## Reports

Every new command writes a six-stage report: `static`, `offline`, `host-baseline`,
`device-upload`, `device-run`, `task-quality`. Schema:
`schemas/report.schema.json`. Passing one stage never substitutes for the
evidence of another stage.
