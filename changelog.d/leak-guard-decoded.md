---
bump: patch
---
### Security

- **The onboard-repo credential-leak guard scans decoded review output**. It grepped the raw JSON bytes, but `post-review.sh` posts decoded string values, so a credential written with JSON escapes (`sk-...`) passed the scan and would have been published. The guard now also scans every decoded string value and object key, and fails closed (exit 2) when the output is not JSON. Found by the scaffolded reviewer on gAmUssA/reels-producer-skill#69; the new escaped-value, escaped-key and unparseable cases fail on the previous template. Consumers refresh with `scaffold.sh --override`.
