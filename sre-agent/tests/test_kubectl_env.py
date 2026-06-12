"""Regression test for the KUBECONFIG env scrub (config.kubectl_env).

A compose-era `KUBECONFIG=/root/.kube/config` baked into an image used to
reach kubectl via the environment and HARD-FAIL in-cluster (uid 1000 can't
read /root), instead of falling back to the ServiceAccount — the bug behind
isolate_pod failing while OPA said allow. `kubectl_env()` must drop the var
whenever there's no usable on-disk kubeconfig.
"""

from __future__ import annotations

import os
from unittest import mock

from agent import config


def test_kubectl_env_strips_poisoned_kubeconfig_when_file_absent() -> None:
    with mock.patch.dict(os.environ, {"KUBECONFIG": "/root/.kube/config"}, clear=False):
        with mock.patch.object(config, "kubectl_auth_args", return_value=[]):
            env = config.kubectl_env()
    assert "KUBECONFIG" not in env


def test_kubectl_env_keeps_kubeconfig_when_file_present() -> None:
    # When kubectl_auth_args() resolves a real --kubeconfig (laptop/demo
    # path), the env var is left intact so both agree on the same file.
    with mock.patch.dict(os.environ, {"KUBECONFIG": "/home/u/.kube/config"}, clear=False):
        with mock.patch.object(
            config, "kubectl_auth_args", return_value=["--kubeconfig", "/home/u/.kube/config"]
        ):
            env = config.kubectl_env()
    assert env.get("KUBECONFIG") == "/home/u/.kube/config"
