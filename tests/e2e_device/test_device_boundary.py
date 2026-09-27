"""실장비 경계 테스트 — 물리 출력 없이 실제 드라이버/스풀러 경로를 검증한다.

L1~L3은 입출력을 스텁으로 대체하지만, 이 스위트는 운영체제 경계까지 실제로 통과한다:

- 카메라: pyvirtualcam(OBS 백엔드) 가상 카메라로 QR 프레임을 송출하고
  실제 cv2.VideoCapture(DirectShow)로 수신해 ScannerView._decode_qr로 복원한다.
  실물 카메라 센서·조명만 수동 영역으로 남는다.
- 프린터: Microsoft Print to PDF에 실제 WindowsPrinterService.print_image로
  인쇄 작업을 스풀하고, 스풀 큐에서 바이트 크기를 확인한 뒤 작업을 삭제한다.
  파일명 대화상자에서 대기하는 동안 삭제하므로 물리 인쇄는 발생하지 않는다.
  실물 용지 인쇄 품질만 수동 영역으로 남는다.
- 오디오: 생성한 wav 파일로 WindowsAudioService.play_file의 실제 winmm MCI
  open/play 명령 수락 여부를 검증한다. 실제 청취 품질만 수동 영역으로 남는다.

장비가 없는 환경에서는 각 테스트가 사유와 함께 skip된다.
"""
from __future__ import annotations

import math
import os
import struct
import threading
import time
import wave
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from e2e.support import TEST_QR_URL, record_metric
from services.qr_generator_service import generate_qr_image
from views.scanner_view import ScannerView

_OBS_CAMERA_NAME = "OBS Virtual Camera"
_PDF_PRINTER_NAME = "Microsoft Print to PDF"
_CAMERA_DECODE_TIMEOUT_SEC = 15.0
_PRINTER_JOB_WAIT_TIMEOUT_SEC = 10.0


def _build_qr_feed_frame(payload: str, width: int, height: int) -> np.ndarray:
    """흰 배경 중앙에 QR을 배치한 RGB 프레임을 만든다."""
    qr_px = min(width, height) * 5 // 6
    qr_rgb = cv2.resize(
        np.asarray(generate_qr_image(payload, output_px=600).convert("RGB")),
        (qr_px, qr_px),
    )
    frame = np.full((height, width, 3), 255, np.uint8)
    top = (height - qr_px) // 2
    left = (width - qr_px) // 2
    frame[top : top + qr_px, left : left + qr_px] = qr_rgb
    return frame


def _find_camera_index(device_name: str) -> int | None:
    """DirectShow 장치 목록에서 이름으로 cv2 인덱스를 찾는다."""
    pygrabber = pytest.importorskip("pygrabber.dshow_graph", reason="pygrabber 미설치")
    devices = pygrabber.FilterGraph().get_input_devices()
    for index, name in enumerate(devices):
        if device_name in name:
            return index
    return None


def test_virtual_camera_feed_decodes_via_real_capture() -> None:
    """가상 카메라 송출 QR이 실제 VideoCapture 경로를 통과해 디코딩된다."""
    pyvirtualcam = pytest.importorskip("pyvirtualcam", reason="pyvirtualcam 미설치")

    try:
        cam_ctx = pyvirtualcam.Camera(width=640, height=480, fps=15, backend="obs")
    except Exception as exc:
        pytest.skip(f"OBS 가상 카메라 드라이버 없음: {exc}")

    with cam_ctx as cam:
        frame = _build_qr_feed_frame(TEST_QR_URL, cam.width, cam.height)
        stop = threading.Event()

        def _feed() -> None:
            while not stop.is_set():
                cam.send(frame)
                cam.sleep_until_next_frame()

        feeder = threading.Thread(target=_feed, daemon=True)
        feeder.start()
        try:
            index = _find_camera_index(_OBS_CAMERA_NAME)
            if index is None:
                pytest.skip(f"{_OBS_CAMERA_NAME} 장치가 DirectShow 목록에 없음")

            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                pytest.fail(f"가상 카메라 인덱스 {index}를 열 수 없습니다")
            try:
                decoded = None
                deadline = time.monotonic() + _CAMERA_DECODE_TIMEOUT_SEC
                frames_read = 0
                while time.monotonic() < deadline:
                    ok, captured = cap.read()
                    if not ok or captured is None:
                        time.sleep(0.1)
                        continue
                    frames_read += 1
                    decoded = ScannerView._decode_qr(captured)
                    if decoded:
                        break
                record_metric("device_camera_frames", frames_read)
            finally:
                cap.release()
        finally:
            stop.set()
            feeder.join(timeout=2)

    assert decoded == TEST_QR_URL
    record_metric("device_camera_qr_decodes")


def test_receipt_print_reaches_real_spooler() -> None:
    """영수증 이미지가 실제 Windows 스풀러 큐까지 도달하고 정리된다."""
    win32print = pytest.importorskip("win32print", reason="pywin32 미설치")
    from services.windows_printer_service import WindowsPrinterService

    service = WindowsPrinterService()
    printers = service.list_printers()
    if _PDF_PRINTER_NAME not in printers:
        pytest.skip(f"{_PDF_PRINTER_NAME} 프린터가 설치되어 있지 않음")

    job_name = f"E2E_DEVICE_PROBE_{os.getpid()}_{int(time.time())}"
    image = Image.new("RGB", (400, 600), "white")
    service.print_image(image, _PDF_PRINTER_NAME, job_name)

    def _enum_jobs() -> list[dict]:
        handle = win32print.OpenPrinter(_PDF_PRINTER_NAME)
        try:
            return list(win32print.EnumJobs(handle, 0, 99, 2))
        finally:
            win32print.ClosePrinter(handle)

    found: dict | None = None
    try:
        deadline = time.monotonic() + _PRINTER_JOB_WAIT_TIMEOUT_SEC
        while time.monotonic() < deadline:
            mine = [j for j in _enum_jobs() if j["pDocument"] == job_name]
            if mine:
                found = mine[0]
                break
            time.sleep(0.2)
        assert found is not None, "인쇄 작업이 스풀 큐에 나타나지 않았습니다"
        assert found["TotalPages"] >= 1
        assert found["Size"] > 0
        record_metric("device_printer_jobs")
    finally:
        # 파일명 입력 대기 중인 작업을 삭제해 물리 출력을 막는다.
        for job in _enum_jobs():
            if job["pDocument"] == job_name:
                try:
                    handle = win32print.OpenPrinter(_PDF_PRINTER_NAME)
                    try:
                        win32print.SetJob(
                            handle, job["JobId"], 0, None, win32print.JOB_CONTROL_DELETE
                        )
                    finally:
                        win32print.ClosePrinter(handle)
                except Exception:
                    pass
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not any(j["pDocument"] == job_name for j in _enum_jobs()):
                break
            time.sleep(0.3)


def test_camera_list_probe_keeps_active_stream_stable() -> None:
    """스트리밍 중 list_cameras() 프로브가 점유 카메라를 건드리지 않는다.

    DirectShow는 사용 중인 장치를 다시 열면 기존 스트림이 튀거나 오픈이
    장시간 블록된다(실측: 읽기 실패→재연결 사이클, 프로브 19.8s).
    ScannerView가 점유한 인덱스는 프로브에서 제외되어야 한다.
    """
    pyvirtualcam = pytest.importorskip("pyvirtualcam", reason="pyvirtualcam 미설치")

    try:
        cam_ctx = pyvirtualcam.Camera(width=640, height=480, fps=15, backend="obs")
    except Exception as exc:
        pytest.skip(f"OBS 가상 카메라 드라이버 없음: {exc}")

    with cam_ctx as cam:
        frame = _build_qr_feed_frame(TEST_QR_URL, cam.width, cam.height)
        stop = threading.Event()

        def _feed() -> None:
            while not stop.is_set():
                cam.send(frame)
                cam.sleep_until_next_frame()

        feeder = threading.Thread(target=_feed, daemon=True)
        feeder.start()
        try:
            index = _find_camera_index(_OBS_CAMERA_NAME)
            if index is None:
                pytest.skip(f"{_OBS_CAMERA_NAME} 장치가 DirectShow 목록에 없음")

            frames: list[float] = []
            statuses: list[tuple[float, str | None]] = []
            scanner = ScannerView(
                camera_index=index,
                on_frame_ready=lambda _b64: frames.append(time.monotonic()),
            )
            scanner.set_camera_status_listener(
                lambda m: statuses.append((time.monotonic(), m))
            )
            scanner.start()
            try:
                deadline = time.monotonic() + 15.0
                while not frames and time.monotonic() < deadline:
                    time.sleep(0.1)
                if not frames:
                    pytest.skip("스트리밍 프레임을 수신하지 못함")
                time.sleep(1.0)

                from services.windows_camera_service import WindowsCameraService

                service = WindowsCameraService()
                service._cached_wmi_names_at = 0.0
                service._cached_opencv_indices.clear()
                probe_start = time.monotonic()
                devices = service.list_cameras()
                probe_ms = (time.monotonic() - probe_start) * 1000.0
                time.sleep(0.5)

                # 스트림이 튀었으면 읽기 실패/재연결 상태 이벤트가 발생한다.
                disturbed = [m for ts, m in statuses if ts >= probe_start and m]
                assert not disturbed, f"프로브 중 카메라 상태 이벤트 발생: {disturbed}"
                assert index in [d.index for d in devices], "점유 카메라가 목록에서 빠짐"
                assert probe_ms < 15000.0, (
                    f"프로브 {probe_ms:.0f}ms — 점유 장치 접촉 징후"
                )
                assert time.monotonic() - frames[-1] < 2.0, (
                    "프로브 후 프레임 스트림이 멈춤"
                )
                record_metric("device_camera_probe_stable")
            finally:
                scanner.release()
        finally:
            stop.set()
            feeder.join(timeout=2)


def test_physical_camera_focus_control_path() -> None:
    """실물 카메라 드라이버가 초점 제어 명령에 실제로 응답하는지 확인한다.

    광학 품질(화질·초점 위치)은 수동 영역이며, 여기서는 제어 명령 경로만 검증한다.
    """
    from services.windows_camera_service import (
        WindowsCameraService,
        apply_focus_mode,
        detect_focus_capability,
    )

    service = WindowsCameraService()
    devices = service.list_cameras()
    if not devices:
        pytest.skip("WMI에 보이는 실물 카메라가 없음")

    cap = cv2.VideoCapture(devices[0].index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        pytest.skip(f"실물 카메라 인덱스 {devices[0].index}를 열 수 없음(사용 중일 수 있음)")
    try:
        capability = detect_focus_capability(cap)
        if capability.autofocus_supported is False:
            pytest.skip("카메라가 자동 초점을 지원하지 않음")

        result = apply_focus_mode(cap, capability, mode="auto")
        assert result.applied, "자동 초점 복귀 명령이 실물 드라이버에서 실패"
        record_metric("device_camera_focus_applies")
    finally:
        cap.release()


def test_audio_play_file_uses_real_mci() -> None:
    """생성 wav가 실제 winmm MCI open/play 경로에서 수락된다."""
    from services.windows_audio_service import WindowsAudioService

    service = WindowsAudioService()
    if service._winmm is None:
        pytest.skip("winmm을 로드할 수 없음")
    try:
        if int(service._winmm.waveOutGetNumDevs()) <= 0:
            pytest.skip("오디오 출력 장치가 없음")
    except Exception:
        pytest.skip("오디오 출력 장치 수를 확인할 수 없음")

    wav_path = Path(os.environ.get("TEMP", ".")) / f"e2e_sound_probe_{os.getpid()}.wav"
    sample_rate = 8000
    samples = int(sample_rate * 0.1)
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(
            b"".join(
                struct.pack(
                    "<h",
                    int(12000 * math.sin(2 * math.pi * 440 * i / sample_rate)),
                )
                for i in range(samples)
            )
        )
    try:
        assert service.play_file(str(wav_path)) is True
        record_metric("device_audio_plays")
    finally:
        service.stop()
        try:
            wav_path.unlink()
        except OSError:
            pass
