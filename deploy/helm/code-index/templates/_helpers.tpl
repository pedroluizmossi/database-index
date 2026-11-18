{{- define "code-index.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "code-index.fullname" -}}
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

{{- define "code-index.selectorLabels" -}}
app.kubernetes.io/name: {{ include "code-index.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "code-index.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{ include "code-index.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "code-index.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "code-index.apiService" -}}
{{ include "code-index.fullname" . }}-api
{{- end -}}

{{- define "code-index.secretName" -}}
{{- .Values.secrets.existingSecret | default (printf "%s-secrets" (include "code-index.fullname" .)) -}}
{{- end -}}

{{- define "code-index.dbSecretName" -}}
{{- if .Values.database.url -}}
{{- printf "%s-db" (include "code-index.fullname" .) -}}
{{- else -}}
{{- required "defina database.existingSecret ou database.url" .Values.database.existingSecret -}}
{{- end -}}
{{- end -}}

{{- define "code-index.dbSecretKey" -}}
{{- if .Values.database.url -}}POSTGRES_URL{{- else -}}{{ .Values.database.key }}{{- end -}}
{{- end -}}

{{/* Hosts aceitos no header Host: o público, os extras e os nomes do Service interno (CLI remota). */}}
{{- define "code-index.allowedHosts" -}}
{{- $svc := include "code-index.apiService" . -}}
{{- $hosts := list $svc (printf "%s.%s" $svc .Release.Namespace) (printf "%s.%s.svc" $svc .Release.Namespace) (printf "%s.%s.svc.cluster.local" $svc .Release.Namespace) -}}
{{- concat $hosts .Values.extraAllowedHosts | join "," -}}
{{- end -}}

{{- define "code-index.internalUrl" -}}
http://{{ include "code-index.apiService" . }}.{{ .Release.Namespace }}.svc.cluster.local:{{ .Values.service.port }}
{{- end -}}

{{/* Ambiente comum a api, worker e migrations. */}}
{{- define "code-index.env" -}}
- name: POSTGRES_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "code-index.dbSecretName" . }}
      key: {{ include "code-index.dbSecretKey" . }}
- name: HOME
  value: /tmp
{{- end -}}

{{- define "code-index.envFrom" -}}
- configMapRef:
    name: {{ include "code-index.fullname" . }}-config
- secretRef:
    name: {{ include "code-index.secretName" . }}
{{- end -}}

{{/* Worker: só os segredos que ele usa (Voyage, GitHub, Wiki.js). Nunca CODE_INDEX_AUTH_SECRET nem os
segredos do login: o worker lê conteúdo não confiável (clones, páginas), e a chave da sessão vazada
permitiria forjar cookies de admin na api. */}}
{{- define "code-index.workerSecretEnv" -}}
{{- $secret := include "code-index.secretName" . -}}
{{- range $k := .Values.worker.secretKeys }}
- name: {{ $k }}
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ $k }}
      optional: true
{{- end }}
{{- end -}}

{{/* Ambiente do mapa "config" (mesmo filtro do configmap.yaml), para pods que não usam o ConfigMap. */}}
{{- define "code-index.configEnv" -}}
{{- range $k, $v := .Values.config }}
{{- if not (eq (toString $v) "") }}
- name: {{ $k }}
  value: {{ $v | toString | quote }}
{{- end }}
{{- end }}
{{- end -}}

{{/* Pods que falam com o Postgres (NetworkPolicy). */}}
{{- define "code-index.dbAccessLabel" -}}
code-index/db-access: "true"
{{- end -}}

{{/* Saída de api, worker e migrations: DNS, Postgres e a internet (Voyage, GitHub, Wiki, login). */}}
{{- define "code-index.appEgress" -}}
{{- $np := .Values.networkPolicy -}}
- ports:
    - {port: 53, protocol: UDP}
    - {port: 53, protocol: TCP}
{{- if $np.postgres.enabled }}
- to:
    - podSelector:
        {{- toYaml $np.postgres.podSelector | nindent 8 }}
  ports:
    - {port: 5432, protocol: TCP}
{{- end }}
- to:
    - ipBlock:
        cidr: {{ $np.egress.cidr }}
        {{- with $np.egress.except }}
        except:
          {{- toYaml . | nindent 10 }}
        {{- end }}
  ports:
    {{- range $np.egress.ports }}
    - {port: {{ . }}, protocol: TCP}
    {{- end }}
{{- end -}}

{{/* Sonda HTTP: o TrustedHost recusa o IP do pod, então manda o host público. */}}
{{- define "code-index.probe" -}}
httpGet:
  path: /healthz
  port: http
  httpHeaders:
    - name: Host
      value: {{ .Values.publicHost | quote }}
{{- end -}}
