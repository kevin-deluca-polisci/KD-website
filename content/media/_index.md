---
title: "Media"
---

## Media Mentions and Appearances

Below are my media appearances, quoted commentary, and other public writing.

{{ $media := getCSV "," "data/media.csv" }}

{{ range $media }}
{{ if not (eq .Title "") }}

**[{{ .Title }}]({{ .URL }})**

*{{ .Outlet }}* — {{ .Type }} ({{ .Date | dateFormat "January 2, 2006" }})

{{ if .Description }}{{ .Description }}{{ end }}

{{ end }}
{{ end }}
