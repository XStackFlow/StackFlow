"""PodErrorLogFetcher Node - Fetches error logs from Kubernetes pods for a given service."""

import subprocess
import json
import re
from typing import Any, Dict, List, Optional
from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.log_utils import filter_error_logs
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class PodErrorLogFetcher(BaseNode):
    """
    Node that retrieves logs from the corresponding Kubernetes pod for a service.
    It prioritizes pods with image matches, restarts, or non-Running phases.
    """

    def __init__(
        self,
        service_name: Resolvable[str] = "{{service_name}}",
        namespace: Resolvable[str] = "default",
        k8s_cluster: Resolvable[str] = "",
        image_tag: Resolvable[str] = "{{image_tag}}",
        label_selector: Resolvable[str] = "app",
        **kwargs
    ):
        """Initialize the PodErrorLogFetcher node."""
        super().__init__(**kwargs)
        self.service_name = service_name
        self.namespace = namespace
        self.k8s_cluster = k8s_cluster
        self.image_tag = image_tag
        self.label_selector = label_selector

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the pod log retrieval."""
        service_name = self._service_name
        namespace = self._namespace
        cluster = self._k8s_cluster
        image_tag = self._image_tag
        label_selector = self._label_selector or "app"
        
        if not service_name:
            raise ValueError("service_name is required.")

        logger.info(f"Probing K8s logs (Cluster: {cluster}, Service: {service_name}, Image Tag: {image_tag})...")
        
        # 1. Switch context
        ctx_res = subprocess.run(["kubectx", cluster], capture_output=True, text=True, check=False)
        if ctx_res.returncode != 0:
            raise RuntimeError(f"Failed to switch to cluster {cluster}: {ctx_res.stderr.strip()}")

        # 2. Fetch pod logs
        pod_logs = self._get_pod_logs(service_name, namespace, image_tag=image_tag, label_selector=label_selector)

        return {
            "pod_logs": pod_logs,
        }

    def _get_pod_logs(self, service: str, namespace: str, image_tag: Optional[str] = None, label_selector: str = "app") -> Optional[str]:
        """Sophisticated retrieval of pod logs, prioritizing failing pods and image matches."""
        label = f"{label_selector}={service}"
        logger.info(f"Querying pods: kubectl get pods -n {namespace} -l {label} (image_tag: {image_tag})")

        get_pods_cmd = [
            "kubectl", "get", "pods", "-n", namespace,
            "-l", label,
            "-o", "jsonpath={range .items[*]}{.metadata.name}{'\\t'}{.status.phase}{'\\t'}{.status.containerStatuses[0].restartCount}{'\\t'}{.spec.containers[*].name}{'\\t'}{.spec.containers[*].image}{'\\n'}{end}"
        ]
        result = subprocess.run(get_pods_cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            raise RuntimeError(f"kubectl get pods -n {namespace} -l {label} failed: {result.stderr.strip()}")

        pod_lines = result.stdout.strip().splitlines()
        if not pod_lines:
            raise RuntimeError(f"No pods found matching label '{label}' in namespace '{namespace}'.")

        # Rank pods: Prioritize those with image match, restarts > 0, or NOT Running phase
        ranked_pods = []
        for line in pod_lines:
            parts = line.split('\t')
            if len(parts) >= 3:
                name, phase, restarts = parts[0], parts[1], int(parts[2] or 0)
                containers = parts[3].split() if len(parts) > 3 else []
                images = parts[4].split() if len(parts) > 4 else []

                score = 0
                if image_tag and any(str(image_tag) in img for img in images):
                    score += 500
                if phase != "Running": score += 100
                score += restarts * 10
                ranked_pods.append({"name": name, "containers": containers, "score": score})

        if not ranked_pods:
            raise RuntimeError(f"Found pods for '{label}' but couldn't parse any (raw output: {result.stdout.strip()!r})")

        ranked_pods.sort(key=lambda x: x["score"], reverse=True)
        target_pod = ranked_pods[0]
        pod_name = target_pod["name"]
        containers = target_pod["containers"]
        
        logger.info(f"Selected pod '{pod_name}' (score {target_pod['score']}) for log extraction.")

        all_logs = []
        for container_name in containers:
            logger.info(f"Fetching logs for pod '{pod_name}', container '{container_name}'")
            
            # 1. Try Current Logs first
            curr_cmd = ["kubectl", "logs", pod_name, "-n", namespace, "-c", container_name, "--tail=100", "--since=30m"]
            curr_res = subprocess.run(curr_cmd, capture_output=True, text=True, check=False)
            curr_content = curr_res.stdout.strip() if curr_res.returncode == 0 else ""
            
            filtered_curr = filter_error_logs(curr_content, context_lines=5) if curr_content else None
            
            if filtered_curr:
                all_logs.append(f"--- Container: {container_name} (Current) ---\n{filtered_curr}")
            else:
                # 2. No errors in current logs, fallback to Previous Logs (-p)
                prev_cmd = curr_cmd + ["-p"]
                prev_res = subprocess.run(prev_cmd, capture_output=True, text=True, check=False)
                prev_content = prev_res.stdout.strip() if prev_res.returncode == 0 else ""
                
                if prev_content:
                    filtered_prev = filter_error_logs(prev_content, context_lines=5)
                    if filtered_prev:
                        all_logs.append(f"--- Container: {container_name} (Previous Instance) ---\n{filtered_prev}")

        if all_logs:
            return f"Pod: {pod_name}\n" + "\n\n".join(all_logs)
        
        return f"Pod: {pod_name}\nLogs found, but no explicit error keywords detected in the tail of any container."
