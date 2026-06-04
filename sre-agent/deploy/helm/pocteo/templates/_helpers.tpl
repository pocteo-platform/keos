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

{{/* Pre-install validation — refuse install with empty auth secrets. */}}
{{- define "pocteo.requireAuth" -}}
{{- if not .Values.auth.apiToken -}}
{{ fail "values.auth.apiToken must be set (e.g. --set-string auth.apiToken=$(openssl rand -base64 32))" }}
{{- end -}}
{{- if not .Values.auth.hmacKey -}}
{{ fail "values.auth.hmacKey must be set (e.g. --set-string auth.hmacKey=$(openssl rand -base64 32))" }}
{{- end -}}
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
