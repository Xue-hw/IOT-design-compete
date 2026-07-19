from __future__ import annotations


# 这些标识暂时保留，用来追踪 AS7341 经 ESP32-C3 上传的物理来源。
# 它们不是第三个独立产品节点，也不代表 EYE/Cube 已完成正式绑定。
PROVISIONAL_SENSOR_PROXY_IDS = frozenset(
    {
        "focuscube-c3-proxy-01",
    }
)


def describe_device(device_id: str, source: str) -> dict[str, object]:
    """Return non-breaking identity metadata for status consumers."""

    is_sensor_proxy = device_id in PROVISIONAL_SENSOR_PROXY_IDS
    return {
        "product_node": not is_sensor_proxy,
        "device_role": "sensor_proxy" if is_sensor_proxy else "primary_device",
        "physical_source": source,
    }
