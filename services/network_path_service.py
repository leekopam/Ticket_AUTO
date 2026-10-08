"""PC의 네트워크 경로 탐지 — 인터넷 경로(업링크)와 기기 연결 경로(서빙)를 분리한다.

인터넷 경로는 품질 수치의 원인(이더넷/Wi-Fi)을, 기기 연결 경로는 폰이 PC 서버에
도달하는 네트워크(같은 공유기/PC 핫스팟)를 설명한다.
PowerShell Get-Net* 1회 호출로 어댑터·프로필·주소 스냅샷을 모으고,
판정은 순수 함수로 분리해 pytest로 검증한다.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

UPLINK_ETHERNET = "ethernet"
UPLINK_WIFI = "wifi"
UPLINK_OTHER = "other"
UPLINK_NONE = "none"

SERVING_SAME = "same"        # 업링크 어댑터 = 서빙 어댑터 (같은 공유기)
SERVING_HOTSPOT = "hotspot"  # 이 PC의 모바일 핫스팟 어댑터
SERVING_OTHER = "other"      # 별도 NIC (VPN·제3망)
SERVING_NONE = "none"        # 서버 꺼짐/판정 불가

_PROFILE_INTERNET = 4  # IPv4Connectivity enum: Internet
_PS_TIMEOUT_SEC = 6

_PS_SNAPSHOT = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "$a=Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue"
    " | Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*'}"
    " | Select-Object InterfaceAlias,IPAddress;"
    "$p=Get-NetConnectionProfile -ErrorAction SilentlyContinue"
    " | Select-Object InterfaceAlias,Name,IPv4Connectivity;"
    "$d=Get-NetAdapter -ErrorAction SilentlyContinue"
    " | Select-Object Name,InterfaceDescription,PhysicalMediaType,Status;"
    "$n=Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue"
    " | Where-Object {$_.IPAddress -like '192.168.137.*'}"
    " | Select-Object InterfaceAlias,IPAddress,State;"
    "$hs=$null;try{"
    "$ni=[Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime];"
    "$tp=[Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime];"
    "$m=$tp::CreateFromConnectionProfile($ni::GetInternetConnectionProfile());"
    "$c=$m.GetCurrentAccessPointConfiguration();"
    "$hs=[pscustomobject]@{State=\"$($m.TetheringOperationalState)\";SSID=$c.Ssid;Clients=$m.ClientCount}}catch{};"
    "[pscustomobject]@{Addresses=@($a);Profiles=@($p);Adapters=@($d);Neighbors=@($n);Hotspot=$hs}"
    " | ConvertTo-Json -Depth 4 -Compress"
)

_HOTSPOT_NET = "192.168.137.0/24"


@dataclass(frozen=True)
class NetworkPathState:
    """네트워크 관리 카드에 표시할 두 경로의 판정 결과."""

    uplink_kind: str
    uplink_name: str      # Wi-Fi SSID 또는 프로필명, 없으면 ""
    serving_kind: str
    serving_name: str     # 서빙 네트워크 프로필명 또는 어댑터 별칭
    serving_ip: str       # QR에 실린 서버 IP
    hotspot_on: bool = False      # 모바일 핫스팟 켜짐 (137.x 어댑터 또는 WinRT On)
    hotspot_ip: str = ""
    hotspot_ssid: str = ""        # 핫스팟 네트워크 이름(SSID)
    hotspot_clients: int = 0      # 접속 기기 수 — WinRT 우선, 없으면 ARP 이웃 수


def _collect_snapshot() -> dict | None:
    """Get-Net* 스냅샷을 PowerShell 1회 호출로 수집한다. 실패 시 None."""
    if sys.platform != "win32":
        return None
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _PS_SNAPSHOT,
            ],
            capture_output=True,
            timeout=_PS_TIMEOUT_SEC,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _as_list(value: object) -> list[dict]:
    """ConvertTo-Json은 항목 1개면 객체로 반환한다 — 항상 리스트로 정규화."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return [value] if isinstance(value, dict) else []


def _is_hotspot_ip(ip: str) -> bool:
    """Windows ICS/모바일 핫스팟 — 호스트 측은 192.168.137.0/24를 쓴다."""
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network(_HOTSPOT_NET)
    except ValueError:
        return False


def _is_hotspot_adapter(ip: str, description: str) -> bool:
    """핫스팟 대역 IP이거나, 가상 어댑터 설명에 Wi-Fi Direct/Hosted Network."""
    if _is_hotspot_ip(ip):
        return True
    desc = description.lower()
    return "wi-fi direct virtual" in desc or "hosted network" in desc


def classify_snapshot(snapshot: dict | None, server_addr: str) -> NetworkPathState:
    """스냅샷+서버 주소에서 업링크·서빙 경로를 판정한다 (순수 함수)."""
    server_ip = urlsplit(server_addr).hostname or ""
    if not isinstance(snapshot, dict):
        return NetworkPathState(
            uplink_kind=UPLINK_NONE,
            uplink_name="",
            serving_kind=SERVING_NONE if not server_ip else SERVING_OTHER,
            serving_name="",
            serving_ip=server_ip,
        )

    addresses = _as_list(snapshot.get("Addresses"))
    profiles = _as_list(snapshot.get("Profiles"))
    adapters = {str(a.get("Name") or ""): a for a in _as_list(snapshot.get("Adapters"))}

    # 업링크 — IPv4Connectivity=Internet인 프로필의 어댑터
    uplink_alias = ""
    uplink_name = ""
    for profile in profiles:
        if profile.get("IPv4Connectivity") == _PROFILE_INTERNET:
            uplink_alias = str(profile.get("InterfaceAlias") or "")
            uplink_name = str(profile.get("Name") or "")
            break
    uplink_media = str(adapters.get(uplink_alias, {}).get("PhysicalMediaType") or "")
    if not uplink_alias:
        uplink_kind = UPLINK_NONE
    elif "802.11" in uplink_media:
        uplink_kind = UPLINK_WIFI
    elif "802.3" in uplink_media:
        uplink_kind = UPLINK_ETHERNET
    else:
        uplink_kind = UPLINK_OTHER

    # 서빙 — QR 서버 주소의 IP를 소유한 어댑터
    serving_kind = SERVING_NONE
    serving_name = ""
    if server_ip:
        serving_alias = next(
            (
                str(a.get("InterfaceAlias") or "")
                for a in addresses
                if str(a.get("IPAddress") or "") == server_ip
            ),
            "",
        )
        serving_adapter = adapters.get(serving_alias, {})
        serving_desc = str(serving_adapter.get("InterfaceDescription") or "")
        if _is_hotspot_adapter(server_ip, serving_desc):
            serving_kind = SERVING_HOTSPOT
        elif serving_alias and serving_alias == uplink_alias:
            serving_kind = SERVING_SAME
        elif serving_alias:
            serving_kind = SERVING_OTHER
        else:
            # 어댑터 매칭 실패 — 주소만이라도 판정 근거로 쓴다
            serving_kind = SERVING_HOTSPOT if server_ip.startswith("192.168.137.") else SERVING_OTHER
        profile_name = next(
            (
                str(p.get("Name") or "")
                for p in profiles
                if str(p.get("InterfaceAlias") or "") == serving_alias
            ),
            "",
        )
        serving_name = profile_name or serving_alias
        if serving_kind == SERVING_SAME:
            serving_name = uplink_name or serving_name

    # 핫스팟 상태 — 서빙 어댑터와 무관하게, 137.x 주소를 가진 어댑터가 있으면 켜짐
    hotspot_ip = next(
        (
            str(a.get("IPAddress") or "")
            for a in addresses
            if _is_hotspot_ip(str(a.get("IPAddress") or ""))
        ),
        "",
    )
    hotspot_ssid = ""
    hotspot_clients = 0
    hs = snapshot.get("Hotspot")
    if isinstance(hs, dict):
        hotspot_ssid = str(hs.get("SSID") or "")
        try:
            hotspot_clients = int(hs.get("Clients") or 0)
        except (TypeError, ValueError):
            hotspot_clients = 0
        winrt_on = str(hs.get("State") or "") == "On"
    else:
        winrt_on = False
    neighbors = _as_list(snapshot.get("Neighbors"))
    if not hotspot_clients:
        # ARP 이웃 폴백 — 유효 상태(Reachable/Stale/Delay)만 집계해
        # 브로드캐스트(Permanent)·죽은 항목(Unreachable/Incomplete)을 제외한다
        hotspot_clients = sum(
            1
            for n in neighbors
            if str(n.get("IPAddress") or "") != hotspot_ip
            and str(n.get("State") or "") in ("Reachable", "Stale", "Delay")
        )

    return NetworkPathState(
        uplink_kind=uplink_kind,
        uplink_name=uplink_name,
        serving_kind=serving_kind,
        serving_name=serving_name,
        serving_ip=server_ip,
        hotspot_on=bool(hotspot_ip) or winrt_on,
        hotspot_ip=hotspot_ip,
        hotspot_ssid=hotspot_ssid,
        hotspot_clients=hotspot_clients,
    )


def detect_network_paths(server_addr: str, *, snapshot: dict | None = None) -> NetworkPathState:
    """현재 PC의 인터넷 경로·기기 연결 경로를 판정한다."""
    if snapshot is None:
        snapshot = _collect_snapshot()
    state = classify_snapshot(snapshot, server_addr)
    logger.debug(
        "네트워크 경로 판정: uplink=%s(%s) serving=%s(%s %s) hotspot=%s",
        state.uplink_kind, state.uplink_name,
        state.serving_kind, state.serving_name, state.serving_ip,
        state.hotspot_on,
    )
    return state


# 가상 어댑터 설명 키워드 — QR에 절대 실리면 안 되는 대역(WSL·VMware 등)
_VIRTUAL_DESC_HINTS = ("hyper-v", "vmware", "virtualbox", "loopback", "vethernet")


def order_serving_ips(ips: list[str], *, snapshot: dict | None = None) -> list[str]:
    """LAN IP 후보를 기기 도달 가능성 순으로 정렬한다.

    우선순위: 켜진 핫스팟 어댑터(192.168.137.x) > Internet 프로필 어댑터 >
    가상이 아닌 어댑터 > 나머지(가상·APIPA 포함, 원래 순서).
    QR의 기본 주소와 alt 후보 목록을 같은 규칙으로 만든다.
    """
    unique = list(dict.fromkeys(ips))
    if not unique:
        return []
    if snapshot is None:
        snapshot = _collect_snapshot()

    addresses = _as_list(snapshot.get("Addresses")) if isinstance(snapshot, dict) else []
    profiles = _as_list(snapshot.get("Profiles")) if isinstance(snapshot, dict) else []
    adapters = {str(a.get("Name") or ""): a for a in _as_list(snapshot.get("Adapters"))} if isinstance(snapshot, dict) else {}

    internet_alias = next(
        (
            str(p.get("InterfaceAlias") or "")
            for p in profiles
            if p.get("IPv4Connectivity") == _PROFILE_INTERNET
        ),
        "",
    )
    virtual = {
        name
        for name, a in adapters.items()
        if any(h in str(a.get("InterfaceDescription") or "").lower() for h in _VIRTUAL_DESC_HINTS)
        or name.lower().startswith("vethernet")
    }

    def _alias_of(ip: str) -> str:
        return next(
            (
                str(a.get("InterfaceAlias") or "")
                for a in addresses
                if str(a.get("IPAddress") or "") == ip
            ),
            "",
        )

    hotspot = [ip for ip in unique if _is_hotspot_ip(ip)]
    internet = [
        ip for ip in unique
        if ip not in hotspot and internet_alias and _alias_of(ip) == internet_alias
    ]
    normal = [
        ip for ip in unique
        if ip not in hotspot and ip not in internet
        and not ip.startswith("169.254.")
        and (not _alias_of(ip) or _alias_of(ip) not in virtual)
    ]
    rest = [ip for ip in unique if ip not in hotspot and ip not in internet and ip not in normal]
    return hotspot + internet + normal + rest


def choose_serving_ip(ips: list[str], *, snapshot: dict | None = None) -> str:
    """LAN IP 후보에서 기기가 실제로 도달할 서버 주소를 고른다.

    `order_serving_ips`의 첫 항목 — 우선순위 규칙은 그 함수와 동일하다.
    """
    ordered = order_serving_ips(ips, snapshot=snapshot)
    return ordered[0] if ordered else ""
