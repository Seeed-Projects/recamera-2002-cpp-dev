import diagnose_demo


def manifest(long_running=False, requires_camera=False):
    return {
        "name": "demo_x",
        "build": {"binary": "demo_x"},
        "runtime": {"long_running": long_running, "requires_camera": requires_camera},
    }


def test_missing_install_is_reported():
    findings, hints, ok = diagnose_demo.classify(
        manifest(), {"dir": "No such file or directory", "logs": "", "processes": "",
                     "resources": "", "kernel": "", "devices": ""}, {})
    assert not ok
    assert any("directory missing" in item for item in findings)
    assert any("install" in item for item in hints)


def test_long_running_without_pid_is_reported():
    findings, hints, ok = diagnose_demo.classify(
        manifest(long_running=True), {"dir": "demo_x\ndemo.json", "logs": "", "processes": "",
                                      "resources": "", "kernel": "", "devices": "", "pid": "pid="}, {})
    assert any("no run.pid" in item for item in findings)


def test_camera_service_conflict_is_reported():
    findings, hints, ok = diagnose_demo.classify(
        manifest(requires_camera=True), {"dir": "demo_x", "logs": "", "processes": "root sscma-node",
                                         "resources": "", "kernel": "", "devices": "/dev/video0",
                                         "pid": "pid="}, {})
    assert any("camera services" in item for item in findings)


def test_ion_error_is_reported():
    findings, hints, ok = diagnose_demo.classify(
        manifest(), {"dir": "demo_x", "logs": "ion ioctl fail:: Out of memory", "processes": "",
                     "resources": "", "kernel": "", "devices": "", "pid": "pid="}, {})
    assert any("ION" in item for item in findings)
    assert any("ION" in item or "memory" in item for item in hints)


def test_healthy_long_running_process():
    findings, hints, ok = diagnose_demo.classify(
        manifest(long_running=True),
        {"dir": "total 20\n-rwxr-xr-x 1 recamera recamera 10064 Oct 22 12:40 demo_x\n"
                "-rw-r--r-- 1 recamera recamera   549 Oct 22 12:40 demo.json",
         "logs": "sleeper ready", "processes": "/userdata/demos/demo_x/demo_x",
         "resources": "Mem: 100 60 20", "kernel": "", "devices": "/dev/video0 /dev/snd/pcmC0D0p",
         "pid": "pid=123"},
        {"demo_x": "hash", "demo.json": "hash"},
    )
    assert ok, findings