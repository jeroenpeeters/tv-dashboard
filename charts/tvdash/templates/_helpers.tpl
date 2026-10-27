{{- define "tvdash.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tvdash.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "tvdash.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "tvdash.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "tvdash.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tvdash.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "tvdash.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "tvdash.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "tvdash.image" -}}
{{- if contains "/OWNER/" .Values.image.repository -}}
{{- fail "image.repository still has the OWNER placeholder: use a packaged release of the chart, or set image.repository" -}}
{{- end -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) -}}
{{- end -}}

{{- define "tvdash.secretName" -}}
{{- if .Values.secret.existingSecret -}}
{{- .Values.secret.existingSecret -}}
{{- else -}}
{{- include "tvdash.fullname" . -}}
{{- end -}}
{{- end -}}

{{/* A hass-token is only expected from the Secret when we know it is there. */}}
{{- define "tvdash.hasHassToken" -}}
{{- if or .Values.secret.existingSecret .Values.secret.hassToken -}}true{{- end -}}
{{- end -}}

{{/* "name=address,name=address", the first one is the default TV. */}}
{{- define "tvdash.tvs" -}}
{{- $parts := list -}}
{{- range .Values.tvs -}}
{{- $parts = append $parts (printf "%s=%s" (required "every tvs entry needs a name" .name) (required "every tvs entry needs an address" .address)) -}}
{{- end -}}
{{- join "," $parts -}}
{{- end -}}
