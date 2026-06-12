{{/* Common helpers — Keos Helm chart */}}

{{/* fullname → fully-qualified resource name */}}
{{- define "pocteo.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Standard labels every resource carries */}}
{{- define "pocteo.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: pocteo
{{- with .Values.global.labels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* Image string: <registry>/<repo>:<tag>, falling back to upstream when
     repo is already fully-qualified. */}}
{{/* Build the final container image reference.
     Args (dict):
       global      — .Values.global (provides .imageRegistry)
       repository  — short or fully-qualified image name
       tag         — image tag
       firstParty  — true ⇒ prefix global.imageRegistry. false / unset ⇒
                     emit the repository name verbatim (stock images
                     like ollama, caddy, nats, qdrant, opa pull from
                     their upstream registries, NOT from our org). */}}
{{- define "pocteo.image" -}}
{{- $registry := .global.imageRegistry | default "" -}}
{{- $repo := .repository -}}
{{- $tag := .tag -}}
{{- $firstParty := .firstParty | default false -}}
{{- if and $firstParty $registry -}}
{{ printf "%s/%s:%s" $registry $repo $tag }}
{{- else -}}
{{ printf "%s:%s" $repo $tag }}
{{- end -}}
{{- end -}}

{{/* Pre-install validation — refuse install with empty auth secrets,
     UNLESS auth.existingSecret points at a pre-created Secret (GitOps/SOPS
     path — Helm never sees the plaintext token). */}}
{{- define "pocteo.requireAuth" -}}
{{- if not .Values.auth.existingSecret -}}
{{- if not .Values.auth.apiToken -}}
{{ fail "values.auth.apiToken must be set (e.g. --set-string auth.apiToken=$(openssl rand -base64 32)) — or set auth.existingSecret to use a pre-created (SOPS/GitOps-managed) Secret" }}
{{- end -}}
{{- if not .Values.auth.hmacKey -}}
{{ fail "values.auth.hmacKey must be set (e.g. --set-string auth.hmacKey=$(openssl rand -base64 32)) — or set auth.existingSecret" }}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Name of the Secret holding SHP_API_TOKEN / AUDIT_HMAC_KEY /
     FALCO_WEBHOOK_TOKEN. Defaults to the chart-managed `<fullname>-auth`;
     set `auth.existingSecret` to point at a pre-created Secret instead —
     the GitOps path supplies a SOPS-decrypted Secret this way, so Helm
     never templates the plaintext (Phase 23). */}}
{{- define "pocteo.authSecretName" -}}
{{- .Values.auth.existingSecret | default (printf "%s-auth" (include "pocteo.fullname" .)) -}}
{{- end -}}

{{/* Pod-level security context block. Renders the configured
     `.Values.security.podSecurityContext` verbatim. Components that need to
     deviate (e.g. Ollama writing to its model dir as root) inline an
     override instead of using this helper. */}}
{{- define "pocteo.podSecurityContext" -}}
{{- with .Values.security.podSecurityContext }}
securityContext:
{{ toYaml . | indent 2 }}
{{- end }}
{{- end -}}

{{/* Container-level security context. Use inside a container spec under
     `containers[*].securityContext`. */}}
{{- define "pocteo.containerSecurityContext" -}}
{{- with .Values.security.containerSecurityContext }}
securityContext:
{{ toYaml . | indent 2 }}
{{- end }}
{{- end -}}

{{/* NetworkPolicy egress rules for reaching the Kubernetes API server
     (and optionally the public internet for external LLM APIs). The
     apiserver always lives on a private IP, so we explicitly allow the
     configured apiServerCIDRs on 443/6443 — the previous
     "0.0.0.0/0 except RFC1918" rule blocked it. Render under a netpol's
     `egress:` list. Usage: {{ include "pocteo.apiServerEgress" . | nindent 4 }} */}}
{{- define "pocteo.apiServerEgress" -}}
# Kubernetes API server — reachable only on private IPs (ClusterIP or
# node IP), so allow the configured apiserver CIDRs on 443/6443.
- to:
  {{- range .Values.networkPolicy.apiServerCIDRs }}
    - ipBlock:
        cidr: {{ . }}
  {{- end }}
  ports:
    - { port: 443, protocol: TCP }
    - { port: 6443, protocol: TCP }
{{- if .Values.networkPolicy.allowPublicEgress }}
# Public internet on 443 for external LLM APIs (excludes RFC1918 to limit
# LAN/metadata SSRF). Disable via networkPolicy.allowPublicEgress=false.
- to:
    - ipBlock:
        cidr: 0.0.0.0/0
        except:
          - 10.0.0.0/8
          - 172.16.0.0/12
          - 192.168.0.0/16
  ports:
    - { port: 443, protocol: TCP }
{{- end }}
{{- end -}}
