{{- define "cutback.name" -}}
{{- printf "%s-cutback" .Release.Name | trunc 53 | trimSuffix "-" -}}
{{- end -}}
{{- define "cutback.labels" -}}
app.kubernetes.io/name: cutback
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}
{{- define "cutback.image" -}}
{{- printf "%s@%s" .Values.image.repository (required "Set image.digest to the reviewed registry sha256 digest" .Values.image.digest) -}}
{{- end -}}
{{- define "cutback.security" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
capabilities: {drop: [ALL]}
seccompProfile: {type: RuntimeDefault}
{{- end -}}
