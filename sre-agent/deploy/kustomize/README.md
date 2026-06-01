# Keos Kustomize overlay

For operators who avoid Helm. Mirrors the Helm chart's resources as plain
manifests. Regenerate from the Helm chart with:

```bash
helm template pocteo ../helm/pocteo \
    --namespace pocteo \
    --set-string auth.apiToken=PLACEHOLDER \
    --set-string auth.hmacKey=PLACEHOLDER \
    > base/all.yaml
```

Then apply via `kustomize`:

```bash
kubectl apply -k overlays/edge
```

## Overlay structure

```
deploy/kustomize/
├── base/                  # raw manifests rendered from Helm
└── overlays/
    ├── laptop/            # k3d / Docker Desktop tweaks
    ├── edge/              # Jetson / Talos: smaller resources, ARM images
    └── prod/              # ingress + cert-manager + Prometheus scrape
```

This skeleton is in the repo for completeness; populate the overlays once
the Helm chart stabilises and your target cluster shape is known.
