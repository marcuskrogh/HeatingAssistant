# Workspace

Agreed agent workspace setup for this repository.

## Issue tracker

| Field | Value |
|-------|-------|
| Provider | jira |
| Mirror to markdown | true |
| Mirror path | docs/agents/ISSUES.md |

### Provider settings

#### jira
| Field | Value |
|-------|-------|
| Site | https://marcusknielsen.atlassian.net |
| Project key | SWD |
| Auth | Atlassian MCP (fallback: env JIRA_EMAIL + JIRA_API_TOKEN) |
| Override file | docs/agents/jira.md (optional) |

## Artifacts

| Artifact | Path |
|----------|------|
| Agents dir | docs/agents |
| Workspace | docs/agents/WORKSPACE.md |
| Continuity mirror | docs/agents/ISSUES.md |
| Roadmap | docs/ROADMAP.md |
| Plan | docs/PLAN-<slug>.md |
| Bug | docs/BUG.md |
| Iterate | docs/ITERATE.md |
| Model | docs/MODEL.md |
| Research | docs/RESEARCH.md |

## Delivery

| Field | Value |
|-------|-------|
| Base branch | main |
| Branch pattern | `swd-<n>-<short-description>` |
| Open PR by default | true |
| Merge strategy | merge |
| Require `gh` for review/ship | true |

## Pipeline

| Field | Value |
|-------|-------|
| Skills | explore → (research/model) → define → implement → review-fix → ship; **or** bug → implement → review-fix → ship; **or** ship → iterate → review-fix → ship (+ summarise; `/review` = one-shot) |
| One-issue continuity | true |
| Tracker backend | resolved via provider above (`skills/tracker` when installed) |

## Notes

- Do not invent workspace defaults if this file is missing — re-run `/setup`.
- Cloud Agents may still need a `cursor/…` branch prefix for the hosting environment; map to `swd-<n>-…` in titles, PR text, and Jira links.
