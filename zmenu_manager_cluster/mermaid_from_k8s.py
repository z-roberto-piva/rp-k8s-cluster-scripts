#!/usr/bin/env python3
"""
mermaid_from_k8s.py

Generate a Mermaid flowchart from a folder of Kubernetes manifests (YAML).
Relationships covered (best effort):
- Ingress -> Service (by spec.rules[].http.paths[].backend.service.name)
- Service -> Pod/Deployment/StatefulSet/DaemonSet (by label selector)
- HPA -> Deployment/StatefulSet (by spec.scaleTargetRef)
- ConfigMap/Secret -> Pod/Deployment/StatefulSet (by volumes/env/envFrom)
- PVC -> Pod/Deployment/StatefulSet (by volumes[].persistentVolumeClaim.claimName)
- ServiceAccount -> Workloads (by spec.template.spec.serviceAccountName) and RoleBinding/ClusterRoleBinding
- RoleBinding/ClusterRoleBinding -> Role/ClusterRole (by roleRef)
- NetworkPolicy -> Pod/Workload (by podSelector); arrows show "applies to"
- CronJob -> Job -> Pod template
- PodDisruptionBudget -> Deployment/StatefulSet (by label selector)
- Horizontal links are namespaced. Subgraphs per namespace.

Output: Mermaid "flowchart LR" to STDOUT
Usage:
  python mermaid_from_k8s.py /path/to/manifests > out.mmd

Requires: PyYAML (pip install pyyaml)
"""

import sys
import os
import re
import yaml
from collections import defaultdict
from typing import Dict, Any, List, Tuple, Set, Optional

# -------- Utilities --------

def read_yaml_documents(root: str) -> List[Dict[str, Any]]:
    docs = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith((".yml", ".yaml")):
                path = os.path.join(dirpath, fn)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for doc in yaml.safe_load_all(f):
                            if isinstance(doc, dict):
                                docs.append(doc)
                except Exception as e:
                    print(f"# Skipping {path}: {e}", file=sys.stderr)
    return docs

def meta(obj: Dict[str, Any]) -> Tuple[str, str, str]:
    kind = obj.get("kind", "Unknown")
    md = obj.get("metadata", {}) or {}
    name = md.get("name", "noname")
    ns = md.get("namespace", "default")
    return kind, ns, name

def key(kind: str, ns: str, name: str) -> str:
    return f"{kind}|{ns}|{name}"

def labels_of_template(obj: Dict[str, Any]) -> Dict[str, str]:
    tpl = (
        obj.get("spec", {})
        .get("template", {})
        .get("metadata", {})
        .get("labels", {})
    ) or {}
    return tpl

def selector_of(obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sel = obj.get("spec", {}).get("selector")
    if isinstance(sel, dict):
        return sel
    return None

def match_selector(labels: Dict[str, str], selector: Dict[str, Any]) -> bool:
    if not selector:
        return False
    # Support matchLabels only; partial support for matchExpressions
    ml = selector.get("matchLabels", {})
    for k, v in (ml or {}).items():
        if labels.get(k) != v:
            return False
    me = selector.get("matchExpressions", [])
    for expr in me or []:
        key = expr.get("key")
        operator = expr.get("operator")
        values = expr.get("values", [])
        lv = labels.get(key)
        if operator == "In" and lv not in values:
            return False
        if operator == "NotIn" and lv in values:
            return False
        if operator == "Exists" and key not in labels:
            return False
        if operator == "DoesNotExist" and key in labels:
            return False
        # Gt/Lt skipped (rare in labels)
    return True

def sanitize_id(s: str) -> str:
    # Mermaid node ids must be simple; replace nonword
    return re.sub(r"[^\w]", "_", s)

def short_kind(kind: str) -> str:
    # Shorten common kinds
    mapping = {
        "Deployment": "Deploy",
        "StatefulSet": "Sts",
        "DaemonSet": "Ds",
        "HorizontalPodAutoscaler": "HPA",
        "PersistentVolumeClaim": "PVC",
        "PersistentVolume": "PV",
        "ConfigMap": "CM",
        "ServiceAccount": "SA",
        "ClusterRole": "CRole",
        "ClusterRoleBinding": "CRB",
        "Role": "Role",
        "RoleBinding": "RB",
        "NetworkPolicy": "NetPol",
        "PodDisruptionBudget": "PDB",
    }
    return mapping.get(kind, kind)

# -------- Index resources --------

def index_resources(docs: List[Dict[str, Any]]):
    by_kind_ns_name: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = defaultdict(lambda: defaultdict(dict))
    workloads: Set[str] = set()  # kinds with pod templates

    workload_kinds = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
    for d in docs:
        kind, ns, name = meta(d)
        by_kind_ns_name[kind][ns][name] = d
        if kind in workload_kinds or (d.get("spec", {}).get("template")):
            workloads.add(key(kind, ns, name))

    # Build synthetic "PodTemplate" entries for CronJob to Job template visualization
    pod_templates = {}  # key -> labels
    for kind, ns_map in by_kind_ns_name.items():
        for ns, name_map in ns_map.items():
            for name, obj in name_map.items():
                if "template" in obj.get("spec", {}):
                    pod_templates[key(kind, ns, name)] = labels_of_template(obj)

    return by_kind_ns_name, pod_templates

# -------- Relationship extraction --------

def rels_from_ingress(by: Dict, ns: str) -> List[Tuple[str, str, str]]:
    edges = []
    for name, ing in by.get("Ingress", {}).get(ns, {}).items():
        from_id = f"Ingress|{ns}|{name}"
        rules = (ing.get("spec", {}) or {}).get("rules", []) or []
        # Also consider defaultBackend
        default_backend = (ing.get("spec", {}) or {}).get("defaultBackend", {})
        backends = []
        for r in rules:
            paths = (((r or {}).get("http") or {}).get("paths")) or []
            for p in paths:
                svc = (((p or {}).get("backend") or {}).get("service") or {}).get("name")
                if svc:
                    backends.append(svc)
        if default_backend:
            svc = (default_backend.get("service") or {}).get("name")
            if svc:
                backends.append(svc)
        for svc in set(backends):
            to_id = f"Service|{ns}|{svc}"
            edges.append((from_id, to_id, "routes to"))
    return edges

def rels_service_to_workloads(by: Dict, ns: str, pod_templates: Dict[str, Dict[str, str]]) -> List[Tuple[str, str, str]]:
    edges = []
    for name, svc in by.get("Service", {}).get(ns, {}).items():
        sel = svc.get("spec", {}).get("selector") or {}
        if not sel:
            continue
        for k in pod_templates.keys():
            k_kind, k_ns, k_name = k.split("|", 2)
            if k_ns != ns: 
                continue
            labels = pod_templates[k]
            if match_selector(labels, {"matchLabels": sel}):
                edges.append((f"Service|{ns}|{name}", f"{k_kind}|{ns}|{k_name}", "selects"))
    return edges

def rels_hpa(by: Dict, ns: str) -> List[Tuple[str, str, str]]:
    edges = []
    for name, hpa in by.get("HorizontalPodAutoscaler", {}).get(ns, {}).items():
        ref = (hpa.get("spec", {}) or {}).get("scaleTargetRef", {}) or {}
        to_kind = ref.get("kind")
        to_name = ref.get("name")
        if to_kind and to_name:
            edges.append((f"HorizontalPodAutoscaler|{ns}|{name}", f"{to_kind}|{ns}|{to_name}", "scales"))
    return edges

def rels_volumes_env(by: Dict, ns: str) -> List[Tuple[str, str, str]]:
    edges = []
    for kind in ["Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"]:
        for name, wl in by.get(kind, {}).get(ns, {}).items():
            podspec = ((wl.get("spec", {}) or {}).get("template", {}) or {}).get("spec", {}) if kind != "CronJob" else ((wl.get("spec", {}) or {}).get("jobTemplate", {}) or {}).get("spec", {}).get("template", {}).get("spec", {})
            if not podspec:
                continue
            # volumes
            for vol in podspec.get("volumes", []) or []:
                if "configMap" in vol:
                    cmn = (vol["configMap"] or {}).get("name")
                    if cmn:
                        edges.append((f"{kind}|{ns}|{name}", f"ConfigMap|{ns}|{cmn}", "mounts"))
                if "secret" in vol:
                    sn = (vol["secret"] or {}).get("secretName")
                    if sn:
                        edges.append((f"{kind}|{ns}|{name}", f"Secret|{ns}|{sn}", "mounts"))
                if "persistentVolumeClaim" in vol:
                    pvc = (vol["persistentVolumeClaim"] or {}).get("claimName")
                    if pvc:
                        edges.append((f"{kind}|{ns}|{name}", f"PersistentVolumeClaim|{ns}|{pvc}", "uses"))
            # env & envFrom
            for c in podspec.get("containers", []) or []:
                for env in c.get("env", []) or []:
                    value_from = env.get("valueFrom") or {}
                    if "configMapKeyRef" in value_from:
                        cmn = value_from["configMapKeyRef"].get("name")
                        if cmn:
                            edges.append((f"{kind}|{ns}|{name}", f"ConfigMap|{ns}|{cmn}", "reads"))
                    if "secretKeyRef" in value_from:
                        sn = value_from["secretKeyRef"].get("name")
                        if sn:
                            edges.append((f"{kind}|{ns}|{name}", f"Secret|{ns}|{sn}", "reads"))
                for eff in c.get("envFrom", []) or []:
                    if "configMapRef" in eff and eff["configMapRef"].get("name"):
                        edges.append((f"{kind}|{ns}|{name}", f"ConfigMap|{ns}|{eff['configMapRef']['name']}", "reads"))
                    if "secretRef" in eff and eff["secretRef"].get("name"):
                        edges.append((f"{kind}|{ns}|{name}", f"Secret|{ns}|{eff['secretRef']['name']}", "reads"))
            # SA
            sa = podspec.get("serviceAccountName")
            if sa:
                edges.append((f"{kind}|{ns}|{name}", f"ServiceAccount|{ns}|{sa}", "runs as"))
    return edges

def rels_sa_rbac(by: Dict, ns: str) -> List[Tuple[str, str, str]]:
    edges = []
    # RoleBinding in ns
    for name, rb in by.get("RoleBinding", {}).get(ns, {}).items():
        role_ref = (rb.get("roleRef") or {})
        role_kind = role_ref.get("kind")
        role_name = role_ref.get("name")
        if role_kind and role_name:
            edges.append((f"RoleBinding|{ns}|{name}", f"{role_kind}|{ns}|{role_name}" if role_kind=="Role" else f"{role_kind}|cluster|{role_name}", "binds"))
        for s in rb.get("subjects", []) or []:
            if s.get("kind") == "ServiceAccount":
                sa_ns = s.get("namespace", ns)
                sa_name = s.get("name")
                if sa_name:
                    edges.append((f"RoleBinding|{ns}|{name}", f"ServiceAccount|{sa_ns}|{sa_name}", "to"))
    # ClusterRoleBinding (cluster scoped)
    for name, crb in by.get("ClusterRoleBinding", {}).get("cluster", {}).items():
        role_ref = (crb.get("roleRef") or {})
        role_name = role_ref.get("name")
        if role_name:
            edges.append((f"ClusterRoleBinding|cluster|{name}", f"ClusterRole|cluster|{role_name}", "binds"))
        for s in crb.get("subjects", []) or []:
            if s.get("kind") == "ServiceAccount":
                sa_ns = s.get("namespace", ns)
                sa_name = s.get("name")
                if sa_name:
                    edges.append((f"ClusterRoleBinding|cluster|{name}", f"ServiceAccount|{sa_ns}|{sa_name}", "to"))
    return edges

def rels_pvc_pv(by: Dict) -> List[Tuple[str, str, str]]:
    edges = []
    for ns, name_map in by.get("PersistentVolumeClaim", {}).items():
        for name, pvc in name_map.items():
            vol = (pvc.get("spec") or {}).get("volumeName")
            if vol:
                edges.append((f"PersistentVolumeClaim|{ns}|{name}", f"PersistentVolume|cluster|{vol}", "bound to"))
    return edges

def rels_networkpolicy(by: Dict, ns: str, pod_templates: Dict[str, Dict[str, str]]) -> List[Tuple[str, str, str]]:
    edges = []
    for name, np in by.get("NetworkPolicy", {}).get(ns, {}).items():
        sel = (np.get("spec") or {}).get("podSelector") or {}
        for k in pod_templates.keys():
            k_kind, k_ns, k_name = k.split("|", 2)
            if k_ns != ns: 
                continue
            labels = pod_templates[k]
            if match_selector(labels, sel):
                edges.append((f"NetworkPolicy|{ns}|{name}", f"{k_kind}|{ns}|{k_name}", "applies"))
    return edges

def rels_pdb(by: Dict, ns: str, pod_templates: Dict[str, Dict[str, str]]) -> List[Tuple[str, str, str]]:
    edges = []
    for name, pdb in by.get("PodDisruptionBudget", {}).get(ns, {}).items():
        sel = selector_of(pdb) or {}
        for k in pod_templates.keys():
            k_kind, k_ns, k_name = k.split("|", 2)
            if k_ns != ns:
                continue
            labels = pod_templates[k]
            if match_selector(labels, sel):
                edges.append((f"PodDisruptionBudget|{ns}|{name}", f"{k_kind}|{ns}|{k_name}", "protects"))
    return edges

# -------- Mermaid rendering --------

def node_line(kind: str, ns: str, name: str) -> str:
    nid = sanitize_id(f"{kind}|{ns}|{name}")
    label = f"{short_kind(kind)}\\n{name}"
    shape = ""
    if kind in ("Service", "Ingress"):
        shape = ")"
        return f'{nid}("{label}")'
    if kind in ("ConfigMap", "Secret", "PersistentVolumeClaim", "PersistentVolume"):
        return f"{nid}{{{{{label}}}}}"  # curly for data-ish
    if kind in ("HorizontalPodAutoscaler", "NetworkPolicy", "PodDisruptionBudget"):
        return f"{nid}[[{label}]]"
    if kind in ("Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding", "ServiceAccount"):
        return f"{nid}([{label}])"
    # default: rectangle
    return f"{nid}[{label}]"

def edge_line(from_key: str, to_key: str, label: str) -> str:
    fid = sanitize_id(from_key)
    tid = sanitize_id(to_key)
    return f"{fid} -- {label} --> {tid}"

def render_mermaid(by: Dict, edges: List[Tuple[str, str, str]]):
    out = []
    out.append("flowchart LR")
    # Cluster resources
    cluster_kinds = {"PersistentVolume", "ClusterRole", "ClusterRoleBinding"}
    # collect namespaces
    namespaces: Set[str] = set()
    for kind, ns_map in by.items():
        for ns in ns_map.keys():
            if ns != "cluster":
                namespaces.add(ns)
    # Subgraphs per namespace
    for ns in sorted(namespaces):
        out.append(f'  subgraph "{ns}"')
        # print nodes
        for kind, ns_map in by.items():
            if ns in ns_map:
                for name in ns_map[ns].keys():
                    out.append("    " + node_line(kind, ns, name))
        out.append("  end")
    # Cluster scope nodes
    if any(k in by for k in cluster_kinds):
        out.append('  subgraph "cluster-scope"')
        for kind in cluster_kinds:
            for name in by.get(kind, {}).get("cluster", {}).keys():
                out.append("    " + node_line(kind, "cluster", name))
        out.append("  end")
    # Edges
    for f, t, lbl in edges:
        out.append("  " + edge_line(f, t, lbl))
    return "\n".join(out)

# -------- Main --------

def main():
    if len(sys.argv) < 2:
        print("Usage: python mermaid_from_k8s.py <manifests_dir>", file=sys.stderr)
        sys.exit(1)
    root = sys.argv[1]
    docs = read_yaml_documents(root)
    if not docs:
        print("# No YAML manifests found", file=sys.stderr)
        sys.exit(2)
    by, pod_templates = index_resources(docs)

    edges: List[Tuple[str, str, str]] = []
    # Build edges per namespace
    all_namespaces = set()
    for kind, ns_map in by.items():
        for ns in ns_map.keys():
            all_namespaces.add(ns)
    for ns in sorted([n for n in all_namespaces if n != "cluster"]):
        edges += rels_from_ingress(by, ns)
        edges += rels_service_to_workloads(by, ns, pod_templates)
        edges += rels_hpa(by, ns)
        edges += rels_volumes_env(by, ns)
        edges += rels_sa_rbac(by, ns)
        edges += rels_networkpolicy(by, ns, pod_templates)
        edges += rels_pdb(by, ns, pod_templates)
    edges += rels_pvc_pv(by)

    print(render_mermaid(by, edges))

if __name__ == "__main__":
    main()
