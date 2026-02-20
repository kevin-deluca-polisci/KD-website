---
title: "Research"
---

## Publications

Below are my peer-reviewed publications. Most papers are available as PDFs.

{{ $pubs := getCSV "," "data/publications.csv" }}

{{ range $pubs }}
{{ if not (eq .Title "") }}
**{{ .Title }}** ({{ .Year }})

{{ if .Author1 }}{{ .Author1 }}{{ if .Author2 }}, {{ .Author2 }}{{ if .Author3 }}, {{ .Author3 }}{{ end }}{{ end }}{{ end }}

*{{ .Journal }}*{{ if .Volume }} {{ .Volume }}{{ if .Issue }}({{ .Issue }}){{ end }}{{ if .Pages }}: {{ .Pages }}{{ end }}{{ end }}

{{ if .DOI }}[Publisher Link]({{ .DOI }}) {{ end }}{{ if .PDF }}[PDF]({{ .PDF }}){{ end }}

---

{{ end }}
{{ end }}
