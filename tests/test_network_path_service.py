"""network_path_service 분류 로직 테스트 — 스냅샷 주입으로 PowerShell 없이 검증."""
from services.network_path_service import (
    SERVING_HOTSPOT,
    SERVING_NONE,
    SERVING_OTHER,
    SERVING_SAME,
    UPLINK_ETHERNET,
    UPLINK_NONE,
    UPLINK_WIFI,
    choose_serving_ip,
    classify_snapshot,
    order_serving_ips,
)


def _snapshot(
    *,
    addresses=None,
    profiles=None,
    adapters=None,
    neighbors=None,
) -> dict:
    return {
        "Addresses": addresses or [],
        "Profiles": profiles or [],
        "Adapters": adapters or [],
        "Neighbors": neighbors or [],
    }


_ETH_ADAPTER = {
    "Name": "이더넷",
    "InterfaceDescription": "Realtek Gaming 2.5GbE",
    "PhysicalMediaType": "802.3",
    "Status": "Up",
}
_WIFI_ADAPTER = {
    "Name": "Wi-Fi",
    "InterfaceDescription": "RZ616 Wi-Fi 6E",
    "PhysicalMediaType": "Native 802.11",
    "Status": "Up",
}
_HOTSPOT_ADAPTER = {
    "Name": "로컬 영역 연결* 2",
    "InterfaceDescription": "Microsoft Wi-Fi Direct Virtual Adapter",
    "PhysicalMediaType": "Native 802.11",
    "Status": "Up",
}


def test_same_router_ethernet():
    """같은 공유기: 업링크 어댑터 = 서빙 어댑터."""
    snap = _snapshot(
        addresses=[{"InterfaceAlias": "이더넷", "IPAddress": "192.168.31.233"}],
        profiles=[{"InterfaceAlias": "이더넷", "Name": "네트워크", "IPv4Connectivity": 4}],
        adapters=[_ETH_ADAPTER],
    )
    state = classify_snapshot(snap, "https://192.168.31.233:18443")
    assert state.uplink_kind == UPLINK_ETHERNET
    assert state.serving_kind == SERVING_SAME
    assert state.serving_ip == "192.168.31.233"


def test_wifi_uplink_plus_hotspot():
    """Wi-Fi 인터넷 + PC 핫스팟: 서빙이 192.168.137.1이면 핫스팟 판정."""
    snap = _snapshot(
        addresses=[
            {"InterfaceAlias": "Wi-Fi", "IPAddress": "192.168.0.10"},
            {"InterfaceAlias": "로컬 영역 연결* 2", "IPAddress": "192.168.137.1"},
        ],
        profiles=[
            {"InterfaceAlias": "Wi-Fi", "Name": "HomeWiFi", "IPv4Connectivity": 4},
            {"InterfaceAlias": "로컬 영역 연결* 2", "Name": "네트워크 2", "IPv4Connectivity": 3},
        ],
        adapters=[_WIFI_ADAPTER, _HOTSPOT_ADAPTER],
    )
    state = classify_snapshot(snap, "https://192.168.137.1:18443")
    assert state.uplink_kind == UPLINK_WIFI
    assert state.uplink_name == "HomeWiFi"
    assert state.serving_kind == SERVING_HOTSPOT
    assert state.hotspot_on is True
    assert state.hotspot_ip == "192.168.137.1"


def test_hotspot_clients_counted():
    """핫스팟 대역 ARP 이웃을 접속 기기 수로 집계 — 자기 IP·무응답 항목 제외."""
    snap = _snapshot(
        addresses=[
            {"InterfaceAlias": "이더넷", "IPAddress": "192.168.31.233"},
            {"InterfaceAlias": "로컬 영역 연결* 2", "IPAddress": "192.168.137.1"},
        ],
        profiles=[{"InterfaceAlias": "이더넷", "Name": "네트워크", "IPv4Connectivity": 4}],
        adapters=[_ETH_ADAPTER, _HOTSPOT_ADAPTER],
        neighbors=[
            {"InterfaceAlias": "로컬 영역 연결* 2", "IPAddress": "192.168.137.1", "State": "Reachable"},
            {"InterfaceAlias": "로컬 영역 연결* 2", "IPAddress": "192.168.137.55", "State": "Reachable"},
            {"InterfaceAlias": "로컬 영역 연결* 2", "IPAddress": "192.168.137.87", "State": "Stale"},
            {"InterfaceAlias": "로컬 영역 연결* 2", "IPAddress": "192.168.137.99", "State": "Unreachable"},
            # 브로드캐스트 주소는 기기가 아니다
            {"InterfaceAlias": "로컬 영역 연결* 2", "IPAddress": "192.168.137.255", "State": "Permanent"},
        ],
    )
    state = classify_snapshot(snap, "https://192.168.31.233:18443")
    assert state.hotspot_on is True
    assert state.hotspot_clients == 2  # Reachable + Stale만 집계
    # 서빙은 이더넷 — 핫스팟은 켜져 있지만 QR은 이더넷을 가리킴(불일치 상황)
    assert state.serving_kind == SERVING_SAME


def test_wifi_uplink_same_network():
    """Wi-Fi 인터넷이고 폰도 같은 Wi-Fi로 들어오는 경우 — same 판정 + SSID 보존."""
    snap = _snapshot(
        addresses=[{"InterfaceAlias": "Wi-Fi", "IPAddress": "192.168.0.10"}],
        profiles=[{"InterfaceAlias": "Wi-Fi", "Name": "Office5G", "IPv4Connectivity": 4}],
        adapters=[_WIFI_ADAPTER],
    )
    state = classify_snapshot(snap, "https://192.168.0.10:18443")
    assert state.serving_kind == SERVING_SAME
    assert state.serving_name == "Office5G"


def test_other_adapter_not_hotspot():
    """서빙 어댑터가 업링크와 다르고 핫스팟 IP 대역도 아니면 other."""
    snap = _snapshot(
        addresses=[
            {"InterfaceAlias": "이더넷", "IPAddress": "192.168.31.233"},
            {"InterfaceAlias": "이더넷 2", "IPAddress": "10.10.0.5"},
        ],
        profiles=[{"InterfaceAlias": "이더넷", "Name": "네트워크", "IPv4Connectivity": 4}],
        adapters=[_ETH_ADAPTER, {**_ETH_ADAPTER, "Name": "이더넷 2"}],
    )
    state = classify_snapshot(snap, "https://10.10.0.5:18443")
    assert state.serving_kind == SERVING_OTHER
    assert state.serving_name == "이더넷 2"


def test_no_snapshot_and_server_off():
    """PowerShell 실패/비Windows + 서버 주소 없음 → 둘 다 none."""
    state = classify_snapshot(None, "")
    assert state.uplink_kind == UPLINK_NONE
    assert state.serving_kind == SERVING_NONE


def test_snapshot_without_internet_profile():
    """인터넷 프로필이 없으면(오프라인) 업링크 none, 서빙은 주소로 판정."""
    snap = _snapshot(
        addresses=[{"InterfaceAlias": "이더넷", "IPAddress": "192.168.31.233"}],
        profiles=[{"InterfaceAlias": "이더넷", "Name": "네트워크", "IPv4Connectivity": 3}],
        adapters=[_ETH_ADAPTER],
    )
    state = classify_snapshot(snap, "https://192.168.31.233:18443")
    assert state.uplink_kind == UPLINK_NONE
    assert state.serving_kind == SERVING_OTHER


def test_single_item_json_objects():
    """ConvertTo-Json은 항목이 1개면 리스트가 아닌 객체로 반환한다."""
    snap = _snapshot(
        addresses={"InterfaceAlias": "이더넷", "IPAddress": "192.168.31.233"},
        profiles={"InterfaceAlias": "이더넷", "Name": "네트워크", "IPv4Connectivity": 4},
        adapters=_ETH_ADAPTER,
    )
    state = classify_snapshot(snap, "https://192.168.31.233:18443")
    assert state.serving_kind == SERVING_SAME


_VETH_ADAPTER = {
    "Name": "vEthernet (WSL (Hyper-V firewall))",
    "InterfaceDescription": "Hyper-V Virtual Ethernet Adapter",
    "PhysicalMediaType": "Unspecified",
    "Status": "Up",
}


def test_choose_serving_ip_prefers_hotspot():
    """핫스팟 어댑터가 켜져 있으면 후보 순서와 무관하게 137.x를 고른다."""
    ips = ["192.168.31.233", "192.168.137.1", "172.27.208.1"]
    assert choose_serving_ip(ips, snapshot={}) == "192.168.137.1"


def test_choose_serving_ip_prefers_internet_adapter():
    """핫스팟이 없으면 Internet 프로필 어댑터의 IP를 고른다."""
    snap = _snapshot(
        addresses=[
            {"InterfaceAlias": "vEthernet (WSL (Hyper-V firewall))", "IPAddress": "172.27.208.1"},
            {"InterfaceAlias": "이더넷", "IPAddress": "192.168.31.233"},
        ],
        profiles=[{"InterfaceAlias": "이더넷", "Name": "네트워크", "IPv4Connectivity": 4}],
        adapters=[_ETH_ADAPTER, _VETH_ADAPTER],
    )
    ips = ["172.27.208.1", "192.168.31.233"]  # WSL이 첫 항목으로 와도
    assert choose_serving_ip(ips, snapshot=snap) == "192.168.31.233"


def test_choose_serving_ip_skips_virtual_and_apipa():
    """프로필 정보가 없을 때도 가상 어댑터·APIPA는 건너뛴다."""
    snap = _snapshot(
        addresses=[
            {"InterfaceAlias": "vEthernet (WSL (Hyper-V firewall))", "IPAddress": "172.27.208.1"},
            {"InterfaceAlias": "이더넷", "IPAddress": "192.168.31.233"},
        ],
        profiles=[],
        adapters=[_ETH_ADAPTER, _VETH_ADAPTER],
    )
    ips = ["169.254.1.1", "172.27.208.1", "192.168.31.233"]
    assert choose_serving_ip(ips, snapshot=snap) == "192.168.31.233"


def test_choose_serving_ip_fallback():
    """스냅샷이 없거나 후보가 비면 안전한 폴백."""
    assert choose_serving_ip([], snapshot=None) == ""
    assert choose_serving_ip(["10.0.0.2"], snapshot=None) == "10.0.0.2"


def test_order_serving_ips_returns_full_candidate_order():
    """alt 후보용 정렬 — 1순위 뒤에 나머지 후보도 도달 가능성 순으로 유지."""
    snap = _snapshot(
        addresses=[
            {"InterfaceAlias": "vEthernet (WSL (Hyper-V firewall))", "IPAddress": "172.27.208.1"},
            {"InterfaceAlias": "이더넷", "IPAddress": "192.168.31.233"},
        ],
        profiles=[],
        adapters=[_ETH_ADAPTER, _VETH_ADAPTER],
    )
    ips = ["172.27.208.1", "192.168.31.233", "169.254.9.9"]
    ordered = order_serving_ips(ips, snapshot=snap)
    assert ordered[0] == "192.168.31.233"
    # 가상 어댑터·APIPA는 후보 끝으로 밀려나지만 누락되지 않는다
    assert sorted(ordered) == sorted(ips)


def test_order_serving_ips_dedupes_and_empty():
    assert order_serving_ips([], snapshot={}) == []
    assert order_serving_ips(["10.0.0.2", "10.0.0.2"], snapshot={}) == ["10.0.0.2"]
