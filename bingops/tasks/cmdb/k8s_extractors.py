"""K8s 资源字段提取器。

把 Informer 发来的 K8s 原始对象映射为 v2 动态模型的 fields JSONB。
字段 code 与库内 cmdb_model_fields 定义一一对应（docs/cmdb-model-presets.md）。

resource_type → 模型映射（deployments/statefulsets/daemonsets 归一到 k8s_workload）：
- nodes → k8s_node
- namespaces → k8s_namespace
- pods → k8s_pod
- services → k8s_service
- deployments | statefulsets | daemonsets → k8s_workload
- persistentvolumes → k8s_pv
- persistentvolumeclaims → k8s_pvc
- configmaps | secrets → 无模型，消费端跳过
"""

from __future__ import annotations

import re

# ── resource_type → 模型映射 ───────────────────────────────────────────────────

RESOURCE_TYPE_TO_MODEL: dict[str, str] = {
    "nodes": "k8s_node",
    "namespaces": "k8s_namespace",
    "pods": "k8s_pod",
    "services": "k8s_service",
    "deployments": "k8s_workload",
    "statefulsets": "k8s_workload",
    "daemonsets": "k8s_workload",
    "persistentvolumes": "k8s_pv",
    "persistentvolumeclaims": "k8s_pvc",
}

# 归一到 k8s_workload 时的 workload_type 枚举值
WORKLOAD_TYPE_BY_RESOURCE: dict[str, str] = {
    "deployments": "deployment",
    "statefulsets": "statefulset",
    "daemonsets": "daemonset",
}

# 有对应模型但 Informer 会发、本端不入库的类型（静默跳过）
UNMANAGED_RESOURCE_TYPES = {"configmaps", "secrets"}

# 非模型定义但落库必需的内部键（下划线前缀键天然属于内部元数据，无需登记）
INTERNAL_FIELD_KEYS = {"namespace"}


def filter_by_model_fields(fields: dict, allowed_codes: set[str]) -> tuple[dict, list[str]]:
    """按库内模型字段定义过滤提取结果。

    保留规则：模型定义内的字段 code + 内部元数据键（INTERNAL_FIELD_KEYS
    及下划线前缀键，供关系重建用）。模型定义变更后提取器产出的死键会被剔除，
    避免脏数据落库。

    Returns:
        (filtered, dropped)：过滤后的 fields 与被剔除的键清单。
    """
    filtered = {}
    dropped = []
    for key, value in fields.items():
        if key in allowed_codes or key in INTERNAL_FIELD_KEYS or key.startswith("_"):
            filtered[key] = value
        else:
            dropped.append(key)
    return filtered, dropped


def extract(resource_type: str, obj: dict) -> tuple[dict, str]:
    """提取动态字段与资源状态。

    Args:
        resource_type: Informer 消息中的 resource_type（小写复数）。
        obj: K8s 原始对象（优先取消息 raw 段）。

    Returns:
        (fields, status)：fields 为模型定义的动态字段字典，status 为通用状态列值。
    """
    extractor = _EXTRACTORS[resource_type]
    return extractor(obj, resource_type)


# ── 各模型提取实现 ─────────────────────────────────────────────────────────────


def _extract_node(obj: dict, _rt: str) -> tuple[dict, str]:
    status_obj = obj.get("status") or {}
    spec = obj.get("spec") or {}
    node_info = status_obj.get("nodeInfo") or {}
    labels = (obj.get("metadata") or {}).get("labels") or {}

    internal_ip = next(
        (a.get("address") for a in status_obj.get("addresses") or []
         if a.get("type") == "InternalIP"),
        None,
    )
    fields = {
        "kubelet_version": node_info.get("kubeletVersion"),
        "kernel_version": node_info.get("kernelVersion"),
        "os_image": node_info.get("osImage"),
        "internal_ip": internal_ip,
        "pod_cidr": spec.get("podCIDR"),
        "cpu_capacity": _parse_cpu((status_obj.get("capacity") or {}).get("cpu")),
        "memory_capacity_mb": _parse_memory_mb((status_obj.get("capacity") or {}).get("memory")),
        "instance_id": _parse_instance_id(spec.get("providerID")) or labels.get("alibabacloud.com/instance-id"),
        "nodepool_id": labels.get("alibabacloud.com/nodepool-id"),
    }
    status = "not_ready"
    for cond in status_obj.get("conditions") or []:
        if cond.get("type") == "Ready":
            status = "ready" if cond.get("status") == "True" else "not_ready"
    return _clean(fields), status


def _extract_namespace(obj: dict, _rt: str) -> tuple[dict, str]:
    phase = ((obj.get("status") or {}).get("phase") or "Unknown").lower()
    return {"phase": phase}, phase


def _extract_pod(obj: dict, _rt: str) -> tuple[dict, str]:
    spec = obj.get("spec") or {}
    status_obj = obj.get("status") or {}
    owner_refs = (obj.get("metadata") or {}).get("ownerReferences") or []
    owner = owner_refs[0] if owner_refs else {}
    namespace = (obj.get("metadata") or {}).get("namespace") or ""

    restart_count = sum(
        int(cs.get("restartCount") or 0) for cs in status_obj.get("containerStatuses") or []
    )
    containers = [
        {"name": c.get("name"), "image": c.get("image")} for c in spec.get("containers") or []
    ]
    pvc_names = [
        volume["persistentVolumeClaim"]["claimName"]
        for volume in spec.get("volumes") or []
        if isinstance(volume.get("persistentVolumeClaim"), dict)
        and volume["persistentVolumeClaim"].get("claimName")
    ]
    phase = (status_obj.get("phase") or "unknown").lower()
    fields = {
        "phase": phase,
        "pod_ip": status_obj.get("podIP"),
        "node_name": spec.get("nodeName"),
        "owner_kind": owner.get("kind"),
        "owner_name": owner.get("name"),
        "restart_count": restart_count,
        "qos_class": status_obj.get("qosClass"),
        "containers": containers,
        # 非模型定义字段：仅供关系重建用（下划线前缀 = 内部元数据，前端不渲染）
        "namespace": namespace or None,
        "_pvc_names": pvc_names or None,
    }
    return _clean(fields), phase


def _extract_service(obj: dict, _rt: str) -> tuple[dict, str]:
    spec = obj.get("spec") or {}
    status_obj = obj.get("status") or {}
    namespace = (obj.get("metadata") or {}).get("namespace") or ""

    ingress_list = ((status_obj.get("loadBalancer") or {}).get("ingress")) or []
    lb_ingress = None
    if ingress_list:
        first = ingress_list[0]
        lb_ingress = first.get("ip") or first.get("hostname")

    fields = {
        "service_type": spec.get("type"),
        "cluster_ip": spec.get("clusterIP"),
        "ports": spec.get("ports"),
        "selector": spec.get("selector") or None,
        "lb_ingress": lb_ingress,
        "namespace": namespace or None,
    }
    return _clean(fields), "active"


def _extract_workload(obj: dict, resource_type: str) -> tuple[dict, str]:
    spec = obj.get("spec") or {}
    status_obj = obj.get("status") or {}
    namespace = (obj.get("metadata") or {}).get("namespace") or ""
    template_spec = (spec.get("template") or {}).get("spec") or {}

    if resource_type == "deployments":
        strategy = (spec.get("strategy") or {}).get("type")
    else:
        strategy = (spec.get("updateStrategy") or {}).get("type")

    replicas = spec.get("replicas")
    ready = status_obj.get("readyReplicas") or 0
    if resource_type == "daemonsets":
        # DaemonSet 无 replicas，用期望/就绪节点数表达
        replicas = status_obj.get("desiredNumberScheduled")
        ready = status_obj.get("numberReady") or 0

    if replicas and ready >= replicas:
        status = "ready"
    elif replicas:
        status = "not_ready"
    else:
        status = "unknown"

    fields = {
        "workload_type": WORKLOAD_TYPE_BY_RESOURCE[resource_type],
        "replicas": replicas,
        "ready_replicas": ready,
        "strategy": strategy,
        "images": [c.get("image") for c in template_spec.get("containers") or [] if c.get("image")],
        "namespace": namespace,
    }
    return _clean(fields), status


def _extract_pv(obj: dict, _rt: str) -> tuple[dict, str]:
    spec = obj.get("spec") or {}
    csi = spec.get("csi") or {}
    phase = ((obj.get("status") or {}).get("phase") or "unknown").lower()
    fields = {
        "capacity_gb": _parse_storage_gb((spec.get("capacity") or {}).get("storage")),
        "access_modes": spec.get("accessModes"),
        "storage_class": spec.get("storageClassName"),
        "reclaim_policy": spec.get("persistentVolumeReclaimPolicy"),
        "csi_driver": csi.get("driver"),
        "volume_handle": csi.get("volumeHandle"),
    }
    return _clean(fields), phase


def _extract_pvc(obj: dict, _rt: str) -> tuple[dict, str]:
    spec = obj.get("spec") or {}
    status_obj = obj.get("status") or {}
    namespace = (obj.get("metadata") or {}).get("namespace") or ""
    phase = (status_obj.get("phase") or "unknown").lower()

    requests = (spec.get("resources") or {}).get("requests") or {}
    capacity = requests.get("storage") or (status_obj.get("capacity") or {}).get("storage")
    fields = {
        "capacity_gb": _parse_storage_gb(capacity),
        "access_modes": spec.get("accessModes"),
        "storage_class": spec.get("storageClassName"),
        "volume_name": spec.get("volumeName"),
        "namespace": namespace or None,
    }
    return _clean(fields), phase


_EXTRACTORS = {
    "nodes": _extract_node,
    "namespaces": _extract_namespace,
    "pods": _extract_pod,
    "services": _extract_service,
    "deployments": _extract_workload,
    "statefulsets": _extract_workload,
    "daemonsets": _extract_workload,
    "persistentvolumes": _extract_pv,
    "persistentvolumeclaims": _extract_pvc,
}


# ── 单位解析工具 ───────────────────────────────────────────────────────────────


def _parse_cpu(value: str | None) -> float | None:
    """解析 K8s CPU 数量：'4' → 4.0，'3900m' → 3.9。"""
    if not value:
        return None
    value = str(value)
    if value.endswith("m"):
        try:
            return round(int(value[:-1]) / 1000, 3)
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_memory_mb(value: str | None) -> int | None:
    """解析 K8s 内存数量到 MB：'16002820Ki' → 15627 MB。"""
    if not value:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([A-Za-z]*)", str(value))
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    multipliers = {
        "": 1,
        "k": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
        "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
    }
    if unit not in multipliers:
        return None
    return int(number * multipliers[unit] / 1024**2)


def _parse_storage_gb(value: str | None) -> float | None:
    """解析存储容量到 GB：'20Gi' → 20.0（二进制单位按 1024 折算）。"""
    if not value:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([A-Za-z]*)", str(value))
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    multipliers = {
        "": 1,
        "k": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
        "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
    }
    if unit not in multipliers:
        return None
    return round(number * multipliers[unit] / 1024**3, 2)


def _parse_instance_id(provider_id: str | None) -> str | None:
    """从 spec.providerID 解析云主机实例 ID。

    格式如 aliyun://cn-shanghai.i-uf6xxx、gce://project/zone/instance。
    """
    if not provider_id:
        return None
    tail = provider_id.rsplit(".", 1)[-1] if "." in provider_id else provider_id.rsplit("/", 1)[-1]
    return tail or None


def _clean(fields: dict) -> dict:
    """剔除值为 None / 空容器的字段，保持 fields JSONB 精简。"""
    return {
        key: value for key, value in fields.items()
        if value is not None and value != "" and value != [] and value != {}
    }
