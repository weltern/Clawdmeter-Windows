# Pricing

This is a trimmed copy of the real `pricing.md` rate card, kept for offline parser
tests. It includes only the three tables the updater parses (Model pricing, Fast
mode, Batch processing) plus enough surrounding prose/headings to exercise the
section-finding logic. Do NOT let tests hit the network — feed this string in.

## Model pricing

The following table shows pricing for all Claude models:

| Model             | Base Input Tokens | 5m Cache Writes | 1h Cache Writes | Cache Hits & Refreshes | Output Tokens |
|-------------------|-------------------|-----------------|-----------------|----------------------|---------------|
| Claude Fable 5      | $10 / MTok        | $12.50 / MTok   | $20 / MTok      | $1 / MTok | $50 / MTok    |
| Claude Mythos 5 ([limited availability](https://anthropic.com/glasswing)) | $10 / MTok        | $12.50 / MTok   | $20 / MTok      | $1 / MTok | $50 / MTok    |
| Claude Opus 4.8     | $5 / MTok         | $6.25 / MTok    | $10 / MTok      | $0.50 / MTok | $25 / MTok    |
| Claude Opus 4.7     | $5 / MTok         | $6.25 / MTok    | $10 / MTok      | $0.50 / MTok | $25 / MTok    |
| Claude Opus 4.6     | $5 / MTok         | $6.25 / MTok    | $10 / MTok      | $0.50 / MTok | $25 / MTok    |
| Claude Opus 4.5   | $5 / MTok         | $6.25 / MTok    | $10 / MTok      | $0.50 / MTok | $25 / MTok    |
| Claude Opus 4.1 ([deprecated](/docs/en/about-claude/model-deprecations)) | $15 / MTok        | $18.75 / MTok   | $30 / MTok      | $1.50 / MTok | $75 / MTok    |
| Claude Opus 4 ([retired, except on Vertex AI](/docs/en/about-claude/model-deprecations)) | $15 / MTok        | $18.75 / MTok   | $30 / MTok      | $1.50 / MTok | $75 / MTok    |
| Claude Haiku 3.5 ([retired, except on Bedrock and Vertex AI](/docs/en/about-claude/model-deprecations)) | $0.80 / MTok      | $1 / MTok       | $1.60 / MTok     | $0.08 / MTok | $4 / MTok     |

<Note>
MTok = Million tokens.
</Note>

### Fast mode pricing

[Fast mode](/docs/en/build-with-claude/fast-mode) provides faster output at premium pricing.

| Model | Input | Output |
|:------|:------|:-------|
| Claude Opus 4.6 / Claude Opus 4.7 | $30 / MTok | $150 / MTok |
| Claude Opus 4.8 | $10 / MTok | $50 / MTok |

### Batch processing

The Batch API allows asynchronous processing with a 50% discount.

| Model             | Batch input      | Batch output    |
|-------------------|------------------|-----------------|
| Claude Fable 5        | $5 / MTok        | $25 / MTok      |
| Claude Mythos 5 ([limited availability](https://anthropic.com/glasswing)) | $5 / MTok        | $25 / MTok      |
| Claude Opus 4.8       | $2.50 / MTok     | $12.50 / MTok   |
| Claude Opus 4.7       | $2.50 / MTok     | $12.50 / MTok   |
| Claude Opus 4.6       | $2.50 / MTok     | $12.50 / MTok   |
| Claude Opus 4.5     | $2.50 / MTok     | $12.50 / MTok   |
| Claude Opus 4.1 ([deprecated](/docs/en/about-claude/model-deprecations)) | $7.50 / MTok     | $37.50 / MTok   |
| Claude Opus 4 ([retired, except on Vertex AI](/docs/en/about-claude/model-deprecations)) | $7.50 / MTok     | $37.50 / MTok   |
| Claude Haiku 3.5 ([retired, except on Bedrock and Vertex AI](/docs/en/about-claude/model-deprecations)) | $0.40 / MTok     | $2 / MTok       |

### Long context pricing

(prose that is not a table, to confirm parsing stops at the next heading)
